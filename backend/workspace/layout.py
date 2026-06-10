"""单一布局真相源（Single Layout Truth）。

纯函数：无 I/O、不 mkdir、只吃显式 id。所有工作区路径由此一处计算，
消灭 workspace.bot_workspace 与 skills.constants.bot_ws 的重复定义。

Phase 1：bot_dir 返回当前扁平路径（workspaces/bot_{id}），零行为变化。
Phase 2：改为嵌套 workspaces/group_{gid}/bots/bot_{id} 并要求 group_id。
"""
from pathlib import Path

from skills.constants import WORKSPACE_ROOT


def group_dir(gid: int) -> Path:
    return WORKSPACE_ROOT / f"group_{gid}"


def group_shared_dir(gid: int) -> Path:
    return group_dir(gid) / "shared"


def group_runs_dir(gid: int) -> Path:
    return group_dir(gid) / "runs"


def bot_dir(bot_id: int) -> Path:
    # Phase 1: 扁平兼容。Phase 2 改签名为 bot_dir(gid, bot_id) → 嵌套。
    return WORKSPACE_ROOT / f"bot_{bot_id}"
