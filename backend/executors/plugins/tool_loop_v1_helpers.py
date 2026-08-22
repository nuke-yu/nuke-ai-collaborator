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
                              FormatProjectedContext, MarkUsageAdopted,
                              MarkUsageExecuted, ObserveMemory,
                              ProcessLearningCase, RecallExperiences,
                              RecallGroupFacts, RecallMemory, RecallSkills,
                              ResolveLearningRefs, VerifyUsage)
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
from executors.plugins.tool_loop_signals import extract_completion_signals
from executors.tool_dispatch import execute_tool_call as _execute_tool_call

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
                auto_compact=False,
                operation="before_finalize_review",
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
                auto_compact=False,
                operation="before_finalize_regeneration",
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
    """Execute a fork skill as a real, attenuated sub-agent.

    Security contract (§7.5.2):
      - refuses past SPAWN_MAX_DEPTH (prevents skill→skill explosion);
      - child tools run at spawn_depth+1 through the permission pipeline with a
        ruleset attenuated by derive_subagent_ruleset (bypass not propagated,
        blanket high-risk allows dropped; the engine denies `ask` at depth>0);
      - tool gating: no declared tool_schemas → single call, and if the model
        still requests tools we return a notice rather than executing anything.
    Tokens roll into the parent ai_service.usage (canonical accumulator).
    """
    if spawn_depth >= config.SPAWN_MAX_DEPTH:
        return f"[fork skill 已达最大深度 {config.SPAWN_MAX_DEPTH}，拒绝执行]"

    child_ctx = {
        "bot_id": bot_id,
        "group_id": group_id,
        "spawn_depth": spawn_depth + 1,
        "ruleset": permissions.derive_subagent_ruleset(parent_ruleset),
        "broadcaster": broadcaster,
        "run_id": run_id,
        "allowed_memory_refs": allowed_memory_refs,
    }
    messages = [{"role": "user", "content": task or "请执行此技能。"}]

    from executors.tool_dispatch import dispatch_tool
    for iteration in range(1, max_iter + 1):
        try:
            result = await ai_service.call(
                skill_content, messages, model, provider, temperature, 4096,
                tools=tool_schemas or None, auto_compact=True,
                operation="skill_fork",
            )
        except Exception as e:
            return f"[fork skill 执行错误] {e}"

        if result["type"] == "text":
            return result["content"]
        if result["type"] != "tool_calls":
            return f"[fork skill 返回了非文本类型: {result['type']}]"

        calls = result.get("calls", [])
        if not tool_schemas:
            names = ", ".join(c["name"] for c in calls)
            return f"[fork skill 请求工具 {names} 但未声明 allowed_tools，已拒绝]"

        messages.append(result["assistant_message"])
        for call in calls:
            child_ctx["step_id"] = f"{run_id}:fork:{iteration}" if run_id else ""
            child_ctx["attempt_id"] = call.get("id", "")
            out, _is_err = await dispatch_tool(
                call["name"], call.get("arguments", {}), child_ctx
            )
            if tool_records is not None:
                tool_records.append({
                    "name": call["name"],
                    "args": child_ctx.get(
                        "_executed_arguments",
                        call.get("arguments", {}),
                    ),
                    "result": out,
                    "is_error": _is_err,
                    "step_id": child_ctx["step_id"],
                    "attempt_id": child_ctx["attempt_id"],
                    "memory_refs": list(
                        child_ctx.get("_validated_memory_refs", ())
                    ),
                    "spawn_depth": spawn_depth + 1,
                })
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "name": call["name"],
                "content": out,
            })

    return "[fork skill 达到最大迭代次数，未完成]"


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
    memory_result = await runner.memory.recall(RecallMemory(
        scope=memory_scope,
        query=runner.ctx.user_message,
        metadata={
            "role": runner.bot.get("role") or "",
            "history": runner.ctx.history,
        },
    ))
    memory = memory_result.rendered_context
    learned_contexts = []
    try:
        group_port = getattr(runner, "group_knowledge", None)
        if group_port is None:
            from memory.bootstrap import build_group_knowledge_client
            group_port = build_group_knowledge_client(
                Principal.bot(runner.bot["id"], runner.ctx.group_id)
            )
        group_result = await group_port.recall_facts(RecallGroupFacts(
            scope=memory_scope,
            query=runner.ctx.user_message,
            limit=5,
            char_budget=1600,
        ))
        if group_result.rendered_context:
            learned_contexts.append(group_result.rendered_context)
    except (aiosqlite.OperationalError, AttributeError):
        logger.warning(
            "group fact recall unavailable; group schema is not ready",
            exc_info=True,
        )
    learning_port = getattr(runner, "learning", None)
    if learning_port is None:
        from memory.canonical import build_learning_client
        learning_port = build_learning_client()
    try:
        experience_context, runner.retrieved_experience_ids = await learning_port.recall_experiences(
            RecallExperiences(
                scope=memory_scope,
                query=runner.ctx.user_message,
                run_id=getattr(runner, "run_id", runner.session_id),
            )
        )
    except (aiosqlite.OperationalError, AttributeError):
        logger.warning("experience recall unavailable; group schema is not ready", exc_info=True)
        experience_context, runner.retrieved_experience_ids = "", []
    if experience_context:
        learned_contexts.append(experience_context)
    try:
        skill_context, runner.retrieved_skill_ids = await learning_port.recall_skills(
            RecallSkills(
                scope=memory_scope,
                query=runner.ctx.user_message,
                run_id=getattr(runner, "run_id", runner.session_id),
            )
        )
    except (aiosqlite.OperationalError, AttributeError):
        skill_context, runner.retrieved_skill_ids = "", []
    if skill_context:
        learned_contexts.append(skill_context)
    try:
        resolved_refs = await learning_port.resolve_learning_refs(
            ResolveLearningRefs(
                scope=memory_scope,
                experience_ids=tuple(runner.retrieved_experience_ids),
                skill_ids=tuple(runner.retrieved_skill_ids),
            )
        )
        runner.injected_memory_refs = (
            tuple(resolved_refs)
            if isinstance(resolved_refs, (tuple, list))
            and all(isinstance(ref, str) for ref in resolved_refs)
            else ()
        )
    except (aiosqlite.OperationalError, AttributeError):
        logger.warning(
            "learning reference resolution unavailable", exc_info=True
        )
        runner.injected_memory_refs = ()
    if getattr(runner.ctx, "personal_user_id", None) is not None:
        personal_scope = MemoryScope.personal(
            user_id=runner.ctx.personal_user_id,
            group_id=runner.ctx.group_id,
            actor_id=f"user:{runner.ctx.personal_user_id}",
            bot_id=runner.bot["id"],
        )
        personal_port = getattr(runner, "personal", None)
        if personal_port is None:
            from memory.canonical import build_personal_knowledge_client
            personal_port = build_personal_knowledge_client(
                Principal.user(runner.ctx.personal_user_id, [runner.ctx.group_id])
            )
        personal_context = await personal_port.format_projected_context(
            FormatProjectedContext(scope=personal_scope)
        )
        if personal_context:
            memory = f"{memory}\n\n{personal_context}" if memory else personal_context

    memory, learned_contexts = _apply_memory_context_budget(
        runner, memory, learned_contexts
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

    tool_names = [t.name for t in runner.executor.manifest.tools]
    if not skill_discovery:
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
    from runtime_features.code_mode import append_code_mode_prompt
    runner.system_prompt_base = append_code_mode_prompt(
        runner.system_prompt_base, runner.tool_schemas
    )
    runner.system_prompt = append_code_mode_prompt(
        runner.system_prompt, runner.tool_schemas
    )
    # Letta active memory functions are explicitly opt-in. They are not added
    # to the generic tool registry; dispatch_tool routes them to the composed
    # Memory controller, preserving the existing ToolRouter policy.
    _executor_config = runner.bot.get("executor_config") or {}
    _allowed_tools = runner.bot.get("allowed_tools")
    _memory_tools_allowed = not _allowed_tools or all(
        name in _allowed_tools for name in ("memory_read", "memory_write")
    )
    if (_executor_config.get("memory_functions_enabled") and _memory_tools_allowed
            and getattr(runner, "memory_functions", None) is not None):
        runner.tool_schemas.extend(runner.memory_functions.tool_schemas())
    from memory.application.references import add_tool_ref_parameter
    runner.tool_schemas = add_tool_ref_parameter(
        runner.tool_schemas, runner.injected_memory_refs
    )
    _enforce_final_context_budget(runner)

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
        from sessions.manifest import build_capability_manifest
        executor_version = getattr(runner.executor.manifest, "version", "1")
        if not isinstance(executor_version, str):
            executor_version = "1"
        runner.capability_manifest, runner.manifest_hash = build_capability_manifest(
            provider=runner.provider,
            model=runner.model_name,
            executor_id=runner.executor.executor_id,
            executor_version=executor_version,
            system_prompt=runner.system_prompt,
            bot=runner.bot,
            tool_schemas=runner.tool_schemas,
            skills=runner.skills_snapshot,
            permission_rules=runner.ruleset,
            sandbox_policy={"permission_mode": runner.ruleset.mode if runner.ruleset else "default"},
            memory_revision=";".join(sorted(str(ref) for ref in runner.injected_memory_refs)),
        )
        await runner.ctx.interaction.create_session(
            session_id=runner.session_id,
            bot_id=runner.bot["id"],
            group_id=runner.ctx.group_id,
            config=_session_config,
            user_message=runner.ctx.user_message,
            executor_id=runner.executor.executor_id,
            manifest=runner.capability_manifest,
            manifest_hash=runner.manifest_hash,
            manifest_version=runner.capability_manifest["manifest_version"],
        )
        await runner.ctx.interaction.append_session_event(runner.session_id, "session_start", {
            "user_content": user_content if isinstance(user_content, str) else json.dumps(user_content, ensure_ascii=False),
            "manifest_hash": runner.manifest_hash,
        })

    context_evidence_links = _context_evidence_links(
        runner.injected_memory_refs, runner.always_skills
    )
    if context_evidence_links:
        await runner.ctx.interaction.append_session_event(
            runner.session_id,
            "context_evidence_injected",
            {
                "evidence_links": context_evidence_links,
                "reference_count": len(context_evidence_links),
                "causal_usage": False,
                "recovery_resume": _resuming,
            },
        )

    from memory.application.execution_runs import start_run
    try:
        await start_run(
        run_id=getattr(runner, "run_id", runner.session_id),
        group_id=runner.ctx.group_id,
        bot_id=runner.bot["id"],
        session_id=runner.session_id,
        thread_id=thread_id,
        provider=runner.provider,
        model=runner.model_name,
        executor=runner.executor.executor_id,
        )
        from memory.application.reflexion_service import record_memory_injection
        runner.memory_injection_decision_id = await record_memory_injection(
            run_id=getattr(runner, "run_id", runner.session_id),
            group_id=runner.ctx.group_id,
            bot_id=runner.bot["id"],
            memory_refs=runner.injected_memory_refs,
        )
    except aiosqlite.OperationalError:
        logger.warning("run trace unavailable; group schema is not ready", exc_info=True)
        
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
    from memory.application.reflexion_service import record_memory_adoption
    from memory.application.causal_usage import (
        collect_causal_usages,
        verification_for_usage,
    )

    usages = collect_causal_usages(
        runner.tool_records,
        getattr(runner, "injected_memory_refs", ()),
    )
    if not usages:
        return
    decision_id = await record_memory_adoption(
        run_id=runner.run_id,
        group_id=runner.ctx.group_id,
        bot_id=runner.bot["id"],
        evidence_by_ref={
            usage.memory_ref: usage.action_evidence_ids
            for usage in usages
        },
    )
    if decision_id is None:
        return

    for usage in usages:
        item_ids = (usage.item_id,)
        await learning_port.mark_usage_adopted(MarkUsageAdopted(
            scope=scope,
            kind=usage.kind,
            item_ids=item_ids,
            run_id=runner.run_id,
            adopted_via="decision_trace",
            evidence={
                "decision_id": decision_id,
                "memory_ref": usage.memory_ref,
            },
        ))
        await learning_port.mark_usage_executed(MarkUsageExecuted(
            scope=scope,
            kind=usage.kind,
            item_ids=item_ids,
            run_id=runner.run_id,
            evidence={
                "action_match": True,
                "evidence_ids": list(usage.action_evidence_ids),
                "memory_ref": usage.memory_ref,
            },
        ))
        verification = verification_for_usage(
            usage,
            runner.tool_records,
            terminal_outcome="completed",
        )
        if verification is not None:
            status, evidence = verification
            await learning_port.verify_usage(VerifyUsage(
                scope=scope,
                kind=usage.kind,
                item_ids=item_ids,
                run_id=runner.run_id,
                status=status,
                evidence=evidence,
            ))


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
    invoked = build_invoked_skills_block(getattr(runner, "invoked_skills", {}))
    parts = [p for p in [fresh_prefix, invoked, ft_xml, file_contents] if p]
    return "\n\n".join(parts)


_MCPSHOT_RE = MCPSHOT_RE


_check_and_attach_file = check_and_attach_file
