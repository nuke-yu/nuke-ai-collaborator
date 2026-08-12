"""Compatibility facade for callers migrating away from the old pipeline.

All persistence and dispatch behavior belongs to ``memory.application``.
This module intentionally contains no learning handlers or legacy database
routing; it can be removed after external callers finish importing the
canonical factories directly.
"""
from __future__ import annotations

from memory.canonical import build_learning_client, build_pipeline_dispatcher
from memory.application.pipeline import CanonicalPipelineJobRepository
from memory.domain import MemoryScope


async def process_case(case_id: str, group_id: int, *, input_version: str = "1") -> str:
    return await CanonicalPipelineJobRepository().enqueue(
        MemoryScope.group(group_id=group_id, actor_id="pipeline"),
        "evaluate_case", case_id, input_version,
    )


async def enqueue_turn_observation(
    *, message_id: int, bot_id: int, group_id: int, input_version: str = "1"
) -> str:
    repository = CanonicalPipelineJobRepository()
    return await repository.enqueue(
        MemoryScope.group(group_id=group_id, actor_id="pipeline"),
        "observe_turn", f"{message_id}:{bot_id}", input_version,
    )


async def enqueue_missing_turn_observations(group_id: int, *, limit: int = 100) -> int:
    return await build_learning_client().repair_observation_gaps(group_id, limit=limit)


async def dispatch_group(
    group_id: int,
    *,
    limit: int = 10,
    lease_seconds: int = 60,
    job_types: set[str] | frozenset[str] | None = None,
) -> dict[str, int]:
    # Canonical dispatch owns the complete handler map. ``job_types`` is kept
    # for source compatibility. Learning callers historically expected a
    # case's distill/compile/project children to finish in the same facade
    # call, so drain only that canonical chain while preserving observation
    # fan-out as independently leased stages.
    del job_types
    repository = CanonicalPipelineJobRepository()
    scope = MemoryScope.group(group_id=group_id, actor_id="pipeline")
    ready = await repository.list_ready(scope, limit=max(limit, 100))
    learning_chain = bool(ready) and all(
        str(job["job_type"]) in {
            "evaluate_case", "distill_case",
            "compile_skill_candidate", "project_skill",
        }
        for job in ready[: max(limit, 100)]
    )
    dispatcher = build_pipeline_dispatcher()
    result = await dispatcher.dispatch_group(
        group_id, limit=limit, lease_seconds=lease_seconds,
    )
    if learning_chain:
        for _ in range(4):
            next_ready = await repository.list_ready(scope, limit=max(limit, 100))
            if not next_ready or not all(
                str(job["job_type"]) in {
                    "evaluate_case", "distill_case",
                    "compile_skill_candidate", "project_skill",
                }
                for job in next_ready
            ):
                break
            await dispatcher.dispatch_group(
                group_id, limit=limit, lease_seconds=lease_seconds,
            )
    return result


async def job_stats(group_id: int) -> dict[str, int]:
    return dict(await CanonicalPipelineJobRepository().stats(
        MemoryScope.group(group_id=group_id, actor_id="pipeline")
    ))
