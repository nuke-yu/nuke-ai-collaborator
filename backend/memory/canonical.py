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
    """Build the canonical dispatcher with the complete handler map."""
    composition = _runtime_composition()
    database = composition.database
    learning = CanonicalLearningService(database)
    projection_outbox = composition.projection_outbox
    from ai.client import call_ai_once
    fact_service = BotFactObservationService(
        database,
        projection_outbox,
    )
    fact_observer = CanonicalBotFactObserver(database, fact_service, call_ai_once)
    observation_loader = CanonicalObservationLoader(database)
    from core import config
    summary_observer = CanonicalSummaryObserver(
        database, call_ai_once, threshold=getattr(config, "SUMMARY_THRESHOLD", 5)
    )
    reflection_service = BotReflectionService(
        database,
        projection_outbox,
    )
    reflection_observer = CanonicalReflectionObserver(
        database,
        reflection_service,
        call_ai_once,
        min_facts=getattr(config, "REFLECT_MIN_FACTS", 5),
        importance_threshold=getattr(config, "REFLECT_IMPORTANCE_THRESHOLD", 3.0),
        max_insights=getattr(config, "REFLECT_MAX_INSIGHTS", 5),
        max_backlog=getattr(config, "REFLECT_MAX_BACKLOG", 50),
        max_level=getattr(config, "REFLECT_MAX_LEVEL", 2),
    )
    tool_compression_observer = CanonicalToolCompressionObserver(
        database,
        call_ai_once,
        threshold=getattr(config, "TOOL_EVENT_COMPRESS_THRESHOLD", 10),
        max_batch=getattr(config, "TOOL_EVENT_COMPRESS_MAX_BATCH", 50),
        max_insights=getattr(config, "TOOL_EVENT_COMPRESS_MAX_INSIGHTS", 5),
    )
    skill_projection = CanonicalSkillProjectionService(database)
    case_evaluator = CanonicalCaseEvaluator(database)
    experience_distiller = CanonicalExperienceDistiller(
        database,
        projection_outbox,
    )
    skill_compiler = CanonicalSkillCompiler(database)

    async def enqueue_observation(
        group_id: int, input_id: str, input_version: str
    ) -> dict[str, object]:
        child_types = (
            "observe_turn_fact",
            "observe_turn_summary",
            "observe_turn_reflection",
            "observe_turn_tool_compression",
        )
        scope = MemoryScope.group(
            group_id=group_id, actor_id="service:canonical_observation"
        )
        child_job_ids = [
            await learning.job_repository.enqueue(
                scope, job_type=job_type, input_id=input_id,
                input_version=input_version,
            )
            for job_type in child_types
        ]
        return {"child_job_ids": child_job_ids}

    async def observe_fact(
        group_id: int, input_id: str, _input_version: str
    ) -> dict[str, object]:
        event = await observation_loader.load(group_id, input_id)
        if event is None or not event.enabled:
            return {"stage": "fact", "skipped": True}
        record_ids = await fact_observer.observe(event)
        return {"stage": "fact", "skipped": False, "record_ids": list(record_ids)}

    async def observe_summary(
        group_id: int, input_id: str, _input_version: str
    ) -> dict[str, object]:
        event = await observation_loader.load(group_id, input_id)
        if event is None or not event.enabled:
            return {"stage": "summary", "skipped": True}
        return await summary_observer.observe(event)

    async def observe_reflection(
        group_id: int, input_id: str, _input_version: str
    ) -> dict[str, object]:
        event = await observation_loader.load(group_id, input_id)
        if event is None or not event.enabled:
            return {"stage": "reflection", "skipped": True}
        return await reflection_observer.observe(event)

    async def observe_tool_compression(
        group_id: int, input_id: str, _input_version: str
    ) -> dict[str, object]:
        event = await observation_loader.load(group_id, input_id)
        if event is None or not event.enabled:
            return {"stage": "tool_compression", "skipped": True}
        return await tool_compression_observer.observe(event)

    async def project_skill(
        group_id: int, skill_id: str, input_version: str
    ) -> dict[str, object]:
        path = await skill_projection.project(skill_id, group_id)
        return {"skill_id": skill_id, "path": path, "input_version": input_version}

    async def evaluate_case(
        group_id: int, case_id: str, input_version: str
    ) -> dict[str, object]:
        evaluation = await case_evaluator.evaluate(group_id, case_id)
        if evaluation["should_distill"]:
            scope = MemoryScope.group(
                group_id=group_id, actor_id="service:canonical_case_evaluator"
            )
            distill_job_id = await learning.job_repository.enqueue(
                scope, "distill_case", case_id, input_version
            )
            return {
                **evaluation,
                "case_id": case_id,
                "input_version": input_version,
                "distill_job_id": distill_job_id,
                "promotion_required": False,
            }
        return {**evaluation, "case_id": case_id, "input_version": input_version,
                "promotion_required": False}

    async def distill_case(
        group_id: int, case_id: str, input_version: str
    ) -> dict[str, object]:
        return await experience_distiller.distill(group_id, case_id, input_version)

    async def compile_skill_candidate(
        group_id: int, record_id: str, input_version: str
    ) -> dict[str, object]:
        return await skill_compiler.compile(group_id, record_id, input_version)

    return CanonicalPipelineDispatcher(
        learning.job_repository,
        {
            "observe_turn": enqueue_observation,
            "observe_turn_fact": observe_fact,
            "observe_turn_summary": observe_summary,
            "observe_turn_reflection": observe_reflection,
            "observe_turn_tool_compression": observe_tool_compression,
            "project_skill": project_skill,
            "evaluate_case": evaluate_case,
            "distill_case": distill_case,
            "compile_skill_candidate": compile_skill_candidate,
        },
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


async def set_personal_access_rule(**kwargs) -> None:
    from memory.application.personal_vault import set_personal_access_rule as _set
    await _set(database=PersonalVaultDatabase(), **kwargs)


async def delete_personal_access_rule(**kwargs) -> bool:
    from memory.application.personal_vault import delete_personal_access_rule as _delete
    return await _delete(database=PersonalVaultDatabase(), **kwargs)
