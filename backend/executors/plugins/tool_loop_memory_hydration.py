"""Context hydration for conversation, group, and learned memory."""
from __future__ import annotations

import logging

import aiosqlite

from memory.contracts import (
    RecallExperiences,
    RecallGroupFacts,
    RecallMemory,
    RecallSkills,
    ResolveLearningRefs,
    FormatProjectedContext,
)
from memory.domain import MemoryScope, Principal

logger = logging.getLogger(__name__)


async def hydrate_memory_context(runner, memory_scope, *, apply_budget):
    """Recall and budget all memory sources used to build a session prompt."""
    memory_result = await runner.memory.recall(RecallMemory(
        scope=memory_scope,
        query=runner.ctx.user_message,
        metadata={"role": runner.bot.get("role") or "", "history": runner.ctx.history},
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
            scope=memory_scope, query=runner.ctx.user_message,
            limit=5, char_budget=1600,
        ))
        if group_result.rendered_context:
            learned_contexts.append(group_result.rendered_context)
    except (aiosqlite.OperationalError, AttributeError):
        logger.warning("group fact recall unavailable; group schema is not ready", exc_info=True)

    learning_port = getattr(runner, "learning", None)
    if learning_port is None:
        from memory.canonical import build_learning_client
        learning_port = build_learning_client()
    try:
        experience_context, runner.retrieved_experience_ids = await learning_port.recall_experiences(
            RecallExperiences(
                scope=memory_scope, query=runner.ctx.user_message,
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
                scope=memory_scope, query=runner.ctx.user_message,
                run_id=getattr(runner, "run_id", runner.session_id),
            )
        )
    except (aiosqlite.OperationalError, AttributeError):
        skill_context, runner.retrieved_skill_ids = "", []
    if skill_context:
        learned_contexts.append(skill_context)

    try:
        resolved_refs = await learning_port.resolve_learning_refs(ResolveLearningRefs(
            scope=memory_scope,
            experience_ids=tuple(runner.retrieved_experience_ids),
            skill_ids=tuple(runner.retrieved_skill_ids),
        ))
        runner.injected_memory_refs = (
            tuple(resolved_refs)
            if isinstance(resolved_refs, (tuple, list))
            and all(isinstance(ref, str) for ref in resolved_refs)
            else ()
        )
    except (aiosqlite.OperationalError, AttributeError):
        logger.warning("learning reference resolution unavailable", exc_info=True)
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

    return apply_budget(runner, memory, learned_contexts)
