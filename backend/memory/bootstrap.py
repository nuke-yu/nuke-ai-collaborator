"""Composition root for the Memory module.

Only this module chooses concrete adapters. Domain and application code never
import it, which keeps in-process, IPC, and future service deployments
interchangeable.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from memory.adapters.runtime import (
    redact_projection_content,
    redact_projection_error,
)
from memory.adapters.projections import ChromaBotMemoryProjectionReader
from memory.adapters.algorithms import GraphitiTemporalAlgorithmAdapter
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
from memory.application.letta_controller import LettaMemoryFunctionController
from memory.domain import Principal
from memory.infrastructure import MemorySchemaManager, ProjectionOutbox, SQLiteMemoryDatabase, PersonalVaultDatabase, SQLitePersonalVaultPolicy
from memory.module import MemoryModule
from memory.ports import MemoryACLPort, TemporalGraphPort
from memory.composition import MemoryComposition
from memory.application.context import (
    configure_database,
    capture_memory_context,
    configure_composition,
    current_composition,
    configure_service,
    restore_memory_context,
    reset_memory_context,
)

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


def build_temporal_graph_client() -> TemporalGraphPort:
    """Return the composition-owned Graphiti temporal graph port."""
    return memory_composition().temporal_graph


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
    from memory.application import (
        CanonicalExperienceDistiller,
        CanonicalLearningService,
        CanonicalSkillCompiler,
        CanonicalSkillProjectionService,
    )
    from memory.adapters.algorithms import Mem0FactEngine
    from memory.infrastructure import (
        CentralMemberDirectory,
        CurrentMemorySecretProvider,
        CurrentMemorySettings,
        CurrentSkillWorkspace,
    )
    from ai.client import call_ai_once

    composition = MemoryComposition(
        module=build_memory_module(
            drain_interval_seconds=drain_interval_seconds,
        ),
        member_directory=CentralMemberDirectory(),
        secret_provider=CurrentMemorySecretProvider(),
        skill_workspace=CurrentSkillWorkspace(),
        fact_engine=Mem0FactEngine(),
        settings=CurrentMemorySettings(),
        model=call_ai_once,
        temporal_graph=GraphitiTemporalAlgorithmAdapter(),
    )
    composition.memory_functions = LettaMemoryFunctionController(
        composition.database, composition.projection_outbox, build_memory_acl()
    )
    return composition


def install_memory_composition(composition: MemoryComposition) -> MemoryComposition:
    """Install one composition into the current task context.

    ``build_memory_composition`` is deliberately side-effect free.  Only a
    composition root calls this function, making context replacement and
    teardown explicit for embedded hosts and tests.
    """
    from memory.application import (
        CanonicalExperienceDistiller,
        CanonicalLearningService,
        CanonicalSkillCompiler,
        CanonicalSkillProjectionService,
    )
    from memory.composition_pipeline import build_pipeline_dispatcher
    from memory.ports import (
        FactEnginePort,
        MemberDirectoryPort,
        MemorySecretPort,
        MemorySettingsPort,
        ModelPort,
        SkillWorkspacePort,
        TemporalGraphPort,
    )

    dependencies = {
        "member_directory": (composition.member_directory, MemberDirectoryPort),
        "secret_provider": (composition.secret_provider, MemorySecretPort),
        "skill_workspace": (composition.skill_workspace, SkillWorkspacePort),
        "fact_engine": (composition.fact_engine, FactEnginePort),
        "settings": (composition.settings, MemorySettingsPort),
        "model": (composition.model, ModelPort),
        "temporal_graph": (composition.temporal_graph, TemporalGraphPort),
    }
    for name, (dependency, protocol) in dependencies.items():
        if not isinstance(dependency, protocol):
            raise TypeError(f"Memory composition dependency {name!r} does not implement {protocol.__name__}")

    # Construct the complete service graph before touching any ambient state.
    from memory.application.pipeline import CanonicalPipelineJobRepository
    job_repository = CanonicalPipelineJobRepository(composition.database)
    services = {
        "learning": CanonicalLearningService(composition.database, job_repository),
        "skill_compiler": CanonicalSkillCompiler(composition.database, job_repository),
        "skill_projection": CanonicalSkillProjectionService(
            composition.database, composition.skill_workspace
        ),
        "experience_distiller": CanonicalExperienceDistiller(
            composition.database, composition.projection_outbox, job_repository
        ),
        "projection_outbox": composition.projection_outbox,
        "projection_reconciler": composition.module.reconciler,
        "memory_functions": composition.memory_functions,
        **{name: dependency for name, (dependency, _) in dependencies.items()},
    }
    services["pipeline"] = build_pipeline_dispatcher(composition)

    configure_database(composition.database)
    configure_composition(composition)
    for name, service in services.items():
        configure_service(name, service)
    return composition


@asynccontextmanager
async def memory_context(composition: MemoryComposition) -> AsyncIterator[MemoryComposition]:
    """Temporarily bind a composition for this async context.

    Lifecycle ownership remains with the composition root that started the
    module; this scope only manages dependency bindings.
    """
    previous = capture_memory_context()
    try:
        install_memory_composition(composition)
        yield composition
    finally:
        restore_memory_context(previous)


def memory_composition() -> MemoryComposition:
    """Return the process-local canonical Memory composition."""
    global _memory_composition
    scoped = current_composition()
    if scoped is not None:
        return scoped
    if _memory_composition is None:
        _memory_composition = install_memory_composition(
            build_memory_composition()
        )
    return _memory_composition


class _ResetMemoryComposition:
    """Awaitable teardown result preserving the historical sync API."""

    def __init__(self, task: asyncio.Task | None = None) -> None:
        self._task = task

    def __await__(self):
        async def wait() -> None:
            if self._task is not None:
                await self._task
        return wait().__await__()


def reset_memory_composition() -> _ResetMemoryComposition:
    """Drop the composition; works for both sync and async callers."""
    global _memory_composition
    old = _memory_composition
    _memory_composition = None
    reset_memory_context()
    if old is None:
        return _ResetMemoryComposition()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(old.module.stop())
        return _ResetMemoryComposition()
    return _ResetMemoryComposition(loop.create_task(old.module.stop()))


def memory_module() -> MemoryModule:
    """Return the process-local canonical Memory module."""
    return memory_composition().module
