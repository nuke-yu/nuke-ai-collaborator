"""Pipeline wiring owned by the Memory composition boundary.

This module is intentionally outside ``application`` and ``canonical``.  It
may choose the product's concrete model/configuration adapters, while the
pipeline use cases remain unaware of them.
"""
from __future__ import annotations

from memory.application import (
    BotFactObservationService,
    BotReflectionService,
    CanonicalBotFactObserver,
    CanonicalCaseEvaluator,
    CanonicalExperienceDistiller,
    CanonicalLearningService,
    CanonicalObservationLoader,
    CanonicalReflectionObserver,
    CanonicalSkillCompiler,
    CanonicalSkillProjectionService,
    CanonicalSummaryObserver,
    CanonicalToolCompressionObserver,
)
from memory.application.pipeline import CanonicalPipelineDispatcher
from memory.composition import MemoryComposition
from memory.domain import MemoryScope
from memory.infrastructure.pipeline_jobs import CanonicalPipelineJobRepository


def build_pipeline_dispatcher(composition: MemoryComposition) -> CanonicalPipelineDispatcher:
    """Build all pipeline handlers against the supplied composition only."""
    required = {
        "member_directory": composition.member_directory,
        "fact_engine": composition.fact_engine,
        "skill_workspace": composition.skill_workspace,
        "settings": composition.settings,
        "model": composition.model,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise RuntimeError(
            "Memory composition is incomplete; missing: " + ", ".join(missing)
        )
    database = composition.database
    projection_outbox = composition.projection_outbox
    job_repository = CanonicalPipelineJobRepository(database)
    learning = CanonicalLearningService(database, job_repository)
    model = composition.model
    settings = composition.settings

    fact_service = BotFactObservationService(database, projection_outbox)
    fact_observer = CanonicalBotFactObserver(
        database, fact_service, model, composition.fact_engine
    )
    observation_loader = CanonicalObservationLoader(
        database, composition.member_directory
    )
    summary_observer = CanonicalSummaryObserver(
        database, model, threshold=settings.get("SUMMARY_THRESHOLD", 5)
    )
    reflection_service = BotReflectionService(database, projection_outbox)
    reflection_observer = CanonicalReflectionObserver(
        database,
        reflection_service,
        model,
        min_facts=settings.get("REFLECT_MIN_FACTS", 5),
        importance_threshold=settings.get("REFLECT_IMPORTANCE_THRESHOLD", 3.0),
        max_insights=settings.get("REFLECT_MAX_INSIGHTS", 5),
        max_backlog=settings.get("REFLECT_MAX_BACKLOG", 50),
        max_level=settings.get("REFLECT_MAX_LEVEL", 2),
    )
    tool_compression_observer = CanonicalToolCompressionObserver(
        database,
        model,
        threshold=settings.get("TOOL_EVENT_COMPRESS_THRESHOLD", 10),
        max_batch=settings.get("TOOL_EVENT_COMPRESS_MAX_BATCH", 50),
        max_insights=settings.get("TOOL_EVENT_COMPRESS_MAX_INSIGHTS", 5),
    )
    skill_projection = CanonicalSkillProjectionService(
        database, composition.skill_workspace
    )
    case_evaluator = CanonicalCaseEvaluator(database)
    experience_distiller = CanonicalExperienceDistiller(
        database, projection_outbox, job_repository
    )
    skill_compiler = CanonicalSkillCompiler(database, job_repository)

    async def enqueue_observation(group_id: int, input_id: str, input_version: str):
        child_types = (
            "observe_turn_fact", "observe_turn_summary", "observe_turn_reflection",
            "observe_turn_tool_compression",
        )
        scope = MemoryScope.group(group_id=group_id, actor_id="service:canonical_observation")
        child_job_ids = [
            await learning.job_repository.enqueue(scope, job_type, input_id, input_version)
            for job_type in child_types
        ]
        return {"child_job_ids": child_job_ids}

    async def observe_fact(group_id: int, input_id: str, _input_version: str):
        event = await observation_loader.load(group_id, input_id)
        if event is None or not event.enabled:
            return {"stage": "fact", "skipped": True}
        record_ids = await fact_observer.observe(event)
        return {"stage": "fact", "skipped": False, "record_ids": list(record_ids)}

    async def observe_summary(group_id: int, input_id: str, _input_version: str):
        event = await observation_loader.load(group_id, input_id)
        if event is None or not event.enabled:
            return {"stage": "summary", "skipped": True}
        return await summary_observer.observe(event)

    async def observe_reflection(group_id: int, input_id: str, _input_version: str):
        event = await observation_loader.load(group_id, input_id)
        if event is None or not event.enabled:
            return {"stage": "reflection", "skipped": True}
        return await reflection_observer.observe(event)

    async def observe_tool_compression(group_id: int, input_id: str, _input_version: str):
        event = await observation_loader.load(group_id, input_id)
        if event is None or not event.enabled:
            return {"stage": "tool_compression", "skipped": True}
        return await tool_compression_observer.observe(event)

    async def project_skill(group_id: int, skill_id: str, input_version: str):
        path = await skill_projection.project(skill_id, group_id)
        return {"skill_id": skill_id, "path": path, "input_version": input_version}

    async def evaluate_case(group_id: int, case_id: str, input_version: str):
        evaluation = await case_evaluator.evaluate(group_id, case_id)
        if evaluation["should_distill"]:
            scope = MemoryScope.group(group_id=group_id, actor_id="service:canonical_case_evaluator")
            distill_job_id = await learning.job_repository.enqueue(
                scope, "distill_case", case_id, input_version
            )
            return {
                **evaluation, "case_id": case_id, "input_version": input_version,
                "distill_job_id": distill_job_id, "promotion_required": False,
            }
        return {**evaluation, "case_id": case_id, "input_version": input_version,
                "promotion_required": False}

    async def distill_case(group_id: int, case_id: str, input_version: str):
        return await experience_distiller.distill(group_id, case_id, input_version)

    async def compile_skill_candidate(group_id: int, record_id: str, input_version: str):
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
