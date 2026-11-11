"""Tool handlers — the code that runs when the LLM calls each tool.

Rules (from the Hermes plugin guide): handlers receive args (dict), do the
work, and ALWAYS return a JSON string — success and errors alike. Never
raise. Accept **kwargs for forward compatibility (Hermes injects task_id,
session_id, conversation_id, ...).
"""

import json
from pathlib import Path

from . import schemas  # noqa: F401  (kept for the register() import surface)
from .timecapsule_engine import CapsuleError, TimeCapsule

# Resolved lazily per call: the store root comes from the plugin config
# (timecapsule_root) when set, otherwise the plugin's own data directory.
_ROOT_OVERRIDE = None


def set_timecapsule_root(path):
    global _ROOT_OVERRIDE
    _ROOT_OVERRIDE = path or None


def _timecapsule():
    root = _ROOT_OVERRIDE
    if not root:
        try:
            from plugins.plugin_storage import plugin_data_dir
            root = str(plugin_data_dir("timecapsule"))
        except Exception:
            root = str(Path.home() / ".hermes" / "plugin-data" / "timecapsule")
    return TimeCapsule(root)


def _ok(payload):
    return json.dumps(payload)


def _err(message):
    return json.dumps({"ok": False, "error": message})


def timecapsule_seal(args, **kwargs):
    try:
        c = _timecapsule()
        return _ok(c.seal(
            message=args.get("message") or "",
            condition_type=args.get("release_condition") or "",
            release_date=args.get("release_date"),
            silence_days=args.get("silence_days"),
            grace_days=args.get("grace_days", 3),
            delete_days=args.get("delete_days", 7),
            author=args.get("author") or "",
            label=args.get("label") or "",
        ))
    except CapsuleError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"timecapsule_seal failed: {e}")


def timecapsule_list(args, **kwargs):
    try:
        c = _timecapsule()
        return _ok(c.list_capsules(args.get("status")))
    except CapsuleError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"timecapsule_list failed: {e}")


def timecapsule_open(args, **kwargs):
    try:
        c = _timecapsule()
        capsule_id = args.get("capsule_id")
        if not capsule_id:
            return _err("capsule_id is required")
        return _ok(c.open_capsule(capsule_id, args.get("author") or ""))
    except CapsuleError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"timecapsule_open failed: {e}")


def timecapsule_delete(args, **kwargs):
    try:
        c = _timecapsule()
        capsule_id = args.get("capsule_id")
        if not capsule_id:
            return _err("capsule_id is required")
        return _ok(c.delete_capsule(capsule_id, args.get("delete_days")))
    except CapsuleError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"timecapsule_delete failed: {e}")


def timecapsule_restore(args, **kwargs):
    try:
        c = _timecapsule()
        capsule_id = args.get("capsule_id")
        if not capsule_id:
            return _err("capsule_id is required")
        return _ok(c.restore_capsule(capsule_id))
    except CapsuleError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"timecapsule_restore failed: {e}")


def timecapsule_reseal(args, **kwargs):
    try:
        c = _timecapsule()
        capsule_id = args.get("capsule_id")
        if not capsule_id:
            return _err("capsule_id is required")
        return _ok(c.reseal_capsule(capsule_id, args.get("author") or ""))
    except CapsuleError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"timecapsule_reseal failed: {e}")


def timecapsule_status(args, **kwargs):
    try:
        c = _timecapsule()
        return _ok(c.status())
    except CapsuleError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"timecapsule_status failed: {e}")
