import asyncio
import uuid
import sys

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
from database import get_db, save_message, get_messages
from ai_client import call_ai_once, call_ai_stream_messages, AIError, AIContextOverflowError
from memory import get_memory_context, add_to_chroma, maybe_summarize
from role_router import build_context_message
from workspace import load_context_files, format_context_blocks, append_log, archive_run
from skills import list_skills_all, load_always_skills, filter_skills_by_context
from skills.constants import bot_ws as _bot_ws
import executors.compact as compact

_DOOM_LOOP_THRESHOLD = 5  # breaks on the Nth consecutive tool-only iteration (inclusive)


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
    tool_schemas: list | None = None,
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
        _use_cached_mc = compact.should_use_cached_microcompact(provider)

        # Cross-compaction file tracker: path → "read" | "modified"
        _file_tracker: dict[str, str] = {}

        def _build_reinject() -> str:
            """Combine static workspace context, file-tracker XML, and live file contents."""
            ft_xml = compact.build_file_tracker_xml(_file_tracker)
            file_contents = compact.build_file_contents_for_reinject(
                _file_tracker, workspace_dir=str(_bot_ws(bot["id"]))
            )
            parts = [p for p in [context_text, ft_xml, file_contents] if p]
            return "\n\n".join(parts)

        # Strategy 1 (pre-run): clear stale tool results from DB-loaded history
        messages = compact.apply_tool_result_microcompact(messages)
        # Strategy 2+4 (pre-run): compact if history already over threshold
        _pre_tokens = compact.estimate_tokens(messages)
        if _pre_tokens > compact._PRE_RUN_TOKEN_THRESHOLD:
            messages = await compact.compact_conversation(
                messages, system_prompt, provider, model_name, temperature,
                context_text=_build_reinject(),
            )
            await ctx.broadcaster.broadcast(ctx.group_id, {
                "type": "compaction", "temp_id": temp_id,
                "strategy": "pre_run",
                "message": f"历史已预压缩（{_pre_tokens:,} tokens > {compact._PRE_RUN_TOKEN_THRESHOLD:,}）",
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
                messages = await compact.compact_conversation(
                    messages, system_prompt, provider, model_name, temperature,
                    context_text=_build_reinject(),
                )
                await ctx.broadcaster.broadcast(ctx.group_id, {
                    "type": "compaction", "temp_id": temp_id,
                    "strategy": "overflow_recovery",
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
                messages = await compact.compact_conversation(
                    messages, system_prompt, provider, model_name, temperature,
                    context_text=_build_reinject(),
                )
                await ctx.broadcaster.broadcast(ctx.group_id, {
                    "type": "compaction", "temp_id": temp_id,
                    "strategy": "overflow_recovery",
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
                _consecutive_tool_only = 0
                tool_records: list[dict] = []
                _active_schemas = tool_schemas  # may be narrowed by skill allowed-tools
                while iter_count < max_iter:
                    iter_count += 1
                    # If previous skill declared allowed-tools, narrow the schema list for this round
                    _skill_allowed = execution_ctx.pop("skill_allowed_tools", None)
                    if _skill_allowed is not None:
                        _active_schemas = [s for s in tool_schemas if s.get("name") in _skill_allowed] or tool_schemas
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
                            context_text=_build_reinject(),
                        )
                        await ctx.broadcaster.broadcast(ctx.group_id, {
                            "type": "compaction", "temp_id": temp_id,
                            "strategy": "overflow_recovery",
                            "message": "上下文溢出，已自动压缩并重试",
                        })
                        result = await call_ai_once(
                            system_prompt, messages, provider, _iter_model,
                            temperature, max_tokens, _active_schemas,
                            use_cached_microcompact=_use_cached_mc,
                        )
                    if result["type"] == "tool_calls":
                        _consecutive_tool_only += 1
                        if _consecutive_tool_only >= _DOOM_LOOP_THRESHOLD:
                            full_text = f"[循环保护] 连续 {_consecutive_tool_only} 次工具调用，已终止循环"
                            break
                        messages.append(result["assistant_message"])
                        for call in result["calls"]:
                            await ctx.broadcaster.broadcast(ctx.group_id, {
                                "type": "tool_call", "temp_id": temp_id,
                                "tool": call["name"], "args": call["arguments"],
                            })
                            tool_result = await tool_executor.execute(
                                call["name"], call["arguments"], context=execution_ctx
                            )
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
                                    fork_allowed = fork_info.get("allowed_tools", [])
                                    fork_schemas = (
                                        [s for s in tool_schemas if s.get("name") in fork_allowed]
                                        if fork_allowed else None
                                    )
                                    fork_model = fork_info.get("model") or model_name
                                    tool_result = await _run_fork_skill(
                                        fork_info.get("content", ""),
                                        fork_task,
                                        provider, fork_model, temperature,
                                        tool_schemas=fork_schemas,
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
                            context_text=_build_reinject(),
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
                    else:
                        _consecutive_tool_only = 0
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
        asyncio.create_task(compact.maybe_compact_db_history(
            ctx.group_id, bot["id"], provider, model_name, temperature, ctx.broadcaster
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

        return ExecutionResult(full_text=full_text, msg_id=msg_id)
