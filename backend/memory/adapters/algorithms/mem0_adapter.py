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
        self,
        command: ObserveMemory,
        existing_records: Sequence[Mapping[str, Any]] = (),
        ai_call_fn: Any = None,
    ) -> Sequence[Mapping[str, Any]]:
        """Extract candidate facts and reconcile actions against existing records using LLM Prompt & Rule Fallback."""
        if ai_call_fn is not None:
            actions_objs = await self._engine.reconcile_with_llm(
                command.content, existing_records, ai_call_fn=ai_call_fn
            )
        else:
            candidates = self._engine.extract_candidate_facts(command.content)
            actions_objs = [self._engine.reconcile_fact(existing_records, c) for c in candidates]

        actions: list[dict[str, Any]] = []
        for action in actions_objs:
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
