"""Dashboard backend — FastAPI routes for the Time Capsule tab.

Mounted at /api/plugins/timecapsule/. Reads the same store.json the
agent tools use: the capsule store on disk is the single source of
truth — the human layer never keeps its own copy of the state.

The seal rule, enforced here exactly as in the tools: the tab NEVER
returns the content of a sealed capsule. The show route returns
content only when the capsule's status is open. Every action route
calls the engine's own methods (seal/open/delete/restore/reseal) —
the API layer reimplements no state-machine logic.

One deliberate exception to "call the public methods": the show route
uses the engine's _load/_refresh/_find under _FileLock instead of
open_capsule, because open_capsule records activity (it resets the
silence clock) and advances a sealed manual capsule into grace.
Viewing a capsule must do neither — it is read-only. The show route
does the same lazy refresh list/status do (sealed -> grace -> open,
expired deletion countdowns announced), but no _touch, so looking at
a silent capsule does not prove you are still there.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from timecapsule_engine import CapsuleError, TimeCapsule, _FileLock, _public  # noqa: E402


def _resolve_root():
    try:
        from plugins.plugin_storage import plugin_data_dir
        return str(plugin_data_dir("timecapsule"))
    except Exception:
        pass
    home = os.environ.get("HERMES_HOME")
    if home:
        return str(Path(home) / "plugin-data" / "timecapsule")
    return str(Path.home() / ".hermes" / "plugin-data" / "timecapsule")


def _timecapsule():
    return TimeCapsule(_resolve_root())


router = None
try:
    from fastapi import APIRouter, Request

    router = APIRouter()

    @router.get("/capsules")
    def capsules(status: str = None):
        try:
            return _timecapsule().list_capsules(status or None)
        except CapsuleError as e:
            return {"ok": False, "error": str(e)}

    @router.get("/capsules/{capsule_id}")
    def show_capsule(capsule_id: str):
        """Metadata for every capsule; content ONLY when it is open.

        Read-only: runs the engine's lazy refresh (same as list/status)
        but records no activity, so viewing never resets the silence
        clock and never moves a sealed manual capsule into grace.
        """
        try:
            c = _timecapsule()
            with _FileLock(c.root):
                data = c._load()
                now = c._now()
                changed = c._refresh(data, now)
                if changed:
                    c._save(data)
                capsule = c._find(data, capsule_id)
                out = {"ok": True, **_public(capsule)}
                out["content"] = capsule["message"] if capsule["status"] == "open" else None
                return out
        except CapsuleError as e:
            return {"ok": False, "error": str(e)}

    @router.get("/status")
    def status():
        try:
            return _timecapsule().status()
        except CapsuleError as e:
            return {"ok": False, "error": str(e)}

    @router.post("/seal")
    async def seal(request: Request):
        data = await request.json()
        try:
            c = _timecapsule()
            return c.seal(
                message=data.get("message") or "",
                condition_type=data.get("release_condition") or "",
                release_date=data.get("release_date"),
                silence_days=data.get("silence_days"),
                grace_days=data.get("grace_days", 3),
                delete_days=data.get("delete_days", 7),
                author=data.get("author") or "",
                label=data.get("label") or "",
            )
        except CapsuleError as e:
            return {"ok": False, "error": str(e)}

    @router.post("/capsules/{capsule_id}/open")
    async def open_capsule(capsule_id: str, request: Request):
        data = await request.json()
        try:
            c = _timecapsule()
            return c.open_capsule(capsule_id, (data.get("author") or ""))
        except CapsuleError as e:
            return {"ok": False, "error": str(e)}

    @router.post("/capsules/{capsule_id}/delete")
    async def delete_capsule(capsule_id: str, request: Request):
        data = await request.json()
        try:
            c = _timecapsule()
            return c.delete_capsule(capsule_id, data.get("delete_days"))
        except CapsuleError as e:
            return {"ok": False, "error": str(e)}

    @router.post("/capsules/{capsule_id}/restore")
    async def restore_capsule(capsule_id: str):
        try:
            c = _timecapsule()
            return c.restore_capsule(capsule_id)
        except CapsuleError as e:
            return {"ok": False, "error": str(e)}

    @router.post("/capsules/{capsule_id}/reseal")
    async def reseal_capsule(capsule_id: str, request: Request):
        data = await request.json()
        try:
            c = _timecapsule()
            return c.reseal_capsule(capsule_id, (data.get("author") or ""))
        except CapsuleError as e:
            return {"ok": False, "error": str(e)}

except ImportError:
    # FastAPI not importable in this context (e.g. linting the file outside
    # Hermes). The router is only needed when the dashboard loads it.
    router = None
