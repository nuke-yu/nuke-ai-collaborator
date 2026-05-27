from pathlib import Path

WORKSPACE_ROOT = Path(__file__).parent.parent / "workspaces"
SYSTEM_SKILLS_ROOT = WORKSPACE_ROOT / "system" / "skills"
ROLES_ROOT = WORKSPACE_ROOT / "roles"

LEARNED_ACTIVE = "skills/learned/active"
LEARNED_DRAFT = "skills/learned/draft"


def bot_ws(bot_id: int) -> Path:
    """Return bot workspace path (no mkdir — caller is responsible)."""
    return WORKSPACE_ROOT / f"bot_{bot_id}"


def group_ws(group_id: int) -> Path:
    return WORKSPACE_ROOT / f"group_{group_id}" / "shared"
