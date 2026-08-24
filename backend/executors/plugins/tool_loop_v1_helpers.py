import asyncio
import uuid
import sys
import re
import json
import logging
from typing import Any
import aiosqlite

from executors.base import build_group_section, ExecutionResult
from core import config
import permissions
from ai.client import call_ai_once, AIError
from memory.contracts import (AssembleCase, CompleteExperienceUsage, CompleteSkillUsage,
                              FormatProjectedContext, ObserveMemory,
                              ProcessLearningCase, RecallExperiences,
                              RecallGroupFacts, RecallMemory, RecallSkills,
                              ResolveLearningRefs)
from memory.domain import MemoryScope, Principal
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
from executors.plugins.tool_loop_context import drop_oldest_message_group
from executors.plugins.tool_loop_budget import apply_memory_context_budget, enforce_final_context_budget
from executors.plugins.tool_loop_retry import inject_failure_insight, maybe_autogen_retry
from executors.plugins.tool_loop_provenance import tool_evidence_links, context_evidence_links
from executors.plugins.tool_loop_prompt import UNTRUSTED_LEARNING_POLICY, attach_untrusted_learning_data
from executors.plugins.tool_loop_usage import accumulate_usage
from executors.plugins.tool_loop_fork_skill import run_fork_skill as _run_fork_skill_impl
from executors.plugins.tool_loop_signals import extract_completion_signals
from executors.tool_dispatch import execute_tool_call as _execute_tool_call
from executors.plugins.tool_loop_primitives import tool_loop_core as _tool_loop_core_impl
from executors.plugins.tool_loop_primitives import before_finalize_hook as _before_finalize_hook_impl
from executors.plugins.tool_loop_session_persistence import persist_session_start
from executors.plugins.tool_loop_schema_assembly import assemble_tool_schemas
from executors.plugins.tool_loop_memory_hydration import hydrate_memory_context

logger = logging.getLogger(__name__)
_DOOM_LOOP_THRESHOLD = config.DOOM_LOOP_THRESHOLD
_UNTRUSTED_LEARNING_POLICY = UNTRUSTED_LEARNING_POLICY


def _get_helper(name: str, default: Any) -> Any:
    mod = sys.modules.get("executors.plugins.tool_loop_v1")
    return getattr(mod, name, default) if mod else default


_attach_untrusted_learning_data = attach_untrusted_learning_data


_apply_memory_context_budget = apply_memory_context_budget


# Compatibility export: existing tests and callers import this helper here.
_drop_oldest_message_group = drop_oldest_message_group


_enforce_final_context_budget = enforce_final_context_budget


_tool_evidence_links = tool_evidence_links


_inject_failure_insight = inject_failure_insight


_maybe_autogen_retry = maybe_autogen_retry


_context_evidence_links = context_evidence_links


_acc_usage = accumulate_usage


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
    import executors.plugins.tool_loop_v1 as tool_loop_v1
    return await _tool_loop_core_impl(
        system_prompt, messages, provider, model_name, temperature,
        max_tokens, tool_schemas, max_iter,
        call_ai_once=tool_loop_v1.call_ai_once,
        execute_tool_call=tool_loop_v1._execute_tool_call,
    )



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
    return await _before_finalize_hook_impl(
        draft, snap_messages, system_prompt, config, provider, model_name,
        temperature, max_tokens, ai_service, user_message,
    )


async def _run_fork_skill(
    skill_content: str,
    task: str,
    provider: str,
    model: str,
    temperature: float,
    ai_service: AIService,
    tool_schemas: list | None = None,
    *,
    parent_ruleset=None,
    spawn_depth: int = 0,
    group_id: int | None = None,
    bot_id: int | None = None,
    broadcaster=None,
    max_iter: int = 8,
    run_id: str | None = None,
    allowed_memory_refs: tuple[str, ...] = (),
    tool_records: list[dict] | None = None,
) -> str:
    """Compatibility facade for the attenuated fork-skill executor."""
    return await _run_fork_skill_impl(
        skill_content, task, provider, model, temperature, ai_service, tool_schemas,
        parent_ruleset=parent_ruleset, spawn_depth=spawn_depth, group_id=group_id,
        bot_id=bot_id, broadcaster=broadcaster, max_iter=max_iter, run_id=run_id,
        allowed_memory_refs=allowed_memory_refs, tool_records=tool_records,
    )


async def setup_session(runner) -> None:
    skill_discovery = bool(runner.executor.manifest.workspace.skill_discovery)
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

    import executors.plugins.tool_loop_v1 as _tool_loop_v1_mod
    _bcm = getattr(_tool_loop_v1_mod, "build_context_message", build_context_message)
    history, user_msg = _bcm(
        runner.ctx.user_message, runner.ctx.sender["name"], runner.ctx.history, is_workflow=runner.ctx.is_workflow
    )
    import core.workflow as _wf
    thread_id = _wf.current_thread_id(runner.ctx.group_id)
    memory_scope = MemoryScope.bot(
        group_id=runner.ctx.group_id,
        bot_id=runner.bot["id"],
        actor_id=f"bot:{runner.bot['id']}",
        thread_id=thread_id,
        run_id=getattr(runner, "run_id", runner.session_id),
        purpose="task_context_recall",
    )
    memory, learned_contexts = await hydrate_memory_context(
        runner, memory_scope, apply_budget=_apply_memory_context_budget
    )

    if skill_discovery:
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

    if learned_contexts:
        runner.system_prompt_base += f"\n\n{_UNTRUSTED_LEARNING_POLICY}"

    runner.system_prompt = runner.system_prompt_base

    user_content = build_image_content(user_msg, runner.ctx.file_url, runner.ctx.file_type, runner.provider)
    if isinstance(user_content, str):
        user_content, _ = compact.truncate_user_message(user_content, runner.ctx.group_id, runner.model_name)
    elif isinstance(user_content, list):
        for block in user_content:
            if isinstance(block, dict) and block.get("type") == "text":
                truncated_text, _ = compact.truncate_user_message(block.get("text", ""), runner.ctx.group_id, runner.model_name)
                block["text"] = truncated_text
    user_content = _attach_untrusted_learning_data(user_content, learned_contexts)
    
    _resuming = bool(runner.ctx.resume_session_id)
    if _resuming:
        resumed = list(runner.ctx.resume_messages or [])
        if resumed and resumed[0].get("role") == "system":
            resumed = resumed[1:]
        runner.messages = resumed
    else:
        runner.messages = list(history) + [{"role": "user", "content": user_content}]

    assemble_tool_schemas(runner, skill_discovery=skill_discovery)
    _enforce_final_context_budget(runner)

    await persist_session_start(
        runner,
        user_content=user_content,
        resuming=_resuming,
        thread_id=thread_id,
    )

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
            runner.temperature, runner.max_tokens, reinject_fn=runner._build_reinject,
            operation="final_draft",
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


_extract_completion_signals = extract_completion_signals


async def _finalize_causal_memory_usage(
    runner,
    *,
    scope: MemoryScope,
    learning_port,
) -> None:
    """Advance only cited Memory through the evidence-bearing usage lifecycle."""
    return await _finalize_usage_impl(runner, scope=scope, learning_port=learning_port)
async def cleanup_and_finalize(runner) -> ExecutionResult:
    signals = _extract_completion_signals(
        runner.messages,
        runner.tool_records,
        getattr(runner, "execution_error", None),
    )

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
        return ExecutionResult(
            full_text=runner.full_text, msg_id=None, signals=signals,
            session_id=runner.session_id,
        )

    attached = runner.execution_ctx.get("attached_file")
    save_kwargs = {
        "input_tokens": runner.ai_service.usage.input_tokens or None,
        "output_tokens": runner.ai_service.usage.output_tokens or None,
        "cache_read_tokens": runner.ai_service.usage.cache_read_tokens or None,
        "cache_creation_tokens": runner.ai_service.usage.cache_creation_tokens or None,
    }
    import core.workflow as _wf
    try:
        observation_thread_id = _wf.current_thread_id(runner.ctx.group_id)
    except Exception:
        observation_thread_id = None
        logger.warning(
            "failed to resolve observation thread for group %s",
            runner.ctx.group_id,
            exc_info=True,
        )
    save_kwargs["meta"] = {
        "session_id": runner.session_id,
        "memory_observation": {
            "thread_id": observation_thread_id,
            "run_id": runner.run_id,
            "version": "1",
        }
    }
    if attached:
        save_kwargs["file_url"] = attached["url"]
        save_kwargs["file_name"] = attached["name"]
        save_kwargs["file_type"] = attached["type"]

    msg_id = await runner.ctx.interaction.save_message(
        runner.ctx.group_id, runner.bot["id"], runner.full_text,
        **save_kwargs
    )

    save_payload = {
        "type": "stream_end", "temp_id": runner.temp_id, "id": msg_id,
        "member_id": runner.bot["id"], "sender_name": runner.bot["name"],
        "preview": runner.full_text[:100],
        "created_at": "",
        "session_id": runner.session_id,
    }
    if attached:
        from core import media as _media
        # DB stores the canonical ref; the live payload carries a freshly-signed URL.
        save_payload["file_url"] = _media.presign(attached["url"])
        save_payload["file_name"] = attached["name"]
        save_payload["file_type"] = attached["type"]

    await runner.ctx.interaction.broadcast(runner.ctx.group_id, save_payload)

    tool_names_called = [
        m["name"] for m in runner.messages
        if m.get("role") == "tool" and m.get("name")
    ]
    
    runner.messages.append({"role": "assistant", "content": runner.full_text})
    await runner.ctx.interaction.save_session_snapshot(runner.session_id, runner.messages)
    await runner.ctx.interaction.update_session_status(runner.session_id, "completed")
    try:
        from memory.application.execution_runs import finish_run
        await finish_run(
            run_id=runner.run_id, group_id=runner.ctx.group_id, status="completed",
            iterations=runner.iter_count, input_tokens=runner.ai_service.usage.input_tokens,
            output_tokens=runner.ai_service.usage.output_tokens,
        )
        bot_scope = MemoryScope.bot(
            group_id=runner.ctx.group_id,
            bot_id=runner.bot["id"],
            actor_id=f"bot:{runner.bot['id']}",
            run_id=runner.run_id,
            purpose="case_learning",
        )
        learning_port = getattr(runner, "learning", None)
        if learning_port is None:
            from memory.canonical import build_learning_client
            learning_port = build_learning_client()
        case_id = await learning_port.assemble_case(AssembleCase(
            scope=bot_scope,
            run_id=runner.run_id,
            task=runner.ctx.user_message,
            outcome="completed",
            tool_records=runner.tool_records,
        ))
        await _finalize_causal_memory_usage(
            runner,
            scope=bot_scope,
            learning_port=learning_port,
        )
        if case_id:
            await learning_port.process_case(ProcessLearningCase(
                scope=bot_scope,
                case_id=case_id,
            ))
        await learning_port.complete_experience_usage(CompleteExperienceUsage(
            scope=bot_scope,
            record_ids=tuple(runner.retrieved_experience_ids),
            run_id=runner.run_id,
            outcome="completed",
            input_tokens=runner.ai_service.usage.input_tokens,
            output_tokens=runner.ai_service.usage.output_tokens,
            tool_attempts=len(runner.tool_records),
        ))
        await learning_port.complete_skill_usage(CompleteSkillUsage(
            scope=bot_scope,
            skill_ids=tuple(runner.retrieved_skill_ids),
            run_id=runner.run_id,
            outcome="completed",
        ))
    except aiosqlite.OperationalError:
        logger.warning("run learning finalization unavailable; group schema is not ready", exc_info=True)

    # Durable completion must win the per-group writer lock before optional
    # background side effects are spawned. Otherwise a memory/archive task can
    # acquire the lock between snapshot and status and leave the foreground run
    # indefinitely reported as running.
    try:
        await runner.memory.observe(ObserveMemory(
            scope=MemoryScope.bot(
                group_id=runner.ctx.group_id,
                bot_id=runner.bot["id"],
                actor_id=f"bot:{runner.bot['id']}",
                thread_id=observation_thread_id,
                run_id=runner.run_id,
                purpose="task_result_observation",
            ),
            source_id=f"message:{msg_id}",
            content=runner.full_text,
            metadata={"message_id": msg_id},
        ))
    except Exception:
        logger.exception(
            "failed to enqueue durable memory observation for message %s", msg_id
        )
    bg.spawn(bus.publish(CompactionTriggered(
        group_id=runner.ctx.group_id,
        bot_id=runner.bot["id"],
        provider=runner.provider,
        model_name=runner.model_name,
        temperature=runner.temperature
    )))
    _append_log = _get_helper("append_log", append_log)
    bg.spawn(_append_log(
        runner.bot["id"], runner.full_text,
        user_message=runner.ctx.user_message,
        sender_name=runner.ctx.sender.get("name", ""),
        tool_calls=tool_names_called,
        iterations=runner.iter_count if tool_names_called else 0,
        executor=runner.executor.executor_id,
        group_id=runner.ctx.group_id,
    ))
    if runner.ctx.group_id and runner.tool_records:
        _archive_run = _get_helper("archive_run", archive_run)
        bg.spawn(_archive_run(
            runner.ctx.group_id, runner.temp_id, runner.bot,
            user_message=runner.ctx.user_message,
            sender_name=runner.ctx.sender.get("name", ""),
            tool_records=runner.tool_records,
            reply=runner.full_text,
            iterations=runner.iter_count,
            model=runner.model_name,
            executor=runner.executor.executor_id,
        ))
    return ExecutionResult(
        full_text=runner.full_text, msg_id=msg_id, signals=signals,
        session_id=runner.session_id,
    )


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

    _t0 = asyncio.get_running_loop().time()
    from executors.tool_dispatch import dispatch_tool
    dispatch_contexts = [
        {
            **runner.execution_ctx,
            "step_id": f"{runner.run_id}:step:{_iter}",
            "attempt_id": call["id"],
        }
        for call in calls
    ]
    raw_results = await asyncio.gather(*[
        dispatch_tool(call["name"], call["arguments"], dispatch_context)
        for call, dispatch_context in zip(calls, dispatch_contexts)
    ])
    _duration = round(asyncio.get_running_loop().time() - _t0, 2)

    pending_insights: list[str] = []
    for call, dispatch_context, (tool_result, is_error) in zip(
        calls, dispatch_contexts, raw_results
    ):
        tool_result, is_error = await _maybe_autogen_retry(
            runner, call["name"], call["arguments"], dispatch_context,
            tool_result, is_error,
        )
        memory_refs = list(
            dispatch_context.get("_validated_memory_refs", ())
        )
        executed_arguments = dispatch_context.get(
            "_executed_arguments", call["arguments"]
        )
        tool_result, _ = _check_and_attach_file(runner, tool_result)
        from observability import classify_tool_effect
        await runner.ctx.interaction.append_session_event(runner.session_id, "tool_result", {
            "tool_call_id": call["id"],
            "tool_name": call["name"],
            "result": tool_result,
            "is_error": is_error,
            "memory_refs": memory_refs,
            "evidence_links": _tool_evidence_links(memory_refs, dispatch_context),
            "duration_ms": int(_duration * 1000),
            "_observability": classify_tool_effect(
                call["name"], executed_arguments
            ).to_metadata(),
        })
        runner._track_vfs_modifications(call["name"], executed_arguments)

        display_result, truncated_path = compact.truncate_tool_result(call["name"], tool_result, runner.ctx.group_id, runner.model_name)

        runner.tool_records.append({
            "name": call["name"],
            "args": executed_arguments,
            "result": display_result,
            "is_error": is_error,
            "step_id": f"{runner.run_id}:step:{_iter}",
            "attempt_id": call["id"],
            "memory_refs": memory_refs,
        })
        if is_error:
            insight = await _inject_failure_insight(runner, call["name"], display_result)
            if insight:
                pending_insights.append(insight)
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
    for insight in pending_insights:
        runner.messages.append({"role": "user", "content": insight})
    if pending_insights:
        await runner.ctx.interaction.save_session_snapshot(runner.session_id, runner.messages)


async def execute_serial_tools(runner, calls, iteration=None) -> None:
    _iter = iteration or runner.iter_count
    pending_insights: list[str] = []
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

        _t0 = asyncio.get_running_loop().time()
        from executors.tool_dispatch import dispatch_tool
        dispatch_context = {
                **runner.execution_ctx,
                "step_id": f"{runner.run_id}:step:{_iter}",
                "attempt_id": call["id"],
        }
        tool_result, is_error = await dispatch_tool(
            call["name"], call["arguments"], dispatch_context
        )
        tool_result, is_error = await _maybe_autogen_retry(
            runner, call["name"], call["arguments"], dispatch_context,
            tool_result, is_error,
        )
        memory_refs = list(
            dispatch_context.get("_validated_memory_refs", ())
        )
        executed_arguments = dispatch_context.get(
            "_executed_arguments", call["arguments"]
        )
        tool_result, _ = _check_and_attach_file(runner, tool_result)
        _duration = round(asyncio.get_running_loop().time() - _t0, 2)

        from observability import classify_tool_effect
        await runner.ctx.interaction.append_session_event(runner.session_id, "tool_result", {
            "tool_call_id": call["id"],
            "tool_name": call["name"],
            "result": tool_result,
            "is_error": is_error,
            "memory_refs": memory_refs,
            "evidence_links": _tool_evidence_links(memory_refs, dispatch_context),
            "duration_ms": int(_duration * 1000),
            "_observability": classify_tool_effect(
                call["name"], executed_arguments
            ).to_metadata(),
        })

        runner._track_vfs_modifications(call["name"], executed_arguments)

        if call["name"] == "run_skill":
            tool_result = await runner._handle_run_skill_result(tool_result)
            # Pin inline skill bodies so they survive micro/auto-compaction
            # (run_skill is in _MICROCOMPACT_TOOLS; its tool message gets cleared).
            _sname = call.get("arguments", {}).get("name")
            if _sname and isinstance(tool_result, str) and tool_result.startswith("<skill_instructions>"):
                runner.invoked_skills[_sname] = tool_result

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
            "args": executed_arguments,
            "result": display_result,
            "is_error": is_error,
            "step_id": f"{runner.run_id}:step:{_iter}",
            "attempt_id": call["id"],
            "memory_refs": memory_refs,
        })
        if is_error:
            insight = await _inject_failure_insight(runner, call["name"], display_result)
            if insight:
                pending_insights.append(insight)
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
    for insight in pending_insights:
        runner.messages.append({"role": "user", "content": insight})
    if pending_insights:
        await runner.ctx.interaction.save_session_snapshot(runner.session_id, runner.messages)


from executors.plugins.tool_loop_presentation import (
    THINKING_I18N, generate_thinking_preview, build_invoked_skills_block,
)
from executors.plugins.tool_loop_media import MCPSHOT_RE, check_and_attach_file
from executors.plugins.tool_loop_reinject import (
    get_fresh_context_prefix as _get_fresh_context_prefix_impl,
    build_reinject as _build_reinject_impl,
)
from executors.plugins.tool_loop_usage_finalize import finalize_causal_memory_usage as _finalize_usage_impl



async def get_fresh_context_prefix(runner) -> tuple[str, str]:
    from core.orchestration import prompt_builder
    return await _get_fresh_context_prefix_impl(runner, prompt_builder)


async def build_reinject(runner) -> str:
    return await _build_reinject_impl(
        runner, compact=compact, bot_workspace=_bot_ws,
        invoked_skills_block=build_invoked_skills_block,
    )


_MCPSHOT_RE = MCPSHOT_RE


_check_and_attach_file = check_and_attach_file
