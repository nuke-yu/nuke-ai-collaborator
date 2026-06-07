"""
workspace_tools.py — ToolDef declarations, handler implementations, and
registration for the tool_loop_v1 executor's workspace tool set.

Kept separate from the execution loop so tool definitions (stable) don't
intermingle with the AI loop logic (frequently changed).
"""
import asyncio
import fnmatch
import os
import re
import shlex
import sys
from pathlib import Path

from executors.base import ExecutionContext, ExecutionResult, ToolDef
from core import config
from executors import tool_executor, registry as _executor_registry
import workspace as _ws
import editing
from skills import run_skill
import executors.compact as compact
import permissions
from core.bg import spawn as bg_spawn  # aliased: a local var named `bg` is used below
from executors.plugins import win_sandbox

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
            "properties": {
                "path": {"type": "string", "description": "相对于工作区根目录的路径"},
                "offset": {"type": "integer", "description": "读取文件的起始字符偏移量"},
                "limit": {"type": "integer", "description": "最大读取字符长度"}
            },
            "required": ["path"],
        },
        concurrency_safe=True,
    ),
    ToolDef(
        name="write_file",
        description="仅用于新建文件或整文件重写；改已有文件请用 edit_file（只发 diff，避免大文件被输出长度截断）。",
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
        name="edit_file",
        description=(
            "对工作区已有文件做精确字符串替换（只发改动片段，不必重发整文件）。"
            "把 old_string 替换为 new_string；old_string 必须在文件中唯一"
            "（否则报错，请加更多上下文或用 replace_all）。修改已有文件首选本工具。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path":        {"type": "string", "description": "相对于工作区根目录的路径"},
                "old_string":  {"type": "string", "description": "要被替换的原文（需与文件内容一致，可含多行）"},
                "new_string":  {"type": "string", "description": "替换后的新内容"},
                "replace_all": {"type": "boolean", "description": "是否替换所有匹配，默认 false", "default": False},
            },
            "required": ["path", "old_string", "new_string"],
        },
    ),
    ToolDef(
        name="list_workspace",
        description="列出 Bot 工作区的目录结构",
        parameters={"type": "object", "properties": {}},
        concurrency_safe=True,
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
        concurrency_safe=True,
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
        description="派生子 Agent：将子任务委托给另一个 Bot 执行。background=true 时立即返回，子 Agent 在后台运行，完成后结果自动注回当前对话",
        parameters={
            "type": "object",
            "properties": {
                "bot_name":   {"type": "string",  "description": "目标 Bot 的名称"},
                "task":       {"type": "string",  "description": "委托给子 Agent 的具体任务描述"},
                "background": {"type": "boolean", "description": "后台运行，立即返回不等待结果", "default": False},
            },
            "required": ["bot_name", "task"],
        },
    ),
]

# ---------------------------------------------------------------------------
# Spawn-agent support
# ---------------------------------------------------------------------------

_SPAWN_MAX_DEPTH = config.SPAWN_MAX_DEPTH

# Running background sub-agent tasks: task_id → asyncio.Task
_bg_tasks: dict[str, asyncio.Task] = {}


class _NullBroadcaster:
    """Silent broadcaster for sub-agent runs — suppresses all WS events."""
    async def broadcast(self, group_id, message):
        pass


async def _run_bg_agent(
    sub_ctx: ExecutionContext,
    bot_name: str,
    parent_steer: "asyncio.Queue | None",
    task_id: str,
) -> None:
    """Run a sub-agent in the background and inject result into parent's steer channel."""
    try:
        result = await _executor_registry.get(
            sub_ctx.bot.get("executor_id", "tool_loop_v1")
        ).run(sub_ctx)
        reply = result.full_text or "[子 Agent 未返回内容]"
    except asyncio.CancelledError:
        raise
    except Exception as e:
        reply = f"[后台子Agent 执行错误] {e}"
    finally:
        _bg_tasks.pop(task_id, None)

    if parent_steer is not None:
        await parent_steer.put(f"[后台子Agent「{bot_name}」已完成]\n{reply}")

    await sub_ctx.broadcaster.broadcast(sub_ctx.group_id, {
        "type": "bg_agent_done",
        "bot_name": bot_name,
        "preview": reply[:300],
    })


async def _spawn_agent_handler(bot_name: str, task: str, background: bool = False, context: dict = None) -> str:
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
        interaction=ctx.get("interaction"),
        spawn_depth=spawn_depth + 1,
        # Attenuate the parent's permissions for the child (blast-radius
        # containment): bypass doesn't propagate, blanket high-risk allows are
        # dropped; deny + scoped allows are kept. The child also can't prompt
        # (engine denies ask when spawn_depth>0).
        ruleset=permissions.derive_subagent_ruleset(ctx.get("ruleset")),
    )

    if background:
        import uuid as _uuid
        task_id = _uuid.uuid4().hex
        parent_steer = ctx.get("steer_channel")
        bg = asyncio.create_task(_run_bg_agent(sub_ctx, bot_name, parent_steer, task_id))
        _bg_tasks[task_id] = bg
        return f"[后台子Agent 已启动] Bot「{bot_name}」正在后台执行，完成后结果将自动注回对话。task_id={task_id}"

    try:
        result = await _executor_registry.get(
            target.get("executor_id", "tool_loop_v1")
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
    "~/.docker",          # config.json holds registry auth tokens
    "~/.config/gh",       # GitHub CLI oauth tokens
    "~/.config/git",      # git credential store / config
    "~/.password-store",  # pass(1) GPG-encrypted secrets
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
    ".git-credentials",   # plaintext git http creds
    ".npmrc",             # npm auth token
    ".pypirc",            # PyPI upload token
    ".dockercfg",         # legacy docker registry auth
    "*.keystore",
    "*.jks",
    ".htpasswd",
    "cookies.sqlite",     # Firefox cookie store
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
    if filename.lower() in _SENSITIVE_FILENAME_ALLOWLIST:
        return False

    # Directory prefix check (primarily for home-dir expanded paths)
    for prefix in _SENSITIVE_PATH_PREFIXES:
        expanded = str(Path(prefix).expanduser())
        if p_str == expanded or p_str.startswith(expanded + os.sep):
            return True

    # Point 6: Defensive Depth (DFT-023)
    # Block sensitive directories regardless of where they appear in the path
    sensitive_dirs = {".ssh", ".aws", ".docker", ".gnupg", ".kube", ".password-store"}
    if any(d in p.parts for d in sensitive_dirs):
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

# ---------------------------------------------------------------------------
# Shell command safety: pre-compiled regex patterns (tier 2 backstop).
#
# Rationale for upgrading from substring to regex:
#   Substring matching is trivially bypassed by extra whitespace, variable
#   indirection, or obfuscation (e.g. `base64 -d | bash`).
#   Regex with word boundaries and operator-aware splits are more precise
#   while keeping false-positive rates low.
#
# These patterns are a BACKSTOP — the primary gate is the HIL permission
# system (ruleset). This layer catches only the highest-severity commands
# that should never be auto-approved regardless of ruleset.
# ---------------------------------------------------------------------------

_DANGEROUS_PATTERNS: list[tuple[re.Pattern, str]] = [
    # --- system / disk destruction ---
    (re.compile(r'\brm\s+\S*[rR]\S*\s+/\s*$', re.MULTILINE),
     "禁止删除根目录"),
    (re.compile(r'\brm\s+\S*[rR]\S*\s+(~|\$HOME)(\s|$)'),
     "禁止删除家目录"),
    (re.compile(r'\bmkfs\b', re.IGNORECASE),
     "禁止格式化磁盘"),
    (re.compile(r'\bdd\b.*\bif\s*=\s*/dev/', re.IGNORECASE),
     "禁止 dd 读写磁盘设备"),
    (re.compile(r'>\s*/dev/sd[a-z]\d*\b', re.IGNORECASE),
     "禁止写入磁盘块设备"),
    (re.compile(r'\bchmod\s+(\S+\s+)?777\s+/', re.IGNORECASE),
     "禁止递归修改根目录权限"),
    (re.compile(r'>\s*/etc/passwd\b'),
     "禁止覆盖 /etc/passwd"),
    (re.compile(r'>\s*/etc/shadow\b'),
     "禁止覆盖 /etc/shadow"),
    # --- system control ---
    (re.compile(r'\b(shutdown|reboot|poweroff|halt)\b', re.IGNORECASE),
     "禁止关机/重启命令"),
    # fork bomb: :(){ :|:& };:
    (re.compile(r':\s*\(\s*\)\s*\{[^}]*:\s*\|'),
     "禁止 fork bomb"),
    # --- execution obfuscation / RCE (NEW) ---
    # base64 decode is a classic code obfuscation vector; block decoding
    # regardless of what the decoded output is piped to.
    (re.compile(r'\bbase64\s+(-d|--decode)\b', re.IGNORECASE),
     "禁止 base64 解码（混淆执行风险）"),
    # Downloading and immediately executing a remote script.
    (re.compile(
        r'\b(curl|wget)\b[^\n|]*\|\s*(bash|sh|zsh|dash|ash|python\d*|perl|ruby|node)\b',
        re.IGNORECASE,
    ), "禁止将网络下载内容直接管道到解释器执行"),
    # eval with shell substitution — dynamic code execution.
    (re.compile(r'\beval\b.*?(\$\(|`)'),
     "禁止 eval 执行 shell 替换（代码注入风险）"),
]


# --- run_shell danger check, layer 2: tokenized analysis ------------------
# The raw-string regexes above match the literal command and are blind to
# shell quoting/escaping/wrapping. A tokenized pass defeats the cheap evasions:
#   rm -rf "/"        (quoted target)        r''m -rf /     (quoted command name)
#   /usr/bin/fdisk …  (absolute path)        sudo rm -rf ~  (wrapper prefix)
#   env X=1 rm -rf "$HOME"                    cd /tmp && rm -rf /   (command chain)
# shlex dequotes + de-escapes, so all of the above resolve to the real binary.

# Binaries destructive/irreversible enough to block outright (basename match).
_BLOCK_BINARIES = frozenset({
    "mkfs", "mke2fs", "mkdosfs", "fdisk", "parted", "mkswap", "wipefs",
    "shutdown", "reboot", "poweroff", "halt", "init", "telinit",
})

# Command prefixes that wrap the real command — strip them to find argv0.
_CMD_WRAPPERS = frozenset({
    "sudo", "doas", "env", "command", "builtin", "exec", "nohup", "time",
    "nice", "ionice", "stdbuf", "setsid", "xargs", "timeout",
})

# Shell interpreters: `bash -c "<cmd>"` hides the real command in a string arg,
# evading both layers — so we recurse into the -c payload.
_SHELL_INTERPRETERS = frozenset({"bash", "sh", "zsh", "dash", "ash", "ksh"})

# rm targets that mean "catastrophic scope" once combined with -r/-f.
_RM_CATASTROPHIC = frozenset({"/", "~", "$HOME", "${HOME}", "/*", "*", "~/*", "$HOME/*"})
_RM_RECURSIVE_RE = re.compile(r"^-[a-zA-Z]*r[a-zA-Z]*$")


def _iter_simple_commands(cmd: str) -> list[list[str]] | None:
    """Tokenize cmd respecting quotes, split into argv lists on shell control
    operators (; && || | & ( ) < >). Returns None if cmd can't be parsed
    (unbalanced quotes) — caller then relies on the regex layer only."""
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ""          # '#' mid-command is not a comment for our purposes
        tokens = list(lex)
    except ValueError:
        return None
    commands: list[list[str]] = []
    cur: list[str] = []
    for tok in tokens:
        if tok and all(ch in ";&|()<>\n" for ch in tok):   # operator token
            if cur:
                commands.append(cur)
                cur = []
        else:
            cur.append(tok)
    if cur:
        commands.append(cur)
    return commands


def _resolved_argv0(argv: list[str]) -> tuple[str | None, list[str]]:
    """Strip leading VAR=val assignments and wrapper commands; return
    (basename_of_real_command, remaining_args). Returns (None, …) when the
    command is variable/substitution indirection that can't be statically
    resolved (e.g. `$CMD …`)."""
    i = 0
    changed = True
    while changed and i < len(argv):
        changed = False
        # leading env-assignments (incl. those introduced by `env VAR=val`)
        while i < len(argv) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argv[i]):
            i += 1
            changed = True
        # wrapper command + its own flags
        while i < len(argv) and os.path.basename(argv[i]) in _CMD_WRAPPERS:
            i += 1
            changed = True
            while i < len(argv) and argv[i].startswith("-"):
                i += 1
    if i >= len(argv):
        return None, []
    c0 = argv[i]
    if c0.startswith("$") or c0.startswith("`") or "$(" in c0:
        return None, argv[i + 1:]
    return os.path.basename(c0), argv[i + 1:]


def _check_tokenized(cmd: str) -> tuple[bool, str]:
    """Block dangerous operations identified on the shlex-resolved command."""
    commands = _iter_simple_commands(cmd)
    if commands is None:
        return False, ""
    for argv in commands:
        base, rest = _resolved_argv0(argv)
        if base is None:
            continue
        if base in _BLOCK_BINARIES:
            return True, f"禁止危险命令 {base}"
        if base in _SHELL_INTERPRETERS:
            for j, a in enumerate(rest):
                if a == "-c" and j + 1 < len(rest):
                    inner_blocked, inner_reason = _check_shell_command(rest[j + 1])
                    if inner_blocked:
                        return True, inner_reason
        if base == "rm" and any(_RM_RECURSIVE_RE.match(a) or a == "--recursive" for a in rest):
            if any(a in _RM_CATASTROPHIC for a in rest if not a.startswith("-")):
                return True, "禁止递归删除根目录/家目录"
        if base in ("chmod", "chown") and any(a in ("-R", "--recursive") for a in rest):
            if any(a == "/" for a in rest):
                return True, f"禁止递归修改根目录{'权限' if base == 'chmod' else '属主'}"
        if base == "dd" and any(a.startswith("if=/dev/") for a in rest):
            return True, "禁止 dd 读写磁盘设备"
    return False, ""


def _check_shell_command(cmd: str) -> tuple[bool, str]:
    """Check cmd for dangerous operations. Returns (blocked, reason).

    Two layers (block if EITHER fires):
      1. raw-string regexes (_DANGEROUS_PATTERNS) — catch structure that
         tokenization loses (redirects, pipe-to-interpreter, fork bomb, eval+subst).
      2. tokenized analysis (_check_tokenized) — shlex-resolve the real binary to
         defeat quote/whitespace/path-prefix/wrapper/command-chain evasions.
    """
    for pattern, reason in _DANGEROUS_PATTERNS:
        if pattern.search(cmd):
            return True, reason
    return _check_tokenized(cmd)


# --- run_shell sandbox tier 1: env allowlist + cwd confinement -------------

# Only these env vars (and LC_* locale vars) are passed to spawned shells.
# Everything else — API keys, tokens, cloud creds — is stripped so a command
# the model runs can't exfiltrate the host's secrets.
_SHELL_ENV_ALLOW = {
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE",
    "TERM", "TMPDIR", "TZ", "PWD", "HOSTNAME",
}
_SHELL_ENV_ALLOW_PREFIX = ("LC_",)


def _sandbox_env() -> dict:
    """Build a minimal env for spawned shells, stripping host secrets."""
    return {
        k: v for k, v in os.environ.items()
        if k in _SHELL_ENV_ALLOW or k.startswith(_SHELL_ENV_ALLOW_PREFIX)
    }


def _resolve_shell_cwd(cwd: str, bot_id) -> tuple[Path | None, str]:
    """Confine the shell working directory to the bot's workspace.

    Returns (path, "") on success or (None, reason) on rejection. An empty cwd
    defaults to the workspace root; relative paths resolve under it; any target
    that escapes the workspace (absolute path or '..' traversal) is rejected.
    """
    if bot_id is None:
        return None, "缺少 bot_id，无法确定工作区"
    root = _ws.bot_workspace(bot_id).resolve()
    candidate = (cwd or "").strip()
    if not candidate:
        return root, ""
    p = Path(candidate)
    target = (p if p.is_absolute() else root / p)
    try:
        target = target.resolve()
        if target.is_relative_to(root):
            return target, ""
    except (OSError, ValueError):
        pass
    return None, f"工作目录越界，必须位于工作区内：{cwd}"


_TOOL_RESULT_MAX_CHARS = config.TOOL_RESULT_MAX_CHARS
_TOOL_RESULT_HEAD_TAIL = _TOOL_RESULT_MAX_CHARS // 2


async def _default_secret_redactor(
    name: str, arguments: dict, result: str, context: dict
) -> str | None:
    """Mask credentials in tool output before it enters the shared model context.

    Runs BEFORE the truncator (registration order) so the full, pre-truncation
    text — including whatever truncation persists — is already redacted. Covers
    builtin / run_shell / run_skill (MCP is redacted in its own provider)."""
    from executors.redaction import redact_secrets
    redacted, n = redact_secrets(result)
    if n:
        import logging
        logging.getLogger(__name__).warning(
            "redacted %d secret(s) from '%s' output", n, name
        )
        return redacted
    return None


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
    """Block dangerous shell commands before execution.

    Tier-2 backstop: run_shell is the highest-risk tool, so when no permission
    ruleset is available to evaluate it (ruleset is None), fail closed rather
    than open. The permission hook (_permission_check_hook) handles the case
    where a ruleset *is* present — there it can ask/deny per the pipeline.
    """
    if name != "run_shell":
        return None
    if context.get("ruleset") is None:
        return {"block": True, "reason": "run_shell 未接入权限系统（无 ruleset），出于安全已拒绝执行"}
    cmd = (arguments.get("cmd") or "").strip()
    blocked, reason = _check_shell_command(cmd)
    if blocked:
        return {"block": True, "reason": f"{reason}（命令：{cmd}）"}
    return None



# Tools that can escape the workspace or cause side effects. When no permission
# ruleset is available to gate them (ruleset is None), fail closed rather than
# open (DFT-024) — otherwise an executor that forgets to build a ruleset (e.g.
# the old react_v1) would run these with zero checks. Read-only workspace tools
# stay allowed so such a bot can still inspect its own workspace.
_APPROVAL_REQUIRED_TOOLS = frozenset({
    "run_shell", "write_file", "read_local_file", "write_local_file", "spawn_agent",
})

# RD 流水线的内部记账工具（Jira/PR 替身）：人把关在工作流的 4 道门，不在每次工具调用，
# 故这些工具不走权限询问，直接放行。
_AUTO_ALLOW_TOOLS = frozenset({
    "create_jira_ticket", "list_jira_tickets", "create_pr",
})


async def _permission_check_hook(name: str, arguments: dict, context: dict) -> dict | None:
    """Run the permission decision pipeline before every tool call."""
    if name in _AUTO_ALLOW_TOOLS:
        return None  # internal RD bookkeeping tools — gated at the workflow doors, not here
    ruleset = context.get("ruleset")
    if ruleset is None:
        if name in _APPROVAL_REQUIRED_TOOLS:
            return {"block": True,
                    "reason": f"{name} 未接入权限系统（无 ruleset），出于安全已拒绝执行"}
        return None  # read-only workspace tools are safe without a ruleset

    result = await permissions.check(
        tool_name=name,
        arguments=arguments,
        ruleset=ruleset,
        bot_id=context.get("bot_id"),
        broadcaster=context.get("broadcaster"),
        group_id=context.get("group_id"),
        spawn_depth=context.get("spawn_depth", 0),
    )

    if result["action"] == "deny":
        return {"block": True, "reason": result.get("reason", "权限拒绝")}

    if result.get("persist_rule"):
        rule = result["persist_rule"]
        # DFT-063: held + exception-logged instead of a bare create_task.
        bg_spawn(permissions.save_rule(
            context.get("bot_id"), rule.tool_pattern, rule.args_pattern, rule.action
        ))

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

async def _handle_read_file(path: str, offset: int | None = None, limit: int | None = None, context: dict = None, **kwargs) -> str:
    bot_id = (context or {}).get("bot_id")
    return await _ws.read_file(bot_id, path, offset=offset, limit=limit) if bot_id else "[错误] 缺少 bot_id"


async def _handle_write_file(path: str, content: str, context: dict = None) -> str:
    bot_id = (context or {}).get("bot_id")
    return await _ws.write_file(bot_id, path, content) if bot_id else "[错误] 缺少 bot_id"


async def _handle_edit_file(path: str, old_string: str, new_string: str,
                            replace_all: bool = False, context: dict = None) -> str:
    bot_id = (context or {}).get("bot_id")
    if not bot_id:
        return "[错误] 缺少 bot_id"
    return await _ws.edit_file(bot_id, path, old_string, new_string, replace_all=replace_all)


async def _handle_list_workspace(context: dict = None) -> str:
    bot_id = (context or {}).get("bot_id")
    return await _ws.list_workspace(bot_id) if bot_id else "[错误] 缺少 bot_id"


async def _handle_run_skill(name: str, args: str = "", context: dict = None) -> str:
    bot_id = (context or {}).get("bot_id")
    return await run_skill(bot_id, name, args, ctx=context) if bot_id else "[错误] 缺少 bot_id"


# --- run_shell sandbox tier 3: dynamic port allocation ----------------------

_INTERCEPT_PORTS = {"8000", "8080", "3000", "5000", "5173", "80"}

def _allocate_free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]


def _intercept_command_ports(cmd: str, env: dict) -> tuple[str, str | None, int | None]:
    allocated_port = None
    intercepted_port = None
    for p in sorted(_INTERCEPT_PORTS, key=len, reverse=True):
        pattern = r"(?<![\d\-])" + re.escape(p) + r"(?![\d])"
        if re.search(pattern, cmd):
            intercepted_port = p
            allocated_port = _allocate_free_port()
            env["APP_PORT"] = str(allocated_port)
            env["PORT"] = str(allocated_port)
            cmd = re.sub(pattern, str(allocated_port), cmd)
            break
    return cmd, intercepted_port, allocated_port


def _wrap_command_with_limits(cmd: str, limit_bytes: int) -> str:
    if not _IS_WINDOWS:
        return f"ulimit -v { limit_bytes // 1024 } 2>/dev/null; {cmd}"
    return cmd


def _check_shell_command_paths(cmd: str, work_dir: Path) -> str | None:
    home_dir = Path("~").expanduser().resolve()
    home_dir_str = str(home_dir)
    
    # 1. Check path-like patterns under /Users, /home, or ~
    path_pattern = r'(?:/Users/|/home/|~)(?:/[a-zA-Z0-9_\-\.]+)+'
    for match in re.findall(path_pattern, cmd):
        try:
            resolved = Path(match).expanduser().resolve()
            if not resolved.is_relative_to(work_dir.resolve()):
                return f"工作区沙箱限制：禁止读写工作区外的路径「{match}」"
        except Exception:
            pass

    # 2. Check direct home directory string references in arguments
    if home_dir_str in cmd:
        for word in re.split(r'[\s\'\"<>\|;&]+', cmd):
            if home_dir_str in word:
                try:
                    resolved = Path(word).expanduser().resolve()
                    if not resolved.is_relative_to(work_dir.resolve()):
                        return f"工作区沙箱限制：禁止读写工作区外的路径「{word}」"
                except Exception:
                    pass
    return None


async def _handle_run_shell(
    cmd: str, cwd: str = "", timeout: int = 30,
    background: bool = False, context: dict = None,
) -> str:
    bot_id = (context or {}).get("bot_id")
    work_dir, err = _resolve_shell_cwd(cwd, bot_id)
    if err:
        return f"[安全拒绝] {err}"
    
    restricted_err = _check_shell_command_paths(cmd, work_dir)
    if restricted_err:
        return f"[安全拒绝] {restricted_err}"
    
    _max_timeout = min(timeout, 300)
    sandbox_env = _sandbox_env()
    
    cmd, intercepted_port, allocated_port = _intercept_command_ports(cmd, sandbox_env)
    safe_cmd = _wrap_command_with_limits(cmd, config.SHELL_MEMORY_LIMIT_BYTES)
    
    try:
        if background:
            proc = await asyncio.create_subprocess_exec(
                *_DEFAULT_SHELL, safe_cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=str(work_dir),
                env=sandbox_env,
                start_new_session=True if not _IS_WINDOWS else False
            )
            if _IS_WINDOWS:
                win_sandbox.apply_memory_limit(proc.pid, config.SHELL_MEMORY_LIMIT_BYTES)
            
            msg = f"已在后台启动（PID: {proc.pid}），命令：{cmd}"
            if allocated_port:
                msg += f"\n[端口分配] 系统已自动分配可用端口: {allocated_port} (注入为环境变量 PORT / APP_PORT)"
            return msg
            
        proc = await asyncio.create_subprocess_exec(
            *_DEFAULT_SHELL, safe_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(work_dir),
            env=sandbox_env,
        )
        if _IS_WINDOWS:
            win_sandbox.apply_memory_limit(proc.pid, config.SHELL_MEMORY_LIMIT_BYTES)
        
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_max_timeout)
        
        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        parts = []
        if intercepted_port:
            parts.append(f"[安全拦截] 已将硬编码端口 {intercepted_port} 替换为动态端口 {allocated_port}")
            
        parts.append(f"exit_code: {proc.returncode}")
        if out:
            parts.append(f"stdout:\n{out}")
        if err:
            parts.append(f"stderr:\n{err}")
        return "\n".join(parts)
        
    except asyncio.CancelledError:
        try:
            proc.kill()
        except Exception:
            pass
        raise
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return f"[安全拦截] 命令执行超时（超过 {_max_timeout} 秒已被强行终止）"
    except Exception as e:
        return f"[系统错误] {e}"


# Note: _is_sensitive_path only covers read_file/write_file; run_shell commands can still access these paths.
async def _handle_read_local_file(path: str, context: dict = None) -> str:
    if _is_sensitive_path(path):
        return f"[安全拒绝] 不允许读取敏感路径：{path}"
    try:
        return await asyncio.to_thread(Path(path).read_text, encoding="utf-8")
    except FileNotFoundError:
        return f"[文件不存在] {path}"
    except Exception as e:
        return f"[读取错误] {e}"


async def _handle_write_local_file(path: str, content: str, context: dict = None) -> str:
    if _is_sensitive_path(path):
        return f"[安全拒绝] 不允许写入敏感路径：{path}"
    try:
        p = Path(path)

        def _do_write() -> None:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

        await asyncio.to_thread(_do_write)
        return f"已写入 {path}（{len(content)} 字符）"
    except Exception as e:
        return f"[写入错误] {e}"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_workspace_tools() -> None:
    """Register all workspace tool handlers and hooks into the global tool_executor."""
    tool_executor.add_before_hook(_permission_check_hook)
    tool_executor.add_before_hook(_default_shell_guard)
    # Redactor BEFORE truncator: secrets are masked on the full output before any
    # head/tail truncation, so nothing truncation persists can leak a credential.
    tool_executor.add_after_hook(_default_secret_redactor)
    tool_executor.add_after_hook(_default_output_truncator)
    handlers = {
        "read_file":        _handle_read_file,
        "write_file":       _handle_write_file,
        "edit_file":        _handle_edit_file,
        "list_workspace":   _handle_list_workspace,
        "run_skill":        _handle_run_skill,
        "run_shell":        _handle_run_shell,
        "read_local_file":  _handle_read_local_file,
        "write_local_file": _handle_write_local_file,
        "spawn_agent":      _spawn_agent_handler,
    }
    for tdef in _WORKSPACE_TOOLS:
        tool_executor.register(tdef, handlers[tdef.name])
