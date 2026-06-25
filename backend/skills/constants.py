from pathlib import Path

import os
WORKSPACE_ROOT = Path(os.environ.get("NUKE_WORKSPACE_ROOT") or (Path(__file__).parent.parent / "workspaces"))
SYSTEM_SKILLS_ROOT = WORKSPACE_ROOT / "system" / "skills"
TRAITS_ROOT = WORKSPACE_ROOT / "system" / "traits"
ROLES_ROOT = WORKSPACE_ROOT / "roles"            # legacy global roles (migration source only; not scanned at runtime after Plan 1)
TEMPLATES_ROOT = WORKSPACE_ROOT / "templates"    # global role templates root (copied into groups on creation)

LEARNED_ACTIVE = "skills/learned/active"
LEARNED_DRAFT = "skills/learned/draft"


def bot_ws(bot_id: int, group_id: int | None = None) -> Path:
    """Return bot workspace path (no mkdir — caller is responsible)."""
    # 委托 layout（单一布局真相源）。函数内 import 避免与 layout 的循环依赖。
    from workspace import layout
    return layout.bot_dir(group_id, bot_id)


def group_ws(group_id: int) -> Path:
    from workspace import layout
    return layout.group_shared_dir(group_id)
