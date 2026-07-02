import logging
import asyncio
import threading
from pathlib import Path
from datetime import date, datetime
from typing import Dict

from skills.constants import WORKSPACE_ROOT, LEARNED_ACTIVE as _LEARNED_ACTIVE, LEARNED_DRAFT as _LEARNED_DRAFT
from workspace.templates import (
    IDENTITY_TEMPLATE, SOUL_TEMPLATE, BOOTSTRAP_TEMPLATE,
    AGENT_TEMPLATE, DEV_AGENT_TEMPLATE, QA_AGENT_TEMPLATE, BA_AGENT_TEMPLATE,
    MEMORY_TEMPLATE, BOARD_TEMPLATE, SPEC_TEMPLATE
)

_SUBDIRS = ["skills", "logs"]
_HISTORY_LIMIT = 10

# Point 5: VFS Path-based Locking
# Registry of registries, keyed by event loop ID to avoid 'different event loop' errors in tests
_LOOP_REGISTRIES: Dict[int, Dict[Path, asyncio.Lock]] = {}
_REGISTRY_LOCK = threading.Lock()

def _get_path_lock(path: Path) -> asyncio.Lock:
    """Return a path-specific lock bound to the current event loop."""
    loop = asyncio.get_running_loop()
    loop_id = id(loop)
    resolved = path.resolve()
    
    with _REGISTRY_LOCK:
        if loop_id not in _LOOP_REGISTRIES:
            _LOOP_REGISTRIES[loop_id] = {}
        registry = _LOOP_REGISTRIES[loop_id]
        if resolved not in registry:
            registry[resolved] = asyncio.Lock()
        return registry[resolved]

def clear_group_locks(group_id: int):
    """M-5: Clear all VFS path locks for a specific group to prevent memory leaks."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop_id = id(loop)
    group_prefix = str(group_workspace(group_id).parent.resolve())
    
    with _REGISTRY_LOCK:
        if loop_id not in _LOOP_REGISTRIES:
            return
        registry = _LOOP_REGISTRIES[loop_id]
        to_delete = [p for p in registry if str(p).startswith(group_prefix)]
        for p in to_delete:
            del registry[p]



def bot_workspace(bot_id: int, group_id: int | None = None) -> Path:
    from workspace import layout
    path = layout.bot_dir(group_id, bot_id)
    path.mkdir(parents=True, exist_ok=True)
    for sub in _SUBDIRS:
        (path / sub).mkdir(exist_ok=True)
    return path


def group_workspace(group_id: int) -> Path:
    from workspace import layout
    path = layout.group_shared_dir(group_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_path(workspace: Path, relative: str) -> Path | None:
    try:
        resolved = (workspace / relative).resolve()
        if resolved.is_relative_to(workspace.resolve()):
            return resolved

        # Extract group_id from workspace path (e.g. ".../group_11/bots/bot_1033")
        group_id = None
        for part in workspace.parts:
            if part.startswith("group_"):
                try:
                    group_id = int(part.split("_")[1])
                    break
                except ValueError:
                    pass

        # Check allowed external/shared roots to support symlinked skills
        from workspace import layout
        allowed_roots = [
            (layout._root() / "system" / "skills").resolve(),
            layout.external_global_skills_dir().resolve(),
        ]
        if group_id is not None:
            allowed_roots.append(layout.group_shared_dir(group_id).resolve())
            allowed_roots.append(layout.group_roles_dir(group_id).resolve())

        for root in allowed_roots:
            if resolved.is_relative_to(root):
                return resolved
    except Exception:
        log.warning("vfs: failed to resolve safe path for %s", relative, exc_info=True)
    return None


# 路由默认「共享优先」：有群组上下文时，写入默认落群组 shared 区——代码/文档/PR 等协作物
# 不再依赖 bot 记得加 workspace/ 前缀（少加前缀只会落到共享区里，不会再静默退回私有）。
# 仅以下「私有命名空间」留在 bot 私有区：bot 身份/记忆文件 + 私有技能 + 日志。
# 它们既是写入落点也是读取落点（startup 读 AGENT.md/MEMORY.md 等），必须按 bot 隔离
# （见 CLAUDE.md 群组隔离）；把它们漏出共享会同时破坏隔离与「读不到自己的身份文件」。
_PRIVATE_PREFIXES = ("skills/", "logs/")
_PRIVATE_FILES = {"IDENTITY.md", "SOUL.md", "BOOTSTRAP.md", "AGENT.md", "MEMORY.md"}

def _normalize_vfs_path(path_str: str, bot_id: int, group_id: int | None) -> str:
    """Strip absolute filesystem prefixes that bots sometimes leak into VFS calls.

    Bots occasionally pass real filesystem paths (e.g. workspaces/group_3/shared/workspace/...)
    instead of VFS-relative paths (workspace/...).  Strip the known prefix so routing
    works correctly rather than silently writing to the wrong place.
    """
    from workspace import layout
    root = layout._root()

    # Strip leading WORKSPACE_ROOT dirname (e.g. "workspaces/group_3/..." → "group_3/...")
    root_prefix = root.name + "/"
    if path_str.startswith(root_prefix):
        path_str = path_str[len(root_prefix):]

    if group_id is None:
        return path_str

    # Strip group-relative shared prefix (e.g. "group_3/shared/" → "")
    shared_rel = str(layout.group_shared_dir(group_id).relative_to(root)).replace("\\", "/") + "/"
    if path_str.startswith(shared_rel):
        return path_str[len(shared_rel):]

    # Strip group-relative private prefix (e.g. "group_3/bots/bot_1010/" → "")
    private_rel = str(layout.bot_dir(group_id, bot_id).relative_to(root)).replace("\\", "/") + "/"
    if path_str.startswith(private_rel):
        return path_str[len(private_rel):]

    return path_str


def _get_effective_ws(bot_id: int, path_str: str, group_id: int | None = None) -> tuple[Path, str]:
    """群组文件路由（全程不查 DB —— group_id 由调用方在边界显式解析后传入）。

    - 默认共享：已知 group 且路径不在私有命名空间 → 群组 shared 区。
    - 私有命名空间（skills/ logs/ 前缀，或 bot 身份/记忆文件）→ bot 私有区，
      嵌套 group_{gid}/bots/bot_{id}。
    - group_id 为 None（无群组上下文）→ 一律落 bot 私有（扁平），不再反查 DB。

    Returns (workspace_root, normalized_path_str).
    """
    path_str = _normalize_vfs_path(path_str, bot_id, group_id)
    is_private = path_str in _PRIVATE_FILES or path_str.startswith(_PRIVATE_PREFIXES)
    if group_id is not None and not is_private:
        return group_workspace(group_id), path_str
    return bot_workspace(bot_id, group_id), path_str



async def read_file(bot_id: int, path: str, offset: int | None = None, limit: int | None = None, group_id: int | None = None) -> str:
    ws, path = _get_effective_ws(bot_id, path, group_id)
    p = _safe_path(ws, path)
    if p is None:
        return f"[错误] 非法路径: {path}"
    if not p.exists():
        return f"[文件不存在] {path}"
    
    lock = _get_path_lock(p)
    async with lock:
        try:
            text = await asyncio.to_thread(p.read_text, encoding="utf-8")
            if offset is not None or limit is not None:
                try:
                    start = int(offset) if offset is not None else 0
                    if start < 0:
                        start = 0
                except (ValueError, TypeError):
                    start = 0
                try:
                    length = int(limit) if limit is not None else None
                    if length is not None and length < 0:
                        length = 0
                except (ValueError, TypeError):
                    length = None
                
                end = (start + length) if length is not None else len(text)
                return text[start:end]
            return text
        except Exception as e:
            return f"[读取错误] {e}"


_WRITE_PROTECTED = {"MEMORY.md", "RETRO_LATEST.md"}


def _history_dir(ws: Path, p: Path) -> Path:
    rel = p.relative_to(ws)
    parent_str = str(rel.parent)
    stem = rel.stem
    if parent_str == ".":
        return ws / ".history" / stem
    return ws / ".history" / parent_str / stem


def _save_to_history(ws: Path, p: Path) -> None:
    try:
        existing = p.read_text(encoding="utf-8")
        hdir = _history_dir(ws, p)
        hdir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        (hdir / f"{ts}.md").write_text(existing, encoding="utf-8")
        versions = sorted(hdir.glob("*.md"), key=lambda f: f.name)
        while len(versions) > _HISTORY_LIMIT:
            versions.pop(0).unlink(missing_ok=True)
    except Exception:
        log.exception("vfs: failed to save history for %s", p)


def list_file_history(bot_id: int, path: str, group_id: int | None = None) -> list[dict]:
    ws, path = _get_effective_ws(bot_id, path, group_id)
    p = _safe_path(ws, path)
    if p is None:
        return []
    hdir = _history_dir(ws, p)
    if not hdir.exists():
        return []
    versions = sorted(hdir.glob("*.md"), key=lambda f: f.name, reverse=True)
    return [{"ts": f.stem, "size": f.stat().st_size} for f in versions]


def read_file_history_version(bot_id: int, path: str, ts: str, group_id: int | None = None) -> str:
    ws, path = _get_effective_ws(bot_id, path, group_id)
    p = _safe_path(ws, path)
    if p is None:
        return "[错误] 非法路径"
    if not ts.replace("T", "").isdigit():
        return "[错误] 非法版本标识"
    hdir = _history_dir(ws, p)
    version_file = hdir / f"{ts}.md"
    if not version_file.exists():
        return "[版本不存在]"
    try:
        return version_file.read_text(encoding="utf-8")
    except Exception as e:
        return f"[读取错误] {e}"


async def _commit_text(ws: Path, p: Path, rel: str, path: str, new_text: str, bot_id: int, action: str) -> str:
    """Internal helper to write text to VFS/disk, handle history/drafts, and dispatch CodeCommitted events."""
    def _do_write() -> str:
        # Redirect learned/active writes → learned/draft (requires user approval)
        if rel.startswith(_LEARNED_ACTIVE):
            draft_path = ws / _LEARNED_DRAFT / p.name
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text(new_text, encoding="utf-8")
            return f"__DRAFT_WRITTEN__:{p.name}"   # sentinel for broadcast
        # Direct writes to learned/draft/ also return sentinel so tool_loop broadcasts
        if rel.startswith(_LEARNED_DRAFT):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(new_text, encoding="utf-8")
            return f"__DRAFT_WRITTEN__:{p.name}"
        # Save history before overwriting if content differs
        if p.exists():
            try:
                if p.read_text(encoding="utf-8") != new_text:
                    _save_to_history(ws, p)
            except Exception:
                log.exception("workspace: failed to save history for %s", p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(new_text, encoding="utf-8")
        if action == "write":
            return f"已写入 {path}（{len(new_text)} 字符）"
        else:
            return f"已修改 {path}"

    result = await asyncio.to_thread(_do_write)

    # Emit CodeCommitted event if a code file is written (main thread async)
    is_shared = ws.parent.name.startswith("group_")
    if (
        is_shared 
        and p.suffix in {".py", ".js", ".ts", ".go", ".java"}
        and not result.startswith("__DRAFT_WRITTEN__")
    ):
        from bus import publish
        from bus.events import CodeCommitted
        from db import connect
        async with connect() as conn:
            async with conn.execute("SELECT group_id FROM members WHERE id = ?", (bot_id,)) as cur:
                row = await cur.fetchone()
            if row:
                commit_msg = f"Auto-commit by Bot {bot_id}" if action == "write" else f"Auto-edit by Bot {bot_id}"
                await publish(CodeCommitted(
                    group_id=row[0],
                    ticket_id="auto", # Ideally passed via context
                    files=[path],
                    commit_msg=commit_msg,
                    author_id=bot_id
                ))

    return result


async def write_file(bot_id: int, path: str, content: str, group_id: int | None = None) -> str:
    # group_id 显式贯穿：交给 _get_effective_ws 统一路由（默认群组 shared，仅私有命名空间
    # skills//logs//身份记忆文件落 bot 私有）。bot_id=0 的系统写（BOARD.md / prs/）走共享分支。
    ws, path = _get_effective_ws(bot_id, path, group_id)
    p = _safe_path(ws, path)
    if p is None:
        return f"[错误] 非法路径: {path}"
    if not p.is_relative_to(ws.resolve()):
        return f"[只读] {path} 位于只读共享区域，无法写入。"
    if p.name in _WRITE_PROTECTED:
        return f"[受保护] {p.name} 是永久记忆文件，Bot 无法覆盖。如需追加记录，请通过工作区面板手动编辑。"
    rel = str(p.relative_to(ws)).replace("\\", "/")

    lock = _get_path_lock(p)
    async with lock:
        return await _commit_text(ws, p, rel, path, content, bot_id, "write")


def make_dir(bot_id: int, path: str, group_id: int | None = None) -> str:
    """Create an (empty) directory in the bot workspace. Sandbox-confined.

    Writing a file already mkdir-parents, so this is only needed for the
    "new folder" action — e.g. building a directory-form skill folder-first
    (skills/<name>/ then add SKILL.md + scripts) via the workspace panel.
    """
    ws, path = _get_effective_ws(bot_id, path, group_id)
    p = _safe_path(ws, path)
    if p is None:
        return f"[错误] 非法路径: {path}"
    if not p.is_relative_to(ws.resolve()):
        return f"[只读] {path} 位于只读共享区域，无法创建子目录。"
    if p.exists():
        return "目录已存在" if p.is_dir() else f"[错误] 已存在同名文件: {path}"
    p.mkdir(parents=True, exist_ok=True)
    return f"已创建目录 {path}"


def delete_path(bot_id: int, path: str, group_id: int | None = None) -> str:
    """Delete a file or directory (recursive) in the bot workspace. Sandbox-confined.

    Refuses the workspace root and write-protected files (MEMORY.md / RETRO_LATEST.md).
    Backs the workspace panel's delete action for manual skills (single-file or
    directory-form).
    """
    import shutil
    ws, path = _get_effective_ws(bot_id, path, group_id)
    p = _safe_path(ws, path)
    if p is None:
        return f"[错误] 非法路径: {path}"
    if not p.is_relative_to(ws.resolve()):
        return f"[只读] {path} 位于只读共享区域，无法删除。"
    if p == ws.resolve():
        return "[错误] 不能删除工作区根目录"
    if p.name in _WRITE_PROTECTED:
        return f"[受保护] {p.name} 不可删除"
    if not p.exists():
        return f"[文件不存在] {path}"
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink()
    return f"已删除 {path}"


async def edit_file(bot_id: int, path: str, old_string: str | None = None, new_string: str | None = None,
                    replace_all: bool = False, group_id: int | None = None,
                    edits: list | None = None) -> str:
    ws, path = _get_effective_ws(bot_id, path, group_id)
    p = _safe_path(ws, path)
    if p is None:
        return f"[错误] 非法路径: {path}"
    if not p.is_relative_to(ws.resolve()):
        return f"[只读] {path} 位于只读共享区域，无法修改。"
    if p.name in _WRITE_PROTECTED:
        return f"[受保护] {p.name} 是永久记忆文件，Bot 无法编辑。如需修改记录，请通过工作区面板手动编辑。"
    if not p.exists():
        return f"[文件不存在] {path}"

    rel = str(p.relative_to(ws)).replace("\\", "/")
    lock = _get_path_lock(p)
    async with lock:
        try:
            raw = await asyncio.to_thread(p.read_text, encoding="utf-8")
        except Exception as e:
            return f"[读取错误] {e}"

        import editing
        # IO 边界：在「无 BOM 的 LF」平面做匹配，写回时还原原行尾/BOM。
        # 这样 CRLF 文件能被 LF 的 old_string 命中，又不静默改用户的行尾风格。
        bom, body = editing.strip_bom(raw)
        eol = editing.detect_eol(body)
        current = editing.to_lf(body)

        suffix = ""
        if edits is not None:
            # 批量：顺序应用、原子（任一失配整体不落盘）、一次提交。
            norm = [(editing.to_lf(e.get("old_string", "")), editing.to_lf(e.get("new_string", "")))
                    for e in edits]
            try:
                updated, applied, skipped = editing.apply_batch(current, norm)
            except editing.EditError as e:
                return f"[编辑失败] {e}"
            suffix = f"（{applied} 处已改" + (f"，{skipped} 处已是目标态跳过）" if skipped else "）")
        else:
            old_lf = editing.to_lf(old_string)
            new_lf = editing.to_lf(new_string)
            try:
                updated = editing.apply_replacement(current, old_lf, new_lf, replace_all=replace_all)
            except editing.EditError as e:
                # 幂等恢复：目标已存在、old 不在 → 视为已应用，不当失败处理。
                if editing.idempotent_skip(current, old_lf, new_lf):
                    return "[已是目标状态] new_string 已存在且 old_string 不在文件中，视为已应用，未改动"
                # 失配 hint：回吐近邻上下文帮模型重锚 old_string。
                return f"[编辑失败] {e}\n{editing.mismatch_hint(current, old_lf)}"

        if updated == current:
            return "[无改动] 替换前后内容一致"

        out = bom + editing.restore_eol(updated, eol)
        result = await _commit_text(ws, p, rel, path, out, bot_id, "edit")
        return f"{result}{suffix}" if suffix else result


async def read_anchored(bot_id: int, path: str, group_id: int | None = None) -> str:
    """带行哈希锚的只读视图：每行前缀 L<n>#<hash>，供 edit_anchored 按锚精准定位。"""
    ws, path = _get_effective_ws(bot_id, path, group_id)
    p = _safe_path(ws, path)
    if p is None:
        return f"[错误] 非法路径: {path}"
    if not p.exists():
        return f"[文件不存在] {path}"
    try:
        raw = await asyncio.to_thread(p.read_text, encoding="utf-8")
    except Exception as e:
        return f"[读取错误] {e}"
    import editing
    _, body = editing.strip_bom(raw)
    return editing.annotate(editing.to_lf(body))


async def edit_anchored(bot_id: int, path: str, edits: list, group_id: int | None = None) -> str:
    """按行哈希锚编辑：edits=[{anchor, op(replace/delete/insert_after), text}]。
    锚用内容哈希定位，行位移也有效；原子（任一锚失效/冲突即整体不落盘）。"""
    ws, path = _get_effective_ws(bot_id, path, group_id)
    p = _safe_path(ws, path)
    if p is None:
        return f"[错误] 非法路径: {path}"
    if p.name in _WRITE_PROTECTED:
        return f"[受保护] {p.name} 是永久记忆文件，Bot 无法编辑。如需修改记录，请通过工作区面板手动编辑。"
    if not p.exists():
        return f"[文件不存在] {path}"

    rel = str(p.relative_to(ws)).replace("\\", "/")
    lock = _get_path_lock(p)
    async with lock:
        try:
            raw = await asyncio.to_thread(p.read_text, encoding="utf-8")
        except Exception as e:
            return f"[读取错误] {e}"
        import editing
        bom, body = editing.strip_bom(raw)
        eol = editing.detect_eol(body)
        current = editing.to_lf(body)
        norm = [{**e, "text": editing.to_lf(e.get("text", ""))} for e in (edits or [])]
        try:
            updated = editing.apply_anchored_edits(current, norm)
        except editing.HashlineError as e:
            return f"[锚点编辑失败] {e}"

        if updated == current:
            return "[无改动] 替换前后内容一致"

        out = bom + editing.restore_eol(updated, eol)
        return await _commit_text(ws, p, rel, path, out, bot_id, "edit")


# 遍历工作区时剪枝掉的重型目录（依赖/构建产物）。rglob("*") 会急切枚举整棵树——
# 一旦工作区里有 node_modules/venv 等，会卡住目录列举并撑爆上下文 token；用 os.walk
# 原地剪枝 dirnames 才能真正“不进入”这些目录。
_WS_IGNORE_DIRS = {
    "node_modules", "venv", ".venv", "env", "__pycache__", "dist", "build",
    "target", ".git", ".next", ".nuxt", ".cache", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "site-packages", "vendor", ".gradle", ".idea", ".history",
}
_WS_MAX_ENTRIES = 500


def walk_visible(root: Path, max_entries: int = _WS_MAX_ENTRIES,
                 skip_hidden: bool = True) -> tuple[list[Path], bool]:
    """遍历 root，剪枝重型依赖/构建目录（绝不进入 node_modules/.git 等），并对总数封顶。

    重型/已知噪声目录（_WS_IGNORE_DIRS，含 .git/.history）**始终不递归进去**；
    skip_hidden 额外决定是否过滤其它 dotfile/dotdir：
      - True（默认，LLM 上下文/工具输出）：隐藏所有 dotfile，且 _WS_IGNORE_DIRS 既不递归也不列出，
        避免把 .env 等密钥注入上下文；
      - False（UI 文件树）：保留 .gitignore 等给人看；node_modules 等重目录仍被整体剪掉，但 .git
        作为条目放行（让人看到仓库存在，仍不递归进去）。
    返回 (排序后的路径列表, 是否因超过 max_entries 被截断)。
    """
    import os
    if not root.exists():
        return [], False
    paths: list[Path] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        # 原地剪枝 + 排序：os.walk 的物理顺序依赖文件系统(inode)、非确定；先排序保证
        # 遍历顺序稳定，这样在超大工作区因 max_entries 截断时每次前缀一致，UI 树不抖动。
        all_dirs = sorted(dirnames)
        dirnames[:] = [
            d for d in all_dirs
            if d not in _WS_IGNORE_DIRS and (not skip_hidden or not d.startswith("."))
        ]
        base = Path(dirpath)
        for name in all_dirs:
            if skip_hidden:
                if name.startswith(".") or name in _WS_IGNORE_DIRS:
                    continue
            else:
                if name in _WS_IGNORE_DIRS and name != ".git":
                    continue
            paths.append(base / name)
        for name in sorted(filenames):
            if skip_hidden and name.startswith("."):
                continue
            paths.append(base / name)
        if len(paths) >= max_entries:
            truncated = True
            break
    return sorted(paths)[:max_entries], truncated


async def list_workspace(bot_id: int, group_id: int | None = None) -> str:
    def _tree(root: Path, skip_hidden: bool = True) -> list[str]:
        lines = []
        paths, truncated = walk_visible(root)  # LLM 工具输出：隐藏 dotfile
        for p in paths:
            rel = p.relative_to(root)
            indent = "  " * (len(rel.parts) - 1)
            icon = "📁" if p.is_dir() else "📄"
            lines.append(f"{indent}{icon} {p.name}")
        if truncated:
            lines.append(f"  …（已截断，仅显示前 {_WS_MAX_ENTRIES} 项；已跳过 node_modules/venv 等目录）")
        return lines

    sections = []

    # Shared group workspace (code repos, docs, prs) — most relevant for Dev/QA
    if group_id is not None:
        shared = group_workspace(group_id)
        shared_lines = _tree(shared)
        # Remind bot to use relative paths like workspace/my-app as cwd in run_shell
        header = "【共享工作区】（run_shell 用相对路径，如 cwd=\"workspace/my-app\"）"
        sections.append(header + ("\n" + "\n".join(shared_lines) if shared_lines else "\n（空）"))

    # Bot private workspace
    ws = bot_workspace(bot_id, group_id)
    private_lines = _tree(ws)
    header = "【私有工作区】（仅本 bot 可见）"
    sections.append(header + ("\n" + "\n".join(private_lines) if private_lines else "\n（空）"))

    return "\n\n".join(sections) if sections else "（工作区为空）"


# ---------------------------------------------------------------------------
# Context loading — hierarchical (group-level → bot-level)
# ---------------------------------------------------------------------------

def _read_md(path: Path) -> str | None:
    if path.exists():
        try:
            return path.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return None


async def load_group_context(group_id: int) -> str:
    """群组共享工作区上下文：目录树 + 关键项目文档。

    无条件加载——所有群组 bot 都需要知道共享区有什么。
    gating 不在此做；调用方按需决定是否注入。
    """

    def _build() -> list[str]:
        shared = group_workspace(group_id)
        parts: list[str] = []

        # 1. 目录树（剪枝重型目录，避免 rglob 急切枚举 node_modules 等导致卡顿/爆 token）
        tree_lines: list[str] = []
        shared_paths, truncated = walk_visible(shared)  # 注入 LLM 上下文：隐藏 dotfile
        for p in shared_paths:
            rel = p.relative_to(shared)
            indent = "  " * (len(rel.parts) - 1)
            icon = "📁" if p.is_dir() else "📄"
            tree_lines.append(f"{indent}{icon} {p.name}")
        if truncated:
            tree_lines.append(f"  …（已截断，仅显示前 {_WS_MAX_ENTRIES} 项；已跳过 node_modules/venv 等目录）")
        header = (
            "【共享工作区目录】"
            "（read_file/write_file 用 workspace/... docs/... prs/... 前缀；"
            "run_shell 用 cwd=\"workspace/my-app\"）"
        )
        parts.append(header + ("\n" + "\n".join(tree_lines) if tree_lines else "\n（空）"))

        # 2. 关键项目文档（按存在情况加载）
        for rel_path, label in [
            ("workspace/PROJECTS.md", "项目清单"),
            ("BOARD.md", "工作看板"),
            ("SPEC.md", "需求文档"),
        ]:
            content = _read_md(shared / rel_path)
            if content:
                parts.append(f"【{label}】\n{content}")

        return parts

    parts = await asyncio.to_thread(_build)
    return "\n\n".join(parts) if parts else ""


async def load_context_files(bot_id: int, group_id: int | None,
                             file_names: list[str]) -> list[dict]:
    """Return context blocks as [{source, name, content}].

    Loading order (later overrides earlier for same filename):
      1. bot workspace files
      2. group shared files (same names, if group_id given)

    Returned as a list so callers can inject as user-message prefix
    rather than stuffing into system prompt.
    """
    blocks = []
    bot_ws = bot_workspace(bot_id, group_id)
    group_ws = group_workspace(group_id) if group_id else None

    for name in file_names:
        bot_content = _read_md(bot_ws / name)
        group_content = group_ws and _read_md(group_ws / name)

        if bot_content:
            blocks.append({"source": "bot", "name": name, "content": bot_content})
        if group_content:
            # Group-level file appended after (or replaces context for that name)
            blocks.append({"source": "group", "name": name, "content": group_content})

    return blocks


def format_context_blocks(blocks: list[dict]) -> str:
    """Format context blocks into a readable string for injection."""
    parts = []
    for b in blocks:
        label = b["name"] if b["source"] == "bot" else f"{b['name']} (群组)"
        parts.append(f"=== {label} ===\n{b['content']}")
    return "\n\n".join(parts)


# legacy shim so simple_v1 keeps working
async def read_startup_files(bot_id: int, file_names: list[str]) -> str:
    blocks = await load_context_files(bot_id, None, file_names)
    return format_context_blocks(blocks)


# ---------------------------------------------------------------------------
# Workspace init
# ---------------------------------------------------------------------------

from workspace.templates import (
    IDENTITY_TEMPLATE,
    SOUL_TEMPLATE,
    BOOTSTRAP_TEMPLATE,
    AGENT_TEMPLATE,
    MEMORY_TEMPLATE,
    BOARD_TEMPLATE,
    SPEC_TEMPLATE,
    API_CONTRACT_TEMPLATE,
    RETRO_LATEST_TEMPLATE,
)

async def init_bot_workspace(bot: dict):
    """Create default workspace files for a newly created bot."""
    bot_id = bot["id"]
    name = bot.get("name", "Bot")
    role = bot.get("role", "")
    system_prompt = (bot.get("system_prompt") or "").strip() or f"你是 {name}，{role}。"
    personality_prompt = (bot.get("personality_prompt") or "").strip() or "- 诚实、专业、高效。"

    ws = bot_workspace(bot_id, bot.get("group_id"))

    identity = IDENTITY_TEMPLATE.format(name=name, role=role, system_prompt=system_prompt)
    soul = SOUL_TEMPLATE.format(name=name, personality_prompt=personality_prompt)
    bootstrap = BOOTSTRAP_TEMPLATE
    memory = MEMORY_TEMPLATE.format(name=name)

    role_lower = (role or "").lower()
    if any(kw in role_lower for kw in ("dev", "开发", "engineer", "工程师", "developer")):
        agent = DEV_AGENT_TEMPLATE.format(name=name, role=role or name)
    elif any(kw in role_lower for kw in ("qa", "test", "测试", "quality")):
        agent = QA_AGENT_TEMPLATE.format(name=name, role=role or name)
    elif any(kw in role_lower for kw in ("ba", "产品", "分析", "product", "analyst", "pm")):
        agent = BA_AGENT_TEMPLATE.format(name=name, role=role or name)
    else:
        agent = AGENT_TEMPLATE.format(name=name, role=role or name)

    for filename, content in [
        ("IDENTITY.md", identity),
        ("SOUL.md", soul),
        ("BOOTSTRAP.md", bootstrap),
        ("AGENT.md", agent),
        ("MEMORY.md", memory),
    ]:
        p = ws / filename
        if not p.exists():
            p.write_text(content, encoding="utf-8")

    # Ensure skills/ manual and learned subdirectories exist
    skills_dir = ws / "skills"
    skills_dir.mkdir(exist_ok=True)
    (skills_dir / "manual").mkdir(exist_ok=True)
    (skills_dir / "learned").mkdir(exist_ok=True)
    (skills_dir / "learned" / "active").mkdir(exist_ok=True)
    (skills_dir / "learned" / "draft").mkdir(exist_ok=True)

    # 1. system -> system/skills (4 levels up)
    system_link = skills_dir / "system"
    if not system_link.exists() and not system_link.is_symlink():
        try:
            system_link.symlink_to("../../../../system/skills")
        except Exception as e:
            log.warning(f"Failed to symlink system skills: {e}", exc_info=True)

    # 2. group -> group shared/skills (3 levels up)
    group_link = skills_dir / "group"
    if not group_link.exists() and not group_link.is_symlink():
        try:
            group_link.symlink_to("../../../shared/skills")
        except Exception as e:
            log.warning(f"Failed to symlink group skills: {e}", exc_info=True)

    # 3. role -> group roles/<role>/skills (3 levels up)
    if role:
        role_link = skills_dir / "role"
        expected_target = f"../../../roles/{role}/skills"
        if role_link.is_symlink():
            try:
                actual_target = os.readlink(role_link)
                if actual_target != expected_target:
                    role_link.unlink()
            except Exception:
                log.exception("workspace: failed to reset role skills link for role %s", role)
        if not role_link.exists() and not role_link.is_symlink():
            try:
                role_link.symlink_to(expected_target)
            except Exception as e:
                log.warning(f"Failed to symlink role skills to {expected_target}: {e}", exc_info=True)
    else:
        role_link = skills_dir / "role"
        if role_link.is_symlink():
            try:
                role_link.unlink()
            except Exception:
                log.exception("workspace: failed to remove role skills link")



def list_workspace_tree(bot_id: int, group_id: int | None = None, role: str | None = None) -> list[dict]:
    """Return file tree as list of {path, name, is_dir} for UI, filtering out disabled skills."""
    ws = bot_workspace(bot_id, group_id)
    
    # Resolve disabled skill stems for the bot to hide them from the tree
    try:
        from skills.discovery import _list_skills_all_sync
        all_skills = _list_skills_all_sync(bot_id, group_id, role)
        disabled_stems = {s["name"] for s in all_skills if s.get("status") in ("disabled", "deprecated")}
    except Exception:
        disabled_stems = set()

    result = []
    # UI 文件树：剪枝重型目录(node_modules/.git/.history)防卡顿，但保留 .gitignore 等 dotfile 给人看
    paths, _ = walk_visible(ws, max_entries=2000, skip_hidden=False)
    for p in paths:
        rel = str(p.relative_to(ws)).replace("\\", "/")
        
        # Filter out disabled skills from the skills/ folder
        parts = rel.split("/")
        if len(parts) >= 2 and parts[0] == "skills" and not p.is_dir():
            if p.stem in disabled_stems:
                continue
                
        result.append({"path": rel, "name": p.name, "is_dir": p.is_dir()})
    return result


async def init_group_workspace(group_id: int, group_name: str = ""):
    """Create default shared workspace files for a newly created group."""
    ws = group_workspace(group_id)
    (ws / "docs").mkdir(exist_ok=True)        # 群组共享文档（BA分析/QA报告/设计说明）
    (ws / "workspace").mkdir(exist_ok=True)   # 代码 git 树落点（仅放代码）
    (ws / "skills").mkdir(exist_ok=True)
    (ws / "prs").mkdir(exist_ok=True)         # PR 记录（固定协调件目录）
    (ws.parent / "runs").mkdir(exist_ok=True)  # workspaces/group_{id}/runs/


    # 创建 PROJECTS.md 项目清单（空模板，由 Bot 按实际项目填充）
    projects_md = ws / "workspace" / "PROJECTS.md"
    if not projects_md.exists():
        projects_content = f"""# 项目清单 · {group_name or '群组'}

> 记录所有项目的元数据，由 PM/BA Bot 维护。

## 活跃项目

| 项目名 | 路径 | 负责人 | 状态 | 当前任务 |
|--------|------|--------|------|----------|

### 项目状态说明（Dev / QA 信号机制）

- 🟢 **开发中** - Dev Bot 正在开发此项目
- 🟡 **待验收** - Dev 完成开发后改为此状态，**QA Bot 以此判断要测哪个项目**
- 🔴 **阻塞** - 有问题需要解决
- ✅ **已完成** - 通过验收，可部署

> **Dev Bot**：完成开发后，必须将本文件中该项目的状态改为 `🟡 待验收`，作为通知 QA 的信号。
> **QA Bot**：开始测试前，先读本文件，找 `🟡 待验收` 的项目行，取其 **路径** 字段。若工单里指定了项目名，优先用工单里的。

## QA 测试指引

1. 读本文件**活跃项目**表格，找状态为 `🟡 待验收` 的项目，取得其 **路径** 字段
2. 查看 [SPEC.md](../SPEC.md) 了解需求和验收标准
3. 查看 [BOARD.md](../BOARD.md) 了解当前迭代目标
4. 用 `read_file(path="workspace/<project>/<文件名>")` 读取代码
5. 用 `run_shell(cmd="...", cwd="workspace/<project>")` 在代码目录执行命令
6. 将测试结果写入 [docs/test-report.md](../docs/test-report.md)

## 添加新项目

创建新项目时，在上方表格添加一行，并在 `workspace/` 下创建对应目录。
"""
        projects_md.write_text(projects_content, encoding="utf-8")

    display = group_name or f"群组 {group_id}"
    today = date.today().isoformat()

    # 四个固定协调件全部建群即物理落地（含写保护的 RETRO_LATEST.md）
    for filename, content in [
        ("BOARD.md", BOARD_TEMPLATE.format(display=display, today=today)),
        ("SPEC.md", SPEC_TEMPLATE.format(display=display)),
        ("API_CONTRACT.md", API_CONTRACT_TEMPLATE.format(display=display)),
        ("RETRO_LATEST.md", RETRO_LATEST_TEMPLATE.format(display=display)),
    ]:
        p = ws / filename
        if not p.exists():
            p.write_text(content, encoding="utf-8")

    # 建群拷贝：按群语言把全局角色模板拷进 group_<id>/roles/（幂等，System 池不拷）。
    # 延迟 import 避免 workspace 包 import 期与 skills.store 形成环。
    from workspace.role_provision import provision_group_roles
    provision_group_roles(group_id)


async def init_all_bots(bots: list[dict]):
    """Backfill workspace files for all existing bots that don't have them yet."""
    for bot in bots:
        if bot.get("type") == "bot":
            await init_bot_workspace(bot)


async def append_log(
    bot_id: int,
    reply: str,
    *,
    user_message: str = "",
    sender_name: str = "",
    tool_calls: list[str] | None = None,
    iterations: int = 0,
    executor: str = "",
    group_id: int | None = None,
):
    """Append a structured timestamped entry to today's log file (non-blocking).

    Args:
        reply:        Bot's final reply text.
        user_message: The user message that triggered this run.
        sender_name:  Display name of the sender.
        tool_calls:   List of tool names called (may contain duplicates).
        iterations:   Number of tool-loop iterations executed.
        executor:     Executor plugin id (e.g. 'tool_loop_v1').
    """
    ws = bot_workspace(bot_id, group_id)
    log_file = ws / "logs" / f"{date.today().isoformat()}.md"
    ts = datetime.now().strftime("%H:%M")

    parts = [f"## {ts}"]
    if sender_name or user_message:
        who = f"@{sender_name}" if sender_name else ""
        parts.append(f"\n**用户{(' · ' + who) if who else ''}：** {user_message[:200].strip()}")
    if tool_calls:
        from collections import Counter
        counts = Counter(tool_calls)
        summary = "、".join(f"{name} ×{n}" if n > 1 else name for name, n in counts.items())
        parts.append(f"**工具：** {summary}")
        if iterations:
            parts.append(f"**迭代：** {iterations} 轮")
    elif executor:
        parts.append(f"**执行器：** {executor}")
    reply_preview = reply.strip()[:200]
    if len(reply.strip()) > 200:
        reply_preview += "…"
    parts.append(f"**回复：** {reply_preview}")

    text = "\n".join(parts) + "\n"

    def _write():
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n" + text)

    await asyncio.to_thread(_write)


def _build_archive_markdown(
    group_id: int,
    run_id: str,
    bot: dict,
    user_message: str,
    sender_name: str,
    tool_records: list[dict] | None,
    reply: str,
    iterations: int,
    model: str,
    executor: str,
    now: datetime,
) -> str:
    _RESULT_PREVIEW = 500
    lines = [
        f"# Run · {bot.get('name', '')} · {now.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"- **Group:** {group_id}",
        f"- **Bot:** {bot.get('name', '')} (id={bot.get('id', '')})",
        f"- **Executor:** {executor}",
        f"- **Model:** {model}",
        f"- **Run ID:** {run_id}",
        "",
        "---",
        "",
        "## Input",
        "",
        f"**From:** @{sender_name}" if sender_name else "**From:** (unknown)",
        "",
        f"> {user_message[:500].strip()}" if user_message else "> (no message)",
        "",
        "---",
        "",
        "## Execution",
        "",
        f"**Iterations:** {iterations}",
        f"**Tools called:** {len(tool_records or [])}",
        "",
    ]

    for i, rec in enumerate(tool_records or [], 1):
        import json as _json
        args_str = _json.dumps(rec.get("args", {}), ensure_ascii=False)
        result_preview = rec.get("result", "")[:_RESULT_PREVIEW]
        if len(rec.get("result", "")) > _RESULT_PREVIEW:
            result_preview += "…"
        lines += [
            f"### Tool {i} — {rec.get('name', '')}",
            "",
            f"**Args:** `{args_str}`",
            "",
            "**Result:**",
            "```",
            result_preview,
            "```",
            "",
        ]

    lines += [
        "---",
        "",
        "## Output",
        "",
        reply.strip()[:2000] + ("…" if len(reply.strip()) > 2000 else ""),
        "",
    ]
    return "\n".join(lines)


async def archive_run(
    group_id: int,
    run_id: str,
    bot: dict,
    *,
    user_message: str = "",
    sender_name: str = "",
    tool_records: list[dict] | None = None,
    reply: str = "",
    iterations: int = 0,
    model: str = "",
    executor: str = "",
):
    """Write a full execution record to the group's runs directory.

    One file per run, named YYYY-MM-DD_HHMMSS_{run_id[:8]}.md.
    tool_records: list of {"name": str, "args": dict, "result": str}.
    """
    from workspace import layout
    runs_dir = layout.group_runs_dir(group_id)
    runs_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    filename = f"{now.strftime('%Y-%m-%d_%H%M%S')}_{run_id[:8]}.md"
    run_file = runs_dir / filename

    text = _build_archive_markdown(
        group_id=group_id,
        run_id=run_id,
        bot=bot,
        user_message=user_message,
        sender_name=sender_name,
        tool_records=tool_records,
        reply=reply,
        iterations=iterations,
        model=model,
        executor=executor,
        now=now,
    )

    def _write():
        run_file.write_text(text, encoding="utf-8")

    await asyncio.to_thread(_write)

log = logging.getLogger(__name__)
