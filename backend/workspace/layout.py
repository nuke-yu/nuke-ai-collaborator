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


def bot_dir(gid: int | None, bot_id: int) -> Path:
    """bot 私有区路径。

    gid 显式给出 → 嵌套 group_{gid}/bots/bot_{id}（正路）。
    gid is None → 过渡垫片，走旧扁平路径 bot_{id}（Task 4-9 期间保持系统可跑，
    Task 10 移除垫片，强制显式 gid）。
    """
    if gid is None:
        return WORKSPACE_ROOT / f"bot_{bot_id}"
    return group_dir(gid) / "bots" / f"bot_{bot_id}"
