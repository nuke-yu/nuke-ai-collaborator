"""Shell environment and cwd confinement policies."""
from __future__ import annotations

import os
from pathlib import Path
import workspace as _ws

SHELL_ENV_ALLOW = {
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE",
    "TERM", "TMPDIR", "TZ", "PWD", "HOSTNAME",
}
SHELL_ENV_ALLOW_PREFIX = ("LC_",)


def sandbox_env() -> dict:
    return {
        key: value for key, value in os.environ.items()
        if key in SHELL_ENV_ALLOW or key.startswith(SHELL_ENV_ALLOW_PREFIX)
    }


def resolve_shell_cwd(cwd: str, bot_id, group_id: int | None = None) -> tuple[Path | None, str]:
    if bot_id is None:
        return None, "缺少 bot_id，无法确定工作区"
    private_root = _ws.bot_workspace(bot_id, group_id).resolve()
    shared_root = _ws.group_workspace(group_id).resolve() if group_id is not None else None
    default_root = shared_root if shared_root is not None else private_root
    candidate = (cwd or "").strip()
    if not candidate:
        return default_root, ""
    path = Path(candidate)
    if path.is_absolute():
        target = path
    else:
        first = candidate.replace("\\", "/").split("/", 1)[0] + "/"
        base = private_root if first in _ws._PRIVATE_PREFIXES else default_root
        target = base / path
    try:
        target = target.resolve()
        for root in (private_root, shared_root):
            if root is not None and target.is_relative_to(root):
                return target, ""
    except (OSError, ValueError):
        pass
    return None, f"工作目录越界，必须位于本群组工作区内：{cwd}"
