import asyncio
import uuid
import sys
import json
import logging
import aiosqlite

from executors.base import build_group_section, ExecutionResult
from core import config
import permissions
from ai.client import call_ai_once, AIError
from ai.memory_provider import get_memory_provider, MemoryContext, MemoryEvent
from core.role_router import build_context_message, build_image_content
from workspace import load_context_files, format_context_blocks, append_log, archive_run
import workspace as _ws
from skills.traits import load_traits
from skills.constants import bot_ws as _bot_ws
import executors.compact as compact
from core import bg
from bus import bus
from bus.events import CompactionTriggered
from core.orchestration.ai_service import AIService
from core.orchestration import prompt_builder
from ai.model_limits import resolve_max_tokens
from executors.plugins.workspace_tools import _IS_WINDOWS
from executors.tool_dispatch import execute_tool_call as _execute_tool_call

logger = logging.getLogger(__name__)
_DOOM_LOOP_THRESHOLD = config.DOOM_LOOP_THRESHOLD


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
    """Minimal tool-calling loop used by tests (no broadcaster, no compaction, no DB)."""
    import executors.plugins.tool_loop_v1 as tool_loop_v1
    messages = list(messages)
    iter_count = 0
    tool_calls_history = []
    while iter_count < max_iter:
        iter_count += 1
        result = await tool_loop_v1.call_ai_once(
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
                tool_result = await tool_loop_v1._execute_tool_call(
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
    """Quality gate before finalizing a reply."""
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
    """Execute a fork skill in an isolated single AI call."""
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


async def setup_session(runner) -> None:
    # Build ruleset
    if runner.ctx.ruleset is not None:
        runner.ruleset = runner.ctx.ruleset
    else:
        perm_mode = (runner.bot.get("executor_config") or {}).get("permission_mode", "default")
        try:
            db_rules = await permissions.load_rules(runner.bot["id"])
        except aiosqlite.OperationalError:
            db_rules = []
        runner.ruleset = permissions.Ruleset(rules=db_rules, mode=perm_mode)

    history, user_msg = build_context_message(
        runner.ctx.user_message, runner.ctx.sender["name"], runner.ctx.history, is_workflow=runner.ctx.is_workflow
    )
    import core.workflow as _wf
    thread_id = _wf.current_thread_id(runner.ctx.group_id)
    memory = await runner.memory.recall(MemoryContext(
        bot_id=runner.bot["id"], group_id=runner.ctx.group_id,
        role=runner.bot.get("role") or "", query=runner.ctx.user_message, history=runner.ctx.history,
        thread_id=thread_id,
    ))

    if runner.executor.manifest.workspace.skill_discovery:
        runner.system_prompt_base, runner.skills_xml, runner.skills_snapshot, runner.always_skills = await prompt_builder.compile_system_prompt(
            runner.bot, runner.ctx, runner.model_name, memory
        )
    else:
        from workspace.layout import get_group_language
        lang = get_group_language(runner.ctx.group_id)
        runner.system_prompt_base = prompt_builder.build_system_prompt_base(
            runner.bot, runner.ctx, memory, always_section="", lang=lang
        )

    # Group workspace context: unconditionally injected for all group bots.
    if runner.ctx.group_id is not None:
        group_ctx = await _ws.load_group_context(runner.ctx.group_id)
        if group_ctx:
            runner.system_prompt_base += f"\n\n{group_ctx}"

    runner.system_prompt = runner.system_prompt_base

    user_content = build_image_content(user_msg, runner.ctx.file_url, runner.ctx.file_type, runner.provider)
    if isinstance(user_content, str):
        user_content, _ = compact.truncate_user_message(user_content, runner.ctx.group_id, runner.model_name)
    elif isinstance(user_content, list):
        for block in user_content:
            if isinstance(block, dict) and block.get("type") == "text":
                truncated_text, _ = compact.truncate_user_message(block.get("text", ""), runner.ctx.group_id, runner.model_name)
                block["text"] = truncated_text
    
    _resuming = bool(runner.ctx.resume_session_id)
    if _resuming:
        resumed = list(runner.ctx.resume_messages or [])
        if resumed and resumed[0].get("role") == "system":
            resumed = resumed[1:]
        runner.messages = resumed
    else:
        runner.messages = list(history) + [{"role": "user", "content": user_content}]

    tool_names = [t.name for t in runner.executor.manifest.tools]
    if not runner.executor.manifest.workspace.skill_discovery:
        tool_names = [name for name in tool_names if name != "run_skill"]
    from executors.tool_router import router as _tool_router
    if _tool_router.has_providers():
        from executors import tool_executor
        builtin_schemas = tool_executor.get_schemas(tool_names)
        builtin_names = {b["function"]["name"] for b in builtin_schemas}
        mcp_schemas = [
            s for s in _tool_router.get_external_schemas()
            if s["function"]["name"] not in builtin_names
        ]
        _mcp_vis = (runner.bot.get("executor_config") or {}).get("mcp") or {}
        mcp_schemas = prompt_builder.filter_mcp_schemas(
            mcp_schemas, _mcp_vis.get("allow"), _mcp_vis.get("block"))
        mcp_schemas, deferred_names = prompt_builder.apply_external_schema_budget(mcp_schemas)
        if deferred_names:
            logger.warning(
                "tool schema budget: deferred %d MCP tool(s): %s",
                len(deferred_names), deferred_names,
            )
            _budget_note = prompt_builder.build_budget_note(deferred_names, runner.ctx.group_id)
            runner.system_prompt_base += _budget_note
            runner.system_prompt += _budget_note
        runner.tool_schemas = builtin_schemas + mcp_schemas
    else:
        from executors import tool_executor
        runner.tool_schemas = tool_executor.get_schemas(tool_names)
    runner.tool_schemas = prompt_builder.restrict_schemas(runner.tool_schemas, runner.bot.get("allowed_tools"))

    if _resuming:
        await runner.ctx.interaction.update_session_status(runner.session_id, "running")
    else:
        _session_config = {
            "system_prompt": runner.system_prompt,
            "provider": runner.provider,
            "model_name": runner.model_name,
            "temperature": runner.temperature,
            "max_tokens": runner.max_tokens,
        }
        await runner.ctx.interaction.create_session(
            session_id=runner.session_id,
            bot_id=runner.bot["id"],
            group_id=runner.ctx.group_id,
            config=_session_config,
            user_message=runner.ctx.user_message,
            executor_id=runner.executor.executor_id,
        )
        await runner.ctx.interaction.append_session_event(runner.session_id, "session_start", {
            "user_content": user_content if isinstance(user_content, str) else json.dumps(user_content, ensure_ascii=False),
        })
        
    await runner.ctx.interaction.broadcast(runner.ctx.group_id, {
        "type": "stream_start", "temp_id": runner.temp_id,
        "member_id": runner.bot["id"], "sender_name": runner.bot["name"],
        "sender_type": "bot", "avatar_color": runner.bot["avatar_color"],
        "session_id": runner.session_id,
    })
    if runner.skills_snapshot:
        await runner.ctx.interaction.broadcast(runner.ctx.group_id, {
            "type": "skills_loaded", "temp_id": runner.temp_id,
            "member_id": runner.bot["id"], "skills": runner.skills_snapshot,
        })


async def run_pre_compaction(runner) -> None:
    runner.messages = compact.apply_tool_result_microcompact(runner.messages)
    _pre_tokens = compact.estimate_tokens(runner.messages)
    if _pre_tokens > compact._PRE_RUN_TOKEN_THRESHOLD:
        runner.messages = await compact.compact_conversation(
            runner.messages, runner.system_prompt, runner.provider, runner.model_name, runner.temperature,
            context_text=await runner._build_reinject(),
        )
        await runner.ctx.interaction.broadcast(runner.ctx.group_id, {
            "type": "compaction", "temp_id": runner.temp_id,
            "strategy": "pre_run",
            "message": f"历史已预压缩（{_pre_tokens:,} tokens > {compact._PRE_RUN_TOKEN_THRESHOLD:,}）",
            "session_id": runner.session_id,
        })


async def poll_and_inject_signals(runner) -> None:
    if runner.ctx.steer_channel and not runner.ctx.steer_channel.empty():
        steers = []
        while not runner.ctx.steer_channel.empty():
            steers.append(runner.ctx.steer_channel.get_nowait())
        steer_text = "\n".join(steers)
        runner.messages.append({"role": "user", "content": f"[用户中途指令] {steer_text}"})
        await runner.ctx.interaction.broadcast(runner.ctx.group_id, {
            "type": "steer_injected", "temp_id": runner.temp_id,
            "member_id": runner.bot["id"], "message": steer_text[:300],
        })
        
    if not runner.rewake_queue.empty():
        rewakes = []
        while not runner.rewake_queue.empty():
            rewakes.append(runner.rewake_queue.get_nowait())
        rewake_text = "\n".join(rewakes)
        runner.messages.append({"role": "user", "content": f"[系统唤醒] {rewake_text}"})
        await runner.ctx.interaction.broadcast(runner.ctx.group_id, {
            "type": "rewake_injected", "temp_id": runner.temp_id,
            "member_id": runner.bot["id"], "message": rewake_text[:300],
        })


async def _stream_final(runner) -> None:
    if runner.full_text:
        chunk_size = 20
        for i in range(0, len(runner.full_text), chunk_size):
            chunk = runner.full_text[i:i+chunk_size]
            await runner.ctx.interaction.broadcast(runner.ctx.group_id, {
                "type": "stream_chunk", "temp_id": runner.temp_id, "delta": chunk,
                "session_id": runner.session_id,
            })
            await asyncio.sleep(0.02)
        return

    try:
        async for chunk in runner.ai_service.stream(
            runner.system_prompt, runner.messages, runner.model_name, runner.provider,
            runner.temperature, runner.max_tokens, reinject_fn=runner._build_reinject
        ):
            runner.full_text += chunk
            await runner.ctx.interaction.broadcast(runner.ctx.group_id, {
                "type": "stream_chunk", "temp_id": runner.temp_id, "delta": chunk,
                "session_id": runner.session_id,
            })
    except AIError as e:
        await runner.ctx.interaction.broadcast(runner.ctx.group_id, {
            "type": "stream_error", "temp_id": runner.temp_id, "message": str(e),
            "session_id": runner.session_id,
        })


async def finalize_reply(runner) -> None:
    if not runner.bf_config or not runner.bf_config.get("reviewer_prompt"):
        await _stream_final(runner)
        return
    snap = list(runner.messages)
    try:
        gen = await runner.ai_service.call(
            runner.system_prompt, snap, runner.model_name, runner.provider,
            runner.temperature, runner.max_tokens, reinject_fn=runner._build_reinject
        )
        draft = gen["content"] if gen["type"] == "text" else ""
    except Exception:
        await _stream_final(runner)
        return

    approved_text = await _before_finalize_hook(
        draft, snap, runner.system_prompt, runner.bf_config,
        runner.provider, runner.model_name, runner.temperature, runner.max_tokens,
        runner.ai_service, runner.ctx.user_message,
    )
    runner.full_text = approved_text
    chunk_size = 20
    for i in range(0, len(approved_text), chunk_size):
        await runner.ctx.interaction.broadcast(runner.ctx.group_id, {
            "type": "stream_chunk", "temp_id": runner.temp_id,
            "delta": approved_text[i:i+chunk_size],
            "session_id": runner.session_id,
        })


async def cleanup_and_finalize(runner) -> ExecutionResult:
    # Extract structured signals from tool calls in assistant messages
    signals = []
    for msg in runner.messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                func = tc.get("function", {})
                name = func.get("name")
                if name in ("signal_stage_done", "signal_rework"):
                    try:
                        args = json.loads(func.get("arguments") or "{}") if isinstance(func.get("arguments"), str) else func.get("arguments", {})
                    except Exception:
                        args = {}
                    signals.append({
                        "name": name,
                        "arguments": args
                    })

    if runner.ctx.spawn_depth > 0:
        await runner.ctx.interaction.broadcast(runner.ctx.group_id, {
            "type": "stream_end", "temp_id": runner.temp_id, "id": None,
            "member_id": runner.bot["id"], "sender_name": runner.bot["name"],
            "preview": runner.full_text[:100], "created_at": "",
            "session_id": runner.session_id,
        })
        runner.messages.append({"role": "assistant", "content": runner.full_text})
        await runner.ctx.interaction.save_session_snapshot(runner.session_id, runner.messages)
        await runner.ctx.interaction.update_session_status(runner.session_id, "completed")
        return ExecutionResult(full_text=runner.full_text, msg_id=None, signals=signals)

    msg_id = await runner.ctx.interaction.save_message(
        runner.ctx.group_id, runner.bot["id"], runner.full_text,
        input_tokens=runner.ai_service.usage.input_tokens or None,
        output_tokens=runner.ai_service.usage.output_tokens or None,
        cache_read_tokens=runner.ai_service.usage.cache_read_tokens or None,
        cache_creation_tokens=runner.ai_service.usage.cache_creation_tokens or None,
    )

    await runner.ctx.interaction.broadcast(runner.ctx.group_id, {
        "type": "stream_end", "temp_id": runner.temp_id, "id": msg_id,
        "member_id": runner.bot["id"], "sender_name": runner.bot["name"],
        "preview": runner.full_text[:100],
        "created_at": "",
        "session_id": runner.session_id,
    })

    tool_names_called = [
        m["name"] for m in runner.messages
        if m.get("role") == "tool" and m.get("name")
    ]
    
    import core.workflow as _wf
    bg.spawn(runner.memory.observe(MemoryEvent(
        bot_id=runner.bot["id"], group_id=runner.ctx.group_id,
        role=runner.bot.get("role") or "", bot_name=runner.bot["name"],
        message_id=msg_id, text=runner.full_text,
        provider=runner.provider, model=runner.model_name,
        thread_id=_wf.current_thread_id(runner.ctx.group_id),
    )))
    bg.spawn(bus.publish(CompactionTriggered(
        group_id=runner.ctx.group_id,
        bot_id=runner.bot["id"],
        provider=runner.provider,
        model_name=runner.model_name,
        temperature=runner.temperature
    )))
    import executors.plugins.tool_loop_v1 as tool_loop_v1
    bg.spawn(tool_loop_v1.append_log(
        runner.bot["id"], runner.full_text,
        user_message=runner.ctx.user_message,
        sender_name=runner.ctx.sender.get("name", ""),
        tool_calls=tool_names_called,
        iterations=runner.iter_count if tool_names_called else 0,
        executor=runner.executor.executor_id,
        group_id=runner.ctx.group_id,
    ))
    if runner.ctx.group_id and runner.tool_records:
        bg.spawn(tool_loop_v1.archive_run(
            runner.ctx.group_id, runner.temp_id, runner.bot,
            user_message=runner.ctx.user_message,
            sender_name=runner.ctx.sender.get("name", ""),
            tool_records=runner.tool_records,
            reply=runner.full_text,
            iterations=runner.iter_count,
            model=runner.model_name,
            executor=runner.executor.executor_id,
        ))


    runner.messages.append({"role": "assistant", "content": runner.full_text})
    await runner.ctx.interaction.save_session_snapshot(runner.session_id, runner.messages)
    await runner.ctx.interaction.update_session_status(runner.session_id, "completed")
    return ExecutionResult(full_text=runner.full_text, msg_id=msg_id, signals=signals)


async def execute_parallel_tools(runner, calls, iteration=None) -> None:
    _iter = iteration or runner.iter_count
    for call in calls:
        await runner.ctx.interaction.broadcast(runner.ctx.group_id, {
            "type": "tool_call", "temp_id": runner.temp_id,
            "tool": call["name"], "args": call["arguments"],
            "session_id": runner.session_id,
        })
        await runner.ctx.interaction.broadcast(runner.ctx.group_id, {
            "type": "tool_progress_start", "temp_id": runner.temp_id,
            "call_id": call["id"], "tool_name": call["name"],
            "tool_args": call["arguments"], "iteration": _iter,
        })
        await runner.ctx.interaction.append_session_event(runner.session_id, "tool_call", {
            "tool_call_id": call["id"],
            "tool_name": call["name"],
            "arguments": call.get("arguments", {}),
        })

    _t0 = asyncio.get_event_loop().time()
    from executors.tool_dispatch import dispatch_tool
    raw_results = await asyncio.gather(*[
        dispatch_tool(c["name"], c["arguments"], runner.execution_ctx)
        for c in calls
    ])
    _duration = round(asyncio.get_event_loop().time() - _t0, 2)

    for call, (tool_result, is_error) in zip(calls, raw_results):
        await runner.ctx.interaction.append_session_event(runner.session_id, "tool_result", {
            "tool_call_id": call["id"],
            "tool_name": call["name"],
            "result": tool_result,
            "is_error": is_error,
        })
        runner._track_vfs_modifications(call["name"], call["arguments"])

        display_result, truncated_path = compact.truncate_tool_result(call["name"], tool_result, runner.ctx.group_id, runner.model_name)

        runner.tool_records.append({
            "name": call["name"],
            "args": call["arguments"],
            "result": display_result,
        })
        runner.messages.append({
            "role": "tool",
            "tool_call_id": call["id"],
            "name": call["name"],
            "content": display_result,
        })
        await runner.ctx.interaction.save_session_snapshot(runner.session_id, runner.messages)
        await runner.ctx.interaction.broadcast(runner.ctx.group_id, {
            "type": "tool_result", "temp_id": runner.temp_id,
            "tool": call["name"], "result": display_result[:300],
            "session_id": runner.session_id,
        })
        await runner.ctx.interaction.broadcast(runner.ctx.group_id, {
            "type": "tool_progress_end", "temp_id": runner.temp_id,
            "call_id": call["id"], "tool_name": call["name"],
            "duration_sec": _duration,
            "result": display_result[:800],
            "is_error": is_error,
        })


async def execute_serial_tools(runner, calls, iteration=None) -> None:
    _iter = iteration or runner.iter_count
    for call in calls:
        await runner.ctx.interaction.broadcast(runner.ctx.group_id, {
            "type": "tool_call", "temp_id": runner.temp_id,
            "tool": call["name"], "args": call["arguments"],
            "session_id": runner.session_id,
        })
        await runner.ctx.interaction.broadcast(runner.ctx.group_id, {
            "type": "tool_progress_start", "temp_id": runner.temp_id,
            "call_id": call["id"], "tool_name": call["name"],
            "tool_args": call["arguments"], "iteration": _iter,
        })
        await runner.ctx.interaction.append_session_event(runner.session_id, "tool_call", {
            "tool_call_id": call["id"],
            "tool_name": call["name"],
            "arguments": call.get("arguments", {}),
        })

        _t0 = asyncio.get_event_loop().time()
        from executors.tool_dispatch import dispatch_tool
        tool_result, is_error = await dispatch_tool(
            call["name"], call["arguments"], runner.execution_ctx
        )
        _duration = round(asyncio.get_event_loop().time() - _t0, 2)

        await runner.ctx.interaction.append_session_event(runner.session_id, "tool_result", {
            "tool_call_id": call["id"],
            "tool_name": call["name"],
            "result": tool_result,
            "is_error": is_error,
        })

        runner._track_vfs_modifications(call["name"], call["arguments"])

        if call["name"] == "run_skill":
            tool_result = await runner._handle_run_skill_result(tool_result)

        display_result = tool_result
        if call["name"] == "write_file" and tool_result.startswith("__DRAFT_WRITTEN__:"):
            skill_name = tool_result.split(":", 1)[1]
            display_result = f"已写入草稿技能「{skill_name}」，等待用户审批后生效。"
            await runner.ctx.interaction.broadcast(runner.ctx.group_id, {
                "type": "skill_draft_added",
                "member_id": runner.bot["id"],
                "skill_name": skill_name,
                "message": f"{runner.bot['name']} 写入了新的自学技能「{skill_name}」，请在 Skill 管理面板审批。",
            })

        display_result, truncated_path = compact.truncate_tool_result(call["name"], display_result, runner.ctx.group_id, runner.model_name)

        runner.tool_records.append({
            "name": call["name"],
            "args": call["arguments"],
            "result": display_result,
        })
        runner.messages.append({
            "role": "tool",
            "tool_call_id": call["id"],
            "name": call["name"],
            "content": display_result,
        })

        await runner.ctx.interaction.save_session_snapshot(runner.session_id, runner.messages)
        await runner.ctx.interaction.broadcast(runner.ctx.group_id, {
            "type": "tool_result", "temp_id": runner.temp_id,
            "tool": call["name"], "result": display_result[:300],
            "session_id": runner.session_id,
        })
        await runner.ctx.interaction.broadcast(runner.ctx.group_id, {
            "type": "tool_progress_end", "temp_id": runner.temp_id,
            "call_id": call["id"], "tool_name": call["name"],
            "duration_sec": _duration,
            "result": display_result[:800],
            "is_error": is_error,
        })


THINKING_I18N = {
    "zh": {
        "templates": [
            "分析用户需求和当前任务状态...",
            "检查之前的执行记录...",
            "评估可用的工具和方法...",
            "制定下一步执行计划...",
            "确认工具调用的参数和预期结果...",
            "验证上一步的执行结果...",
            "整理最终回复内容...",
        ],
        "iter_1": "iteration {iter_count}: {template_0}",
        "iter_2": "iteration {iter_count}: 上一步完成了 {completed}，需要继续...",
        "iter_2_fallback": "初步分析",
        "iter_other": "iteration {iter_count}: 继续执行剩余任务，整合结果..."
    },
    "en": {
        "templates": [
            "Analyzing user requirements and current task status...",
            "Checking previous execution history...",
            "Evaluating available tools and methods...",
            "Formulating next step plan...",
            "Confirming tool call parameters and expected results...",
            "Verifying previous execution results...",
            "Formatting final response content...",
        ],
        "iter_1": "iteration {iter_count}: {template_0}",
        "iter_2": "iteration {iter_count}: Previous step completed {completed}, continuing...",
        "iter_2_fallback": "initial analysis",
        "iter_other": "iteration {iter_count}: Continuing with remaining tasks, consolidating results..."
    }
}


def generate_thinking_preview(runner, iter_count: int) -> str:
    from workspace.layout import get_group_language
    lang = get_group_language(runner.ctx.group_id)
    L = THINKING_I18N.get(lang, THINKING_I18N["zh"])
    
    tool_names = [rec["name"] for rec in runner.tool_records[-3:]] if runner.tool_records else []
    
    if iter_count == 1:
        return L["iter_1"].format(iter_count=iter_count, template_0=L["templates"][0])
    elif iter_count == 2:
        completed = ', '.join(tool_names[:2]) if tool_names else L["iter_2_fallback"]
        return L["iter_2"].format(iter_count=iter_count, completed=completed)
    else:
        return L["iter_other"].format(iter_count=iter_count)



async def get_fresh_context_prefix(runner) -> tuple[str, str]:
    from core.orchestration import prompt_builder
    return await prompt_builder.get_fresh_context_prefix(
        runner.bot["id"],
        runner.ctx.group_id,
        runner.executor.manifest.workspace.startup_files,
        runner.skills_xml
    )


async def build_reinject(runner) -> str:
    fresh_prefix, _ = await runner._get_fresh_context_prefix()
    ft_xml = compact.build_file_tracker_xml(runner.file_tracker)
    file_contents = compact.build_file_contents_for_reinject(
        runner.file_tracker, workspace_dir=str(_bot_ws(runner.bot["id"], runner.ctx.group_id))
    )
    parts = [p for p in [fresh_prefix, ft_xml, file_contents] if p]
    return "\n\n".join(parts)

