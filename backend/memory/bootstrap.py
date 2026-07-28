"""Composition root for the Memory module.

Only this module chooses concrete adapters. Domain and application code never
import it, which keeps in-process, IPC, and future service deployments
interchangeable.
"""
from __future__ import annotations

from memory.adapters.runtime import (
    LegacyConversationMemoryAdapter,
    LegacyMemoryProjectionDelivery,
    LegacyMemoryProjectionReconciler,
    LegacyBotMemoryProjectionReader,
    LegacyLearningAdapter,
    LegacyPersonalKnowledgeAdapter,
    legacy_memory_database,
    redact_projection_content,
    redact_projection_error,
)
from memory.application import (
    AuthorizedPersonalKnowledgeService,
    BotFactObservationService,
    BotMemoryProjectionAuditService,
    BotMemoryProjectionRolloutGate,
    BotReflectionService,
    CanonicalChromaBackfillService,
    CanonicalRelationService,
    GroupFactService,
)
from memory.domain import Principal
from memory.infrastructure import MemorySchemaManager, ProjectionOutbox
from memory.module import MemoryModule
from memory.ports import MemoryACLPort

_memory_module: MemoryModule | None = None
_bot_memory_projection_rollout_gate: BotMemoryProjectionRolloutGate | None = None


def build_memory_client(bot: dict | None = None) -> LegacyConversationMemoryAdapter:
    """Build the current in-process client behind stable Memory contracts."""
    from ai.memory_provider import get_memory_provider

    return LegacyConversationMemoryAdapter(get_memory_provider(bot))


def build_personal_knowledge_client(principal: Principal) -> AuthorizedPersonalKnowledgeService:
    """Build the fail-closed personal-memory application boundary."""
    return AuthorizedPersonalKnowledgeService(
        LegacyPersonalKnowledgeAdapter(), build_memory_acl(), principal
    )


def build_learning_client() -> LegacyLearningAdapter:
    return LegacyLearningAdapter()


def build_group_knowledge_client() -> GroupFactService:
    return GroupFactService(legacy_memory_database)


def build_bot_fact_observation_client() -> BotFactObservationService:
    return BotFactObservationService(
        legacy_memory_database,
        get_memory_module().projection_outbox,
    )


def build_bot_reflection_client() -> BotReflectionService:
    return BotReflectionService(
        legacy_memory_database,
        get_memory_module().projection_outbox,
    )


def build_memory_relation_client() -> CanonicalRelationService:
    return CanonicalRelationService(legacy_memory_database)


def build_bot_memory_projection_auditor() -> BotMemoryProjectionAuditService:
    from core import config

    return BotMemoryProjectionAuditService(
        legacy_memory_database,
        LegacyBotMemoryProjectionReader(),
        limit=config.MEMORY_PROJECTION_AUDIT_LIMIT,
    )


def build_bot_memory_projection_rollout_gate() -> BotMemoryProjectionRolloutGate:
    from core import config

    global _bot_memory_projection_rollout_gate
    if _bot_memory_projection_rollout_gate is None:
        _bot_memory_projection_rollout_gate = BotMemoryProjectionRolloutGate(
            legacy_memory_database,
            required_passes=config.MEMORY_PROJECTION_ROLLOUT_REQUIRED_PASSES,
            min_observation_seconds=(
                config.MEMORY_PROJECTION_ROLLOUT_MIN_OBSERVATION_SECONDS
            ),
            min_audit_interval_seconds=(
                config.MEMORY_PROJECTION_ROLLOUT_MIN_AUDIT_INTERVAL_SECONDS
            ),
            reopen_cooldown_seconds=(
                config.MEMORY_PROJECTION_ROLLOUT_REOPEN_COOLDOWN_SECONDS
            ),
            cache_ttl_seconds=(
                config.MEMORY_PROJECTION_ROLLOUT_CACHE_TTL_SECONDS
            ),
        )
    return _bot_memory_projection_rollout_gate


def build_canonical_chroma_backfill_client() -> CanonicalChromaBackfillService:
    return CanonicalChromaBackfillService(
        legacy_memory_database,
        LegacyBotMemoryProjectionReader(),
        redact_projection_content,
    )


def build_memory_acl() -> MemoryACLPort:
    """Build the production ACL policy behind its stable application port."""
    from memory.adapters.algorithms import LettaACLAlgorithmAdapter

    return LettaACLAlgorithmAdapter()


def build_memory_module(*, drain_interval_seconds: float = 60.0) -> MemoryModule:
    """Compose an embeddable Memory runtime from host-specific adapters."""
    delivery = LegacyMemoryProjectionDelivery()
    outbox = ProjectionOutbox(
        legacy_memory_database,
        delivery,
        error_sanitizer=redact_projection_error,
    )
    return MemoryModule(
        legacy_memory_database,
        MemorySchemaManager(legacy_memory_database),
        outbox,
        LegacyMemoryProjectionReconciler(),
        drain_interval_seconds=drain_interval_seconds,
    )


def get_memory_module() -> MemoryModule:
    """Return the process-local Memory composition used by legacy callers."""
    global _memory_module
    if _memory_module is None:
        _memory_module = build_memory_module()
    return _memory_module
