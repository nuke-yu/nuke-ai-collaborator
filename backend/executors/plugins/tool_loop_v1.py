import asyncio
import uuid
import sys
import json

from executors.base import (
    BotExecutor, ExecutionContext, ExecutionResult,
    PluginManifest, WorkspaceConfig, CollabConfig, build_group_section,
)
from executors import tool_executor
from executors.plugins.workspace_tools import (
    _IS_WINDOWS, _WORKSPACE_TOOLS,
    _build_skills_xml, _with_personality,
    register_workspace_tools,
)
from db import get_db
import permissions
from ai.client import call_ai_once, call_ai_stream_messages, AIError, AIContextOverflowError
from ai.memory import get_memory_context, add_to_chroma, maybe_summarize
from core.role_router import build_context_message, build_image_content
from workspace import load_context_files, format_context_blocks, append_log, archive_run
from skills import list_skills_all, load_always_skills, filter_skills_by_context
from skills.constants import bot_ws as _bot_ws
import executors.compact as compact

_DOOM_LOOP_THRESHOLD = 5  # breaks on the Nth consecutive tool-only iteration (inclusive)


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
    return await tool_executor.execute(name, arguments, context=context)


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
    ``_DOOM_LOOP_THRESHOLD`` consecutive tool-only iterations occur.
    """
    messages = list(messages)
    iter_count = 0
    _consecutive_tool_only = 0
    while iter_count < max_iter:
        iter_count += 1
        result = await call_ai_once(
            system_prompt, messages, provider, model_name,
            temperature, max_tokens, tool_schemas,
        )
        if result["type"] == "tool_calls":
            _consecutive_tool_only += 1
            if _consecutive_tool_only >= _DOOM_LOOP_THRESHOLD:
                return f"[循环保护] 连续 {_consecutive_tool_only} 次工具调用，已终止循环"
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
            _consecutive_tool_only = 0
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
    broadcaster,
    group_id: int,
    temp_id: str,
    user_message: str,
    usage_out: list | None = None,
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
            if usage_out is not None:
                _acc_usage(usage_out, review)
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
            if usage_out is not None:
                _acc_usage(usage_out, regen)
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
    tool_schemas: list | None = None,
    usage_out: list | None = None,
) -> str:
    """Execute a fork skill in an isolated single AI call.

    skill_content → system prompt; task (args or user_message) → user turn.
    tool_schemas: if provided, enables tool calling in the fork (allowed-tools whitelist).
    """
    try:
        result = await call_ai_once(
            skill_content,
            [{"role": "user", "content": task or "请执行此技能。"}],
            provider, model, temperature, 4096,
            tool_schemas or None,
        )
        if usage_out is not None:
            _acc_usage(usage_out, result)
        if result["type"] == "text":
            return result["content"]
        if result["type"] == "tool_calls":
            names = [c["name"] for c in result.get("calls", [])]
            return f"[fork skill 请求工具调用: {', '.join(names)}]（fork 不支持多轮工具循环）"
        return f"[fork skill 返回了非文本类型: {result['type']}]"
    except Exception as e:
        return f"[fork skill 执行错误] {e}"


class ToolLoopV1(BotExecutor):
    executor_id = "tool_loop_v1"
    display_name = "工具调用循环"

    def register_tools(self):
        register_workspace_tools()

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

        # Build ruleset: inherit from parent spawn if provided, otherwise load from DB
        if ctx.ruleset is not None:
            _ruleset = ctx.ruleset
        else:
            perm_mode = (bot.get("executor_config") or {}).get("permission_mode", "default")
            db_rules = await permissions.load_rules(bot["id"])
            _ruleset = permissions.Ruleset(rules=db_rules, mode=perm_mode)

        history, user_msg = build_context_message(ctx.user_message, ctx.sender["name"], ctx.history)
        base = _with_personality(
            bot["system_prompt"] or f"你是{bot['name']}，{bot.get('role', '')}。", bot
        )
        memory = await get_memory_context(bot["id"], bot.get("role") or "", ctx.user_message)

        skills_snapshot = []
        always_skills = []
        skills_xml = ""
        if self.manifest.workspace.skill_discovery:
            raw_skills = list_skills_all(bot["id"], group_id=ctx.group_id,
                                         role=bot.get("role"))
            lazy_candidates = [
                s for s in raw_skills
                if not s.get("always")
                and s.get("status", "active") != "disabled"
                and s.get("user_invocable", True)
            ]
            lazy_candidates = filter_skills_by_context(lazy_candidates, ctx.user_message)
            skills_xml, injected_names = _build_skills_xml(lazy_candidates, model_name)
            for s in raw_skills:
                if s.get("status", "active") == "disabled":
                    skills_snapshot.append({**s, "injected": None})
                elif s.get("always"):
                    skills_snapshot.append({**s, "injected": "full"})
                elif not s.get("user_invocable", True):
                    skills_snapshot.append({**s, "injected": None})
                else:
                    inj = "metadata" if s["name"] in injected_names else None
                    skills_snapshot.append({**s, "injected": inj})
            if any(s.get("always") for s in raw_skills if s.get("status", "active") != "disabled"):
                always_skills = load_always_skills(bot["id"], ctx.group_id,
                                                   bot.get("role"))

        # Build context prefix (moved logic to a helper for dynamic refreshing)
        async def _get_fresh_context_prefix():
            blocks = await load_context_files(
                bot["id"], ctx.group_id, self.manifest.workspace.startup_files
            )
            text = format_context_blocks(blocks)
            prefix = ""
            if text:
                prefix += f"【工作区文件】\n{text}\n\n"
            if skills_xml:
                prefix += f"{skills_xml}\n使用 run_skill(name=\"技能名\") 调用\n\n"
            return prefix, text

        context_prefix, context_text = await _get_fresh_context_prefix()

        always_section = ""
        if always_skills:
            parts = [f"=== {s['name']} ===\n{s['content']}" for s in always_skills]
            always_section = "\n\n【常驻技能 · 始终激活】\n" + "\n\n".join(parts)

        group_section = build_group_section(ctx)
        os_info = f"Windows (PowerShell)" if _IS_WINDOWS else f"{sys.platform} (shell: /bin/sh)"
        system_prompt_base = (
            base
            + (f"\n\n{memory}" if memory else "")
            + (f"\n\n【群组信息】\n{group_section}" if group_section else "")
            + always_section
            + f"\n\n【运行环境】\nOS: {os_info}\n路径分隔符: {'\\\\' if _IS_WINDOWS else '/'}\n使用 run_shell 执行命令时请使用适合当前 OS 的语法。"
            + "\n\n【自学技能规则】\n当你发现可复用的规律或用户说「记住这个做法」时，用 write_file 将技能写入 `skills/learned/draft/<skill-name>.md`，系统会自动请求用户审批。禁止直接写入 `skills/learned/active/`。"
            + ctx.workflow_suffix
        )
        system_prompt = system_prompt_base

        provider = bot.get("model_provider", "deepseek")
        temperature = bot.get("temperature", 0.7)
        max_tokens = bot.get("max_tokens", 4096)

        # Build initial user content WITHOUT context prefix (will inject dynamically)
        user_content = build_image_content(user_msg, ctx.file_url, ctx.file_type, provider)
        
        _resuming = bool(ctx.resume_session_id)
        if _resuming:
            # DFT-018: continue the crashed session from its reconstructed WAL
            resumed = list(ctx.resume_messages or [])
            if resumed and resumed[0].get("role") == "system":
                resumed = resumed[1:]  # system prompt is supplied separately
            messages = resumed
        else:
            # Initial message contains only the user request
            messages = list(history) + [{"role": "user", "content": user_content}]

        tool_names = [t.name for t in self.manifest.tools]
        tool_schemas = tool_executor.get_schemas(tool_names)

        temp_id = str(uuid.uuid4())
        if _resuming:
            _session_id = ctx.resume_session_id
            await ctx.interaction.update_session_status(_session_id, "running")
        else:
            _session_id = str(uuid.uuid4())
            _session_config = {
                "system_prompt": system_prompt,
                "provider": provider,
                "model_name": model_name,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            await ctx.interaction.create_session(
                session_id=_session_id,
                bot_id=bot["id"],
                group_id=ctx.group_id,
                config=_session_config,
                user_message=ctx.user_message,
                executor_id=self.executor_id,
            )
            await ctx.interaction.append_session_event(_session_id, "session_start", {
                "user_content": user_content if isinstance(user_content, str)
                                else json.dumps(user_content, ensure_ascii=False),
            })
        await ctx.interaction.broadcast(ctx.group_id, {
            "type": "stream_start", "temp_id": temp_id,
            "member_id": bot["id"], "sender_name": bot["name"],
            "sender_type": "bot", "avatar_color": bot["avatar_color"],
        })
        if skills_snapshot:
            await ctx.interaction.broadcast(ctx.group_id, {
                "type": "skills_loaded", "temp_id": temp_id,
                "member_id": bot["id"], "skills": skills_snapshot,
            })
        full_text = ""
        _total_input_tokens = 0
        _total_output_tokens = 0
        _total_cache_read_tokens = 0
        _total_cache_creation_tokens = 0
        _use_cached_mc = compact.should_use_cached_microcompact(provider)

        # Cross-compaction file tracker: path → "read" | "modified"
        _file_tracker: dict[str, str] = {}

        async def _build_reinject() -> str:
            """Combine static workspace context, file-tracker XML, and live file contents."""
            # Point 2: Always fetch fresh context during reinjection
            fresh_prefix, fresh_text = await _get_fresh_context_prefix()
            ft_xml = compact.build_file_tracker_xml(_file_tracker)
            file_contents = compact.build_file_contents_for_reinject(
                _file_tracker, workspace_dir=str(_bot_ws(bot["id"]))
            )
            parts = [p for p in [fresh_text, ft_xml, file_contents] if p]
            return "\n\n".join(parts)


        # Strategy 1 (pre-run): clear stale tool results from DB-loaded history
        messages = compact.apply_tool_result_microcompact(messages)
        # Strategy 2+4 (pre-run): compact if history already over threshold
        _pre_tokens = compact.estimate_tokens(messages)
        if _pre_tokens > compact._PRE_RUN_TOKEN_THRESHOLD:
            messages = await compact.compact_conversation(
                messages, system_prompt, provider, model_name, temperature,
                context_text=await _build_reinject(),
            )
            await ctx.interaction.broadcast(ctx.group_id, {
                "type": "compaction", "temp_id": temp_id,
                "strategy": "pre_run",
                "message": f"历史已预压缩（{_pre_tokens:,} tokens > {compact._PRE_RUN_TOKEN_THRESHOLD:,}）",
            })

        iter_count = 0
        tool_records: list[dict] = []

        async def _stream_final():
            nonlocal full_text, messages, _total_input_tokens, _total_output_tokens, _total_cache_read_tokens, _total_cache_creation_tokens
            _sf_usage: list = []
            try:
                async for chunk in call_ai_stream_messages(
                    system_prompt, messages, provider, model_name, temperature, max_tokens,
                    usage_out=_sf_usage,
                ):
                    full_text += chunk
                    await ctx.interaction.broadcast(ctx.group_id, {
                        "type": "stream_chunk", "temp_id": temp_id, "delta": chunk,
                    })
            except AIContextOverflowError:
                _sf_usage.clear()
                messages = await compact.compact_conversation(
                    messages, system_prompt, provider, model_name, temperature,
                    context_text=await _build_reinject(),
                )
                await ctx.interaction.broadcast(ctx.group_id, {
                    "type": "compaction", "temp_id": temp_id,
                    "strategy": "overflow_recovery",
                    "message": "流式回复溢出，已压缩后重试",
                })
                async for chunk in call_ai_stream_messages(
                    system_prompt, messages, provider, model_name, temperature, max_tokens,
                    usage_out=_sf_usage,
                ):
                    full_text += chunk
                    await ctx.interaction.broadcast(ctx.group_id, {
                        "type": "stream_chunk", "temp_id": temp_id, "delta": chunk,
                    })
            for _u in _sf_usage:
                _total_input_tokens += _u.get("input_tokens", 0)
                _total_output_tokens += _u.get("output_tokens", 0)
                _total_cache_read_tokens += _u.get("cache_read_tokens", 0)
                _total_cache_creation_tokens += _u.get("cache_creation_tokens", 0)

        async def _finalize_reply():
            nonlocal full_text, messages, _total_input_tokens, _total_output_tokens, _total_cache_read_tokens, _total_cache_creation_tokens
            if not bf_config or not bf_config.get("reviewer_prompt"):
                await _stream_final()
                return
            snap = list(messages)
            try:
                gen = await call_ai_once(
                    system_prompt, snap, provider, model_name, temperature, max_tokens
                )
                _gu = gen.get("usage") or {}
                _total_input_tokens += _gu.get("input_tokens", 0)
                _total_output_tokens += _gu.get("output_tokens", 0)
                _total_cache_read_tokens += _gu.get("cache_read_tokens", 0)
                _total_cache_creation_tokens += _gu.get("cache_creation_tokens", 0)
                draft = gen["content"] if gen["type"] == "text" else ""
            except AIContextOverflowError:
                messages = await compact.compact_conversation(
                    messages, system_prompt, provider, model_name, temperature,
                    context_text=await _build_reinject(),
                )
                await ctx.interaction.broadcast(ctx.group_id, {
                    "type": "compaction", "temp_id": temp_id,
                    "strategy": "overflow_recovery",
                    "message": "最终回复溢出，已压缩后重试",
                })
                await _stream_final()
                return
            except Exception:
                await _stream_final()
                return
            _bf_usage: list = []
            approved_text = await _before_finalize_hook(
                draft, snap, system_prompt, bf_config,
                provider, model_name, temperature, max_tokens,
                ctx.interaction, ctx.group_id, temp_id, ctx.user_message,
                usage_out=_bf_usage,
            )
            for _u in _bf_usage:
                _total_input_tokens += _u.get("input_tokens", 0)
                _total_output_tokens += _u.get("output_tokens", 0)
                _total_cache_read_tokens += _u.get("cache_read_tokens", 0)
                _total_cache_creation_tokens += _u.get("cache_creation_tokens", 0)
            full_text = approved_text
            chunk_size = 20
            for i in range(0, len(approved_text), chunk_size):
                await ctx.interaction.broadcast(ctx.group_id, {
                    "type": "stream_chunk", "temp_id": temp_id,
                    "delta": approved_text[i:i+chunk_size],
                })

        try:
            if not tool_schemas:
                # No tools registered yet — pure streaming, same UX as simple_v1
                await _finalize_reply()
            else:
                _rewake_queue: asyncio.Queue = asyncio.Queue()
                execution_ctx = {
                    "bot_id": bot["id"],
                    "group_id": ctx.group_id,
                    "user_message": ctx.user_message,
                    "all_bots": ctx.all_bots,
                    "all_members": ctx.all_members,
                    "spawn_depth": ctx.spawn_depth,
                    "broadcaster": ctx.broadcaster,
                    "ruleset": _ruleset,
                    "steer_channel": ctx.steer_channel,
                    "rewake_queue": _rewake_queue,
                }
                iter_count = 0
                _overflow_recovered = False
                _consecutive_tool_only = 0
                tool_records: list[dict] = []
                _active_schemas = tool_schemas  # may be narrowed by skill allowed-tools
                while iter_count < max_iter:
                    iter_count += 1
                    
                    # Point 2: Dynamic Workspace Mounting
                    # Refresh context prefix every turn so bot sees real-time file changes
                    current_prefix, _ = await _get_fresh_context_prefix()
                    
                    # Update system prompt with fresh prefix if needed, or inject as system/user hint
                    # Here we refresh the system_prompt to include the latest context
                    system_prompt = system_prompt_base + (f"\n\n{current_prefix}" if current_prefix else "")

                    # If previous skill declared allowed-tools, narrow the schema list for this round
                    _skill_allowed = execution_ctx.pop("skill_allowed_tools", None)
                    if _skill_allowed is not None:
                        _active_schemas = [s for s in tool_schemas if s["function"]["name"] in _skill_allowed]
                    else:
                        _active_schemas = tool_schemas
                    # If previous skill declared model, override for this round only
                    _iter_model = execution_ctx.pop("skill_model", None) or model_name
                    # Overflow recovery — detect context overflow, compact, retry once
                    try:
                        result = await call_ai_once(
                            system_prompt, messages, provider, _iter_model,
                            temperature, max_tokens, _active_schemas,
                            use_cached_microcompact=_use_cached_mc,
                        )
                    except AIContextOverflowError:
                        if _overflow_recovered:
                            raise AIError("上下文溢出，压缩后仍失败")
                        _overflow_recovered = True
                        if messages and messages[-1].get("role") == "assistant":
                            messages.pop()
                        messages = await compact.compact_conversation(
                            messages, system_prompt, provider, _iter_model, temperature,
                            context_text=await _build_reinject(),
                        )
                        await ctx.interaction.broadcast(ctx.group_id, {
                            "type": "compaction", "temp_id": temp_id,
                            "strategy": "overflow_recovery",
                            "message": "上下文溢出，已自动压缩并重试",
                        })
                        result = await call_ai_once(
                            system_prompt, messages, provider, _iter_model,
                            temperature, max_tokens, _active_schemas,
                            use_cached_microcompact=_use_cached_mc,
                        )

                    _u = result.get("usage") or {}
                    _total_input_tokens += _u.get("input_tokens", 0)
                    _total_output_tokens += _u.get("output_tokens", 0)
                    _total_cache_read_tokens += _u.get("cache_read_tokens", 0)
                    _total_cache_creation_tokens += _u.get("cache_creation_tokens", 0)
                    await sessions.append_event(_session_id, "llm_response", {
                        "content": result.get("content", ""),
                        "tool_calls": (result.get("assistant_message") or {}).get("tool_calls"),
                        "input_tokens": _u.get("input_tokens", 0),
                        "output_tokens": _u.get("output_tokens", 0),
                    })
                    if (_u.get("input_tokens") or _u.get("output_tokens")
                            or _u.get("cache_read_tokens") or _u.get("cache_creation_tokens")):
                        await sessions.add_tokens(
                            _session_id,
                            input_tokens=_u.get("input_tokens", 0),
                            output_tokens=_u.get("output_tokens", 0),
                            cache_read_tokens=_u.get("cache_read_tokens", 0),
                            cache_creation_tokens=_u.get("cache_creation_tokens", 0),
                        )
                    if result["type"] == "tool_calls":
                        _consecutive_tool_only += 1
                        if _consecutive_tool_only >= _DOOM_LOOP_THRESHOLD:
                            full_text = f"[循环保护] 连续 {_consecutive_tool_only} 次工具调用，已终止循环"
                            break
                        messages.append(result["assistant_message"])
                        
                        # Point 5 & Point 2: Shadow Persistence (Intent)
                        # Save full snapshot BEFORE executing tools
                        await sessions.save_snapshot(_session_id, messages)

                        calls = result["calls"]


                        # Parallel execution when every call in this round is concurrency-safe
                        _run_parallel = (
                            len(calls) > 1
                            and all(tool_executor.is_concurrency_safe(c["name"]) for c in calls)
                        )
                        if _run_parallel:
                            for call in calls:
                                await ctx.interaction.broadcast(ctx.group_id, {
                                    "type": "tool_call", "temp_id": temp_id,
                                    "tool": call["name"], "args": call["arguments"],
                                })
                                # WAL: write tool_call BEFORE parallel execution
                                await ctx.interaction.append_session_event(_session_id, "tool_call", {
                                    "tool_call_id": call["id"],
                                    "tool_name": call["name"],
                                    "arguments": call.get("arguments", {}),
                                })
                            raw_results = await asyncio.gather(*[
                                tool_executor.execute(c["name"], c["arguments"], context=execution_ctx)
                                for c in calls
                            ])
                            for call, tool_result in zip(calls, raw_results):
                                await ctx.interaction.append_session_event(_session_id, "tool_result", {
                                    "tool_call_id": call["id"],
                                    "tool_name": call["name"],
                                    "result": tool_result,
                                    "is_error": False,
                                })
                                _fpath = call["arguments"].get("path", "")
                                if _fpath and call["name"] in compact._FILE_READ_TOOLS:
                                    _file_tracker.setdefault(_fpath, "read")
                                tool_records.append({
                                    "name": call["name"],
                                    "args": call["arguments"],
                                    "result": tool_result,
                                })
                                messages.append({
                                    "role": "tool",
                                    "tool_call_id": call["id"],
                                    "name": call["name"],
                                    "content": tool_result,
                                })
                                # Point 5 & Point 2: Shadow Persistence (Result)
                                await ctx.interaction.save_session_snapshot(_session_id, messages)
                                
                                await ctx.interaction.broadcast(ctx.group_id, {
                                    "type": "tool_result", "temp_id": temp_id,
                                    "tool": call["name"], "result": tool_result[:300],
                                })
                        else:
                            # Serial execution — full post-processing per call
                            # (skill side-effects, draft sentinel, fork, etc.)
                            for call in calls:
                                await ctx.interaction.broadcast(ctx.group_id, {
                                    "type": "tool_call", "temp_id": temp_id,
                                    "tool": call["name"], "args": call["arguments"],
                                })
                                await ctx.interaction.append_session_event(_session_id, "tool_call", {
                                    "tool_call_id": call["id"],
                                    "tool_name": call["name"],
                                    "arguments": call.get("arguments", {}),
                                })
                                tool_result = await tool_executor.execute(
                                    call["name"], call["arguments"], context=execution_ctx
                                )
                                await ctx.interaction.append_session_event(_session_id, "tool_result", {
                                    "tool_call_id": call["id"],
                                    "tool_name": call["name"],
                                    "result": tool_result,
                                    "is_error": False,
                                })
                                # Track file reads/writes for cross-compaction persistence
                                _fpath = call["arguments"].get("path", "")
                                if _fpath:
                                    if call["name"] in compact._FILE_WRITE_TOOLS:
                                        _file_tracker[_fpath] = "modified"
                                    elif call["name"] in compact._FILE_READ_TOOLS:
                                        _file_tracker.setdefault(_fpath, "read")
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
                                                f"使用 standard frontmatter（name/description/layer: learned/status: draft）。"
                                            ),
                                        })
                                    # context: fork → run skill in isolated sub-agent AI call
                                    if tool_result == "__SKILL_FORK__":
                                        fork_info = execution_ctx.pop("skill_fork", {})
                                        fork_name = fork_info.get("name", "unknown")
                                        await ctx.interaction.broadcast(ctx.group_id, {
                                            "type": "skill_fork_start", "temp_id": temp_id,
                                            "member_id": bot["id"], "skill_name": fork_name,
                                        })
                                        fork_task = fork_info.get("args") or execution_ctx.get("user_message", "")
                                        fork_allowed = fork_info.get("allowed_tools", [])
                                        fork_schemas = (
                                            [s for s in tool_schemas if s["function"]["name"] in fork_allowed]
                                            if fork_allowed else None
                                        )
                                        fork_model = fork_info.get("model") or model_name
                                        _fork_usage: list = []
                                        child_sid = str(uuid.uuid4())
                                        await ctx.interaction.append_session_event(_session_id, "child_fork", {
                                            "child_session_id": child_sid,
                                            "skill_name": fork_name,
                                        })
                                        tool_result = await _run_fork_skill(
                                            fork_info.get("content", ""),
                                            fork_task,
                                            provider, fork_model, temperature,
                                            tool_schemas=fork_schemas,
                                            usage_out=_fork_usage,
                                        )
                                        await ctx.interaction.append_session_event(_session_id, "child_join", {
                                            "child_session_id": child_sid,
                                            "skill_name": fork_name,
                                            "result": tool_result,
                                        })
                                        for _u in _fork_usage:
                                            _total_input_tokens += _u.get("input_tokens", 0)
                                            _total_output_tokens += _u.get("output_tokens", 0)
                                            _total_cache_read_tokens += _u.get("cache_read_tokens", 0)
                                            _total_cache_creation_tokens += _u.get("cache_creation_tokens", 0)
                                        await ctx.interaction.broadcast(ctx.group_id, {
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
                                    await ctx.interaction.broadcast(ctx.group_id, {
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
                                # Point 5 & Point 2: Shadow Persistence (Result)
                                await ctx.interaction.save_session_snapshot(_session_id, messages)

                                await ctx.interaction.broadcast(ctx.group_id, {
                                    "type": "tool_result", "temp_id": temp_id,
                                    "tool": call["name"], "result": display_result[:300],
                                })
                        # Post-tool-round: 3-strategy compaction pipeline
                        # Strategy 1: clear stale tool results (count-based)
                        messages = compact.apply_tool_result_microcompact(messages)
                        # Strategy 2: snip oldest user/assistant pair
                        messages, _ = compact.snip_if_needed(messages, model_name)
                        # Strategies 3+4: AI compaction if still over threshold
                        messages, _ = await compact.auto_compact_if_needed(
                            messages, model_name, ctx.group_id,
                            system_prompt, provider, temperature,
                            ctx.broadcaster, temp_id, bot["id"],
                            context_text=await _build_reinject(),
                        )
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
                        # Drain rewake messages from asyncRewake hooks
                        if not _rewake_queue.empty():
                            rewakes = []
                            while not _rewake_queue.empty():
                                rewakes.append(_rewake_queue.get_nowait())
                            rewake_text = "\n".join(rewakes)
                            messages.append({"role": "user", "content": f"[系统唤醒] {rewake_text}"})
                            await ctx.broadcaster.broadcast(ctx.group_id, {
                                "type": "rewake_injected", "temp_id": temp_id,
                                "member_id": bot["id"], "message": rewake_text[:300],
                            })
                    else:
                        _consecutive_tool_only = 0
                        # Tools resolved — stream the final answer properly
                        await _finalize_reply()
                        break
                if not full_text:
                    full_text = "[达到最大工具调用次数，任务未完成]"

        except asyncio.CancelledError:
            await sessions.update_session_status(_session_id, "failed")
            await ctx.broadcaster.broadcast(ctx.group_id, {
                "type": "stream_aborted", "temp_id": temp_id, "member_id": bot["id"],
            })
            raise
        except AIError as e:
            await sessions.update_session_status(_session_id, "failed")
            await ctx.broadcaster.broadcast(ctx.group_id, {
                "type": "stream_error", "temp_id": temp_id, "message": str(e),
            })
            return ExecutionResult(full_text="", msg_id=None)

        # Sub-agents: close the stream animation then return without DB/memory ops
        if ctx.spawn_depth > 0:
            await ctx.interaction.broadcast(ctx.group_id, {
                "type": "stream_end", "temp_id": temp_id, "id": None,
                "member_id": bot["id"], "sender_name": bot["name"],
                "preview": full_text[:100], "created_at": "",
            })
            await ctx.interaction.update_session_status(_session_id, "completed")
            return ExecutionResult(full_text=full_text, msg_id=None)

        msg_id = await ctx.interaction.save_message(
            ctx.group_id, bot["id"], full_text,
            input_tokens=_total_input_tokens or None,
            output_tokens=_total_output_tokens or None,
            cache_read_tokens=_total_cache_read_tokens or None,
            cache_creation_tokens=_total_cache_creation_tokens or None,
        )
        # Use get_messages from DB only for final broadcast timestamp if needed, 
        # or simplify interaction.save_message to return it. 
        # For now, we assume save_message handles DB context.
        recent = [] # Simplified as we already saved it

        await ctx.interaction.broadcast(ctx.group_id, {
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
        asyncio.create_task(compact.maybe_compact_db_history(
            ctx.group_id, bot["id"], provider, model_name, temperature, ctx.interaction
        ))
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

        await ctx.interaction.update_session_status(_session_id, "completed")
        return ExecutionResult(full_text=full_text, msg_id=msg_id)
