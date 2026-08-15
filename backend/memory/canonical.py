"""Public factories for canonical Memory application services."""
from __future__ import annotations

from memory.application import (
    CanonicalBotFactObserver,
    CanonicalConversationMemoryService,
    CanonicalObservationLoader,
    CanonicalSummaryObserver,
    CanonicalReflectionObserver,
    CanonicalToolCompressionObserver,
    BotFactObservationService,
    BotReflectionService,
    CanonicalCaseEvaluator,
    CanonicalExperienceDistiller,
    CanonicalSkillCompiler,
)
from memory.application import AuthorizedPersonalKnowledgeService, CanonicalPersonalKnowledgeService
from memory.application import CanonicalLearningService
from memory.application import CanonicalSkillProjectionService
from memory.application.pipeline import CanonicalPipelineDispatcher
from memory.adapters.algorithms import LettaACLAlgorithmAdapter
from memory.domain import MemoryScope
from memory.infrastructure import PersonalVaultDatabase, ProjectionOutbox, SQLitePersonalVaultPolicy


def _runtime_composition():
    """Resolve the single process composition at the application boundary."""
    from memory.bootstrap import memory_composition
    return memory_composition()


async def call_memory_model(*args, **kwargs):
    """Canonical model boundary used by Memory application services."""
    from ai.client import call_ai_once
    return await call_ai_once(*args, **kwargs)


def build_experience_distiller():
    composition = _runtime_composition()
    database = composition.database
    return CanonicalExperienceDistiller(
        database, composition.projection_outbox
    )


def build_projection_reconciler():
    from memory.application.projection_reconciliation import CanonicalProjectionReconciler
    composition = _runtime_composition()
    database = composition.database
    return CanonicalProjectionReconciler(
        database, composition.projection_outbox
    )


def build_projection_outbox() -> ProjectionOutbox:
    return _runtime_composition().projection_outbox


def build_conversation_memory_client() -> CanonicalConversationMemoryService:
    return CanonicalConversationMemoryService(_runtime_composition().database)


def build_personal_knowledge_client(principal):
    """Build the canonical Personal Vault client at the composition boundary."""
    return AuthorizedPersonalKnowledgeService(
        CanonicalPersonalKnowledgeService(PersonalVaultDatabase()),
        LettaACLAlgorithmAdapter(),
        principal,
        vault_policy=SQLitePersonalVaultPolicy(),
    )


def build_learning_client() -> CanonicalLearningService:
    return CanonicalLearningService(_runtime_composition().database)


def build_skill_compiler() -> CanonicalSkillCompiler:
    return CanonicalSkillCompiler(_runtime_composition().database)


def build_skill_projection_client() -> CanonicalSkillProjectionService:
    return CanonicalSkillProjectionService(_runtime_composition().database)


def build_pipeline_dispatcher() -> CanonicalPipelineDispatcher:
    """Compatibility bridge for legacy callers.

    New composition roots use ``memory.composition_pipeline`` directly.
    """
    from memory.composition_pipeline import build_pipeline_dispatcher as _build
    return _build(_runtime_composition())


async def list_personal_apps(*, user_id: int, include_inactive: bool = True):
    from memory.application.personal_vault import list_personal_apps as _list
    return await _list(
        database=PersonalVaultDatabase(),
        user_id=user_id,
        include_inactive=include_inactive,
    )


async def register_personal_app(*, user_id: int, app_id: str, name: str) -> None:
    from memory.application.personal_vault import register_personal_app as _register
    await _register(database=PersonalVaultDatabase(), user_id=user_id, app_id=app_id, name=name)


async def set_personal_app_status(*, user_id: int, app_id: str, active: bool) -> bool:
    from memory.application.personal_vault import set_personal_app_status as _set_status
    return await _set_status(database=PersonalVaultDatabase(), user_id=user_id, app_id=app_id, active=active)


async def list_acl_audit_events(*, user_id: int, limit: int = 100):
    from memory.application.personal_vault import list_acl_audit_events as _list_audit
    return await _list_audit(database=PersonalVaultDatabase(), user_id=user_id, limit=limit)


async def set_personal_access_rule(**kwargs) -> None:
    from memory.application.personal_vault import set_personal_access_rule as _set
    await _set(database=PersonalVaultDatabase(), **kwargs)


async def delete_personal_access_rule(**kwargs) -> bool:
    from memory.application.personal_vault import delete_personal_access_rule as _delete
    return await _delete(database=PersonalVaultDatabase(), **kwargs)
