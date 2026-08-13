"""Canonical Learning usage transition services."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from memory.canonical import build_learning_client
from memory.contracts import MarkUsageAdopted, MarkUsageExecuted, VerifyUsage
from memory.domain import MemoryScope, UsageKind, UsageState


def _service() -> CanonicalLearningService:
    return build_learning_client()


def _scope(group_id: int) -> MemoryScope:
    return MemoryScope.group(group_id=group_id, actor_id="service:learning_usage")


async def mark_adopted(
    *, kind: UsageKind, item_ids: Sequence[str], run_id: str,
    group_id: int | None, adopted_via: str, evidence: Mapping[str, Any],
) -> int:
    if group_id is None:
        return 0
    return await _service().mark_usage_adopted(MarkUsageAdopted(
        scope=_scope(group_id), kind=kind, item_ids=tuple(item_ids),
        run_id=run_id, adopted_via=adopted_via, evidence=evidence,
    ))


async def mark_executed(
    *, kind: UsageKind, item_ids: Sequence[str], run_id: str,
    group_id: int | None, evidence: Mapping[str, Any],
) -> int:
    if group_id is None:
        return 0
    return await _service().mark_usage_executed(MarkUsageExecuted(
        scope=_scope(group_id), kind=kind, item_ids=tuple(item_ids),
        run_id=run_id, evidence=evidence,
    ))


async def mark_verified(
    *, kind: UsageKind, item_ids: Sequence[str], run_id: str,
    group_id: int | None, status: UsageState, evidence: Mapping[str, Any],
) -> int:
    if group_id is None:
        return 0
    return await _service().verify_usage(VerifyUsage(
        scope=_scope(group_id), kind=kind, item_ids=tuple(item_ids),
        run_id=run_id, status=status, evidence=evidence,
    ))


async def record_completion(
    *, kind: UsageKind, item_ids: Sequence[str], run_id: str,
    group_id: int | None, outcome: str, input_tokens: int = 0,
    output_tokens: int = 0, tool_attempts: int = 0,
) -> int:
    """Record terminal completion telemetry without changing usage state."""
    if group_id is None or not item_ids:
        return 0
    service = _service()
    scope = _scope(group_id)
    class _Completion:
        pass
    command = _Completion()
    command.scope = scope
    command.kind = kind
    command.item_ids = tuple(item_ids)
    command.run_id = run_id
    command.outcome = outcome
    command.input_tokens = input_tokens
    command.output_tokens = output_tokens
    command.tool_attempts = tool_attempts
    return await service.record_completion_telemetry(command)
