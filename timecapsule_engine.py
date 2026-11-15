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
LOCK_TIMEOUT_S = 10
LOCK_STALE_S = 30
CLOCK_ROLLBACK_TOLERANCE_S = 300   # 5 minutes
CLOCK_ROLLBACK_LOG_MAX = 10

CONDITION_TYPES = ("date", "silence", "manual")
STATUSES = ("sealed", "grace", "open", "pending-deletion")

_ID_RE = re.compile(r"cap-\d{4,}")


def utcnow_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CapsuleError(Exception):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


def _parse_dt(value):
    """Parse an ISO 8601 date or datetime into an aware UTC datetime."""
    if not isinstance(value, str) or not value.strip():
        raise CapsuleError("release date must be an ISO 8601 date or datetime")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # fromisoformat before 3.11 needs fractional seconds padded to exactly
    # 3 or 6 digits; normalize so the documented formats work everywhere.
    text = re.sub(r"\.(\d+)", lambda m: "." + m.group(1)[:6].ljust(6, "0"), text, count=1)
    try:
        dt = datetime.fromisoformat(text)
    except (ValueError, OverflowError):
        raise CapsuleError(f"unparseable date: {value!r} (use ISO 8601, e.g. 2026-11-11T15:00:00Z)")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _dt(dt):
    return dt.isoformat(timespec="seconds")


def _int_arg(name, value, lo, hi):
    """Strict integer coercion for tool arguments.

    Rejects bool and non-integral floats (e.g. True, 3.9, nan, inf);
    int()-coerces everything else (3.0 -> 3, "3" -> 3) and enforces the
    range. Raises CapsuleError naming the field.
    """
    if isinstance(value, bool):
        raise CapsuleError(f"{name} must be an integer, not a boolean")
    if isinstance(value, float) and not value.is_integer():
        raise CapsuleError(f"{name} must be an integer, got {value!r}")
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        raise CapsuleError(f"{name} must be an integer, got {value!r}")
    if not lo <= n <= hi:
        raise CapsuleError(f"{name} must be {lo}..{hi}, got {n}")
    return n


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
        self._token = uuid.uuid4().hex
        deadline = time.time() + LOCK_TIMEOUT_S
        while True:
            try:
                fd = os.open(str(self._path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(fd, self._token.encode())
                os.close(fd)
                return self
            except (FileExistsError, PermissionError):
                # PermissionError: on Windows the releasing writer's unlink can
                # still be in flight when the next creator probes the path.
                try:
                    age = time.time() - self._path.stat().st_mtime
                except FileNotFoundError:
                    # Lockfile does not exist (e.g. create denied by ACLs):
                    # respect the deadline instead of spinning forever.
                    if time.time() > deadline:
                        raise CapsuleError("timecapsule store could not be locked "
                                           "(permission denied creating the lockfile?)")
                    time.sleep(0.05)
                    continue
                if age > LOCK_STALE_S:
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
            contents = self._path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return False  # already gone, or unreadable — stale-break handles it
        if contents == self._token:
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass
        return False


def _atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        # Windows: the destination may be held open (e.g. the dashboard
        # reading it) — retry the rename briefly before giving up.
        last_exc = None
        for _ in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError as e:
                last_exc = e
                time.sleep(0.05)
        else:
            if last_exc is None:  # pragma: no cover — all attempts raise, so it is set
                raise RuntimeError("atomic write failed without an exception")
            raise last_exc
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


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
                    "clock_high_water": None, "clock_rollbacks": [],
                    "capsules": [], "deletions": []}
        try:
            data = json.loads(self._store_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise CapsuleError("store.json is corrupt — refusing to touch it "
                               "(back it up, fix or delete it by hand)")
        except OSError as e:
            raise CapsuleError(f"store.json could not be read: {e} — refusing to touch it")
        if not isinstance(data, dict) or "capsules" not in data:
            raise CapsuleError("store.json has an unexpected shape — refusing to touch it")
        if not isinstance(data.get("next_id"), int):
            raise CapsuleError("store.json has an unexpected shape — refusing to touch it")
        if not isinstance(data["capsules"], list) or not all(
                self._valid_row(c) for c in data["capsules"]):
            raise CapsuleError("store.json has an unexpected shape — refusing to touch it")
        data.setdefault("deletions", [])
        data.setdefault("last_activity_at", None)
        return data

    @staticmethod
    def _valid_row(c):
        """Shape + scalar-type validation for a capsules[] row.

        Wrong shape is treated as corruption: refuse cleanly, never crash
        a caller with KeyError/TypeError, never touch the file.
        """
        if not isinstance(c, dict):
            return False
        if not _ID_RE.fullmatch(str(c.get("id", ""))):
            return False
        if not isinstance(c.get("message"), str):
            return False
        if c.get("status") not in STATUSES:
            return False
        cond = c.get("condition")
        if not isinstance(cond, dict) or cond.get("type") not in CONDITION_TYPES:
            return False
        if cond.get("type") == "date" and not isinstance(cond.get("date"), str):
            return False
        if cond.get("type") == "silence" and not isinstance(cond.get("days"), int):
            return False
        for field in ("grace_days", "delete_days"):
            v = c.get(field)
            if v is not None and not isinstance(v, int):
                return False
        for field in ("sealed_at", "grace_started_at", "grace_ends_at",
                      "pending_deletion_at", "delete_after", "opened_at"):
            v = c.get(field)
            if v is not None and not isinstance(v, str):
                return False
        return True

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

    def _clock_watch(self, data, now):
        """Warn-and-record clock rollback detection — never refuses.

        The high-water mark is the latest now observed while writing. A
        now earlier than the high-water mark by more than
        CLOCK_ROLLBACK_TOLERANCE_S (dual-boot skew, VM suspend/resume)
        is logged, one entry per episode, and surfaced by status(); the
        store is never refused or bricked in 0.1.x (hard guard + escape
        hatch is a 0.2.0 item). Returns True when the caller should save.
        """
        changed = False
        high = data.get("clock_high_water")
        if high is not None:
            try:
                hw = _parse_dt(high)
            except CapsuleError:
                hw = None
            if hw is not None:
                delta = (now - hw).total_seconds()
                if delta < -CLOCK_ROLLBACK_TOLERANCE_S:
                    log = data.setdefault("clock_rollbacks", [])
                    if not log or log[-1].get("high_water") != high:
                        log.append({"detected_at": _dt(now), "now": _dt(now),
                                    "high_water": high, "delta_s": int(delta)})
                        del log[:-CLOCK_ROLLBACK_LOG_MAX]  # keep the newest N
                        changed = True
        now_iso = _dt(now)
        if high is None or now_iso > high:
            data["clock_high_water"] = now_iso
            changed = True
        return changed

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
                        "pending_deletion_at": capsule.get("pending_deletion_at"),
                        "was_opened": bool(capsule.get("opened_at")),
                        "condition_type": capsule.get("condition", {}).get("type"),
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
        grace_days = _int_arg("grace_days", grace_days, 0, GRACE_DAYS_MAX)
        delete_days = _int_arg("delete_days", delete_days, DELETE_DAYS_MIN, DELETE_DAYS_MAX)

        condition = {"type": condition_type}
        if condition_type == "date":
            if not release_date:
                raise CapsuleError("release_condition 'date' needs release_date (ISO 8601)")
            condition["date"] = _dt(_parse_dt(release_date))
        elif condition_type == "silence":
            if silence_days is None:
                raise CapsuleError("release_condition 'silence' needs silence_days")
            silence_days = _int_arg("silence_days", silence_days, SILENCE_DAYS_MIN, SILENCE_DAYS_MAX)
            condition["days"] = silence_days

        with _FileLock(self.root):
            data = self._load()
            now = self._now()
            self._clock_watch(data, now)
            self._refresh(data, now)  # free expired pending-deletion slots before the cap check
            if len(data["capsules"]) >= MAX_CAPSULES:
                raise CapsuleError(f"store holds the maximum of {MAX_CAPSULES} capsules — "
                                   "open or delete one first")
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
            changed = self._refresh(data, now) or self._clock_watch(data, now)
            if changed:
                self._save(data)
        capsules = [_public(c) for c in data["capsules"]]
        if status_filter:
            if status_filter not in STATUSES:
                raise CapsuleError(f"status must be one of {STATUSES}")
            capsules = [c for c in capsules if c["status"] == status_filter]
        return {"capsules": capsules, "count": len(capsules)}

    def _refuse(self, message, capsule, now, eligible_at=None):
        """Raise an honest refusal whose details are machine-readable.

        details carries the capsule status; when a concrete time is
        known (grace end, condition date, deletion bound), it carries
        eligible_at + remaining_seconds. Never fabricate a time: a
        wrong-author manual refusal has no eligibility.
        """
        details = {"status": capsule["status"]}
        if eligible_at is not None:
            details["eligible_at"] = _dt(eligible_at)
            details["remaining_seconds"] = max(0, int((eligible_at - now).total_seconds()))
        raise CapsuleError(message, details=details)

    def open_capsule(self, capsule_id, author=""):
        with _FileLock(self.root):
            data = self._load()
            now = self._now()
            self._clock_watch(data, now)
            self._refresh(data, now)
            capsule = self._find(data, capsule_id)
            self._touch(data, now)
            status = capsule["status"]
            if status == "pending-deletion":
                self._save(data)
                self._refuse(
                    f"{capsule_id} is pending deletion (gone {capsule['delete_after']}) — "
                    "restore it first if you want it back",
                    capsule, now,
                    eligible_at=_parse_dt(capsule["delete_after"]) if capsule.get("delete_after") else None)
            if status == "open":
                capsule["opened_at"] = capsule.get("opened_at") or _dt(now)
                self._save(data)
                return {"ok": True, "id": capsule_id, "label": capsule.get("label", ""),
                        "content": capsule["message"], "opened_at": capsule["opened_at"]}
            if status == "grace":
                self._save(data)
                self._refuse(
                    f"{capsule_id} is in grace until {capsule['grace_ends_at']} — "
                    "the sealer can re-seal it, or wait for the grace to end",
                    capsule, now,
                    eligible_at=_parse_dt(capsule["grace_ends_at"]) if capsule.get("grace_ends_at") else None)
            # sealed
            cond = capsule["condition"]
            if cond["type"] == "manual":
                sealed_by = capsule.get("sealed_by", "")
                if sealed_by and (author or "").strip() != sealed_by:
                    self._save(data)
                    self._refuse(
                        f"{capsule_id} is manual-release and was sealed by {sealed_by} — "
                        "only the sealer can release it",
                        capsule, now)
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
                self._refuse(
                    f"{capsule_id} released into grace until {capsule['grace_ends_at']} — "
                    "call open again after the grace ends",
                    capsule, now,
                    eligible_at=_parse_dt(capsule["grace_ends_at"]) if capsule.get("grace_ends_at") else None)
            if cond["type"] == "date":
                self._save(data)
                self._refuse(
                    f"{capsule_id} is sealed until {cond['date']} — "
                    "the date has not arrived yet",
                    capsule, now, eligible_at=_parse_dt(cond["date"]))
            # silence, not yet met
            last = data.get("last_activity_at")
            self._save(data)
            eligible = None
            if last:
                try:
                    eligible = _parse_dt(last) + timedelta(days=cond["days"])
                except CapsuleError:
                    eligible = None
            self._refuse(
                f"{capsule_id} is sealed until {cond['days']} days of silence — "
                f"last activity was {last or 'never'}",
                capsule, now, eligible_at=eligible)

    def delete_capsule(self, capsule_id, delete_days=None):
        with _FileLock(self.root):
            data = self._load()
            now = self._now()
            self._clock_watch(data, now)
            self._refresh(data, now)
            capsule = self._find(data, capsule_id)
            if capsule["status"] == "pending-deletion":
                return {"ok": True, "id": capsule_id, "status": "pending-deletion",
                        "delete_after": capsule["delete_after"],
                        "note": "already pending deletion"}
            if delete_days is not None:
                delete_days = _int_arg("delete_days", delete_days, DELETE_DAYS_MIN, DELETE_DAYS_MAX)
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
            self._clock_watch(data, now)
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
            self._clock_watch(data, now)
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
                "note": "re-sealed — the release condition still applies; a date "
                        "capsule past its date re-enters grace on the next observation"}

    def status(self):
        with _FileLock(self.root):
            data = self._load()
            now = self._now()
            changed = self._refresh(data, now) or self._clock_watch(data, now)
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
            "clock": {
                "high_water": data.get("clock_high_water"),
                "rollbacks_detected": len(data.get("clock_rollbacks", [])),
                "last_rollback": (data.get("clock_rollbacks") or [None])[-1],
            },
            "store": str(self._store_path),
        }
