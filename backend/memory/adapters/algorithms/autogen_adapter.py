"""AutoGen Failure Insight Algorithm Adapter implementing MemoryAlgorithmPort."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from memory.adapters.algorithms.autogen_failure_engine import (
    AutoGenFailureEngine, FailureInsight)
from memory.contracts import MemoryHit, ObserveMemory, RecallMemory
from memory.ports.infrastructure import AlgorithmDescriptor, MemoryAlgorithmPort


class AutoGenFailureAlgorithmAdapter:
    """Adapter wrapping AutoGen Failure Learning and root cause diagnosis."""

    descriptor = AlgorithmDescriptor(
        algorithm_id="nuke.autogen.failure_insight",
        source="AutoGen (MIT)",
        version="v0.4",
        license="MIT",
        capabilities=("failure_diagnosis", "root_cause_analysis", "corrective_insight"),
    )

    def __init__(self, engine: AutoGenFailureEngine | None = None) -> None:
        self._engine = engine or AutoGenFailureEngine()

    async def analyze_failure(
        self,
        task: str,
        errors: Sequence[str],
        tool_records: Sequence[Mapping[str, Any]] = (),
        ai_call_fn: Any = None,
    ) -> FailureInsight:
        """Analyze failure trace and return structured FailureInsight using LLM Prompt & Rule Fallback."""
        if ai_call_fn is not None:
            return await self._engine.analyze_failure_with_llm(
                task, errors, tool_records, ai_call_fn=ai_call_fn
            )
        return self._engine.analyze_failure(task, errors, tool_records)

    analyze = analyze_failure
