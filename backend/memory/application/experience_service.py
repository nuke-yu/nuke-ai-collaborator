"""Experience distillation, recall, usage, and projection services."""
from __future__ import annotations

import re
from typing import Any

from memory.application import CanonicalExperienceDistiller, CanonicalLearningService
from memory.contracts import AssembleCase, CompleteExperienceUsage, RecallExperiences
from memory.domain import MemoryScope, UsageKind


def _terms(text: str) -> set[str]:
    value = (text or "").lower()
    terms = set(re.findall(r"[a-z0-9_]{2,}", value))
    zh = "".join(re.findall(r"[\u4e00-\u9fff]", value))
    terms.update(zh[i:i + 2] for i in range(max(0, len(zh) - 1)))
    return terms


def _scope(group_id: int, bot_id: int | None = None, *, run_id: str | None = None) -> MemoryScope:
    if bot_id is not None and bot_id > 0:
        return MemoryScope.bot(group_id=group_id, bot_id=bot_id, actor_id=f"bot:{bot_id}", run_id=run_id)
    return MemoryScope.group(group_id=group_id, actor_id="service:experience_memory", run_id=run_id, bot_id=bot_id)


async def distill_case(case_id: str, group_id: int | None) -> str | None:
    if group_id is None:
        return None
    from memory.application.context import require_experience_distiller, require_projection_outbox
    result = await require_experience_distiller().distill(group_id, case_id)
    if result.get("record_id"):
        await require_projection_outbox().drain(
            group_id, limit=1, event_id=f"experience-vector:{result['record_id']}"
        )
    return result.get("record_id")


async def recall_experiences(*, query: str, run_id: str, group_id: int | None,
                             bot_id: int | None, limit: int = 2,
                             char_budget: int = 2400) -> tuple[str, list[str]]:
    if group_id is None or bot_id is None:
        return "", []
    from memory.application.context import require_learning
    return await require_learning().recall_experiences(
        RecallExperiences(scope=_scope(group_id, bot_id, run_id=run_id), query=query,
                          run_id=run_id, limit=limit, char_budget=char_budget)
    )


async def complete_usage(*, record_ids: list[str], run_id: str, group_id: int | None,
                         outcome: str, input_tokens: int, output_tokens: int,
                         tool_attempts: int) -> None:
    if group_id is None:
        return
    from memory.application.context import require_learning
    await require_learning().record_completion_telemetry(type("Completion", (), {
        "scope": _scope(group_id, run_id=run_id), "kind": UsageKind.EXPERIENCE,
        "item_ids": tuple(record_ids), "run_id": run_id, "outcome": outcome,
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "tool_attempts": tool_attempts,
    })())


async def decay_experiences(group_id: int, *, now_ms: int | None = None,
                            stale_days: int = 90) -> int:
    from memory.application.context import require_learning
    return await require_learning().decay_experiences(
        group_id, now_ms=now_ms, stale_days=stale_days
    )


async def reconcile_experience_projections(group_id: int) -> int:
    from memory.application.context import require_projection_reconciler
    return await require_projection_reconciler().reconcile(group_id)


__all__ = [
    "_terms", "distill_case", "recall_experiences", "complete_usage",
    "decay_experiences", "reconcile_experience_projections",
]
