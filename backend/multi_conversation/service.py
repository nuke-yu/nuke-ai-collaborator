"""Application service coordinating one Agent conversation turn's setup."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import aiosqlite
import permissions

from memory.domain import MemoryScope
from multi_conversation.context import (
    ConversationContextRequest,
    assemble_context,
)


@dataclass(frozen=True, slots=True)
class ConversationSetupPorts:
    """Executor-owned operations composed by the conversation setup service."""

    build_context_message: Callable[..., tuple[list[dict], str]]
    apply_memory_budget: Callable[..., tuple[str, list[str]]]
    prepare_prompt: Callable[..., Any]
    assemble_tool_schemas: Callable[..., Any]
    enforce_context_budget: Callable[..., Any]
    persist_session_start: Callable[..., Any]
    resolve_thread_id: Callable[[int], str | None]


async def setup_turn(runner, ports: ConversationSetupPorts) -> None:
    """Assemble policy, history, recalled context, prompt, tools, and Session start.

    Orchestrators still choose participants and executors. This service
    coordinates the conversation-specific setup around those shared components.
    """
    skill_discovery = bool(runner.executor.manifest.workspace.skill_discovery)
    if runner.ctx.ruleset is not None:
        runner.ruleset = runner.ctx.ruleset
    else:
        permission_mode = (runner.bot.get("executor_config") or {}).get(
            "permission_mode", "default"
        )
        try:
            db_rules = await permissions.load_rules(runner.bot["id"])
        except aiosqlite.OperationalError:
            db_rules = []
        runner.ruleset = permissions.Ruleset(rules=db_rules, mode=permission_mode)

    history, user_msg = ports.build_context_message(
        runner.ctx.user_message,
        runner.ctx.sender["name"],
        runner.ctx.history,
        is_workflow=runner.ctx.is_workflow,
    )
    thread_id = ports.resolve_thread_id(runner.ctx.group_id)
    memory_scope = MemoryScope.bot(
        group_id=runner.ctx.group_id,
        bot_id=runner.bot["id"],
        actor_id=f"bot:{runner.bot['id']}",
        thread_id=thread_id,
        run_id=getattr(runner, "run_id", runner.session_id),
        purpose="task_context_recall",
    )
    context = await assemble_context(ConversationContextRequest(
        scope=memory_scope,
        query=runner.ctx.user_message,
        role=runner.bot.get("role") or "",
        history=runner.ctx.history,
        run_id=getattr(runner, "run_id", runner.session_id),
        group_id=runner.ctx.group_id,
        bot_id=runner.bot["id"],
        personal_user_id=getattr(runner.ctx, "personal_user_id", None),
        memory=runner.memory,
        group_knowledge=getattr(runner, "group_knowledge", None),
        learning=getattr(runner, "learning", None),
        personal=getattr(runner, "personal", None),
        apply_budget=lambda memory, learned: ports.apply_memory_budget(
            runner, memory, learned
        ),
    ))
    runner.retrieved_experience_ids = list(context.retrieved_experience_ids)
    runner.retrieved_skill_ids = list(context.retrieved_skill_ids)
    runner.injected_memory_refs = context.injected_memory_refs
    memory = context.memory
    learned_contexts = list(context.learned_contexts)

    user_content, resuming = await ports.prepare_prompt(
        runner,
        skill_discovery=skill_discovery,
        memory=memory,
        learned_contexts=learned_contexts,
        history=history,
        user_msg=user_msg,
    )
    ports.assemble_tool_schemas(runner, skill_discovery=skill_discovery)
    ports.enforce_context_budget(runner)
    await ports.persist_session_start(
        runner,
        user_content=user_content,
        resuming=resuming,
        thread_id=thread_id,
    )

    await runner.ctx.interaction.broadcast(runner.ctx.group_id, {
        "type": "stream_start",
        "temp_id": runner.temp_id,
        "member_id": runner.bot["id"],
        "sender_name": runner.bot["name"],
        "sender_type": "bot",
        "avatar_color": runner.bot["avatar_color"],
        "session_id": runner.session_id,
    })
    if runner.skills_snapshot:
        await runner.ctx.interaction.broadcast(runner.ctx.group_id, {
            "type": "skills_loaded",
            "temp_id": runner.temp_id,
            "member_id": runner.bot["id"],
            "skills": runner.skills_snapshot,
        })
