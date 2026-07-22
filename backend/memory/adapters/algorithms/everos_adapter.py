"""EverOS Case Algorithm Adapter implementing MemoryAlgorithmPort."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from memory.adapters.algorithms.everos_case_engine import (EverOSCaseEngine,
                                                           ExtractedCase)
from memory.contracts import AssembleCase, MemoryHit, ObserveMemory, RecallMemory
from memory.ports.infrastructure import AlgorithmDescriptor, MemoryAlgorithmPort


class EverOSCaseAlgorithmAdapter:
    """Adapter wrapping EverOS Agent Case Extraction and distillation gating."""

    descriptor = AlgorithmDescriptor(
        algorithm_id="nuke.everos.case_extractor",
        source="EverOS (everalgo)",
        version="v1.0",
        license="Apache-2.0",
        capabilities=("case_extraction", "outcome_evaluation", "distillation_gating"),
    )

    def __init__(self, engine: EverOSCaseEngine | None = None) -> None:
        self._engine = engine or EverOSCaseEngine()

    async def extract_case(self, command: AssembleCase) -> ExtractedCase:
        """Extract structured Case entity from AssembleCase command."""
        return self._engine.extract_case(
            run_id=command.run_id,
            task=command.task,
            outcome=command.outcome,
            tool_records=command.tool_records,
        )
