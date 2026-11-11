"""hermes-timecapsule — sealed messages for later.

Stdlib only. One JSON store, atomic writes, cross-process locking.
A capsule is sealed until its release condition is met; release runs
through a grace ladder; deletion is slow and announced.

    <root>/store.json          — capsules[] + deletions[] (the record)
    <root>/.timecapsule.lock   — cross-process lockfile

Statuses: sealed -> grace -> open, and sealed/grace/open -> pending-deletion
-> (countdown expires) -> removed + a permanent entry in deletions[].

The seal is ACCESS CONTROL, not cryptography: the store holds the content
in plaintext and the code paths enforce who can read it and when. See
SECURITY.md — do not mistake this for an encrypted vault.

Release conditions:
  date    — open after a date/time (UTC, ISO 8601).
  silence — open after N days with no activity on the store (dead-man's
            switch flavour). Activity = seal, open, delete, restore,
            reseal. list/status are read-only and do not reset the clock.
  manual  — only the sealer can release it, by calling open explicitly.

When a condition is met the capsule enters grace for grace_days (default
3). During grace the sealer can re-seal it (back to sealed) or delete it.
After grace it becomes open. grace_days=0 opens immediately.
"""

import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

MESSAGE_MAX = 20000
LABEL_MAX = 100
MAX_CAPSULES = 100
GRACE_DAYS_MAX = 30
DELETE_DAYS_MIN = 1
DELETE_DAYS_MAX = 30
SILENCE_DAYS_MIN = 1
SILENCE_DAYS_MAX = 365

CONDITION_TYPES = ("date", "silence", "manual")
STATUSES = ("sealed", "grace", "open", "pending-deletion")

_ID_RE = re.compile(r"cap-\d{4,}")


def utcnow_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_dt(value):
    """Parse an ISO 8601 date or datetime into an aware UTC datetime."""
    if not isinstance(value, str) or not value.strip():
        raise CapsuleError("release date must be an ISO 8601 date or datetime")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise CapsuleError(f"unparseable date: {value!r} (use ISO 8601, e.g. 2026-11-11T15:00:00Z)")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _dt(dt):
    return dt.isoformat(timespec="seconds")


class CapsuleError(Exception):
    pass


class _FileLock:
    """Cross-process lock via exclusive lockfile creation, with stale-break.

    Same pattern as the estate forum engine (town-square): create the
    lockfile with O_EXCL; if it exists, check its age — a lock older than
    30s is presumed stale (crashed holder) and is broken. Timeout after
    10s of waiting.
    """

    def __init__(self, root):
        self._path = Path(root) / ".timecapsule.lock"

    def __enter__(self):
        deadline = time.time() + 10
        while True:
            try:
                fd = os.open(str(self._path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    age = time.time() - self._path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age > 30:
                    try:
                        self._path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                if time.time() > deadline:
                    raise CapsuleError("timecapsule store is locked by another writer")
                time.sleep(0.05)

    def __exit__(self, *exc):
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        return False


def _atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _check_id(capsule_id):
    if not _ID_RE.fullmatch(str(capsule_id or "")):
        raise CapsuleError(f"invalid capsule id: {capsule_id!r} (expected cap-NNNN)")
    return str(capsule_id)


def _public(capsule):
    """Metadata only — NEVER the content. list/status use this shape."""
    return {
        "id": capsule["id"],
        "label": capsule.get("label", ""),
        "sealed_by": capsule.get("sealed_by", ""),
        "sealed_at": capsule.get("sealed_at", ""),
        "condition": capsule["condition"],
        "grace_days": capsule.get("grace_days", 3),
        "status": capsule["status"],
        "grace_started_at": capsule.get("grace_started_at"),
        "grace_ends_at": capsule.get("grace_ends_at"),
        "pending_deletion_at": capsule.get("pending_deletion_at"),
        "delete_after": capsule.get("delete_after"),
        "opened_at": capsule.get("opened_at"),
    }


class TimeCapsule:
    """One store. All mutating operations take the file lock."""

    def __init__(self, root, now_fn=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._store_path = self.root / "store.json"
        # now_fn: injected clock for tests. Default is real UTC time.
        self._now = now_fn or (lambda: datetime.now(timezone.utc))

    # -- store -----------------------------------------------------------
    def _load(self):
        if not self._store_path.exists():
            return {"version": 1, "next_id": 1, "last_activity_at": None,
                    "capsules": [], "deletions": []}
        try:
            data = json.loads(self._store_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raise CapsuleError("store.json is corrupt — refusing to touch it "
                               "(back it up, fix or delete it by hand)")
        if not isinstance(data, dict) or "capsules" not in data:
            raise CapsuleError("store.json has an unexpected shape — refusing to touch it")
        data.setdefault("deletions", [])
        data.setdefault("next_id", 1)
        data.setdefault("last_activity_at", None)
        return data

    def _save(self, data):
        _atomic_write(self._store_path, json.dumps(data, indent=2))

    # -- state machine ----------------------------------------------------
    def _condition_met(self, capsule, now, last_activity):
        cond = capsule["condition"]
        ctype = cond["type"]
        if ctype == "date":
            return now >= _parse_dt(cond["date"])
        if ctype == "silence":
            if not last_activity:
                return False
            return (now - _parse_dt(last_activity)) >= timedelta(days=cond["days"])
        return False  # manual: only an explicit open by the sealer releases

    def _refresh(self, data, now):
        """Advance statuses lazily. Returns True if anything changed.

        sealed -> grace when the condition is met; grace -> open when the
        grace period ends; pending-deletion -> deleted (announced) when
        the countdown ends. Called at the start of every operation.
        """
        changed = False
        kept = []
        for capsule in data["capsules"]:
            status = capsule["status"]
            if status == "sealed" and self._condition_met(capsule, now, data.get("last_activity_at")):
                capsule["status"] = "grace"
                capsule["grace_started_at"] = _dt(now)
                capsule["grace_ends_at"] = _dt(now + timedelta(days=capsule.get("grace_days", 3)))
                changed = True
                status = "grace"
            if status == "grace":
                ends = _parse_dt(capsule["grace_ends_at"]) if capsule.get("grace_ends_at") else None
                if ends is not None and now >= ends:
                    capsule["status"] = "open"
                    capsule["grace_started_at"] = None
                    capsule["grace_ends_at"] = None
                    changed = True
            if capsule["status"] == "pending-deletion":
                after = _parse_dt(capsule["delete_after"]) if capsule.get("delete_after") else None
                if after is not None and now >= after:
                    data["deletions"].append({
                        "id": capsule["id"],
                        "label": capsule.get("label", ""),
                        "sealed_by": capsule.get("sealed_by", ""),
                        "sealed_at": capsule.get("sealed_at", ""),
                        "deleted_at": _dt(now),
                        "reason": "deletion countdown expired",
                    })
                    changed = True
                    continue  # drop the capsule: it is gone
            kept.append(capsule)
        data["capsules"] = kept
        return changed

    def _touch(self, data, now):
        """Record activity (resets the silence clock)."""
        data["last_activity_at"] = _dt(now)

    def _find(self, data, capsule_id):
        _check_id(capsule_id)
        for capsule in data["capsules"]:
            if capsule["id"] == capsule_id:
                return capsule
        raise CapsuleError(f"no such capsule: {capsule_id}")

    # -- tools ------------------------------------------------------------
    def seal(self, message, condition_type, release_date=None, silence_days=None,
             grace_days=3, delete_days=7, author="", label=""):
        if not isinstance(message, str) or not message.strip():
            raise CapsuleError("message is required")
        if len(message) > MESSAGE_MAX:
            raise CapsuleError(f"message exceeds {MESSAGE_MAX} characters")
        if condition_type not in CONDITION_TYPES:
            raise CapsuleError(f"release_condition must be one of {CONDITION_TYPES}")
        if label and len(label) > LABEL_MAX:
            raise CapsuleError(f"label exceeds {LABEL_MAX} characters")
        try:
            grace_days = int(grace_days)
        except (TypeError, ValueError):
            raise CapsuleError("grace_days must be an integer")
        if not 0 <= grace_days <= GRACE_DAYS_MAX:
            raise CapsuleError(f"grace_days must be 0..{GRACE_DAYS_MAX}")
        try:
            delete_days = int(delete_days)
        except (TypeError, ValueError):
            raise CapsuleError("delete_days must be an integer")
        if not DELETE_DAYS_MIN <= delete_days <= DELETE_DAYS_MAX:
            raise CapsuleError(f"delete_days must be {DELETE_DAYS_MIN}..{DELETE_DAYS_MAX}")

        condition = {"type": condition_type}
        if condition_type == "date":
            if not release_date:
                raise CapsuleError("release_condition 'date' needs release_date (ISO 8601)")
            condition["date"] = _dt(_parse_dt(release_date))
        elif condition_type == "silence":
            if silence_days is None:
                raise CapsuleError("release_condition 'silence' needs silence_days")
            try:
                silence_days = int(silence_days)
            except (TypeError, ValueError):
                raise CapsuleError("silence_days must be an integer")
            if not SILENCE_DAYS_MIN <= silence_days <= SILENCE_DAYS_MAX:
                raise CapsuleError(f"silence_days must be {SILENCE_DAYS_MIN}..{SILENCE_DAYS_MAX}")
            condition["days"] = silence_days

        with _FileLock(self.root):
            data = self._load()
            if len(data["capsules"]) >= MAX_CAPSULES:
                raise CapsuleError(f"store holds the maximum of {MAX_CAPSULES} capsules — "
                                   "open or delete one first")
            now = self._now()
            self._refresh(data, now)
            capsule_id = f"cap-{data['next_id']:04d}"
            data["next_id"] += 1
            capsule = {
                "id": capsule_id,
                "label": (label or "").strip(),
                "sealed_by": (author or "").strip(),
                "sealed_at": _dt(now),
                "message": message,
                "condition": condition,
                "grace_days": grace_days,
                "delete_days": delete_days,
                "status": "sealed",
                "grace_started_at": None,
                "grace_ends_at": None,
                "pending_deletion_at": None,
                "delete_after": None,
                "opened_at": None,
            }
            data["capsules"].append(capsule)
            self._touch(data, now)
            self._save(data)
        return {"sealed": True, **_public(capsule)}

    def list_capsules(self, status_filter=None):
        with _FileLock(self.root):
            data = self._load()
            now = self._now()
            changed = self._refresh(data, now)
            if changed:
                self._save(data)
        capsules = [_public(c) for c in data["capsules"]]
        if status_filter:
            if status_filter not in STATUSES:
                raise CapsuleError(f"status must be one of {STATUSES}")
            capsules = [c for c in capsules if c["status"] == status_filter]
        return {"capsules": capsules, "count": len(capsules)}

    def open_capsule(self, capsule_id, author=""):
        with _FileLock(self.root):
            data = self._load()
            now = self._now()
            self._refresh(data, now)
            capsule = self._find(data, capsule_id)
            self._touch(data, now)
            status = capsule["status"]
            if status == "pending-deletion":
                self._save(data)
                raise CapsuleError(
                    f"{capsule_id} is pending deletion (gone {capsule['delete_after']}) — "
                    "restore it first if you want it back")
            if status == "open":
                capsule["opened_at"] = capsule.get("opened_at") or _dt(now)
                self._save(data)
                return {"ok": True, "id": capsule_id, "label": capsule.get("label", ""),
                        "content": capsule["message"], "opened_at": capsule["opened_at"]}
            if status == "grace":
                self._save(data)
                raise CapsuleError(
                    f"{capsule_id} is in grace until {capsule['grace_ends_at']} — "
                    "the sealer can re-seal it, or wait for the grace to end")
            # sealed
            cond = capsule["condition"]
            if cond["type"] == "manual":
                sealed_by = capsule.get("sealed_by", "")
                if sealed_by and (author or "").strip() != sealed_by:
                    self._save(data)
                    raise CapsuleError(
                        f"{capsule_id} is manual-release and was sealed by {sealed_by} — "
                        "only the sealer can release it")
                capsule["status"] = "grace"
                capsule["grace_started_at"] = _dt(now)
                capsule["grace_ends_at"] = _dt(now + timedelta(days=capsule.get("grace_days", 3)))
                self._refresh(data, now)  # grace_days=0 opens immediately
                if capsule["status"] == "open":
                    capsule["opened_at"] = _dt(now)
                    self._save(data)
                    return {"ok": True, "id": capsule_id, "label": capsule.get("label", ""),
                            "content": capsule["message"], "opened_at": capsule["opened_at"]}
                self._save(data)
                raise CapsuleError(
                    f"{capsule_id} released into grace until {capsule['grace_ends_at']} — "
                    "call open again after the grace ends")
            if cond["type"] == "date":
                self._save(data)
                raise CapsuleError(
                    f"{capsule_id} is sealed until {cond['date']} — "
                    "the date has not arrived yet")
            # silence, not yet met
            last = data.get("last_activity_at")
            self._save(data)
            raise CapsuleError(
                f"{capsule_id} is sealed until {cond['days']} days of silence — "
                f"last activity was {last or 'never'}")

    def delete_capsule(self, capsule_id, delete_days=None):
        with _FileLock(self.root):
            data = self._load()
            now = self._now()
            self._refresh(data, now)
            capsule = self._find(data, capsule_id)
            if capsule["status"] == "pending-deletion":
                return {"ok": True, "id": capsule_id, "status": "pending-deletion",
                        "delete_after": capsule["delete_after"],
                        "note": "already pending deletion"}
            if delete_days is not None:
                try:
                    delete_days = int(delete_days)
                except (TypeError, ValueError):
                    raise CapsuleError("delete_days must be an integer")
                if not DELETE_DAYS_MIN <= delete_days <= DELETE_DAYS_MAX:
                    raise CapsuleError(f"delete_days must be {DELETE_DAYS_MIN}..{DELETE_DAYS_MAX}")
            else:
                delete_days = capsule.get("delete_days", 7)
            capsule["status"] = "pending-deletion"
            capsule["pending_deletion_at"] = _dt(now)
            capsule["delete_after"] = _dt(now + timedelta(days=delete_days))
            capsule["grace_started_at"] = None
            capsule["grace_ends_at"] = None
            self._touch(data, now)
            self._save(data)
        return {"ok": True, "id": capsule_id, "status": "pending-deletion",
                "delete_after": capsule["delete_after"],
                "note": f"restore within {delete_days} days or it is deleted and announced"}

    def restore_capsule(self, capsule_id):
        with _FileLock(self.root):
            data = self._load()
            now = self._now()
            self._refresh(data, now)
            capsule = self._find(data, capsule_id)
            if capsule["status"] != "pending-deletion":
                raise CapsuleError(f"{capsule_id} is not pending deletion (status: {capsule['status']})")
            capsule["status"] = "sealed"
            capsule["pending_deletion_at"] = None
            capsule["delete_after"] = None
            self._touch(data, now)
            self._save(data)
        return {"ok": True, "id": capsule_id, "status": "sealed",
                "note": "restored — the release condition still applies"}

    def reseal_capsule(self, capsule_id, author=""):
        with _FileLock(self.root):
            data = self._load()
            now = self._now()
            self._refresh(data, now)
            capsule = self._find(data, capsule_id)
            if capsule["status"] != "grace":
                raise CapsuleError(f"{capsule_id} is not in grace (status: {capsule['status']}) — "
                                   "only a capsule in grace can be re-sealed")
            sealed_by = capsule.get("sealed_by", "")
            if sealed_by and (author or "").strip() != sealed_by:
                raise CapsuleError(
                    f"{capsule_id} was sealed by {sealed_by} — only the sealer can re-seal it")
            capsule["status"] = "sealed"
            capsule["grace_started_at"] = None
            capsule["grace_ends_at"] = None
            self._touch(data, now)
            self._save(data)
        return {"ok": True, "id": capsule_id, "status": "sealed",
                "note": "re-sealed — the release condition applies again from now"}

    def status(self):
        with _FileLock(self.root):
            data = self._load()
            now = self._now()
            changed = self._refresh(data, now)
            if changed:
                self._save(data)
        counts = {s: 0 for s in STATUSES}
        for capsule in data["capsules"]:
            counts[capsule["status"]] += 1
        return {
            "counts": counts,
            "total_capsules": len(data["capsules"]),
            "deletions_announced": len(data["deletions"]),
            "deletions": data["deletions"],
            "last_activity_at": data.get("last_activity_at"),
            "store": str(self._store_path),
        }
