"""单一布局真相源（Single Layout Truth）。

纯函数：无 I/O、不 mkdir、只吃显式 id。所有工作区路径由此一处计算，
消灭 workspace.bot_workspace 与 skills.constants.bot_ws 的重复定义。

WORKSPACE_ROOT 每次调用时从 skills.constants **实时读取**（不在 import 期缓存），
这样测试/运行时对 skills.constants.WORKSPACE_ROOT 的重绑定能正常生效。

Phase 1：bot_dir 返回当前扁平路径（workspaces/bot_{id}），零行为变化。
Phase 2：bot_dir(gid, bot_id) → 嵌套 group_{gid}/bots/bot_{id}；gid=None 走过渡垫片。
"""
import contextvars
from pathlib import Path

import skills.constants as _const

# ContextVar to override the shared workspace directory path.
current_workspace_path = contextvars.ContextVar("nuke_current_workspace_path", default=None)


def _root() -> Path:
    # 实时读取，避免 import 期缓存导致 WORKSPACE_ROOT 重绑定失效
    return Path(_const.WORKSPACE_ROOT)


def group_dir(gid: int) -> Path:
    return _root() / f"group_{gid}"


def group_shared_dir(gid: int) -> Path:
    overrides = current_workspace_path.get()
    if overrides and gid in overrides:
        return Path(overrides[gid])
    return group_dir(gid) / "shared"



def group_runs_dir(gid: int) -> Path:
    return group_dir(gid) / "runs"


def group_media_dir(gid: int, kind: str) -> Path:
    """Per-group private media (kind ∈ uploads|screenshots).

    Deliberately a sibling of `shared/` — NOT under the workspace — so media
    never enters git worktrees, promotions, or the bot's file-tree context.
    Served only via signed /media URLs (see core.media).
    """
    return group_dir(gid) / "media" / kind


def media_staging_dir() -> Path:
    """Group-agnostic staging area where the (group-unaware) MCP collector drops
    screenshot bytes; the worker then moves them into the owning group's
    `media/screenshots/`. Kept off any group path on purpose."""
    return _root() / "_media_staging"


def group_roles_dir(gid: int) -> Path:
    return group_dir(gid) / "roles"


def external_global_skills_dir() -> Path:
    """Global operator-curated external skill pool (cross-group definitions)."""
    return _root() / "external" / "skills"


def group_external_skills_dir(gid: int) -> Path:
    """Per-group external skill pool — visible ONLY to that group (isolation)."""
    return group_dir(gid) / "external" / "skills"


def templates_roles_dir(lang: str) -> Path:
    return _root() / "templates" / lang / "roles"


def bot_dir(gid: int | None, bot_id: int) -> Path:
    """bot 私有区路径。

    gid 显式给出 → 嵌套 group_{gid}/bots/bot_{id}（正路）。
    gid is None → 过渡垫片，走旧扁平路径 bot_{id}（Task 4-9 期间保持系统可跑，
    Task 10 移除垫片，强制显式 gid）。
    """
    if gid is None:
        return _root() / f"bot_{bot_id}"
    return group_dir(gid) / "bots" / f"bot_{bot_id}"


# In-memory cache to avoid exists() / read_text() on hot paths.
# WARNING: This cache is process-local and does not automatically sync across processes.
# It relies on the architectural constraint that each group's session (reads and writes)
# is pinned to the same worker process. If set_group_language is called from another process
# (e.g., supervisor), other workers' caches will not be invalidated.
_GROUP_LANG_CACHE: dict[int, str] = {}


def get_group_language(group_id: int | None) -> str:
    if group_id is None:
        return "zh"
    if group_id in _GROUP_LANG_CACHE:
        return _GROUP_LANG_CACHE[group_id]
    lang_file = group_dir(group_id) / "lang.txt"
    if lang_file.exists():
        try:
            lang = lang_file.read_text(encoding="utf-8").strip()
            _GROUP_LANG_CACHE[group_id] = lang
            return lang
        except Exception:
            pass
    _GROUP_LANG_CACHE[group_id] = "zh"
    return "zh"


def set_group_language(group_id: int, lang: str):
    current = get_group_language(group_id)
    if current == lang:
        return
    _GROUP_LANG_CACHE[group_id] = lang
    lang_file = group_dir(group_id) / "lang.txt"
    try:
        lang_file.parent.mkdir(parents=True, exist_ok=True)
        lang_file.write_text(lang, encoding="utf-8")
    except Exception:
        pass
