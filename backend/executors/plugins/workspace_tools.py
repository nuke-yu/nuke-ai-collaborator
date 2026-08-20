"""
workspace_tools.py — ToolDef declarations, handler implementations, and
registration for the tool_loop_v1 executor's workspace tool set.

Kept separate from the execution loop so tool definitions (stable) don't
intermingle with the AI loop logic (frequently changed).
"""
import asyncio
import fnmatch
import logging
import os
import re
import shlex
import stat as stat_module
import sys
from pathlib import Path

from executors.base import ExecutionContext, ExecutionResult, ToolDef
from core import config
from executors import tool_executor, registry as _executor_registry
import workspace as _ws
from workspace import layout as _layout
import editing
from skills import run_skill
import executors.compact as compact
import permissions
from executors.plugins import win_sandbox
from executors.plugins.shell_backend import (
    ShellExecRequest, ShellExecResult, ShellBackgroundHandle, ShellExecBackend,
)

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

_IS_WINDOWS  = sys.platform == "win32"
_DEFAULT_SHELL = (
    ["powershell.exe", "-NoProfile", "-Command"] if _IS_WINDOWS else ["/bin/sh", "-c"]
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool parameter schemas using Pydantic
# ---------------------------------------------------------------------------
from pydantic import BaseModel, Field
from typing import Optional, List

class ReadFileParams(BaseModel):
    path: str = Field(..., description="相对于工作区根目录的路径")
    offset: Optional[int] = Field(None, description="读取文件的起始字符偏移量")
    limit: Optional[int] = Field(None, description="最大读取字符长度")

class WriteFileParams(BaseModel):
    path: str
    content: str

class SingleEdit(BaseModel):
    old_string: str
    new_string: str

class EditFileParams(BaseModel):
    path: str = Field(..., description="相对于工作区根目录的路径")
    old_string: Optional[str] = Field(None, description="单次替换：要被替换的原文（需与文件内容一致，可含多行）")
    new_string: Optional[str] = Field(None, description="单次替换：替换后的新内容")
    replace_all: bool = Field(False, description="是否替换所有匹配，默认 false")
    edits: Optional[List[SingleEdit]] = Field(None, description="批量替换：多处一次提交，顺序应用、原子（任一未命中则整体不落盘）。与 old_string/new_string 二选一。")

class ReadAnchoredParams(BaseModel):
    path: str = Field(..., description="相对于工作区根目录的路径")

class AnchoredEditItem(BaseModel):
    anchor: str = Field(..., description="read_anchored 给出的锚，如 L12#a3f0c1d")
    op: Optional[str] = Field(None, description="replace（默认）/ delete / insert_after")
    text: Optional[str] = Field(None, description="replace/insert_after 的新文本；delete 可省")

class EditAnchoredParams(BaseModel):
    path: str = Field(..., description="相对于工作区根目录的路径")
    edits: List[AnchoredEditItem] = Field(..., description="锚点编辑列表")

class ListWorkspaceParams(BaseModel):
    pass

class RunSkillParams(BaseModel):
    name: str = Field(..., description="技能文件名（不含扩展名）")
    args: str = Field("", description="运行技能脚本的参数，默认为空字串")

class RunShellParams(BaseModel):
    cmd: str = Field(..., description="要执行的 shell 命令")
    cwd: Optional[str] = Field(None, description="工作目录：默认=群组共享工作区根（相对路径如 workspace/<repo> 落共享区）；私有区用 skills/ 或 logs/ 前缀；也可传绝对路径（须在本群组工作区内）")
    timeout: int = Field(30, description="超时秒数，默认 30")
    background: bool = Field(False, description="后台运行，立即返回 PID")

class ReadLocalFileParams(BaseModel):
    path: str = Field(..., description="文件的绝对路径")

class WriteLocalFileParams(BaseModel):
    path: str = Field(..., description="文件的绝对路径")
    content: str

class SpawnAgentParams(BaseModel):
    bot_name: str = Field(..., description="目标 Bot 的名称")
    task: str = Field(..., description="委托给子 Agent 的具体任务描述")
    background: bool = Field(False, description="后台运行，立即返回不等待结果")

class SignalStageDoneParams(BaseModel):
    reason: str = Field(..., description="完成当前阶段工作的简短理由、最终结论或交付物说明")

class SignalReworkParams(BaseModel):
    target_stage: str = Field(..., description="需要返工回到的目标阶段名称或角色（如 'Dev'、'QA' 等）")
    reason: str = Field(..., description="需要返工的理由、Bug 报告或测试未通过的说明")
    rework_to_idx: Optional[int] = Field(None, description="（可选）需要返工回到的目标阶段的 0-based 索引")


_WORKSPACE_TOOLS = [
    ToolDef(
        name="read_file",
        description="读取 Bot 工作区内的文件内容",
        parameters=ReadFileParams,
        concurrency_safe=True,
    ),
    ToolDef(
        name="write_file",
        description="仅用于新建文件或整文件重写；改已有文件请用 edit_file（只发 diff，避免大文件被输出长度截断）。",
        parameters=WriteFileParams,
    ),
    ToolDef(
        name="edit_file",
        description=(
            "对工作区已有文件做精确字符串替换（只发改动片段，不必重发整文件）。"
            "把 old_string 替换为 new_string；old_string 必须在文件中唯一"
            "（否则报错，请加更多上下文或用 replace_all）。修改已有文件首选本工具。"
            "一次改多处可用 edits 数组（顺序应用、原子、一次提交）。"
        ),
        parameters=EditFileParams,
    ),
    ToolDef(
        name="read_anchored",
        description=(
            "读取文件并给每行打行哈希锚（L<行号>#<hash>）。配合 edit_anchored 按锚精准改单行/"
            "少数行——锚用内容哈希定位，行位移也有效。大文件里改个别行优于重抄整段 old_string。"
        ),
        parameters=ReadAnchoredParams,
        concurrency_safe=True,
    ),
    ToolDef(
        name="edit_anchored",
        description=(
            "按行哈希锚编辑文件（先用 read_anchored 取锚）。edits 顺序应用、原子（任一锚失效/"
            "冲突则整体不落盘）。每项 {anchor, op, text}，op ∈ replace/delete/insert_after。"
        ),
        parameters=EditAnchoredParams,
    ),
    ToolDef(
        name="list_workspace",
        description="列出 Bot 工作区的目录结构",
        parameters=ListWorkspaceParams,
        concurrency_safe=True,
    ),
    ToolDef(
        name="run_skill",
        description="执行 skills/ 目录中的技能脚本",
        parameters=RunSkillParams,
    ),
    ToolDef(
        name="run_shell",
        description="在本地执行 shell 命令，返回 stdout / stderr / exit_code",
        parameters=RunShellParams,
    ),
    ToolDef(
        name="read_local_file",
        description="按绝对路径读取当前 Bot 私有区、群组共享区或已调用技能的附件文件",
        parameters=ReadLocalFileParams,
        concurrency_safe=True,
    ),
    ToolDef(
        name="write_local_file",
        description="按绝对路径写入当前 Bot 私有区或群组共享区（自动创建父目录）",
        parameters=WriteLocalFileParams,
    ),
    ToolDef(
        name="spawn_agent",
        description="派生子 Agent：将子任务委托给另一个 Bot 执行。background=true 时立即返回，子 Agent 在后台运行，完成后结果自动注回当前对话",
        parameters=SpawnAgentParams,
    ),
    ToolDef(
        name="signal_stage_done",
        description="当完成当前阶段的任务时，调用此工具以通知系统阶段已完成，并触发进入下一阶段（门）。",
        parameters=SignalStageDoneParams,
        concurrency_safe=True,
    ),
    ToolDef(
        name="signal_rework",
        description="当发现上游阶段的问题需要打回重做（返工）时，调用此工具以将工作流回退到指定阶段。",
        parameters=SignalReworkParams,
        concurrency_safe=True,
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


async def _handle_signal_stage_done(reason: str = "", context: dict = None) -> str:
    ctx = context or {}
    runner = ctx.get("runner")
    if runner:
        require_pr = bool(
            (runner.bot.get("executor_config") or {}).get(
                "require_pull_request_completion"
            )
        )
        if require_pr:
            has_pr = any(
                rec.get("name") == "create_pr" and not rec.get("is_error")
                for rec in runner.tool_records
            )
            if not has_pr:
                return (
                    "[错误] 在调用 signal_stage_done 之前，必须先成功调用 create_pr 创建 Pull Request。"
                    "请先调用 create_pr，确认成功后再调用 signal_stage_done。"
                )
    return f"[系统] 已记录阶段完成信号。原因: {reason}。正在推进工作流..."


async def _handle_signal_rework(target_stage: str = "", reason: str = "", context: dict = None) -> str:
    return f"[系统] 已记录返工信号。目标阶段: {target_stage}，原因: {reason}。工作流即将打回..."


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


# --- destructive git detection: route to HIL approval, NOT hard-block ------
# These git invocations destroy state git itself cannot recover — untracked /
# uncommitted working-tree content (`reset --hard`, `clean -f`, `checkout .`) —
# or rewrite shared remote history (`push --force`), or close git's own recovery
# window (`gc --prune=now`, `reflog expire`). Workspace-path confinement does NOT
# bound their blast radius: the lost content was never in git's object store, and
# a force-push escapes the local repo entirely. So unlike _DANGEROUS_PATTERNS
# (hard-blocked), these are legitimate in context and are routed to human
# approval (HIL) — see _permission_check_hook / engine.check(force_ask=...).
#
# Recoverable git ops are intentionally absent: plain `git rm` of a committed
# file, `commit`, ordinary `reset` (soft/mixed), `branch -d` of a merged branch.
# git keeps their objects, so a human can recover them — gating these would only
# breed prompt-fatigue that trains humans to rubber-stamp.

# git's own global options that take a separate value, skipped to reach the real
# subcommand: `git -C <dir> reset --hard`, `git -c user.x=y push --force`.
_GIT_GLOBAL_OPTS_WITH_VALUE = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
})


def _git_subcommand(rest: list[str]) -> tuple[str | None, list[str]]:
    """From the args following `git`, skip global options and return
    (subcommand, its_remaining_args). (None, []) if no subcommand is present."""
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok in _GIT_GLOBAL_OPTS_WITH_VALUE:
            i += 2
            continue
        if tok.startswith("-"):          # other global flag, e.g. --no-pager
            i += 1
            continue
        return tok, rest[i + 1:]
    return None, []


def _destructive_git_reason(rest: list[str]) -> str | None:
    """Reason string if the git args (after `git`) are destructive, else None."""
    sub, args = _git_subcommand(rest)
    if sub is None:
        return None
    aset = set(args)

    def has(*flags: str) -> bool:
        return any(f in aset for f in flags)

    if sub == "reset" and has("--hard"):
        return "git reset --hard 会丢弃已跟踪文件的未提交改动（不可恢复）"
    if sub == "clean" and (has("--force") or any(
            a.startswith("-") and not a.startswith("--") and "f" in a for a in args)):
        return "git clean -f 会删除未跟踪文件（git 从未存过，不可恢复）"
    if sub == "checkout" and (has("-f", "--force") or "." in args or "--" in args):
        return "git checkout 会丢弃工作树未提交改动"
    if sub == "restore" and "--staged" not in aset and (
            has("-f", "--force") or "." in args):
        return "git restore 会丢弃工作树未提交改动"
    if sub == "push" and (
            has("--force", "-f", "--force-with-lease", "--mirror", "--delete", "-d")
            or any(a.startswith("+") for a in args)):
        return "git push --force 会重写远端历史（影响每个克隆）"
    if sub == "gc" and any(
            a.startswith("--prune=") and a != "--prune=never" for a in args):
        return "git gc --prune 立即回收悬空对象，关闭恢复窗口"
    if sub == "reflog" and "expire" in args:
        return "git reflog expire 清空 reflog，关闭恢复窗口"
    if sub == "branch" and (has("-D") or (has("--delete") and has("--force"))):
        return "git branch -D 强制删除分支"
    if sub == "stash" and ("clear" in args or "drop" in args):
        return "git stash clear/drop 丢弃暂存内容"
    if sub == "filter-branch":
        return "git filter-branch 重写历史"
    if sub == "update-ref" and has("-d", "--delete"):
        return "git update-ref -d 删除引用"
    return None


def _is_destructive_git(cmd: str) -> tuple[bool, str]:
    """(True, reason) if cmd contains a git invocation that destroys
    unrecoverable state or rewrites remote history; else (False, "").

    Reuses the same shlex tokenization as the danger guard, so wrappers
    (`sudo git …`), env-assignments, command chains (`cd repo && git …`) and
    `bash -c "git …"` payloads resolve to the real git invocation."""
    commands = _iter_simple_commands(cmd)
    if commands is None:
        return False, ""
    for argv in commands:
        base, rest = _resolved_argv0(argv)
        if base is None:
            continue
        if base in _SHELL_INTERPRETERS:
            for j, a in enumerate(rest):
                if a == "-c" and j + 1 < len(rest):
                    inner, reason = _is_destructive_git(rest[j + 1])
                    if inner:
                        return True, reason
        if base == "git":
            reason = _destructive_git_reason(rest)
            if reason:
                return True, reason
    return False, ""


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


def _resolve_shell_cwd(cwd: str, bot_id, group_id: int | None = None) -> tuple[Path | None, str]:
    """Confine the shell working directory to the bot's workspace.

    放行两个根：本群组共享区 group_{gid}/shared（Dev/QA 在 shared/workspace/<repo> 共享工作树上
    build/跑测/git），以及 bot 私有区 group_{gid}/bots/bot_{id}。

    落点与 VFS 路由一致「共享优先」：空 cwd / 普通相对 cwd → 共享区根（这样 `mkdir -p workspace/pacman`
    不带 cwd 也落 shared/workspace/pacman，不再静默进私有）。私有命名空间前缀（skills/ logs/）→ 私有区。
    无群组上下文（group_id=None）→ 回落私有根。任何越出这两个根的目标（绝对路径越界 / '..' 穿越）拒绝。
    """
    if bot_id is None:
        return None, "缺少 bot_id，无法确定工作区"
    private_root = _ws.bot_workspace(bot_id, group_id).resolve()
    shared_root = _ws.group_workspace(group_id).resolve() if group_id is not None else None
    default_root = shared_root if shared_root is not None else private_root

    candidate = (cwd or "").strip()
    if not candidate:
        return default_root, ""

    p = Path(candidate)
    if p.is_absolute():
        target = p
    else:
        first = candidate.replace("\\", "/").split("/", 1)[0] + "/"
        base = private_root if first in _ws._PRIVATE_PREFIXES else default_root
        target = base / p

    try:
        target = target.resolve()
        for root in (private_root, shared_root):
            if root is not None and target.is_relative_to(root):
                return target, ""
    except (OSError, ValueError):
        pass
    return None, f"工作目录越界，必须位于本群组工作区内：{cwd}"


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
    "create_jira_ticket", "list_jira_tickets", "update_jira_ticket", "create_pr",
})

# These tools cannot mutate the workspace or escape it. Treat them as confined
# so explicit deny rules still win in permissions.check(), while the default
# policy does not suspend an autonomous run waiting for a meaningless approval.
_READ_ONLY_CONFINED_TOOLS = frozenset({
    "list_workspace", "read_file", "read_anchored", "memory_search",
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

    # run_shell confined to the group workspace is auto-allowed: the sandbox
    # (_resolve_shell_cwd) already enforces path boundaries, and the danger guard
    # (_default_shell_guard) blocks destructive patterns.  Deny rules still win.
    #
    # EXCEPTION — destructive git (force_ask): path confinement does NOT bound the
    # blast radius of `reset --hard` / `clean -f` / `push --force` (they destroy
    # content git never stored, or rewrite the shared remote). Those skip both the
    # confined auto-allow and any scoped/blanket allow rule, and always reach human
    # approval — deny rules still win, sub-agents still can't prompt (→ denied).
    workspace_confined = name in _READ_ONLY_CONFINED_TOOLS
    force_ask = False
    if name == "run_shell":
        cwd = (arguments.get("cwd") or "").strip()
        _, err = _resolve_shell_cwd(cwd, context.get("bot_id"), context.get("group_id"))
        workspace_confined = (err is None)
        force_ask, _ = _is_destructive_git((arguments.get("cmd") or "").strip())

    result = await permissions.check(
        tool_name=name,
        arguments=arguments,
        ruleset=ruleset,
        bot_id=context.get("bot_id"),
        broadcaster=context.get("broadcaster"),
        group_id=context.get("group_id"),
        spawn_depth=context.get("spawn_depth", 0),
        workspace_confined=workspace_confined,
        force_ask=force_ask,
        event_recorder=context.get("permission_event_recorder"),
    )

    if result["action"] == "deny":
        return {"block": True, "reason": result.get("reason", "权限拒绝")}

    # Note: persisting an "always" rule is handled in ONE place — the worker's
    # PERMISSION_RESPONSE handler (runtime/worker.py), the universal path for every
    # tool incl MCP, which synthesizes a scoped args_pattern (#5). Saving here too
    # would double-write (and previously wrote a blanket rule that defeated the
    # scoped one).

    # Hot-patch the in-memory ruleset so the same rule takes effect immediately
    # within this session — without this, the DB write is async and the next
    # identical call in the same tool loop still falls through to "ask".
    persist_rule = result.get("persist_rule")
    if persist_rule is not None:
        ruleset.rules.append(persist_rule)

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
    ctx = context or {}
    bot_id = ctx.get("bot_id")
    if not bot_id:
        return "[错误] 缺少 bot_id"
    options = {"offset": offset, "limit": limit, "group_id": ctx.get("group_id")}
    if ctx.get("session_id"):
        options["session_id"] = ctx["session_id"]
    return await _ws.read_file(bot_id, path, **options)


async def _handle_write_file(path: str, content: str, context: dict = None) -> str:
    ctx = context or {}
    bot_id = ctx.get("bot_id")
    if not bot_id:
        return "[错误] 缺少 bot_id"
    options = {"group_id": ctx.get("group_id")}
    if ctx.get("session_id"):
        options["session_id"] = ctx["session_id"]
    res = await _ws.write_file(bot_id, path, content, **options)
    if "[已写入" in res or "成功" in res or not res.startswith("["):
        try:
            from artifacts import register_artifact, ArtifactOrigin, calculate_checksum
            group_id = ctx.get("group_id")
            if group_id:
                content_bytes = content.encode("utf-8")
                checksum = calculate_checksum(content_bytes)
                await register_artifact(
                    group_id=group_id,
                    display_name=os.path.basename(path),
                    origin=ArtifactOrigin.WORKSPACE,
                    storage_locator=path,
                    size_bytes=len(content_bytes),
                    checksum_sha256=checksum,
                    bot_id=bot_id,
                    session_id=ctx.get("session_id"),
                )
        except Exception as e:
            log.warning("Failed to auto-register artifact for write_file %s: %s", path, e)
    return res


async def _handle_edit_file(path: str, old_string: str = None, new_string: str = None,
                            replace_all: bool = False, context: dict = None,
                            edits: list = None, **kwargs) -> str:
    ctx = context or {}
    bot_id = ctx.get("bot_id")
    if not bot_id:
        return "[错误] 缺少 bot_id"
    if edits is None and (old_string is None or new_string is None):
        return "[参数错误] 需提供 old_string+new_string（单次替换），或 edits 数组（批量替换）"
    options = {"replace_all": replace_all, "group_id": ctx.get("group_id"), "edits": edits}
    if ctx.get("session_id"):
        options["session_id"] = ctx["session_id"]
    return await _ws.edit_file(bot_id, path, old_string, new_string, **options)


async def _handle_read_anchored(path: str, context: dict = None, **kwargs) -> str:
    bot_id = (context or {}).get("bot_id")
    if not bot_id:
        return "[错误] 缺少 bot_id"
    ctx = context or {}
    options = {"group_id": ctx.get("group_id")}
    if ctx.get("session_id"):
        options["session_id"] = ctx["session_id"]
    return await _ws.read_anchored(bot_id, path, **options)


async def _handle_edit_anchored(path: str, edits: list = None, context: dict = None, **kwargs) -> str:
    ctx = context or {}
    bot_id = ctx.get("bot_id")
    if not bot_id:
        return "[错误] 缺少 bot_id"
    if not edits:
        return "[参数错误] 需提供 edits 数组（每项 {anchor, op, text}）"
    options = {"group_id": ctx.get("group_id")}
    if ctx.get("session_id"):
        options["session_id"] = ctx["session_id"]
    return await _ws.edit_anchored(bot_id, path, edits, **options)


async def _handle_list_workspace(context: dict = None) -> str:
    ctx = context or {}
    bot_id = ctx.get("bot_id")
    return await _ws.list_workspace(bot_id, group_id=ctx.get("group_id")) if bot_id else "[错误] 缺少 bot_id"


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
            log.exception("workspace_tools: failed to validate shell path candidate %s", match)

    # 2. Check direct home directory string references in arguments
    if home_dir_str in cmd:
        for word in re.split(r'[\s\'\"<>\|;&]+', cmd):
            if home_dir_str in word:
                try:
                    resolved = Path(word).expanduser().resolve()
                    if not resolved.is_relative_to(work_dir.resolve()):
                        return f"工作区沙箱限制：禁止读写工作区外的路径「{word}」"
                except Exception:
                    log.exception("workspace_tools: failed to validate shell home-path candidate %s", word)
    return None


# ---------------------------------------------------------------------------
# 共享工作树承重墙：per-group 进程内互斥（固定分片 → 同群组的并发 run_shell 同进程）
# ---------------------------------------------------------------------------
import threading as _threading
from contextlib import asynccontextmanager as _asynccontextmanager

_WORKTREE_LOCKS: dict = {}            # loop_id -> {group_id: asyncio.Lock}
_WORKTREE_LOCKS_GUARD = _threading.Lock()


def _get_worktree_lock(group_id: int) -> "asyncio.Lock":
    """按 (event loop, group_id) 取/建进程内互斥锁。类比 workspace._get_path_lock。"""
    loop_id = id(asyncio.get_running_loop())
    with _WORKTREE_LOCKS_GUARD:
        per_loop = _WORKTREE_LOCKS.setdefault(loop_id, {})
        if group_id not in per_loop:
            per_loop[group_id] = asyncio.Lock()
        return per_loop[group_id]


def _worktree_lock_for(work_dir: Path, group_id) -> "asyncio.Lock | None":
    """work_dir 落在本群组 shared/workspace（git 工作树）下时返回该群组的锁，否则 None。"""
    if group_id is None:
        return None
    try:
        wt_root = (_ws.group_workspace(group_id).resolve() / "workspace")
        if work_dir.resolve().is_relative_to(wt_root):
            return _get_worktree_lock(group_id)
    except (OSError, ValueError):
        log.exception("workspace_tools: failed to resolve worktree lock for %s", work_dir)
    return None


@_asynccontextmanager
async def _maybe_lock(lock):
    """lock 为 None 时无操作；否则进入临界区。"""
    if lock is None:
        yield
    else:
        async with lock:
            yield


# ---------------------------------------------------------------------------
# run_shell execution backends — see executors/plugins/shell_backend.py.
# The orchestrator (_handle_run_shell) builds a ShellExecRequest and dispatches
# to the selected backend; isolation strength = which backend is selected.
# ---------------------------------------------------------------------------

def _safe_kill(proc) -> None:
    try:
        proc.kill()
    except Exception:
        log.exception("workspace_tools: failed to kill timed-out process")


class LocalShellBackend:
    """Host subprocess — current behavior, moved verbatim. NO cross-group
    isolation; the mem-limit ulimit wrap (a local-only mechanism) lives here."""

    async def ensure_ready(self, group_id) -> None:
        return

    async def healthy(self) -> bool:
        return True

    async def run_foreground(self, req: ShellExecRequest) -> ShellExecResult:
        safe_cmd = _wrap_command_with_limits(req.cmd, req.mem_limit_bytes)
        proc = await asyncio.create_subprocess_exec(
            *_DEFAULT_SHELL, safe_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(req.work_dir),
            env=req.env,
        )
        if _IS_WINDOWS:
            win_sandbox.apply_memory_limit(proc.pid, req.mem_limit_bytes)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=req.timeout_s)
        except asyncio.TimeoutError:
            _safe_kill(proc)
            return ShellExecResult(None, "", "", timed_out=True)
        except asyncio.CancelledError:
            _safe_kill(proc)
            raise
        return ShellExecResult(
            proc.returncode,
            stdout.decode(errors="replace").strip(),
            stderr.decode(errors="replace").strip(),
        )

    async def start_background(self, req: ShellExecRequest) -> ShellBackgroundHandle:
        safe_cmd = _wrap_command_with_limits(req.cmd, req.mem_limit_bytes)
        proc = await asyncio.create_subprocess_exec(
            *_DEFAULT_SHELL, safe_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=str(req.work_dir),
            env=req.env,
            start_new_session=True if not _IS_WINDOWS else False,
        )
        if _IS_WINDOWS:
            win_sandbox.apply_memory_limit(proc.pid, req.mem_limit_bytes)
        return ShellBackgroundHandle(identifier=str(proc.pid))


class ContainerShellBackend:
    """per-group sandbox container (bind-mounts ONLY that group's workspace) —
    group isolation as a mount fact. Delegates lifecycle/exec to ContainerManager
    (see container_sandbox.py); memory is enforced by the container cgroup, so the
    local ulimit wrap is NOT applied here."""

    def __init__(self, manager=None):
        from executors.plugins import container_sandbox
        self._mgr = manager or container_sandbox.ContainerManager()

    async def ensure_ready(self, group_id) -> None:
        if group_id is None:
            raise RuntimeError("container backend 需要 group_id（无群上下文无法隔离）")
        await self._mgr.ensure(group_id, _ws.group_workspace(group_id))

    async def healthy(self) -> bool:
        return await self._mgr.available()

    async def run_foreground(self, req: ShellExecRequest) -> ShellExecResult:
        rc, out, err, timed_out = await self._mgr.exec_foreground(
            req.group_id, cmd=req.cmd, cwd=str(req.work_dir),
            env=req.env, timeout=req.timeout_s,
        )
        return ShellExecResult(rc, out, err, timed_out=timed_out)

    async def start_background(self, req: ShellExecRequest) -> ShellBackgroundHandle:
        ident = await self._mgr.exec_background(
            req.group_id, cmd=req.cmd, cwd=str(req.work_dir), env=req.env,
        )
        return ShellBackgroundHandle(identifier=ident)


_SHELL_BACKEND: ShellExecBackend | None = None


async def get_shell_backend() -> ShellExecBackend:
    """Select (and cache) the run_shell backend per config.SHELL_EXEC_BACKEND.

    'container' is mandatory isolation — if it can't run, run_shell fails closed
    rather than silently falling back to local (group isolation is inviolable).
    'auto' is best-effort for dev: container if healthy, else local."""
    global _SHELL_BACKEND
    if _SHELL_BACKEND is not None:
        return _SHELL_BACKEND
    mode = getattr(config, "SHELL_EXEC_BACKEND", "local")
    if mode == "container":
        _SHELL_BACKEND = ContainerShellBackend()
    elif mode == "auto":
        candidate = ContainerShellBackend()
        _SHELL_BACKEND = candidate if await candidate.healthy() else LocalShellBackend()
    else:
        _SHELL_BACKEND = LocalShellBackend()
    return _SHELL_BACKEND


def set_shell_backend_for_test(backend: ShellExecBackend | None) -> None:
    """Override / reset the cached backend (tests only)."""
    global _SHELL_BACKEND
    _SHELL_BACKEND = backend


async def _handle_run_shell(
    cmd: str, cwd: str = "", timeout: int = 30,
    background: bool = False, context: dict = None,
) -> str:
    ctx = context or {}
    bot_id = ctx.get("bot_id")
    work_dir, err = _resolve_shell_cwd(cwd, bot_id, ctx.get("group_id"))
    if err:
        return f"[安全拒绝] {err}"
    
    restricted_err = _check_shell_command_paths(cmd, work_dir)
    if restricted_err:
        return f"[安全拒绝] {restricted_err}"
    
    timeout_s = min(timeout, 300)
    sandbox_env = _sandbox_env()

    cmd, intercepted_port, allocated_port = _intercept_command_ports(cmd, sandbox_env)
    req = ShellExecRequest(
        cmd=cmd, work_dir=work_dir, env=sandbox_env,
        group_id=ctx.get("group_id"), bot_id=bot_id,
        mem_limit_bytes=config.SHELL_MEMORY_LIMIT_BYTES, timeout_s=timeout_s,
    )

    backend = await get_shell_backend()
    try:
        await backend.ensure_ready(ctx.get("group_id"))

        if background:
            handle = await backend.start_background(req)
            msg = f"已在后台启动（PID: {handle.identifier}），命令：{cmd}"
            if allocated_port:
                msg += f"\n[端口分配] 系统已自动分配可用端口: {allocated_port} (注入为环境变量 PORT / APP_PORT)"
            return msg

        # 前台执行：若 cwd 落在本群组共享工作树，串行化以防并发撞 .git/index。
        # 后台进程是长驻服务（不持锁），故仅前台分支加锁。
        async with _maybe_lock(_worktree_lock_for(work_dir, ctx.get("group_id"))):
            result = await backend.run_foreground(req)

        if result.timed_out:
            return f"[安全拦截] 命令执行超时（超过 {timeout_s} 秒已被强行终止）"

        parts = []
        if intercepted_port:
            parts.append(f"[安全拦截] 已将硬编码端口 {intercepted_port} 替换为动态端口 {allocated_port}")
        parts.append(f"exit_code: {result.exit_code}")
        if result.stdout:
            parts.append(f"stdout:\n{result.stdout}")
        if result.stderr:
            parts.append(f"stderr:\n{result.stderr}")
        return "\n".join(parts)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        return f"[系统错误] {e}"


def _local_file_roots(context: dict, *, allow_write: bool) -> list[Path]:
    """Return identity-scoped roots; invoked skill roots are read-only."""
    group_id = context["group_id"]
    bot_id = context["bot_id"]
    roots = [
        _layout.bot_dir(group_id, bot_id),
        _layout.group_shared_dir(group_id),
    ]
    if not allow_write:
        roots.extend(Path(p) for p in context.get("authorized_read_roots", ()))
    return roots


def _lexical_relative_to_root(path: str, roots: list[Path]) -> tuple[Path, tuple[str, ...]] | None:
    """Select an allowed root without resolving any user-controlled component."""
    requested = Path(os.path.abspath(os.path.expanduser(path)))
    matches: list[tuple[Path, tuple[str, ...]]] = []
    for root in roots:
        declared_root = Path(os.path.abspath(str(root.expanduser())))
        try:
            relative = requested.relative_to(declared_root)
        except ValueError:
            continue
        parts = tuple(part for part in relative.parts if part not in ("", "."))
        if any(part == ".." for part in parts):
            continue
        matches.append((root, parts))
    return max(matches, key=lambda item: len(item[0].parts), default=None)


def _validate_path_in_group_workspace(
    path: str, context: dict | None, *, allow_write: bool = False,
) -> tuple[tuple[Path, tuple[str, ...]] | None, str | None]:
    """Validate lexical scope; secure open performs the authoritative check."""
    ctx = context or {}
    group_id = ctx.get("group_id")
    bot_id = ctx.get("bot_id")
    if group_id is None:
        return None, "[安全拒绝] 无法确定群组上下文，拒绝文件访问"
    if bot_id is None:
        return None, "[安全拒绝] 无法确定 bot_id，拒绝文件访问"
    selected = _lexical_relative_to_root(path, _local_file_roots(ctx, allow_write=allow_write))
    if selected is None:
        action = "写入" if allow_write else "读取"
        return None, f"[安全拒绝] {action}路径不在当前 bot 的授权目录内：{path}"
    if not selected[1]:
        return None, f"[安全拒绝] 文件路径不能是目录根：{path}"
    return selected, None


def _secure_open_root(root: Path) -> int:
    """Open an allowed root beneath WORKSPACE_ROOT without following its components."""
    if (not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY")
            or os.open not in os.supports_dir_fd or os.mkdir not in os.supports_dir_fd):
        raise OSError("当前平台不支持安全的 dir_fd/O_NOFOLLOW 文件访问")
    declared_base = Path(os.path.abspath(str(_layout._root().expanduser())))
    declared_root = Path(os.path.abspath(str(root.expanduser())))
    try:
        relative_root = declared_root.relative_to(declared_base)
    except ValueError as exc:
        raise OSError("授权目录不在工作区根目录内") from exc

    # WORKSPACE_ROOT is operator configuration, so resolving that anchor is safe;
    # every group/bot/skill component below it is opened with O_NOFOLLOW.
    canonical = declared_base.resolve(strict=True)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(canonical.anchor, flags)
    try:
        for part in canonical.parts[1:]:
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        for part in relative_root.parts:
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except Exception:
        os.close(fd)
        raise


def _secure_open_parent(root: Path, parts: tuple[str, ...], *, create: bool) -> tuple[int, str]:
    """Return a held fd for the target parent, traversing each directory safely."""
    fd = _secure_open_root(root)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, 0o755, dir_fd=fd)
                except FileExistsError:
                    pass
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd, parts[-1]
    except Exception:
        os.close(fd)
        raise


def _read_local_file_sync(spec: tuple[Path, tuple[str, ...]]) -> str:
    root, parts = spec
    parent_fd, name = _secure_open_parent(root, parts, create=False)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    with open(fd, "r", encoding="utf-8", closefd=True) as stream:
        file_stat = os.fstat(stream.fileno())
        if not stat_module.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
            raise OSError("拒绝读取非普通文件或硬链接文件")
        return stream.read()


def _write_local_file_sync(spec: tuple[Path, tuple[str, ...]], content: str) -> None:
    root, parts = spec
    parent_fd, name = _secure_open_parent(root, parts, create=True)
    fd = None
    try:
        try:
            fd = os.open(name, os.O_WRONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        except FileNotFoundError:
            fd = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o644, dir_fd=parent_fd,
            )
        file_stat = os.fstat(fd)
        if not stat_module.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
            raise OSError("拒绝写入非普通文件或硬链接文件")
        os.ftruncate(fd, 0)
        with open(fd, "w", encoding="utf-8", closefd=True) as stream:
            fd = None
            stream.write(content)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent_fd)


async def _handle_read_local_file(path: str, context: dict = None) -> str:
    spec, err = _validate_path_in_group_workspace(path, context)
    if err:
        return err
    try:
        return await asyncio.to_thread(_read_local_file_sync, spec)
    except FileNotFoundError:
        return f"[文件不存在] {path}"
    except (NotADirectoryError, OSError) as e:
        return f"[安全拒绝] 无法安全读取路径：{e}"
    except Exception as e:
        return f"[读取错误] {e}"


async def _handle_write_local_file(path: str, content: str, context: dict = None) -> str:
    spec, err = _validate_path_in_group_workspace(path, context, allow_write=True)
    if err:
        return err
    try:
        await asyncio.to_thread(_write_local_file_sync, spec, content)
        return f"已写入 {path}（{len(content)} 字符）"
    except (FileExistsError, NotADirectoryError, OSError) as e:
        return f"[安全拒绝] 无法安全写入路径：{e}"
    except Exception as e:
        return f"[写入错误] {e}"


async def _handle_mcp_authenticate(server: str, context: dict = None) -> str:
    """Start OAuth for a remote MCP server (McpAuthTool style): returns an
    authorization URL for the user to open; tools load once they authorize."""
    from executors.mcp_bridge import bridge
    ctx = context or {}
    result, _is_error = await bridge.authenticate(
        server, group_id=ctx.get("group_id"), trace_id=ctx.get("trace_id"))
    return result


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
        "read_anchored":    _handle_read_anchored,
        "edit_anchored":    _handle_edit_anchored,
        "list_workspace":   _handle_list_workspace,
        "run_skill":        _handle_run_skill,
        "run_shell":        _handle_run_shell,
        "read_local_file":  _handle_read_local_file,
        "write_local_file": _handle_write_local_file,
        "spawn_agent":      _spawn_agent_handler,
        "signal_stage_done": _handle_signal_stage_done,
        "signal_rework":     _handle_signal_rework,
    }
    for tdef in _WORKSPACE_TOOLS:
        tool_executor.register(tdef, handlers[tdef.name])

    # search (ripgrep) / code_intel (jedi) — lazy imports break the cycle (both
    # import our _resolve_shell_cwd / _sandbox_env at their module top).
    from executors.plugins import search_tool
    tool_executor.register(search_tool.SEARCH_TOOL_DEF, search_tool._handle_search)
    from executors.plugins import code_intel_tool
    tool_executor.register(code_intel_tool.CODE_INTEL_TOOL_DEF, code_intel_tool._handle_code_intel)

    # L3 — 3-layer tool-memory retrieval (search → timeline → fetch over the L1
    # tool_events log). Builtin so they stay on the hooked tool_executor path.
    from executors.plugins import memory_search_tool as _mem
    tool_executor.register(_mem.SEARCH_MEMORY_TOOL_DEF, _mem._handle_search_memory)
    tool_executor.register(_mem.MEMORY_TIMELINE_TOOL_DEF, _mem._handle_memory_timeline)
    tool_executor.register(_mem.MEMORY_FETCH_TOOL_DEF, _mem._handle_memory_fetch)

    # MCP OAuth trigger (McpAuthTool style). Builtin so it stays on the hooked
    # tool_executor path; bots that should authenticate MCP servers must include
    # "mcp_authenticate" in their allowed_tools to have it surfaced to the LLM.
    class McpAuthenticateParams(BaseModel):
        server: str = Field(..., description="mcp_servers.json 中的 server 名")

    from executors.base import ToolDef as _ToolDef
    tool_executor.register(
        _ToolDef(
            name="mcp_authenticate",
            description="为需要 OAuth 授权的 remote MCP server 发起授权，返回授权链接交给用户在浏览器打开",
            parameters=McpAuthenticateParams,
        ),
        _handle_mcp_authenticate,
    )
