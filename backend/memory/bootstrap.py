"""Composition root for the Memory module.

Only this module chooses concrete adapters. Domain and application code never
import it, which keeps in-process, IPC, and future service deployments
interchangeable.
"""
from __future__ import annotations

from memory.adapters.runtime import (
    redact_projection_content,
    redact_projection_error,
)
from memory.adapters.projections import ChromaBotMemoryProjectionReader
from memory.application import (
    AuthorizedPersonalKnowledgeService,
    CanonicalConversationMemoryService,
    CanonicalPersonalKnowledgeService,
    AuthorizedGroupKnowledgeService,
    BotFactObservationService,
    BotMemoryProjectionAuditService,
    BotMemoryProjectionRolloutGate,
    BotReflectionService,
    CanonicalChromaBackfillService,
    CanonicalRelationService,
    GroupFactService,
    CanonicalProjectionReconciler,
)
from memory.domain import Principal
from memory.infrastructure import MemorySchemaManager, ProjectionOutbox, SQLiteMemoryDatabase, PersonalVaultDatabase, SQLitePersonalVaultPolicy
from memory.module import MemoryModule
from memory.ports import MemoryACLPort
from memory.composition import MemoryComposition

_memory_composition: MemoryComposition | None = None
_bot_memory_projection_rollout_gate: BotMemoryProjectionRolloutGate | None = None


def build_personal_knowledge_client(principal: Principal) -> AuthorizedPersonalKnowledgeService:
    """Build the fail-closed personal-memory application boundary."""
    return AuthorizedPersonalKnowledgeService(
        CanonicalPersonalKnowledgeService(PersonalVaultDatabase()), build_memory_acl(), principal,
        vault_policy=SQLitePersonalVaultPolicy(),
    )


async def list_personal_apps(*, user_id: int, include_inactive: bool = True):
    from memory.application.personal_vault import list_personal_apps as _list
    return await _list(database=PersonalVaultDatabase(), user_id=user_id, include_inactive=include_inactive)


async def register_personal_app(*, user_id: int, app_id: str, name: str) -> None:
    from memory.application.personal_vault import register_personal_app as _register
    await _register(database=PersonalVaultDatabase(), user_id=user_id, app_id=app_id, name=name)


async def set_personal_app_status(*, user_id: int, app_id: str, active: bool) -> bool:
    from memory.application.personal_vault import set_personal_app_status as _set_status
    return await _set_status(database=PersonalVaultDatabase(), user_id=user_id, app_id=app_id, active=active)


async def list_acl_audit_events(*, user_id: int, limit: int = 100):
    from memory.application.personal_vault import list_acl_audit_events as _list_audit
    return await _list_audit(database=PersonalVaultDatabase(), user_id=user_id, limit=limit)


def build_learning_client():
    from memory.canonical import build_learning_client as _build
    return _build()


def build_group_knowledge_client(
    principal: Principal | None = None,
) -> GroupFactService | AuthorizedGroupKnowledgeService:
    service = GroupFactService(SQLiteMemoryDatabase())
    if principal is None:
        return service
    return AuthorizedGroupKnowledgeService(service, build_memory_acl(), principal)


def build_bot_fact_observation_client() -> BotFactObservationService:
    return BotFactObservationService(
        SQLiteMemoryDatabase(),
        memory_module().projection_outbox,
    )


def build_bot_reflection_client() -> BotReflectionService:
    return BotReflectionService(
        SQLiteMemoryDatabase(),
        memory_module().projection_outbox,
    )


def build_memory_relation_client() -> CanonicalRelationService:
    async def authorize_relation(scope) -> bool:
        actor = scope.actor_id
        if actor.startswith(("system:", "service:")):
            return True
        from db import global_db
        try:
            actor_id = int(actor.split(":", 1)[1])
        except (IndexError, ValueError):
            return False
        async with global_db() as db:
            if actor.startswith("user:"):
                async with db.execute(
                    "SELECT 1 FROM group_memberships WHERE user_id=? AND group_id=?",
                    (actor_id, scope.group_id),
                ) as cur:
                    return await cur.fetchone() is not None
            if actor.startswith("bot:"):
                async with db.execute(
                    "SELECT 1 FROM members WHERE id=? AND group_id=? AND type='bot'",
                    (actor_id, scope.group_id),
                ) as cur:
                    return await cur.fetchone() is not None
        return False
    return CanonicalRelationService(SQLiteMemoryDatabase(), authorize_relation)


def build_bot_memory_projection_auditor() -> BotMemoryProjectionAuditService:
    from core import config

    return BotMemoryProjectionAuditService(
        SQLiteMemoryDatabase(),
        ChromaBotMemoryProjectionReader(),
        limit=config.MEMORY_PROJECTION_AUDIT_LIMIT,
    )


def build_bot_memory_projection_rollout_gate() -> BotMemoryProjectionRolloutGate:
    from core import config

    global _bot_memory_projection_rollout_gate
    if _bot_memory_projection_rollout_gate is None:
        _bot_memory_projection_rollout_gate = BotMemoryProjectionRolloutGate(
            SQLiteMemoryDatabase(),
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
        SQLiteMemoryDatabase(),
        ChromaBotMemoryProjectionReader(),
        redact_projection_content,
    )


def build_memory_acl() -> MemoryACLPort:
    """Build the production ACL policy behind its stable application port."""
    from memory.adapters.algorithms import LettaACLAlgorithmAdapter

    return LettaACLAlgorithmAdapter()


def build_memory_module(*, drain_interval_seconds: float = 60.0) -> MemoryModule:
    """Compose an embeddable Memory runtime from host-specific adapters."""
    # Canonical records own projection intents. Chroma is only a derived
    # Chroma is only a derived delivery target.
    from memory.adapters.projections import ChromaBotMemoryProjectionDelivery

    delivery = ChromaBotMemoryProjectionDelivery()
    database = SQLiteMemoryDatabase()
    outbox = ProjectionOutbox(
        database,
        delivery,
        error_sanitizer=redact_projection_error,
    )
    return MemoryModule(
        database,
        MemorySchemaManager(database),
        outbox,
        CanonicalProjectionReconciler(database, outbox),
        drain_interval_seconds=drain_interval_seconds,
    )


def build_memory_composition(*, drain_interval_seconds: float = 60.0) -> MemoryComposition:
    """Build the explicit process-local Memory dependency composition.

    This is the preferred construction point for runtime code.
    """
    return MemoryComposition(
        module=build_memory_module(
            drain_interval_seconds=drain_interval_seconds,
        )
    )


def memory_composition() -> MemoryComposition:
    """Return the process-local canonical Memory composition."""
    global _memory_composition
    if _memory_composition is None:
        _memory_composition = build_memory_composition()
    return _memory_composition


def memory_module() -> MemoryModule:
    """Return the process-local canonical Memory module."""
    return memory_composition().module
