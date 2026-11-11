"""timecapsule — registration.

Wires the seven tools and the bundled skill. Called exactly once at startup.
If this function crashes, Hermes disables the plugin and continues fine.
"""

import logging
from pathlib import Path

from . import schemas, tools

logger = logging.getLogger(__name__)


def register(ctx):
    """Wire schemas to handlers and register the skill."""
    # Store root: user-configured directory, or the plugin data dir.
    try:
        root = ctx.get_config("timecapsule_root", default="") or ""
    except Exception:
        root = ""
    if root:
        tools.set_timecapsule_root(root)

    ctx.register_tool(name="timecapsule_seal", toolset="timecapsule",
                      schema=schemas.TIMECAPSULE_SEAL, handler=tools.timecapsule_seal)
    ctx.register_tool(name="timecapsule_list", toolset="timecapsule",
                      schema=schemas.TIMECAPSULE_LIST, handler=tools.timecapsule_list)
    ctx.register_tool(name="timecapsule_open", toolset="timecapsule",
                      schema=schemas.TIMECAPSULE_OPEN, handler=tools.timecapsule_open)
    ctx.register_tool(name="timecapsule_delete", toolset="timecapsule",
                      schema=schemas.TIMECAPSULE_DELETE, handler=tools.timecapsule_delete)
    ctx.register_tool(name="timecapsule_restore", toolset="timecapsule",
                      schema=schemas.TIMECAPSULE_RESTORE, handler=tools.timecapsule_restore)
    ctx.register_tool(name="timecapsule_reseal", toolset="timecapsule",
                      schema=schemas.TIMECAPSULE_RESEAL, handler=tools.timecapsule_reseal)
    ctx.register_tool(name="timecapsule_status", toolset="timecapsule",
                      schema=schemas.TIMECAPSULE_STATUS, handler=tools.timecapsule_status)

    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                ctx.register_skill(child.name, skill_md)

    logger.info("timecapsule plugin registered: 7 tools, 1 skill")
