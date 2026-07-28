"""Compatibility adapter for the pre-module conversation memory provider.

All knowledge of the old ``ai.memory_provider`` DTOs is intentionally confined
to this adapter. It can be deleted after callers and algorithms have migrated.
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol

from memory.contracts import (
    ForgetMemory,
    MemoryOperationError,
    ObserveMemory,
    RecallMemory,
    RecallResult,
)
from memory.domain import ScopeKind


class LegacyMemoryProvider(Protocol):
    async def recall(self, ctx: Any) -> str: ...
    async def observe(self, ev: Any) -> None: ...
    async def forget(self, bot_id: int, group_id: int | None) -> None: ...


class LegacyConversationMemoryAdapter:
    """Translate stable module contracts to the existing provider API with Mem0 algorithm extraction."""

    def __init__(
        self,
        provider: LegacyMemoryProvider,
        fact_algorithm: Any = None,
    ) -> None:
        self._provider = provider
        if fact_algorithm is None:
            from memory.adapters.algorithms.mem0_adapter import Mem0FactAlgorithmAdapter

            fact_algorithm = Mem0FactAlgorithmAdapter()
        self._fact_algorithm = fact_algorithm

    async def recall(self, query: RecallMemory) -> RecallResult:
        bot_id = self._require_bot_scope(query.scope.kind, query.scope.bot_id)
        from ai.memory_provider import MemoryContext

        metadata = query_metadata(query)
        context = MemoryContext(
            bot_id=bot_id,
            group_id=query.scope.group_id,
            role=str(metadata.get("role", "")),
            query=query.query,
            history=_optional_list(metadata.get("history")),
            thread_id=query.scope.thread_id,
        )
        rendered = await self._provider.recall(context)
        return RecallResult(
            rendered_context=rendered,
            algorithm_trace=(
                {"algorithm_id": "nuke.legacy.chroma", "version": "v1"},
            ),
            degraded=True,
        )

    async def observe(self, command: ObserveMemory) -> None:
        bot_id = self._require_bot_scope(command.scope.kind, command.scope.bot_id)
        metadata = command.metadata
        message_id = metadata.get("message_id")
        if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id <= 0:
            raise MemoryOperationError("legacy conversation observe requires a positive message_id")
        if not getattr(self._provider, "durable_observation_enabled", True):
            return
        group_id = command.scope.group_id
        if group_id is None:
            raise MemoryOperationError("legacy conversation observe requires group scope")
        from ai.pipeline import enqueue_turn_observation
        await enqueue_turn_observation(
            message_id=message_id,
            bot_id=bot_id,
            group_id=group_id,
        )


    async def forget(self, command: ForgetMemory) -> None:
        bot_id = self._require_bot_scope(command.scope.kind, command.scope.bot_id)
        if command.record_ids:
            raise MemoryOperationError("legacy provider only supports forgetting the complete bot scope")
        await self._provider.forget(bot_id, command.scope.group_id)

    @staticmethod
    def _require_bot_scope(kind: ScopeKind, bot_id: int | None) -> int:
        if kind is not ScopeKind.BOT or bot_id is None:
            raise MemoryOperationError("legacy conversation memory requires bot scope")
        return bot_id


def query_metadata(query: RecallMemory) -> Mapping[str, Any]:
    """Read optional transitional metadata without expanding the stable query."""
    metadata = getattr(query, "metadata", None)
    return metadata if isinstance(metadata, Mapping) else {}


def _optional_list(value: Any) -> list | None:
    return value if isinstance(value, list) else None
