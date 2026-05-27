"""
workspace_tools.py — ToolDef declarations, handler implementations, and
registration for the tool_loop_v1 executor's workspace tool set.

Kept separate from the execution loop so tool definitions (stable) don't
intermingle with the AI loop logic (frequently changed).
"""
import asyncio
import fnmatch
import os
import sys
from pathlib import Path

from executors.base import ExecutionContext, ExecutionResult, ToolDef
from executors import tool_executor, registry as _executor_registry
import workspace as _ws
from skills import run_skill
import executors.compact as compact

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

_IS_WINDOWS  = sys.platform == "win32"
_DEFAULT_SHELL = (
    ["powershell.exe", "-NoProfile", "-Command"] if _IS_WINDOWS else ["/bin/sh", "-c"]
)

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

_WORKSPACE_TOOLS = [
    ToolDef(
        name="read_file",
        description="读取 Bot 工作区内的文件内容",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "相对于工作区根目录的路径"}},
            "required": ["path"],
        },
    ),
    ToolDef(
        name="write_file",
        description="向工作区文件写入内容（会覆盖）",
        parameters={
            "type": "object",
            "properties": {
                "path":    {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    ),
    ToolDef(
        name="list_workspace",
        description="列出 Bot 工作区的目录结构",
        parameters={"type": "object", "properties": {}},
    ),
    ToolDef(
        name="run_skill",
        description="执行 skills/ 目录中的技能脚本",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "技能文件名（不含扩展名）"},
                "args": {"type": "string", "default": ""},
            },
            "required": ["name"],
        },
    ),
    ToolDef(
        name="run_shell",
        description="在本地执行 shell 命令，返回 stdout / stderr / exit_code",
        parameters={
            "type": "object",
            "properties": {
                "cmd":        {"type": "string",  "description": "要执行的 shell 命令"},
                "cwd":        {"type": "string",  "description": "工作目录（绝对路径），默认为用户 home 目录"},
                "timeout":    {"type": "integer", "description": "超时秒数，默认 30", "default": 30},
                "background": {"type": "boolean", "description": "后台运行，立即返回 PID", "default": False},
            },
            "required": ["cmd"],
        },
    ),
    ToolDef(
        name="read_local_file",
        description="读取本地任意路径的文件（工作区外）",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "文件的绝对路径"}},
            "required": ["path"],
        },
    ),
    ToolDef(
        name="write_local_file",
        description="写入本地任意路径的文件（自动创建父目录）",
        parameters={
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "文件的绝对路径"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    ),
    ToolDef(
        name="spawn_agent",
        description="派生子 Agent：将子任务委托给另一个 Bot 同步执行，等待结果返回后继续",
        parameters={
            "type": "object",
            "properties": {
                "bot_name": {"type": "string", "description": "目标 Bot 的名称"},
                "task":     {"type": "string", "description": "委托给子 Agent 的具体任务描述"},
            },
            "required": ["bot_name", "task"],
        },
    ),
]

# ---------------------------------------------------------------------------
# Spawn-agent support
# ---------------------------------------------------------------------------

_SPAWN_MAX_DEPTH = 3


class _NullBroadcaster:
    """Silent broadcaster for sub-agent runs — suppresses all WS events."""
    async def broadcast(self, group_id, message):
        pass


async def _spawn_agent_handler(bot_name: str, task: str, context: dict = None) -> str:
    ctx = context or {}
    group_id    = ctx.get("group_id")
    all_bots    = ctx.get("all_bots", [])
    all_members = ctx.get("all_members", [])
    spawn_depth = ctx.get("spawn_depth", 0)

    if spawn_depth >= _SPAWN_MAX_DEPTH:
        return f"[spawn_agent] 已达最大深度 {_SPAWN_MAX_DEPTH}，拒绝派生"

    target = next((b for b in all_bots if b["name"] == bot_name), None)
    if not target:
        available = "、".join(b["name"] for b in all_bots) or "（无）"
        return f"[spawn_agent] 未找到 Bot「{bot_name}」。可用：{available}"

    sub_ctx = ExecutionContext(
        bot=target,
        group_id=group_id,
        user_message=task,
        sender={"id": 0, "name": "sub_agent", "type": "bot", "avatar_color": "#888"},
        history=[],
        all_bots=all_bots,
        all_members=all_members,
        broadcaster=_NullBroadcaster(),
        spawn_depth=spawn_depth + 1,
    )
    try:
        result = await _executor_registry.get(
            target.get("executor_id", "simple_v1")
        ).run(sub_ctx)
        return result.full_text or "[子 Agent 未返回内容]"
    except Exception as e:
        return f"[spawn_agent 执行错误] {e}"


# ---------------------------------------------------------------------------
# Skills XML builder (prompt-side, reads compact's context window table)
# ---------------------------------------------------------------------------

_SKILL_DESC_MAX_CHARS = 250


def _build_skills_xml(skills: list[dict], model_name: str) -> tuple[str, set[str]]:
    """Build the lazy-skill XML block with token budget control.

    Budget = max(3000 chars, 1% of model context window in chars).
    Returns (xml_string, set_of_included_skill_names).
    """
    context_window = compact._MODEL_CONTEXT_WINDOWS.get(model_name, compact._DEFAULT_CONTEXT_WINDOW)
    budget = max(3000, int(context_window * 0.01 * 4))

    parts: list[str] = []
    used = 0
    included: set[str] = set()
    skipped = 0

    for s in skills:
        desc = (s.get("description") or "")[:_SKILL_DESC_MAX_CHARS]
        snippet_lines = [
            f"    <name>{s['name']}</name>",
            f"    <description>{desc}</description>",
        ]
        if s.get("when_to_use"):
            snippet_lines.append(f"    <when_to_use>{s['when_to_use']}</when_to_use>")
        if s.get("argument_hint"):
            snippet_lines.append(f"    <argument_hint>{s['argument_hint']}</argument_hint>")
        snippet = "  <skill>\n" + "\n".join(snippet_lines) + "\n  </skill>"

        if used + len(snippet) > budget:
            skipped += 1
            continue

        parts.append(snippet)
        used += len(snippet)
        included.add(s["name"])

    if not parts:
        return "", included

    xml = "<available_skills>\n" + "\n".join(parts) + "\n</available_skills>"
    if skipped:
        xml += f"\n<!-- 另有 {skipped} 个技能因 token 预算未列出 -->"
    return xml, included


# ---------------------------------------------------------------------------
# Sensitive path protection
# ---------------------------------------------------------------------------

# Sensitive path prefixes — expanded at runtime with Path.expanduser()
_SENSITIVE_PATH_PREFIXES = [
    "~/.ssh",
    "~/.aws",
    "~/.gnupg",
    "~/.config/gcloud",
    "~/.kube",
]

# Sensitive filename patterns (fnmatch style)
_SENSITIVE_FILENAME_PATTERNS = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_rsa.*",
    "id_ed25519",
    "id_ed25519.*",
    "credentials",
    ".netrc",
    "*.pfx",
    "*.p12",
]

# Filenames explicitly allowed despite matching a broad pattern above
_SENSITIVE_FILENAME_ALLOWLIST = {
    ".env.example",
    ".env.sample",
    ".env.template",
}


def _is_sensitive_path(path: str) -> bool:
    """Return True if path points to a sensitive location that must not be read or written."""
    try:
        p = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        p = Path(path).expanduser()
    p_str = str(p)
    filename = p.name

    # Allowlist check first
    if filename in _SENSITIVE_FILENAME_ALLOWLIST:
        return False

    # Directory prefix check
    for prefix in _SENSITIVE_PATH_PREFIXES:
        expanded = str(Path(prefix).expanduser())
        if p_str == expanded or p_str.startswith(expanded + os.sep):
            return True

    # Filename pattern check (case-insensitive for macOS APFS)
    filename_lower = filename.lower()
    for pattern in _SENSITIVE_FILENAME_PATTERNS:
        if fnmatch.fnmatch(filename_lower, pattern):
            return True

    return False


# ---------------------------------------------------------------------------
# Tool execution hooks
# ---------------------------------------------------------------------------

_DANGEROUS_PATTERNS = [
    ("rm -rf /",       "禁止删除根目录"),
    ("rm -rf ~",       "禁止删除 home 目录"),
    ("rm -rf $HOME",   "禁止删除 home 目录"),
    (":(){:|:&};:",    "禁止 fork bomb"),
    ("mkfs",           "禁止格式化磁盘"),
    ("dd if=/dev/",    "禁止 dd 写入磁盘"),
    ("> /dev/sda",     "禁止写入磁盘设备"),
    ("chmod -R 777 /", "禁止递归修改根目录权限"),
    ("> /etc/passwd",  "禁止覆盖系统文件"),
    ("shutdown",       "禁止关机命令"),
    ("reboot",         "禁止重启命令"),
]

_TOOL_RESULT_MAX_CHARS = 2_000
_TOOL_RESULT_HEAD_TAIL = _TOOL_RESULT_MAX_CHARS // 2


async def _default_output_truncator(
    name: str, arguments: dict, result: str, context: dict
) -> str | None:
    """Truncate long tool results using head+tail strategy."""
    if len(result) <= _TOOL_RESULT_MAX_CHARS:
        return None
    dropped = len(result) - _TOOL_RESULT_MAX_CHARS
    head = result[:_TOOL_RESULT_HEAD_TAIL]
    tail = result[-_TOOL_RESULT_HEAD_TAIL:]
    return head + f"\n\n[... {dropped:,} 字符已省略 ...]\n\n" + tail


async def _default_shell_guard(name: str, arguments: dict, context: dict) -> dict | None:
    """Block dangerous shell commands before execution."""
    if name != "run_shell":
        return None
    cmd = (arguments.get("cmd") or "").strip().lower()
    for pattern, reason in _DANGEROUS_PATTERNS:
        if pattern.lower() in cmd:
            return {"block": True, "reason": f"{reason}（命令：{arguments.get('cmd')}）"}
    return None


# ---------------------------------------------------------------------------
# Prompt helper
# ---------------------------------------------------------------------------

def _with_personality(base_prompt: str, bot: dict) -> str:
    p = (bot.get("personality_prompt") or "").strip()
    return base_prompt + f"\n\n【性格指令】\n{p}" if p else base_prompt


# ---------------------------------------------------------------------------
# Tool handler implementations
# ---------------------------------------------------------------------------

async def _handle_read_file(path: str, context: dict = None) -> str:
    bot_id = (context or {}).get("bot_id")
    return await _ws.read_file(bot_id, path) if bot_id else "[错误] 缺少 bot_id"


async def _handle_write_file(path: str, content: str, context: dict = None) -> str:
    bot_id = (context or {}).get("bot_id")
    return await _ws.write_file(bot_id, path, content) if bot_id else "[错误] 缺少 bot_id"


async def _handle_list_workspace(context: dict = None) -> str:
    bot_id = (context or {}).get("bot_id")
    return await _ws.list_workspace(bot_id) if bot_id else "[错误] 缺少 bot_id"


async def _handle_run_skill(name: str, args: str = "", context: dict = None) -> str:
    bot_id = (context or {}).get("bot_id")
    return await run_skill(bot_id, name, args, ctx=context) if bot_id else "[错误] 缺少 bot_id"


async def _handle_run_shell(
    cmd: str, cwd: str = "", timeout: int = 30,
    background: bool = False, context: dict = None,
) -> str:
    work_dir = cwd.strip() or str(Path.home())
    try:
        if background:
            proc = await asyncio.create_subprocess_exec(
                *_DEFAULT_SHELL, cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=work_dir,
                env={**os.environ},
            )
            return f"已在后台启动（PID: {proc.pid}），命令：{cmd}"
        proc = await asyncio.create_subprocess_exec(
            *_DEFAULT_SHELL, cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
            env={**os.environ},
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        parts = [f"exit_code: {proc.returncode}"]
        if out:
            parts.append(f"stdout:\n{out}")
        if err:
            parts.append(f"stderr:\n{err}")
        return "\n".join(parts)
    except asyncio.TimeoutError:
        return f"[超时] 命令执行超过 {timeout} 秒"
    except Exception as e:
        return f"[错误] {e}"


async def _handle_read_local_file(path: str, context: dict = None) -> str:
    if _is_sensitive_path(path):
        return f"[安全拒绝] 不允许读取敏感路径：{path}"
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"[文件不存在] {path}"
    except Exception as e:
        return f"[读取错误] {e}"


async def _handle_write_local_file(path: str, content: str, context: dict = None) -> str:
    if _is_sensitive_path(path):
        return f"[安全拒绝] 不允许写入敏感路径：{path}"
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已写入 {path}（{len(content)} 字符）"
    except Exception as e:
        return f"[写入错误] {e}"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_workspace_tools() -> None:
    """Register all workspace tool handlers and hooks into the global tool_executor."""
    tool_executor.add_before_hook(_default_shell_guard)
    tool_executor.add_after_hook(_default_output_truncator)
    handlers = {
        "read_file":        _handle_read_file,
        "write_file":       _handle_write_file,
        "list_workspace":   _handle_list_workspace,
        "run_skill":        _handle_run_skill,
        "run_shell":        _handle_run_shell,
        "read_local_file":  _handle_read_local_file,
        "write_local_file": _handle_write_local_file,
        "spawn_agent":      _spawn_agent_handler,
    }
    for tdef in _WORKSPACE_TOOLS:
        tool_executor.register(tdef, handlers[tdef.name])
