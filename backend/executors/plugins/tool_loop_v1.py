import asyncio
import uuid
import os
import sys
from pathlib import Path

_IS_WINDOWS = sys.platform == "win32"
_DEFAULT_SHELL = ["powershell.exe", "-NoProfile", "-Command"] if _IS_WINDOWS else ["/bin/sh", "-c"]

from executors.base import (
    BotExecutor, ExecutionContext, ExecutionResult,
    PluginManifest, ToolDef, WorkspaceConfig, CollabConfig, build_group_section,
)
from executors import tool_executor, registry as _executor_registry
from database import get_db, save_message, get_messages
from ai_client import call_ai_once, call_ai_stream_messages, AIError, AIContextOverflowError
from memory import get_memory_context, add_to_chroma, maybe_summarize
from role_router import build_context_message
from workspace import load_context_files, format_context_blocks, append_log, archive_run
from skills import list_skills, load_always_skills, list_skills_all, run_skill, filter_skills_by_context

_WORKSPACE_TOOLS = [
    ToolDef(
        name="read_file",
        description="读取 Bot 工作区内的文件内容",
        parameters={"type": "object",
                    "properties": {"path": {"type": "string", "description": "相对于工作区根目录的路径"}},
                    "required": ["path"]},
    ),
    ToolDef(
        name="write_file",
        description="向工作区文件写入内容（会覆盖）",
        parameters={"type": "object",
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"]},
    ),
    ToolDef(
        name="list_workspace",
        description="列出 Bot 工作区的目录结构",
        parameters={"type": "object", "properties": {}},
    ),
    ToolDef(
        name="run_skill",
        description="执行 skills/ 目录中的技能脚本",
        parameters={"type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "技能文件名（不含扩展名）"},
                        "args": {"type": "string", "default": ""},
                    },
                    "required": ["name"]},
    ),
    ToolDef(
        name="run_shell",
        description="在本地执行 shell 命令，返回 stdout / stderr / exit_code",
        parameters={"type": "object",
                    "properties": {
                        "cmd": {"type": "string", "description": "要执行的 shell 命令"},
                        "cwd": {"type": "string", "description": "工作目录（绝对路径），默认为用户 home 目录"},
                        "timeout": {"type": "integer", "description": "超时秒数，默认 30", "default": 30},
                        "background": {"type": "boolean", "description": "后台运行，立即返回 PID，不等待命令完成（适合启动服务）", "default": False},
                    },
                    "required": ["cmd"]},
    ),
    ToolDef(
        name="read_local_file",
        description="读取本地任意路径的文件（工作区外）",
        parameters={"type": "object",
                    "properties": {"path": {"type": "string", "description": "文件的绝对路径"}},
                    "required": ["path"]},
    ),
    ToolDef(
        name="write_local_file",
        description="写入本地任意路径的文件（自动创建父目录）",
        parameters={"type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件的绝对路径"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"]},
    ),
    ToolDef(
        name="spawn_agent",
        description="派生子 Agent：将子任务委托给另一个 Bot 同步执行，等待结果返回后继续",
        parameters={"type": "object",
                    "properties": {
                        "bot_name": {"type": "string", "description": "目标 Bot 的名称"},
                        "task": {"type": "string", "description": "委托给子 Agent 的具体任务描述"},
                    },
                    "required": ["bot_name", "task"]},
    ),
]

_SPAWN_MAX_DEPTH = 3  # Maximum spawn recursion depth


class _NullBroadcaster:
    """Silent broadcaster for sub-agent runs — suppresses all WS events."""
    async def broadcast(self, group_id, message):
        pass


async def _spawn_agent_handler(bot_name: str, task: str, context: dict = None) -> str:
    ctx = context or {}
    group_id = ctx.get("group_id")
    all_bots = ctx.get("all_bots", [])
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
        result = await _executor_registry.get(target.get("executor_id", "simple_v1")).run(sub_ctx)
        return result.full_text or "[子 Agent 未返回内容]"
    except Exception as e:
        return f"[spawn_agent 执行错误] {e}"


# ---------------------------------------------------------------------------
# Dangerous shell command patterns — blocked by default
# ---------------------------------------------------------------------------

_COMPACTION_RATIO = 0.60          # context window 用量超过 60% 触发压缩
_COMPACTION_KEEP_RECENT = 6      # 末尾保留的消息条数不压缩（参考 openclaw/opencode 的 recent turns 设计）
_PRE_RUN_TOKEN_THRESHOLD = 20_000  # 跨 run 预压缩阈值（固定，不随模型窗口缩放）

# 各模型 context window（tokens）— 换模型不用改压缩逻辑
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "deepseek-chat":           64_000,
    "deepseek-reasoner":       64_000,
    "gpt-4o":                 128_000,
    "gpt-4o-mini":            128_000,
    "gpt-4-turbo":            128_000,
    "claude-opus-4-7":        200_000,
    "claude-sonnet-4-6":      200_000,
    "claude-haiku-4-5":       200_000,
}
_DEFAULT_CONTEXT_WINDOW = 64_000  # 未知模型的保守默认值
_SKILL_DESC_MAX_CHARS   = 250     # 单条 skill description 最大长度（参考 Claude Code）


def _build_skills_xml(skills: list[dict], model_name: str) -> tuple[str, set[str]]:
    """Build the lazy-skill XML block with token budget control.

    Budget = max(3000 chars, 1% of model context window in chars).
    Descriptions are capped at _SKILL_DESC_MAX_CHARS.
    Returns (xml_string, set_of_included_skill_names).
    """
    context_window = _MODEL_CONTEXT_WINDOWS.get(model_name, _DEFAULT_CONTEXT_WINDOW)
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


def _estimate_tokens(messages: list) -> int:
    """粗略 token 估算：chars / 4（英文） + 开销；中文稍高但数量级一致。"""
    total = 0
    for m in messages:
        content = m.get("content") or ""
        if isinstance(content, list):
            content = str(content)
        total += len(content) // 4 + 1
    return total


def _compaction_threshold(model_name: str) -> int:
    window = _MODEL_CONTEXT_WINDOWS.get(model_name, _DEFAULT_CONTEXT_WINDOW)
    return int(window * _COMPACTION_RATIO)

_DANGEROUS_PATTERNS = [
    ("rm -rf /",      "禁止删除根目录"),
    ("rm -rf ~",      "禁止删除 home 目录"),
    ("rm -rf $HOME",  "禁止删除 home 目录"),
    (":(){:|:&};:",   "禁止 fork bomb"),
    ("mkfs",          "禁止格式化磁盘"),
    ("dd if=/dev/",   "禁止 dd 写入磁盘"),
    ("> /dev/sda",    "禁止写入磁盘设备"),
    ("chmod -R 777 /","禁止递归修改根目录权限"),
    ("> /etc/passwd", "禁止覆盖系统文件"),
    ("shutdown",      "禁止关机命令"),
    ("reboot",        "禁止重启命令"),
]


async def _compact_messages(
    messages: list, system_prompt: str,
    provider: str, model: str, temperature: float,
) -> list:
    """将旧消息压缩为摘要，保留最近 _COMPACTION_KEEP_RECENT 条不变。
    参考 openclaw preemptive-compaction + opencode overflow.ts 设计。
    """
    keep = _COMPACTION_KEEP_RECENT
    if len(messages) <= keep:
        return messages

    split_idx = len(messages) - keep
    # 如果分割点落在 tool 消息上，向左回溯，将整组 tool 消息及其前置 assistant 消息保留在 recent 区域中
    while split_idx > 0 and messages[split_idx].get("role") == "tool":
        split_idx -= 1

    if split_idx <= 0:
        return messages

    old_msgs = messages[:split_idx]
    recent_msgs = messages[split_idx:]

    lines = []
    for m in old_msgs:
        role = m.get("role", "")
        content = m.get("content") or ""
        if isinstance(content, list):
            content = str(content)
        if role == "tool":
            lines.append(f"[工具结果 {m.get('name', '')}]: {content[:500]}")
        else:
            lines.append(f"[{role}]: {content[:1000]}")
    history_text = "\n".join(lines)

    summarize_prompt = (
        "你是一个对话摘要助手。请将以下对话历史简洁地总结为一段摘要，"
        "保留关键决策、已执行的操作和重要结论。用中文回答，100-200字。"
    )
    try:
        result = await call_ai_once(
            summarize_prompt,
            [{"role": "user", "content": f"请总结以下对话历史：\n\n{history_text}"}],
            provider, model, temperature, 1024,
        )
        summary = result["content"] if result["type"] == "text" else ""
    except Exception:
        summary = f"（{len(old_msgs)} 条历史消息已截断）"

    return [{"role": "user", "content": f"【历史摘要】\n{summary}"}] + recent_msgs


async def _before_finalize_hook(
    draft: str,
    snap_messages: list,
    system_prompt: str,
    config: dict,
    provider: str,
    model_name: str,
    temperature: float,
    max_tokens: int,
    broadcaster,
    group_id: int,
    temp_id: str,
    user_message: str,
) -> str:
    """Quality gate before finalizing a reply.

    Reviewer AI checks the draft; on rejection it injects feedback and the
    bot regenerates.  Repeats up to config['max_retries'] times (default 2).
    Reviewer prompt should start its response with APPROVED or REJECTED: <reason>.
    """
    reviewer_prompt = config.get("reviewer_prompt", "")
    max_retries = int(config.get("max_retries", 2))
    cur_messages = list(snap_messages)
    cur_draft = draft

    for attempt in range(max_retries + 1):
        await broadcaster.broadcast(group_id, {
            "type": "before_finalize_review", "temp_id": temp_id,
            "attempt": attempt + 1, "max_retries": max_retries + 1,
        })

        approved, feedback = True, ""
        try:
            review = await call_ai_once(
                reviewer_prompt,
                [{"role": "user", "content": f"【用户问题】\n{user_message}\n\n【待审查回复】\n{cur_draft}"}],
                provider, model_name, temperature, 512,
            )
            feedback = review["content"] if review["type"] == "text" else ""
            approved = not feedback.strip().upper().startswith("REJECTED")
        except Exception:
            break  # fail open

        if approved or attempt >= max_retries:
            await broadcaster.broadcast(group_id, {
                "type": "before_finalize_approved", "temp_id": temp_id,
                "note": "" if approved else "retry budget exhausted",
            })
            break

        await broadcaster.broadcast(group_id, {
            "type": "before_finalize_rejected", "temp_id": temp_id,
            "feedback": feedback[:300], "attempt": attempt + 1,
        })

        cur_messages = cur_messages + [
            {"role": "assistant", "content": cur_draft},
            {"role": "user", "content": f"[审查意见] {feedback}\n\n请根据以上意见修改回复。"},
        ]
        try:
            regen = await call_ai_once(
                system_prompt, cur_messages, provider, model_name, temperature, max_tokens,
            )
            if regen["type"] == "text":
                cur_draft = regen["content"]
        except Exception:
            break

    return cur_draft


async def _run_fork_skill(
    skill_content: str,
    task: str,
    provider: str,
    model: str,
    temperature: float,
) -> str:
    """Execute a fork skill in an isolated single AI call (no tools).

    skill_content → system prompt; task (args or user_message) → user turn.
    """
    try:
        result = await call_ai_once(
            skill_content,
            [{"role": "user", "content": task or "请执行此技能。"}],
            provider, model, temperature, 4096,
        )
        if result["type"] == "text":
            return result["content"]
        return f"[fork skill 返回了非文本类型: {result['type']}]"
    except Exception as e:
        return f"[fork skill 执行错误] {e}"


_TOOL_RESULT_MAX_CHARS = 2_000   # ~500 tokens per tool result
_TOOL_RESULT_HEAD_TAIL = _TOOL_RESULT_MAX_CHARS // 2  # 1K head + 1K tail


async def _default_output_truncator(
    name: str, arguments: dict, result: str, context: dict
) -> str | None:
    """Truncate long tool results using head+tail strategy to preserve both boundaries."""
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


def _with_personality(base_prompt: str, bot: dict) -> str:
    p = (bot.get("personality_prompt") or "").strip()
    return base_prompt + f"\n\n【性格指令】\n{p}" if p else base_prompt


class ToolLoopV1(BotExecutor):
    executor_id = "tool_loop_v1"
    display_name = "工具调用循环"

    def register_tools(self):
        tool_executor.add_before_hook(_default_shell_guard)
        tool_executor.add_after_hook(_default_output_truncator)
        import workspace as ws_module

        async def _read_file(path: str, context: dict = None) -> str:
            bot_id = (context or {}).get("bot_id")
            return await ws_module.read_file(bot_id, path) if bot_id else "[错误] 缺少 bot_id"

        async def _write_file(path: str, content: str, context: dict = None) -> str:
            bot_id = (context or {}).get("bot_id")
            return await ws_module.write_file(bot_id, path, content) if bot_id else "[错误] 缺少 bot_id"

        async def _list_workspace(context: dict = None) -> str:
            bot_id = (context or {}).get("bot_id")
            return await ws_module.list_workspace(bot_id) if bot_id else "[错误] 缺少 bot_id"

        async def _run_skill(name: str, args: str = "", context: dict = None) -> str:
            bot_id = (context or {}).get("bot_id")
            return await run_skill(bot_id, name, args, ctx=context) if bot_id else "[错误] 缺少 bot_id"

        async def _run_shell(cmd: str, cwd: str = "", timeout: int = 30,
                             background: bool = False, context: dict = None) -> str:
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

        async def _read_local_file(path: str, context: dict = None) -> str:
            try:
                return Path(path).read_text(encoding="utf-8")
            except FileNotFoundError:
                return f"[文件不存在] {path}"
            except Exception as e:
                return f"[读取错误] {e}"

        async def _write_local_file(path: str, content: str, context: dict = None) -> str:
            try:
                p = Path(path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
                return f"已写入 {path}（{len(content)} 字符）"
            except Exception as e:
                return f"[写入错误] {e}"

        handlers = {
            "read_file": _read_file,
            "write_file": _write_file,
            "list_workspace": _list_workspace,
            "run_skill": _run_skill,
            "run_shell": _run_shell,
            "read_local_file": _read_local_file,
            "write_local_file": _write_local_file,
            "spawn_agent": _spawn_agent_handler,
        }
        for tdef in _WORKSPACE_TOOLS:
            tool_executor.register(tdef, handlers[tdef.name])

    manifest = PluginManifest(
        description="多轮工具调用循环：AI 可读写工作区文件、执行技能，直至任务完成",
        tools=_WORKSPACE_TOOLS,
        memory_layers=["short_term", "vector_search", "summary", "permanent"],
        workspace=WorkspaceConfig(
            startup_files=["AGENT.md", "BOOTSTRAP.md", "IDENTITY.md", "MEMORY.md"],
            skill_discovery=True,
            writeback_pattern="logs/{date}.md",
        ),
        collaboration=CollabConfig(can_handoff=True, can_spawn_subagent=True),
        max_iterations=10,
    )

    async def run(self, ctx: ExecutionContext) -> ExecutionResult:
        bot = ctx.bot
        max_iter = (bot.get("executor_config") or {}).get("max_iterations", self.manifest.max_iterations)
        bf_config = (bot.get("executor_config") or {}).get("before_finalize")
        model_name = bot.get("model_name", "deepseek-chat")  # needed early for skill budget

        history, user_msg = build_context_message(ctx.user_message, ctx.sender["name"], ctx.history)
        base = _with_personality(
            bot["system_prompt"] or f"你是{bot['name']}，{bot.get('role', '')}。", bot
        )
        memory = await get_memory_context(bot["id"], bot.get("role") or "", ctx.user_message)

        # Build context prefix injected as user message (not system prompt)
        context_blocks = await load_context_files(
            bot["id"], ctx.group_id, self.manifest.workspace.startup_files
        )
        context_text = format_context_blocks(context_blocks)

        skills_snapshot = []
        always_skills = []
        skills_xml = ""
        if self.manifest.workspace.skill_discovery:
            raw_skills = list_skills(bot["id"])
            lazy_candidates = [
                s for s in raw_skills
                if not s.get("always") and s.get("status", "active") != "disabled"
            ]
            lazy_candidates = filter_skills_by_context(lazy_candidates, ctx.user_message)
            skills_xml, injected_names = _build_skills_xml(lazy_candidates, model_name)
            for s in raw_skills:
                if s.get("status", "active") == "disabled":
                    skills_snapshot.append({**s, "injected": None})
                elif s.get("always"):
                    skills_snapshot.append({**s, "injected": "full"})
                else:
                    inj = "metadata" if s["name"] in injected_names else None
                    skills_snapshot.append({**s, "injected": inj})
            if any(s.get("always") for s in raw_skills if s.get("status", "active") != "disabled"):
                always_skills = load_always_skills(bot["id"])

        context_prefix = ""
        if context_text:
            context_prefix += f"【工作区文件】\n{context_text}\n\n"
        if skills_xml:
            context_prefix += f"{skills_xml}\n使用 run_skill(name=\"技能名\") 调用\n\n"

        always_section = ""
        if always_skills:
            parts = [f"=== {s['name']} ===\n{s['content']}" for s in always_skills]
            always_section = "\n\n【常驻技能 · 始终激活】\n" + "\n\n".join(parts)

        group_section = build_group_section(ctx)
        os_info = f"Windows (PowerShell)" if _IS_WINDOWS else f"{sys.platform} (shell: /bin/sh)"
        system_prompt = (
            base
            + (f"\n\n{memory}" if memory else "")
            + (f"\n\n【群组信息】\n{group_section}" if group_section else "")
            + always_section
            + f"\n\n【运行环境】\nOS: {os_info}\n路径分隔符: {'\\\\' if _IS_WINDOWS else '/'}\n使用 run_shell 执行命令时请使用适合当前 OS 的语法。"
            + "\n\n【自学技能规则】\n当你发现可复用的规律或用户说「记住这个做法」时，用 write_file 将技能写入 `skills/learned/draft/<skill-name>.md`，系统会自动请求用户审批。禁止直接写入 `skills/learned/active/`。"
            + ctx.workflow_suffix
        )

        # Inject workspace context as prefix of the first user message
        prefixed_user_msg = (context_prefix + user_msg) if context_prefix else user_msg
        messages = list(history) + [{"role": "user", "content": prefixed_user_msg}]
        tool_names = [t.name for t in self.manifest.tools]
        tool_schemas = tool_executor.get_schemas(tool_names)

        temp_id = str(uuid.uuid4())
        await ctx.broadcaster.broadcast(ctx.group_id, {
            "type": "stream_start", "temp_id": temp_id,
            "member_id": bot["id"], "sender_name": bot["name"],
            "sender_type": "bot", "avatar_color": bot["avatar_color"],
        })
        if skills_snapshot:
            await ctx.broadcaster.broadcast(ctx.group_id, {
                "type": "skills_loaded", "temp_id": temp_id,
                "member_id": bot["id"], "skills": skills_snapshot,
            })

        provider = bot.get("model_provider", "deepseek")
        temperature = bot.get("temperature", 0.7)
        max_tokens = bot.get("max_tokens", 4096)
        full_text = ""

        # P1 #3: Cross-run pre-run compaction — compact DB-loaded history if already too long
        _pre_tokens = _estimate_tokens(messages)
        if _pre_tokens > _PRE_RUN_TOKEN_THRESHOLD:
            messages = await _compact_messages(messages, system_prompt, provider, model_name, temperature)
            await ctx.broadcaster.broadcast(ctx.group_id, {
                "type": "compaction", "temp_id": temp_id,
                "message": f"历史已预压缩（{_pre_tokens:,} tokens > {_PRE_RUN_TOKEN_THRESHOLD:,}）",
            })

        async def _stream_final():
            nonlocal full_text, messages
            try:
                async for chunk in call_ai_stream_messages(
                    system_prompt, messages, provider, model_name, temperature, max_tokens
                ):
                    full_text += chunk
                    await ctx.broadcaster.broadcast(ctx.group_id, {
                        "type": "stream_chunk", "temp_id": temp_id, "delta": chunk,
                    })
            except AIContextOverflowError:
                messages = await _compact_messages(messages, system_prompt, provider, model_name, temperature)
                await ctx.broadcaster.broadcast(ctx.group_id, {
                    "type": "compaction", "temp_id": temp_id,
                    "message": "流式回复溢出，已压缩后重试",
                })
                async for chunk in call_ai_stream_messages(
                    system_prompt, messages, provider, model_name, temperature, max_tokens
                ):
                    full_text += chunk
                    await ctx.broadcaster.broadcast(ctx.group_id, {
                        "type": "stream_chunk", "temp_id": temp_id, "delta": chunk,
                    })

        async def _finalize_reply():
            nonlocal full_text, messages
            if not bf_config or not bf_config.get("reviewer_prompt"):
                await _stream_final()
                return
            snap = list(messages)
            try:
                gen = await call_ai_once(
                    system_prompt, snap, provider, model_name, temperature, max_tokens
                )
                draft = gen["content"] if gen["type"] == "text" else ""
            except AIContextOverflowError:
                messages = await _compact_messages(messages, system_prompt, provider, model_name, temperature)
                await ctx.broadcaster.broadcast(ctx.group_id, {
                    "type": "compaction", "temp_id": temp_id,
                    "message": "最终回复溢出，已压缩后重试",
                })
                await _stream_final()
                return
            except Exception:
                await _stream_final()
                return
            approved_text = await _before_finalize_hook(
                draft, snap, system_prompt, bf_config,
                provider, model_name, temperature, max_tokens,
                ctx.broadcaster, ctx.group_id, temp_id, ctx.user_message,
            )
            full_text = approved_text
            chunk_size = 20
            for i in range(0, len(approved_text), chunk_size):
                await ctx.broadcaster.broadcast(ctx.group_id, {
                    "type": "stream_chunk", "temp_id": temp_id,
                    "delta": approved_text[i:i+chunk_size],
                })

        try:
            if not tool_schemas:
                # No tools registered yet — pure streaming, same UX as simple_v1
                await _finalize_reply()
            else:
                execution_ctx = {
                    "bot_id": bot["id"],
                    "group_id": ctx.group_id,
                    "user_message": ctx.user_message,
                    "all_bots": ctx.all_bots,
                    "all_members": ctx.all_members,
                    "spawn_depth": ctx.spawn_depth,
                    "broadcaster": ctx.broadcaster,
                }
                iter_count = 0
                _overflow_recovered = False
                tool_records: list[dict] = []
                while iter_count < max_iter:
                    iter_count += 1
                    # P1 #2: Overflow recovery — detect context overflow, compact, retry once
                    try:
                        result = await call_ai_once(
                            system_prompt, messages, provider, model_name,
                            temperature, max_tokens, tool_schemas,
                        )
                    except AIContextOverflowError:
                        if _overflow_recovered:
                            raise AIError("上下文溢出，压缩后仍失败")
                        _overflow_recovered = True
                        if messages and messages[-1].get("role") == "assistant":
                            messages.pop()
                        messages = await _compact_messages(
                            messages, system_prompt, provider, model_name, temperature
                        )
                        await ctx.broadcaster.broadcast(ctx.group_id, {
                            "type": "compaction", "temp_id": temp_id,
                            "message": "上下文溢出，已自动压缩并重试",
                        })
                        result = await call_ai_once(
                            system_prompt, messages, provider, model_name,
                            temperature, max_tokens, tool_schemas,
                        )
                    if result["type"] == "tool_calls":
                        messages.append(result["assistant_message"])
                        for call in result["calls"]:
                            await ctx.broadcaster.broadcast(ctx.group_id, {
                                "type": "tool_call", "temp_id": temp_id,
                                "tool": call["name"], "args": call["arguments"],
                            })
                            tool_result = await tool_executor.execute(
                                call["name"], call["arguments"], context=execution_ctx
                            )
                            # skill 指定了 max_iterations → 动态扩展上限
                            if call["name"] == "run_skill":
                                skill_max = execution_ctx.pop("skill_max_iterations", None)
                                if skill_max and skill_max > max_iter:
                                    max_iter = skill_max
                                # learns: true → 注入提示，要求 bot 写执行总结到 draft/
                                skill_learns = execution_ctx.pop("skill_learns", None)
                                if skill_learns:
                                    messages.append({
                                        "role": "user",
                                        "content": (
                                            f"[系统] 技能「{skill_learns}」声明了 learns: true。"
                                            f"请将本次执行的关键发现、规律或改进点总结为一个新技能，"
                                            f"用 write_file 写入 `skills/learned/draft/{skill_learns}-learned.md`，"
                                            f"使用标准 frontmatter（name/description/layer: learned/status: draft）。"
                                        ),
                                    })
                                # context: fork → run skill in isolated sub-agent AI call
                                if tool_result == "__SKILL_FORK__":
                                    fork_info = execution_ctx.pop("skill_fork", {})
                                    fork_name = fork_info.get("name", "unknown")
                                    await ctx.broadcaster.broadcast(ctx.group_id, {
                                        "type": "skill_fork_start", "temp_id": temp_id,
                                        "member_id": bot["id"], "skill_name": fork_name,
                                    })
                                    fork_task = fork_info.get("args") or execution_ctx.get("user_message", "")
                                    tool_result = await _run_fork_skill(
                                        fork_info.get("content", ""),
                                        fork_task,
                                        provider, model_name, temperature,
                                    )
                                    await ctx.broadcaster.broadcast(ctx.group_id, {
                                        "type": "skill_fork_end", "temp_id": temp_id,
                                        "member_id": bot["id"], "skill_name": fork_name,
                                        "result": tool_result[:300],
                                    })
                            # Detect draft sentinel from write_file redirect
                            display_result = tool_result
                            if (call["name"] == "write_file"
                                    and tool_result.startswith("__DRAFT_WRITTEN__:")):
                                skill_name = tool_result.split(":", 1)[1]
                                display_result = f"已写入草稿技能「{skill_name}」，等待用户审批后生效。"
                                await ctx.broadcaster.broadcast(ctx.group_id, {
                                    "type": "skill_draft_added",
                                    "member_id": bot["id"],
                                    "skill_name": skill_name,
                                    "message": f"{bot['name']} 写入了新的自学技能「{skill_name}」，请在 Skill 管理面板审批。",
                                })
                            tool_records.append({
                                "name": call["name"],
                                "args": call["arguments"],
                                "result": display_result,
                            })
                            messages.append({
                                "role": "tool",
                                "tool_call_id": call["id"],
                                "name": call["name"],
                                "content": display_result,
                            })
                            await ctx.broadcaster.broadcast(ctx.group_id, {
                                "type": "tool_result", "temp_id": temp_id,
                                "tool": call["name"], "result": display_result[:300],
                            })
                        # 工具轮结束后检查是否需要压缩上下文（token 估算 + 相对阈值）
                        estimated_tokens = _estimate_tokens(messages)
                        threshold = _compaction_threshold(model_name)
                        if estimated_tokens > threshold:
                            messages = await _compact_messages(
                                messages, system_prompt, provider, model_name, temperature
                            )
                            await ctx.broadcaster.broadcast(ctx.group_id, {
                                "type": "compaction", "temp_id": temp_id,
                                "message": f"上下文已压缩（{estimated_tokens:,} tokens > {threshold:,} 阈值）",
                            })
                        # Inject any pending steer messages before the next AI call
                        if ctx.steer_channel and not ctx.steer_channel.empty():
                            steers = []
                            while not ctx.steer_channel.empty():
                                steers.append(ctx.steer_channel.get_nowait())
                            steer_text = "\n".join(steers)
                            messages.append({"role": "user", "content": f"[用户中途指令] {steer_text}"})
                            await ctx.broadcaster.broadcast(ctx.group_id, {
                                "type": "steer_injected", "temp_id": temp_id,
                                "member_id": bot["id"], "message": steer_text[:300],
                            })
                    else:
                        # Tools resolved — stream the final answer properly
                        await _finalize_reply()
                        break
                if not full_text:
                    full_text = "[达到最大工具调用次数，任务未完成]"

        except AIError as e:
            await ctx.broadcaster.broadcast(ctx.group_id, {
                "type": "stream_error", "temp_id": temp_id, "message": str(e),
            })
            return ExecutionResult(full_text="", msg_id=None)

        # Sub-agents return immediately — no DB persistence, no memory ops, no broadcasts
        if ctx.spawn_depth > 0:
            return ExecutionResult(full_text=full_text, msg_id=None)

        async with get_db() as db:
            msg_id = await save_message(db, ctx.group_id, bot["id"], full_text)
            recent = await get_messages(db, ctx.group_id)

        await ctx.broadcaster.broadcast(ctx.group_id, {
            "type": "stream_end", "temp_id": temp_id, "id": msg_id,
            "member_id": bot["id"], "sender_name": bot["name"],
            "preview": full_text[:100],
            "created_at": recent[-1]["created_at"] if recent else "",
        })

        tool_names_called = [
            m["name"] for m in messages
            if m.get("role") == "tool" and m.get("name")
        ]
        asyncio.create_task(add_to_chroma(msg_id, full_text, bot.get("role") or "", bot["id"]))
        asyncio.create_task(maybe_summarize(ctx.group_id, bot["id"], bot.get("role") or bot["name"], [bot["id"]]))
        asyncio.create_task(append_log(
            bot["id"], full_text,
            user_message=ctx.user_message,
            sender_name=ctx.sender.get("name", ""),
            tool_calls=tool_names_called,
            iterations=iter_count if tool_names_called else 0,
            executor=self.executor_id,
        ))
        if ctx.group_id and tool_records:
            asyncio.create_task(archive_run(
                ctx.group_id, temp_id, bot,
                user_message=ctx.user_message,
                sender_name=ctx.sender.get("name", ""),
                tool_records=tool_records,
                reply=full_text,
                iterations=iter_count,
                model=model_name,
                executor=self.executor_id,
            ))

        return ExecutionResult(full_text=full_text, msg_id=msg_id)
