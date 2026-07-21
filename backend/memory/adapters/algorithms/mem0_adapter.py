"""Mem0 Algorithm Adapter implementing MemoryAlgorithmPort."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from memory.adapters.algorithms.mem0_fact_engine import FactAction, Mem0FactEngine
from memory.contracts import MemoryHit, ObserveMemory, RecallMemory
from memory.ports.infrastructure import AlgorithmDescriptor, MemoryAlgorithmPort


class Mem0FactAlgorithmAdapter:
    """Adapter wrapping Mem0 fact extraction and ADD/UPDATE/DELETE resolution."""

    descriptor = AlgorithmDescriptor(
        algorithm_id="nuke.mem0.fact_pipeline",
        source="mem0 (mem0ai)",
        version="v1.1",
        license="Apache-2.0",
        capabilities=("fact_extraction", "conflict_resolution", "add_update_delete"),
    )

    def __init__(self, engine: Mem0FactEngine | None = None) -> None:
        self._engine = engine or Mem0FactEngine()

    async def extract(
        self, command: ObserveMemory, existing_records: Sequence[Mapping[str, Any]] = ()
    ) -> Sequence[Mapping[str, Any]]:
        """Extract candidate facts and reconcile actions against existing records."""
        candidates = self._engine.extract_candidate_facts(command.content)
        actions: list[dict[str, Any]] = []

        for fact_text in candidates:
            action: FactAction = self._engine.reconcile_fact(existing_records, fact_text)
            actions.append(
                {
                    "action": str(action.action_type),
                    "content": action.content,
                    "target_record_id": action.target_record_id,
                    "old_content": action.old_content,
                    "confidence": action.confidence,
                    "reason": action.reason,
                    "source_id": command.source_id,
                    "scope_kind": str(command.scope.kind),
                    "group_id": command.scope.group_id,
                    "bot_id": command.scope.bot_id,
                    "user_id": command.scope.user_id,
                }
            )

        return actions

    async def retrieve(self, query: RecallMemory) -> Sequence[MemoryHit]:
        """Placeholder for algorithm-native retrieval (delegated to repository)."""
        return ()
