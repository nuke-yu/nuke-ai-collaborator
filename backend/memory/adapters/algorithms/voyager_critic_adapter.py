"""Voyager Critic Algorithm Adapter implementing MemoryAlgorithmPort."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from memory.adapters.algorithms.voyager_critic_engine import (
    CriticResult, VoyagerCriticEngine)
from memory.contracts import MemoryHit, ObserveMemory, RecallMemory
from memory.ports.infrastructure import AlgorithmDescriptor, MemoryAlgorithmPort


class VoyagerCriticAlgorithmAdapter:
    """Adapter wrapping Voyager Critic Environmental Verification Engine."""

    descriptor = AlgorithmDescriptor(
        algorithm_id="nuke.voyager.critic_gate",
        source="Voyager (GPL-3.0 / MIT)",
        version="v1.0",
        license="MIT",
        capabilities=("critic_verification", "success_gating", "env_critique"),
    )

    def __init__(self, engine: VoyagerCriticEngine | None = None) -> None:
        self._engine = engine or VoyagerCriticEngine()

    async def evaluate_success(
        self,
        task: str,
        outcome: str,
        tool_records: Sequence[Mapping[str, Any]] = (),
        error_traces: Sequence[str] = (),
        ai_call_fn: Any = None,
    ) -> CriticResult:
        """Evaluate task completion success using environmental critic engine."""
        return await self._engine.evaluate_success_with_llm(
            task, outcome, tool_records, error_traces, ai_call_fn=ai_call_fn
        )

    evaluate = evaluate_success
