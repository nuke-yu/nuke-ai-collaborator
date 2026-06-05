import logging
import asyncio
import threading
from pathlib import Path
from datetime import date, datetime
from typing import Dict

from skills.constants import WORKSPACE_ROOT, LEARNED_ACTIVE as _LEARNED_ACTIVE, LEARNED_DRAFT as _LEARNED_DRAFT
from workspace.templates import (
    IDENTITY_TEMPLATE, SOUL_TEMPLATE, BOOTSTRAP_TEMPLATE,
    AGENT_TEMPLATE, MEMORY_TEMPLATE, BOARD_TEMPLATE, SPEC_TEMPLATE
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



def bot_workspace(bot_id: int) -> Path:
    path = WORKSPACE_ROOT / f"bot_{bot_id}"
    path.mkdir(parents=True, exist_ok=True)
    for sub in _SUBDIRS:
        (path / sub).mkdir(exist_ok=True)
    return path


def group_workspace(group_id: int) -> Path:
    path = WORKSPACE_ROOT / f"group_{group_id}" / "shared"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_path(workspace: Path, relative: str) -> Path | None:
    try:
        resolved = (workspace / relative).resolve()
        if resolved.is_relative_to(workspace.resolve()):
            return resolved
    except Exception:
        log.exception("vfs: failed to save history for %s", p)
    return None


_SHARED_FILES = {"BOARD.md", "SPEC.md", "API_CONTRACT.md"}


def _get_effective_ws(bot_id: int, path_str: str) -> Path:
    """
    Point 3: Group File Redirection.
    Redirect shared files to group's shared folder; others to bot's private folder.
    """
    ws = bot_workspace(bot_id)
    if path_str in _SHARED_FILES or path_str.startswith("deliverables/"):
        from db import connect_sync
        with connect_sync() as conn:
            row = conn.execute("SELECT group_id FROM members WHERE id = ?", (bot_id,)).fetchone()
            if row:
                return group_workspace(row[0])
    return ws



async def read_file(bot_id: int, path: str) -> str:
    ws = _get_effective_ws(bot_id, path)
    p = _safe_path(ws, path)
    if p is None:
        return f"[错误] 非法路径: {path}"
    if not p.exists():
        return f"[文件不存在] {path}"
    
    lock = _get_path_lock(p)
    async with lock:
        try:
            return await asyncio.to_thread(p.read_text, encoding="utf-8")
        except Exception as e:
            return f"[读取错误] {e}"


_WRITE_PROTECTED = {"MEMORY.md"}


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


def list_file_history(bot_id: int, path: str) -> list[dict]:
    ws = bot_workspace(bot_id)
    p = _safe_path(ws, path)
    if p is None:
        return []
    hdir = _history_dir(ws, p)
    if not hdir.exists():
        return []
    versions = sorted(hdir.glob("*.md"), key=lambda f: f.name, reverse=True)
    return [{"ts": f.stem, "size": f.stat().st_size} for f in versions]


def read_file_history_version(bot_id: int, path: str, ts: str) -> str:
    ws = bot_workspace(bot_id)
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


async def write_file(bot_id: int, path: str, content: str, group_id: int | None = None) -> str:
    # group_id given (e.g. the system rendering BOARD.md) → write straight to the
    # group's shared workspace, bypassing the bot-centric redirection (which can't
    # resolve a group for bot_id=0).
    ws = group_workspace(group_id) if group_id is not None else _get_effective_ws(bot_id, path)
    p = _safe_path(ws, path)
    if p is None:
        return f"[错误] 非法路径: {path}"
    if p.name in _WRITE_PROTECTED:
        return f"[受保护] {p.name} 是永久记忆文件，Bot 无法覆盖。如需追加记录，请通过工作区面板手动编辑。"
    rel = str(p.relative_to(ws)).replace("\\", "/")

    lock = _get_path_lock(p)
    async with lock:
        def _do_write() -> str:
            # Redirect learned/active writes → learned/draft (requires user approval)
            if rel.startswith(_LEARNED_ACTIVE):
                draft_path = ws / _LEARNED_DRAFT / p.name
                draft_path.parent.mkdir(parents=True, exist_ok=True)
                draft_path.write_text(content, encoding="utf-8")
                return f"__DRAFT_WRITTEN__:{p.name}"   # sentinel for broadcast
            # Direct writes to learned/draft/ also return sentinel so tool_loop broadcasts
            if rel.startswith(_LEARNED_DRAFT):
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
                return f"__DRAFT_WRITTEN__:{p.name}"
            # Save history before overwriting if content differs
            if p.exists():
                try:
                    if p.read_text(encoding="utf-8") != content:
                        _save_to_history(ws, p)
                except Exception:
                    pass
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return f"已写入 {path}（{len(content)} 字符）"

        result = await asyncio.to_thread(_do_write)

        # Point 1: Emit CodeCommitted event if a code file is written (main thread async)
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
                    await publish(CodeCommitted(
                        group_id=row[0],
                        ticket_id="auto", # Ideally passed via context
                        files=[path],
                        commit_msg=f"Auto-commit by Bot {bot_id}",
                        author_id=bot_id
                    ))

        return result



async def list_workspace(bot_id: int) -> str:
    ws = bot_workspace(bot_id)
    lines = []
    for p in sorted(ws.rglob("*")):
        rel = p.relative_to(ws)
        indent = "  " * (len(rel.parts) - 1)
        icon = "📁" if p.is_dir() else "📄"
        lines.append(f"{indent}{icon} {p.name}")
    return "\n".join(lines) if lines else "（工作区为空）"



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
    bot_ws = bot_workspace(bot_id)
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
)

async def init_bot_workspace(bot: dict):
    """Create default workspace files for a newly created bot."""
    bot_id = bot["id"]
    name = bot.get("name", "Bot")
    role = bot.get("role", "")
    system_prompt = (bot.get("system_prompt") or "").strip() or f"你是 {name}，{role}。"
    personality_prompt = (bot.get("personality_prompt") or "").strip() or "- 诚实、专业、高效。"

    ws = bot_workspace(bot_id)

    identity = IDENTITY_TEMPLATE.format(name=name, role=role, system_prompt=system_prompt)
    soul = SOUL_TEMPLATE.format(name=name, personality_prompt=personality_prompt)
    bootstrap = BOOTSTRAP_TEMPLATE
    agent = AGENT_TEMPLATE.format(name=name, role=role or name)
    memory = MEMORY_TEMPLATE.format(name=name)

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



def list_workspace_tree(bot_id: int) -> list[dict]:
    """Return file tree as list of {path, name, is_dir} for UI."""
    ws = bot_workspace(bot_id)
    result = []
    for p in sorted(ws.rglob("*")):
        rel = str(p.relative_to(ws)).replace("\\", "/")
        if rel.startswith(".history"):
            continue
        result.append({"path": rel, "name": p.name, "is_dir": p.is_dir()})
    return result


async def init_group_workspace(group_id: int, group_name: str = ""):
    """Create default shared workspace files for a newly created group."""
    ws = group_workspace(group_id)
    (ws / "deliverables").mkdir(exist_ok=True)
    (ws / "skills").mkdir(exist_ok=True)
    (ws.parent / "runs").mkdir(exist_ok=True)  # workspaces/group_{id}/runs/

    display = group_name or f"群组 {group_id}"
    today = date.today().isoformat()

    board = BOARD_TEMPLATE.format(display=display, today=today)
    spec = SPEC_TEMPLATE.format(display=display)

    for filename, content in [
        ("BOARD.md", board),
        ("SPEC.md", spec),
    ]:
        p = ws / filename
        if not p.exists():
            p.write_text(content, encoding="utf-8")


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
    ws = bot_workspace(bot_id)
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
    """Write a full execution record to workspaces/group_{id}/runs/.

    One file per run, named YYYY-MM-DD_HHMMSS_{run_id[:8]}.md.
    tool_records: list of {"name": str, "args": dict, "result": str}.
    """
    runs_dir = WORKSPACE_ROOT / f"group_{group_id}" / "runs"
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
