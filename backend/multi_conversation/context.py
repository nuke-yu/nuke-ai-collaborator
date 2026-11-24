"""Conversation prompt context assembled from the external Memory capability."""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import aiosqlite

from memory.contracts import (
    FormatProjectedContext,
    RecallExperiences,
    RecallGroupFacts,
    RecallMemory,
    RecallSkills,
    ResolveLearningRefs,
)
from memory.domain import MemoryScope, Principal
from memory.ports.api import (
    GroupKnowledgePort,
    LearningPort,
    MemoryQueryPort,
    PersonalKnowledgePort,
)

logger = logging.getLogger(__name__)

ContextBudget = Callable[[str, list[str]], tuple[str, list[str]]]


@dataclass(frozen=True, slots=True)
class ConversationContextRequest:
    """Inputs and Memory ports needed to assemble one turn's recalled context."""

    scope: MemoryScope
    query: str
    role: str
    history: Sequence[dict[str, Any]]
    run_id: str
    group_id: int
    bot_id: int
    personal_user_id: int | None
    memory: MemoryQueryPort
    group_knowledge: GroupKnowledgePort | None
    learning: LearningPort | None
    personal: PersonalKnowledgePort | None
    apply_budget: ContextBudget


@dataclass(frozen=True, slots=True)
class ConversationContext:
    """Rendered context and provenance produced for one conversation turn."""

    memory: str
    learned_contexts: tuple[str, ...]
    retrieved_experience_ids: tuple[str, ...]
    retrieved_skill_ids: tuple[str, ...]
    injected_memory_refs: tuple[str, ...]


async def assemble_context(request: ConversationContextRequest) -> ConversationContext:
    """Recall conversation, group, learned, and optional personal context.

    Memory implementations are supplied through their public ports. This
    service owns prompt-context policy, not Memory storage or retrieval logic.
    """
    memory_result = await request.memory.recall(RecallMemory(
        scope=request.scope,
        query=request.query,
        metadata={"role": request.role, "history": list(request.history)},
    ))
    memory = memory_result.rendered_context
    learned_contexts: list[str] = []

    try:
        group_port = request.group_knowledge
        if group_port is None:
            from memory.bootstrap import build_group_knowledge_client

            group_port = build_group_knowledge_client(
                Principal.bot(request.bot_id, request.group_id)
            )
        group_result = await group_port.recall_facts(RecallGroupFacts(
            scope=request.scope,
            query=request.query,
            limit=5,
            char_budget=1600,
        ))
        if group_result.rendered_context:
            learned_contexts.append(group_result.rendered_context)
    except (aiosqlite.OperationalError, AttributeError):
        logger.warning("group fact recall unavailable; group schema is not ready", exc_info=True)

    learning_port = request.learning
    if learning_port is None:
        from memory.canonical import build_learning_client

        learning_port = build_learning_client()

    try:
        experience_context, experience_ids = await learning_port.recall_experiences(
            RecallExperiences(
                scope=request.scope,
                query=request.query,
                run_id=request.run_id,
            )
        )
    except (aiosqlite.OperationalError, AttributeError):
        logger.warning("experience recall unavailable; group schema is not ready", exc_info=True)
        experience_context, experience_ids = "", []
    if experience_context:
        learned_contexts.append(experience_context)

    try:
        skill_context, skill_ids = await learning_port.recall_skills(
            RecallSkills(
                scope=request.scope,
                query=request.query,
                run_id=request.run_id,
            )
        )
    except (aiosqlite.OperationalError, AttributeError):
        logger.warning("skill recall unavailable; group schema is not ready", exc_info=True)
        skill_context, skill_ids = "", []
    if skill_context:
        learned_contexts.append(skill_context)

    try:
        resolved_refs = await learning_port.resolve_learning_refs(ResolveLearningRefs(
            scope=request.scope,
            experience_ids=tuple(experience_ids),
            skill_ids=tuple(skill_ids),
        ))
        injected_refs = (
            tuple(resolved_refs)
            if isinstance(resolved_refs, (tuple, list))
            and all(isinstance(ref, str) for ref in resolved_refs)
            else ()
        )
    except (aiosqlite.OperationalError, AttributeError):
        logger.warning("learning reference resolution unavailable; group schema is not ready", exc_info=True)
        injected_refs = ()

    if request.personal_user_id is not None:
        personal_scope = MemoryScope.personal(
            user_id=request.personal_user_id,
            group_id=request.group_id,
            actor_id=f"user:{request.personal_user_id}",
            bot_id=request.bot_id,
        )
        personal_port = request.personal
        if personal_port is None:
            from memory.canonical import build_personal_knowledge_client

            personal_port = build_personal_knowledge_client(
                Principal.user(request.personal_user_id, [request.group_id])
            )
        personal_context = await personal_port.format_projected_context(
            FormatProjectedContext(scope=personal_scope)
        )
        if personal_context:
            memory = f"{memory}\n\n{personal_context}" if memory else personal_context

    memory, learned_contexts = request.apply_budget(memory, learned_contexts)
    return ConversationContext(
        memory=memory,
        learned_contexts=tuple(learned_contexts),
        retrieved_experience_ids=tuple(experience_ids),
        retrieved_skill_ids=tuple(skill_ids),
        injected_memory_refs=injected_refs,
    )


async def hydrate_memory_context(runner, memory_scope, *, apply_budget):
    """Compatibility adapter for the existing tool-loop session setup."""
    result = await assemble_context(ConversationContextRequest(
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
        apply_budget=lambda memory, learned: apply_budget(
            runner, memory, learned
        ),
    ))
    runner.retrieved_experience_ids = list(result.retrieved_experience_ids)
    runner.retrieved_skill_ids = list(result.retrieved_skill_ids)
    runner.injected_memory_refs = result.injected_memory_refs
    return result.memory, list(result.learned_contexts)
