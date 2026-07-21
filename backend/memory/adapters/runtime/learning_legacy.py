"""Compatibility boundary for the existing durable learning pipeline."""
from __future__ import annotations

from memory.contracts import MemoryOperationError, ProcessLearningCase
from memory.domain import ScopeKind


class LegacyLearningAdapter:
    async def process_case(self, command: ProcessLearningCase) -> str:
        scope = command.scope
        if scope.kind not in (ScopeKind.GROUP, ScopeKind.BOT) or scope.group_id is None:
            raise MemoryOperationError("learning case processing requires group scope")
        from ai.pipeline import process_case
        return await process_case(command.case_id, scope.group_id)

