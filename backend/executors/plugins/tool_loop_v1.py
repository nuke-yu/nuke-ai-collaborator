import asyncio
import uuid
import sys
import json

from executors.base import (
    BotExecutor, ExecutionContext, ExecutionResult,
    PluginManifest, WorkspaceConfig, CollabConfig, build_group_section,
)
from core import config
from executors import tool_executor
from executors.plugins.workspace_tools import (
    _IS_WINDOWS, _WORKSPACE_TOOLS,
    _build_skills_xml, _with_personality,
    register_workspace_tools,
)
from executors.plugins.rd_tools import RD_TOOLS, register_rd_tools
from db import get_db
import permissions
from ai.client import call_ai_once, call_ai_stream_messages, AIError, AIContextOverflowError
from ai.memory import get_memory_context, add_to_chroma, maybe_summarize
from core.role_router import build_context_message, build_image_content
from workspace import load_context_files, format_context_blocks, append_log, archive_run
from skills import list_skills_all, load_always_skills, filter_skills_by_context
from skills.traits import load_traits
from skills.constants import bot_ws as _bot_ws
import executors.compact as compact
from core import bg
from bus import bus
from bus.events import CompactionTriggered
from core.orchestration.ai_service import AIService
from core.orchestration.prompt_builder import compile_system_prompt
from ai.model_limits import resolve_max_tokens

_DOOM_LOOP_THRESHOLD = config.DOOM_LOOP_THRESHOLD  # breaks on the Nth consecutive tool-only iteration (inclusive)


def _acc_usage(target: list, result: dict) -> None:
    """Append usage from a call_ai_once result into a shared accumulator list."""
    u = result.get("usage") or {}
    if not u:
        return
    target.append({
        "input_tokens": u.get("input_tokens", 0),
        "output_tokens": u.get("output_tokens", 0),
        "cache_read_tokens": u.get("cache_read_tokens", 0),
        "cache_creation_tokens": u.get("cache_creation_tokens", 0),
    })


async def _execute_tool_call(name: str, arguments: dict, context: dict) -> str:
    """Thin wrapper around tool_executor.execute — extracted so tests can mock it."""
    res, _ = await tool_executor.execute(name, arguments, context=context)
    return res


async def _tool_loop_core(
    system_prompt: str,
    messages: list,
    provider: str,
    model_name: str,
    temperature: float,
    max_tokens: int,
    tool_schemas: list,
    max_iter: int = 10,
) -> str:
    """Minimal tool-calling loop used by tests (no broadcaster, no compaction, no DB).

    Returns the final text response, or a doom-loop protection message if
    ``_DOOM_LOOP_THRESHOLD`` consecutive identical tool-only iterations occur.
    """
    messages = list(messages)
    iter_count = 0
    tool_calls_history = []
    while iter_count < max_iter:
        iter_count += 1
        result = await call_ai_once(
            system_prompt, messages, provider, model_name,
            temperature, max_tokens, tool_schemas,
        )
        if result["type"] == "tool_calls":
            def _serialize_calls(calls):
                serialized = []
                for c in calls:
                    serialized.append({
                        "name": c["name"],
                        "arguments": c.get("arguments", {})
                    })
                return json.dumps(serialized, sort_keys=True)

            current_serialized = _serialize_calls(result["calls"])
            tool_calls_history.append(current_serialized)

            if len(tool_calls_history) >= _DOOM_LOOP_THRESHOLD:
                recent_history = tool_calls_history[-_DOOM_LOOP_THRESHOLD:]
                if all(h == current_serialized for h in recent_history):
                    return f"[循环保护] 连续 {_DOOM_LOOP_THRESHOLD} 次完全相同的工具调用，已终止循环"

            messages.append(result["assistant_message"])
            for call in result["calls"]:
                tool_result = await _execute_tool_call(
                    call["name"], call.get("arguments", {}), context={}
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "name": call["name"],
                    "content": tool_result,
                })
        else:
            tool_calls_history.clear()
            return result.get("content", "")
    return "[达到最大工具调用次数，任务未完成]"


async def _before_finalize_hook(
    draft: str,
    snap_messages: list,
    system_prompt: str,
    config: dict,
    provider: str,
    model_name: str,
    temperature: float,
    max_tokens: int,
    ai_service: AIService,
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
        await ai_service.ctx.interaction.broadcast(ai_service.ctx.group_id, {
            "type": "before_finalize_review", "temp_id": ai_service.temp_id,
            "attempt": attempt + 1, "max_retries": max_retries + 1,
        })

        approved, feedback = True, ""
        try:
            review = await ai_service.call(
                reviewer_prompt,
                [{"role": "user", "content": f"【用户问题】\n{user_message}\n\n【待审查回复】\n{cur_draft}"}],
                model_name, provider, temperature, 512,
                auto_compact=False
            )
            feedback = review["content"] if review["type"] == "text" else ""
            approved = not feedback.strip().upper().startswith("REJECTED")
        except Exception:
            break  # fail open

        if approved or attempt >= max_retries:
            await ai_service.ctx.interaction.broadcast(ai_service.ctx.group_id, {
                "type": "before_finalize_approved", "temp_id": ai_service.temp_id,
                "note": "" if approved else "retry budget exhausted",
            })
            break

        await ai_service.ctx.interaction.broadcast(ai_service.ctx.group_id, {
            "type": "before_finalize_rejected", "temp_id": ai_service.temp_id,
            "feedback": feedback[:300], "attempt": attempt + 1,
        })

        cur_messages = cur_messages + [
            {"role": "assistant", "content": cur_draft},
            {"role": "user", "content": f"[审查意见] {feedback}\n\n请根据以上意见修改回复。"},
        ]
        try:
            regen = await ai_service.call(
                system_prompt, cur_messages, model_name, provider, temperature, max_tokens,
                auto_compact=False
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
    ai_service: AIService,
    tool_schemas: list | None = None,
) -> str:
    """Execute a fork skill in an isolated single AI call.

    skill_content → system prompt; task (args or user_message) → user turn.
    tool_schemas: if provided, enables tool calling in the fork (allowed-tools whitelist).
    """
    try:
        result = await ai_service.call(
            skill_content,
            [{"role": "user", "content": task or "请执行此技能。"}],
            model, provider, temperature, 4096,
            tools=tool_schemas or None,
            auto_compact=False
        )
        if result["type"] == "text":
            return result["content"]
        if result["type"] == "tool_calls":
            names = [c["name"] for c in result.get("calls", [])]
            return f"[fork skill 请求工具调用: {', '.join(names)}]（fork 不支持多轮工具循环）"
        return f"[fork skill 返回了非文本类型: {result['type']}]"
    except Exception as e:
        return f"[fork skill 执行错误] {e}"



class ToolLoopRunner:
    """Encapsulates the state and execution flow of a single AI tool loop run. (DFT-075 Refactor)"""
    def __init__(self, executor, ctx: ExecutionContext):
        self.executor = executor
        self.ctx = ctx
        self.bot = ctx.bot
        self.max_iter = (self.bot.get("executor_config") or {}).get("max_iterations", executor.manifest.max_iterations)
        self.bf_config = (self.bot.get("executor_config") or {}).get("before_finalize")
        self.model_name = self.bot.get("model_name", "deepseek-chat")
        self.provider = self.bot.get("model_provider", "deepseek")
        self.temperature = self.bot.get("temperature", 0.7)
        # Per-model output budget: default to the model's real ceiling (not a flat
        # 4096), clamp an explicit config down to that ceiling. Avoids large-file
        # write_file outputs getting truncated just because the default was tiny.
        self.max_tokens = resolve_max_tokens(self.provider, self.model_name, self.bot.get("max_tokens"))
        
        self.messages = []
        self.full_text = ""
        self.iter_count = 0
        self.consecutive_tool_only = 0
        self.tool_calls_history = []
        self.tool_records = []
        self.file_tracker = {}
        self.temp_id = str(uuid.uuid4())
        self.session_id = ctx.resume_session_id or str(uuid.uuid4())
        self.ai_service = AIService(ctx, self.session_id, self.temp_id)
        
        self.skills_xml = ""
        self.skills_snapshot = []
        self.always_skills = []
        self.system_prompt_base = ""
        self.system_prompt = ""
        self.tool_schemas = []
        
        self.rewake_queue = asyncio.Queue()
        self.execution_ctx = {}
        
        self.ruleset = None
        self.use_cached_mc = compact.should_use_cached_microcompact(self.provider)

    async def _get_fresh_context_prefix(self) -> tuple[str, str]:
        blocks = await load_context_files(
            self.bot["id"], self.ctx.group_id, self.executor.manifest.workspace.startup_files
        )
        text = format_context_blocks(blocks)
        prefix = ""
        if text:
            prefix += f"【工作区文件】\n{text}\n\n"
        if self.skills_xml:
            prefix += f"{self.skills_xml}\n使用 run_skill(name=\"技能名\") 调用\n\n"
        return prefix, text

    async def _build_reinject(self) -> str:
        fresh_prefix, fresh_text = await self._get_fresh_context_prefix()
        ft_xml = compact.build_file_tracker_xml(self.file_tracker)
        file_contents = compact.build_file_contents_for_reinject(
            self.file_tracker, workspace_dir=str(_bot_ws(self.bot["id"]))
        )
        parts = [p for p in [fresh_text, ft_xml, file_contents] if p]
        return "\n\n".join(parts)

    async def _stream_final(self):
        if self.full_text:
            chunk_size = 20
            for i in range(0, len(self.full_text), chunk_size):
                chunk = self.full_text[i:i+chunk_size]
                await self.ctx.interaction.broadcast(self.ctx.group_id, {
                    "type": "stream_chunk", "temp_id": self.temp_id, "delta": chunk,
                    "session_id": self.session_id,
                })
                await asyncio.sleep(0.02)
            return

        try:
            async for chunk in self.ai_service.stream(
                self.system_prompt, self.messages, self.model_name, self.provider,
                self.temperature, self.max_tokens, reinject_fn=self._build_reinject
            ):
                self.full_text += chunk
                await self.ctx.interaction.broadcast(self.ctx.group_id, {
                    "type": "stream_chunk", "temp_id": self.temp_id, "delta": chunk,
                    "session_id": self.session_id,
                })
        except AIError as e:
            await self.ctx.interaction.broadcast(self.ctx.group_id, {
                "type": "stream_error", "temp_id": self.temp_id, "message": str(e),
                "session_id": self.session_id,
            })

    async def _finalize_reply(self):
        if not self.bf_config or not self.bf_config.get("reviewer_prompt"):
            await self._stream_final()
            return
        snap = list(self.messages)
        try:
            gen = await self.ai_service.call(
                self.system_prompt, snap, self.model_name, self.provider,
                self.temperature, self.max_tokens, reinject_fn=self._build_reinject
            )
            draft = gen["content"] if gen["type"] == "text" else ""
        except Exception:
            await self._stream_final()
            return

        approved_text = await _before_finalize_hook(
            draft, snap, self.system_prompt, self.bf_config,
            self.provider, self.model_name, self.temperature, self.max_tokens,
            self.ai_service, self.ctx.user_message,
        )
        self.full_text = approved_text
        chunk_size = 20
        for i in range(0, len(approved_text), chunk_size):
            await self.ctx.interaction.broadcast(self.ctx.group_id, {
                "type": "stream_chunk", "temp_id": self.temp_id,
                "delta": approved_text[i:i+chunk_size],
                "session_id": self.session_id,
            })

    async def _setup_session(self):
        # Build ruleset
        if self.ctx.ruleset is not None:
            self.ruleset = self.ctx.ruleset
        else:
            perm_mode = (self.bot.get("executor_config") or {}).get("permission_mode", "default")
            db_rules = await permissions.load_rules(self.bot["id"])
            self.ruleset = permissions.Ruleset(rules=db_rules, mode=perm_mode)

        history, user_msg = build_context_message(self.ctx.user_message, self.ctx.sender["name"], self.ctx.history)
        memory = await get_memory_context(self.bot["id"], self.bot.get("role") or "", self.ctx.user_message)

        if self.executor.manifest.workspace.skill_discovery:
            self.system_prompt_base, self.skills_xml, self.skills_snapshot, self.always_skills = await compile_system_prompt(
                self.bot, self.ctx, self.model_name, memory
            )
        else:
            base = _with_personality(
                self.bot["system_prompt"] or f"你是{self.bot['name']}，{self.bot.get('role', '')}。", self.bot
            )
            group_section = build_group_section(self.ctx)
            bot_traits = self.bot.get("traits", [])
            traits_section = load_traits(bot_traits)
            os_info = f"Windows (PowerShell)" if _IS_WINDOWS else f"{sys.platform} (shell: /bin/sh)"
            self.system_prompt_base = (
                base
                + (f"\n\n{memory}" if memory else "")
                + traits_section
                + (f"\n\n【群组信息】\n{group_section}" if group_section else "")
                + f"\n\n【运行环境】\nOS: {os_info}\n路径分隔符: {'\\' if _IS_WINDOWS else '/'}\n使用 run_shell 执行命令时请使用适合当前 OS 的语法。"
                + "\n\n【自学技能规则】\n当你发现可复用的规律或用户说「记住这个做法」时，用 write_file 将技能写入 `skills/learned/draft/<skill-name>.md`，系统会自动请求用户审批。禁止直接写入 `skills/learned/active/`。"
                + self.ctx.workflow_suffix
            )
        self.system_prompt = self.system_prompt_base

        user_content = build_image_content(user_msg, self.ctx.file_url, self.ctx.file_type, self.provider)
        if isinstance(user_content, str):
            user_content, _ = compact.truncate_user_message(user_content, self.ctx.group_id, self.model_name)
        elif isinstance(user_content, list):
            for block in user_content:
                if isinstance(block, dict) and block.get("type") == "text":
                    truncated_text, _ = compact.truncate_user_message(block.get("text", ""), self.ctx.group_id, self.model_name)
                    block["text"] = truncated_text
        
        _resuming = bool(self.ctx.resume_session_id)
        if _resuming:
            resumed = list(self.ctx.resume_messages or [])
            if resumed and resumed[0].get("role") == "system":
                resumed = resumed[1:]
            self.messages = resumed
        else:
            self.messages = list(history) + [{"role": "user", "content": user_content}]

        tool_names = [t.name for t in self.executor.manifest.tools]
        self.tool_schemas = tool_executor.get_schemas(tool_names)
        # Per-stage tool whitelist (e.g. the BA requirements stage gets only
        # read/skill/ticket tools, so it physically can't write code). Filtering
        # the base schemas here means every iteration — and any skill-level
        # narrowing on top — stays within the stage's allowed set.
        self.tool_schemas = self._restrict_schemas(self.tool_schemas, self.bot.get("allowed_tools"))

        if _resuming:
            await self.ctx.interaction.update_session_status(self.session_id, "running")
        else:
            _session_config = {
                "system_prompt": self.system_prompt,
                "provider": self.provider,
                "model_name": self.model_name,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            }
            await self.ctx.interaction.create_session(
                session_id=self.session_id,
                bot_id=self.bot["id"],
                group_id=self.ctx.group_id,
                config=_session_config,
                user_message=self.ctx.user_message,
                executor_id=self.executor.executor_id,
            )
            await self.ctx.interaction.append_session_event(self.session_id, "session_start", {
                "user_content": user_content if isinstance(user_content, str) else json.dumps(user_content, ensure_ascii=False),
            })
            
        await self.ctx.interaction.broadcast(self.ctx.group_id, {
            "type": "stream_start", "temp_id": self.temp_id,
            "member_id": self.bot["id"], "sender_name": self.bot["name"],
            "sender_type": "bot", "avatar_color": self.bot["avatar_color"],
            "session_id": self.session_id,
        })
        if self.skills_snapshot:
            await self.ctx.interaction.broadcast(self.ctx.group_id, {
                "type": "skills_loaded", "temp_id": self.temp_id,
                "member_id": self.bot["id"], "skills": self.skills_snapshot,
            })

    @staticmethod
    def _restrict_schemas(schemas: list, allowed: list | None) -> list:
        """Keep only tool schemas whose name is in `allowed`. `None`/empty = no
        restriction (return as-is)."""
        if not allowed:
            return schemas
        allowed_set = set(allowed)
        return [s for s in schemas if s["function"]["name"] in allowed_set]

    def _track_vfs_modifications(self, call_name: str, arguments: dict):
        _fpath = arguments.get("path", "")
        if _fpath:
            if call_name in compact._FILE_WRITE_TOOLS:
                self.file_tracker[_fpath] = "modified"
            elif call_name in compact._FILE_READ_TOOLS:
                self.file_tracker.setdefault(_fpath, "read")

    async def _handle_run_skill_result(self, tool_result: str) -> str:
        skill_max = self.execution_ctx.pop("skill_max_iterations", None)
        if skill_max and skill_max > self.max_iter:
            self.max_iter = skill_max
            
        skill_learns = self.execution_ctx.pop("skill_learns", None)
        if skill_learns:
            self.messages.append({
                "role": "user",
                "content": (
                    f"[系统] 技能「{skill_learns}」声明了 learns: true。"
                    f"请将本次执行的关键发现、规律或改进点总结为一个新技能，"
                    f"用 write_file 写入 `skills/learned/draft/{skill_learns}-learned.md`，"
                    f"使用 standard frontmatter（name/description/layer: learned/status: draft）。"
                ),
            })
            
        if tool_result == "__SKILL_FORK__":
            fork_info = self.execution_ctx.pop("skill_fork", {})
            fork_name = fork_info.get("name", "unknown")
            await self.ctx.interaction.broadcast(self.ctx.group_id, {
                "type": "skill_fork_start", "temp_id": self.temp_id,
                "member_id": self.bot["id"], "skill_name": fork_name,
            })
            fork_task = fork_info.get("args") or self.execution_ctx.get("user_message", "")
            fork_allowed = fork_info.get("allowed_tools", [])
            fork_schemas = (
                [s for s in self.tool_schemas if s["function"]["name"] in fork_allowed]
                if fork_allowed else None
            )
            fork_model = fork_info.get("model") or self.model_name
            child_sid = str(uuid.uuid4())
            await self.ctx.interaction.append_session_event(self.session_id, "child_fork", {
                "child_session_id": child_sid,
                "skill_name": fork_name,
            })
            
            tool_result = await _run_fork_skill(
                fork_info.get("content", ""),
                fork_task,
                self.provider, fork_model, self.temperature,
                self.ai_service,
                tool_schemas=fork_schemas,
            )
            await self.ctx.interaction.append_session_event(self.session_id, "child_join", {
                "child_session_id": child_sid,
                "skill_name": fork_name,
                "result": tool_result,
            })
            await self.ctx.interaction.broadcast(self.ctx.group_id, {
                "type": "skill_fork_end", "temp_id": self.temp_id,
                "member_id": self.bot["id"], "skill_name": fork_name,
                "result": tool_result[:300],
            })
        return tool_result

    async def _execute_parallel_tools(self, calls):
        for call in calls:
            await self.ctx.interaction.broadcast(self.ctx.group_id, {
                "type": "tool_call", "temp_id": self.temp_id,
                "tool": call["name"], "args": call["arguments"],
                "session_id": self.session_id,
            })
            await self.ctx.interaction.append_session_event(self.session_id, "tool_call", {
                "tool_call_id": call["id"],
                "tool_name": call["name"],
                "arguments": call.get("arguments", {}),
            })
            
        raw_results = await asyncio.gather(*[
            tool_executor.execute(c["name"], c["arguments"], context=self.execution_ctx)
            for c in calls
        ])
        
        for call, (tool_result, is_error) in zip(calls, raw_results):
            await self.ctx.interaction.append_session_event(self.session_id, "tool_result", {
                "tool_call_id": call["id"],
                "tool_name": call["name"],
                "result": tool_result,
                "is_error": is_error,
            })
            self._track_vfs_modifications(call["name"], call["arguments"])
            
            # Apply truncation if output is too long
            display_result, truncated_path = compact.truncate_tool_result(call["name"], tool_result, self.ctx.group_id, self.model_name)
            
            self.tool_records.append({
                "name": call["name"],
                "args": call["arguments"],
                "result": display_result,
            })
            self.messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "name": call["name"],
                "content": display_result,
            })
            await self.ctx.interaction.save_session_snapshot(self.session_id, self.messages)
            await self.ctx.interaction.broadcast(self.ctx.group_id, {
                "type": "tool_result", "temp_id": self.temp_id,
                "tool": call["name"], "result": display_result[:300],
                "session_id": self.session_id,
            })

    async def _execute_serial_tools(self, calls):
        for call in calls:
            await self.ctx.interaction.broadcast(self.ctx.group_id, {
                "type": "tool_call", "temp_id": self.temp_id,
                "tool": call["name"], "args": call["arguments"],
                "session_id": self.session_id,
            })
            await self.ctx.interaction.append_session_event(self.session_id, "tool_call", {
                "tool_call_id": call["id"],
                "tool_name": call["name"],
                "arguments": call.get("arguments", {}),
            })
            
            tool_result, is_error = await tool_executor.execute(
                call["name"], call["arguments"], context=self.execution_ctx
            )
            await self.ctx.interaction.append_session_event(self.session_id, "tool_result", {
                "tool_call_id": call["id"],
                "tool_name": call["name"],
                "result": tool_result,
                "is_error": is_error,
            })
            
            self._track_vfs_modifications(call["name"], call["arguments"])
                    
            if call["name"] == "run_skill":
                tool_result = await self._handle_run_skill_result(tool_result)
                    
            display_result = tool_result
            if call["name"] == "write_file" and tool_result.startswith("__DRAFT_WRITTEN__:"):
                skill_name = tool_result.split(":", 1)[1]
                display_result = f"已写入草稿技能「{skill_name}」，等待用户审批后生效。"
                await self.ctx.interaction.broadcast(self.ctx.group_id, {
                    "type": "skill_draft_added",
                    "member_id": self.bot["id"],
                    "skill_name": skill_name,
                    "message": f"{self.bot['name']} 写入了新的自学技能「{skill_name}」，请在 Skill 管理面板审批。",
                })
                
            # Apply truncation if output is too long
            display_result, truncated_path = compact.truncate_tool_result(call["name"], display_result, self.ctx.group_id, self.model_name)

            self.tool_records.append({
                "name": call["name"],
                "args": call["arguments"],
                "result": display_result,
            })
            self.messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "name": call["name"],
                "content": display_result,
            })
            
            await self.ctx.interaction.save_session_snapshot(self.session_id, self.messages)
            await self.ctx.interaction.broadcast(self.ctx.group_id, {
                "type": "tool_result", "temp_id": self.temp_id,
                "tool": call["name"], "result": display_result[:300],
                "session_id": self.session_id,
            })

    async def _run_pre_compaction(self):
        self.messages = compact.apply_tool_result_microcompact(self.messages)
        _pre_tokens = compact.estimate_tokens(self.messages)
        if _pre_tokens > compact._PRE_RUN_TOKEN_THRESHOLD:
            self.messages = await compact.compact_conversation(
                self.messages, self.system_prompt, self.provider, self.model_name, self.temperature,
                context_text=await self._build_reinject(),
            )
            await self.ctx.interaction.broadcast(self.ctx.group_id, {
                "type": "compaction", "temp_id": self.temp_id,
                "strategy": "pre_run",
                "message": f"历史已预压缩（{_pre_tokens:,} tokens > {compact._PRE_RUN_TOKEN_THRESHOLD:,}）",
                "session_id": self.session_id,
            })

    async def _poll_and_inject_signals(self):
        if self.ctx.steer_channel and not self.ctx.steer_channel.empty():
            steers = []
            # Note (M-7): In single-threaded asyncio, these empty() -> get_nowait() 
            # sequences are atomic as there are no yield points (awaits) between them.
            while not self.ctx.steer_channel.empty():
                steers.append(self.ctx.steer_channel.get_nowait())
            steer_text = "\n".join(steers)
            self.messages.append({"role": "user", "content": f"[用户中途指令] {steer_text}"})
            await self.ctx.interaction.broadcast(self.ctx.group_id, {
                "type": "steer_injected", "temp_id": self.temp_id,
                "member_id": self.bot["id"], "message": steer_text[:300],
            })
            
        if not self.rewake_queue.empty():
            rewakes = []
            # Note (M-7): In single-threaded asyncio, these empty() -> get_nowait() 
            # sequences are atomic as there are no yield points (awaits) between them.
            while not self.rewake_queue.empty():
                rewakes.append(self.rewake_queue.get_nowait())
            rewake_text = "\n".join(rewakes)
            self.messages.append({"role": "user", "content": f"[系统唤醒] {rewake_text}"})
            await self.ctx.interaction.broadcast(self.ctx.group_id, {
                "type": "rewake_injected", "temp_id": self.temp_id,
                "member_id": self.bot["id"], "message": rewake_text[:300],
            })

    async def _cleanup_and_finalize(self) -> ExecutionResult:
        if self.ctx.spawn_depth > 0:
            await self.ctx.interaction.broadcast(self.ctx.group_id, {
                "type": "stream_end", "temp_id": self.temp_id, "id": None,
                "member_id": self.bot["id"], "sender_name": self.bot["name"],
                "preview": self.full_text[:100], "created_at": "",
                "session_id": self.session_id,
            })
            self.messages.append({"role": "assistant", "content": self.full_text})
            await self.ctx.interaction.save_session_snapshot(self.session_id, self.messages)
            await self.ctx.interaction.update_session_status(self.session_id, "completed")
            return ExecutionResult(full_text=self.full_text, msg_id=None)

        msg_id = await self.ctx.interaction.save_message(
            self.ctx.group_id, self.bot["id"], self.full_text,
            input_tokens=self.ai_service.usage.input_tokens or None,
            output_tokens=self.ai_service.usage.output_tokens or None,
            cache_read_tokens=self.ai_service.usage.cache_read_tokens or None,
            cache_creation_tokens=self.ai_service.usage.cache_creation_tokens or None,
        )


        await self.ctx.interaction.broadcast(self.ctx.group_id, {
            "type": "stream_end", "temp_id": self.temp_id, "id": msg_id,
            "member_id": self.bot["id"], "sender_name": self.bot["name"],
            "preview": self.full_text[:100],
            "created_at": "",
            "session_id": self.session_id,
        })

        tool_names_called = [
            m["name"] for m in self.messages
            if m.get("role") == "tool" and m.get("name")
        ]
        
        bg.spawn(add_to_chroma(msg_id, self.full_text, self.bot.get("role") or "", self.bot["id"]))
        bg.spawn(maybe_summarize(self.ctx.group_id, self.bot["id"], self.bot.get("role") or self.bot["name"], [self.bot["id"]]))
        bg.spawn(bus.publish(CompactionTriggered(
            group_id=self.ctx.group_id,
            bot_id=self.bot["id"],
            provider=self.provider,
            model_name=self.model_name,
            temperature=self.temperature
        )))
        bg.spawn(append_log(
            self.bot["id"], self.full_text,
            user_message=self.ctx.user_message,
            sender_name=self.ctx.sender.get("name", ""),
            tool_calls=tool_names_called,
            iterations=self.iter_count if tool_names_called else 0,
            executor=self.executor.executor_id,
        ))
        if self.ctx.group_id and self.tool_records:
            bg.spawn(archive_run(
                self.ctx.group_id, self.temp_id, self.bot,
                user_message=self.ctx.user_message,
                sender_name=self.ctx.sender.get("name", ""),
                tool_records=self.tool_records,
                reply=self.full_text,
                iterations=self.iter_count,
                model=self.model_name,
                executor=self.executor.executor_id,
            ))

        self.messages.append({"role": "assistant", "content": self.full_text})
        await self.ctx.interaction.save_session_snapshot(self.session_id, self.messages)
        await self.ctx.interaction.update_session_status(self.session_id, "completed")
        return ExecutionResult(full_text=self.full_text, msg_id=msg_id)

    async def execute(self) -> ExecutionResult:
        await self._setup_session()
        await self._run_pre_compaction()

        try:
            if not self.tool_schemas:
                await self._finalize_reply()
            else:
                self.execution_ctx = {
                    "bot_id": self.bot["id"],
                    "group_id": self.ctx.group_id,
                    "user_message": self.ctx.user_message,
                    "all_bots": self.ctx.all_bots,
                    "all_members": self.ctx.all_members,
                    "spawn_depth": self.ctx.spawn_depth,
                    
                    "ruleset": self.ruleset,
                    "steer_channel": self.ctx.steer_channel,
                    "rewake_queue": self.rewake_queue,
                }
                
                _active_schemas = self.tool_schemas
                while self.iter_count < self.max_iter:
                    self.iter_count += 1
                    current_prefix, _ = await self._get_fresh_context_prefix()
                    self.system_prompt = self.system_prompt_base + (f"\n\n{current_prefix}" if current_prefix else "")

                    _skill_allowed = self.execution_ctx.pop("skill_allowed_tools", None)
                    if _skill_allowed is not None:
                        _active_schemas = [s for s in self.tool_schemas if s["function"]["name"] in _skill_allowed]
                    else:
                        _active_schemas = self.tool_schemas
                        
                    _iter_model = self.execution_ctx.pop("skill_model", None) or self.model_name

                    try:
                        result = await self.ai_service.call(
                            self.system_prompt, self.messages, _iter_model, self.provider,
                            self.temperature, self.max_tokens, _active_schemas,
                            use_cached_microcompact=self.use_cached_mc,
                            reinject_fn=self._build_reinject
                        )
                    except AIError as e:
                        await self.ctx.interaction.broadcast(self.ctx.group_id, {
                            "type": "stream_error", "temp_id": self.temp_id, "message": str(e),
                        })
                        break

                    if result["type"] == "text":
                        self.consecutive_tool_only = 0
                        self.tool_calls_history.clear()
                        self.full_text = result["content"]
                        await self._finalize_reply()
                        break
                    
                    if result["type"] == "tool_calls":
                        def _serialize_calls(calls):
                            serialized = []
                            for c in calls:
                                serialized.append({
                                    "name": c["name"],
                                    "arguments": c.get("arguments", {})
                                })
                            return json.dumps(serialized, sort_keys=True)

                        current_serialized = _serialize_calls(result["calls"])
                        self.tool_calls_history.append(current_serialized)

                        if len(self.tool_calls_history) >= _DOOM_LOOP_THRESHOLD:
                            recent_history = self.tool_calls_history[-_DOOM_LOOP_THRESHOLD:]
                            if all(h == current_serialized for h in recent_history):
                                self.full_text = f"[循环保护] 连续 {_DOOM_LOOP_THRESHOLD} 次完全相同的工具调用，已终止循环"
                                break
                        self.messages.append(result["assistant_message"])
                        await self.ctx.interaction.save_session_snapshot(self.session_id, self.messages)

                        calls = result["calls"]
                        _run_parallel = (
                            len(calls) > 1
                            and all(tool_executor.is_concurrency_safe(c["name"]) for c in calls)
                        )
                        if _run_parallel:
                            await self._execute_parallel_tools(calls)
                        else:
                            await self._execute_serial_tools(calls)
                            
                        self.messages = compact.apply_tool_result_microcompact(self.messages)
                        self.messages, _ = compact.snip_if_needed(self.messages, self.model_name)
                        self.messages, _ = await compact.auto_compact_if_needed(
                            self.messages, self.model_name, self.ctx.group_id,
                            self.system_prompt, self.provider, self.temperature,
                            self.ctx.interaction, self.temp_id, self.bot["id"],
                            context_text=await self._build_reinject(),
                        )
                        
                        await self._poll_and_inject_signals()
                    else:
                        self.consecutive_tool_only = 0
                        await self._finalize_reply()
                        break
                        
                if not self.full_text:
                    self.full_text = "[达到最大工具调用次数，任务未完成]"

        except asyncio.CancelledError:
            await self.ctx.interaction.update_session_status(self.session_id, "failed")
            await self.ctx.interaction.broadcast(self.ctx.group_id, {
                "type": "stream_aborted", "temp_id": self.temp_id, "member_id": self.bot["id"],
                "session_id": self.session_id,
            })
            raise
        except AIError as e:
            await self.ctx.interaction.update_session_status(self.session_id, "failed")
            await self.ctx.interaction.broadcast(self.ctx.group_id, {
                "type": "stream_error", "temp_id": self.temp_id, "message": str(e),
                "session_id": self.session_id,
            })
            return ExecutionResult(full_text="", msg_id=None)

        return await self._cleanup_and_finalize()

class ToolLoopV1(BotExecutor):
    executor_id = "tool_loop_v1"
    display_name = "工具调用循环"

    def register_tools(self):
        register_workspace_tools()
        register_rd_tools()

    manifest = PluginManifest(
        description="多轮工具调用循环：AI 可读写工作区文件、执行技能，直至任务完成",
        tools=_WORKSPACE_TOOLS + RD_TOOLS,
        memory_layers=["short_term", "vector_search", "summary", "permanent"],
        workspace=WorkspaceConfig(
            startup_files=["AGENT.md", "BOOTSTRAP.md", "IDENTITY.md", "MEMORY.md"],
            skill_discovery=True,
            writeback_pattern="logs/{date}.md",
        ),
        collaboration=CollabConfig(can_handoff=True, can_spawn_subagent=True),
        max_iterations=100,
    )

    async def run(self, ctx: ExecutionContext) -> ExecutionResult:
        if ctx.interaction is None:
            from core.orchestration.interaction import StandardInteraction
            ctx.interaction = StandardInteraction(ctx)
            
        runner = ToolLoopRunner(self, ctx)
        return await runner.execute()
