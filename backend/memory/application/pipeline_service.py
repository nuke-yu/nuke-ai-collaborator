"""Canonical application operations for Memory learning pipelines."""
from __future__ import annotations

from memory.application.context import require_learning, require_pipeline
from memory.domain import MemoryScope


async def process_case(case_id: str, group_id: int, *, input_version: str = "1") -> str:
    return await require_learning().job_repository.enqueue(
        MemoryScope.group(group_id=group_id, actor_id="pipeline"),
        "evaluate_case", case_id, input_version,
    )


async def enqueue_turn_observation(
    *, message_id: int, bot_id: int, group_id: int, input_version: str = "1"
) -> str:
    repository = require_learning().job_repository
    return await repository.enqueue(
        MemoryScope.group(group_id=group_id, actor_id="pipeline"),
        "observe_turn", f"{message_id}:{bot_id}", input_version,
    )


async def enqueue_missing_turn_observations(group_id: int, *, limit: int = 100) -> int:
    return await require_learning().repair_observation_gaps(group_id, limit=limit)


async def dispatch_group(
    group_id: int,
    *,
    limit: int = 10,
    lease_seconds: int = 60,
) -> dict[str, int]:
    repository = require_learning().job_repository
    scope = MemoryScope.group(group_id=group_id, actor_id="pipeline")
    ready = await repository.list_ready(scope, limit=max(limit, 100))
    learning_chain = bool(ready) and all(
        str(job["job_type"]) in {
            "evaluate_case", "distill_case",
            "compile_skill_candidate", "project_skill",
        }
        for job in ready[: max(limit, 100)]
    )
    dispatcher = require_pipeline()
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
    return dict(await require_learning().job_repository.stats(
        MemoryScope.group(group_id=group_id, actor_id="pipeline")
    ))
