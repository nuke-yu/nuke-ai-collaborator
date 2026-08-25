"""Low-cardinality evaluation counters for Memory quality and economics.

The registry is process-local by design; callers periodically export its
snapshot through the existing Worker metrics snapshot rather than sharing
Prometheus exposition text across processes.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable, Mapping


@dataclass
class MemoryEvaluation:
    graph_resolution_total: int = 0
    graph_resolution_correct: int = 0
    retrieval_queries: int = 0
    retrieval_relevant_hits: int = 0
    retrieval_relevant_total: int = 0
    skill_reuse_total: int = 0
    skill_reuse_success: int = 0
    operation_count: int = 0
    operation_latency_ms_total: float = 0.0
    operation_cost_total: float = 0.0
    tokenizer_samples: int = 0
    tokenizer_abs_error_total: float = 0.0
    _started: dict[str, float] = field(default_factory=dict, repr=False)

    def record_graph_resolution(self, predicted: Iterable[str], expected: Iterable[str]) -> None:
        p, e = set(predicted), set(expected)
        self.graph_resolution_total += 1
        if p == e:
            self.graph_resolution_correct += 1

    def record_retrieval(self, retrieved_ids: Iterable[str], relevant_ids: Iterable[str]) -> None:
        retrieved, relevant = set(retrieved_ids), set(relevant_ids)
        self.retrieval_queries += 1
        self.retrieval_relevant_hits += len(retrieved & relevant)
        self.retrieval_relevant_total += len(relevant)

    def record_skill_reuse(self, success: bool) -> None:
        self.skill_reuse_total += 1
        self.skill_reuse_success += int(bool(success))

    def record_tokenizer_calibration(self, estimated: int, actual: int) -> None:
        self.tokenizer_samples += 1
        self.tokenizer_abs_error_total += abs(int(estimated) - int(actual))

    def start_operation(self, operation_id: str) -> None:
        self._started[str(operation_id)] = time.perf_counter()

    def finish_operation(self, operation_id: str, *, cost: float = 0.0) -> None:
        started = self._started.pop(str(operation_id), None)
        if started is None:
            return
        self.operation_count += 1
        self.operation_latency_ms_total += (time.perf_counter() - started) * 1000
        self.operation_cost_total += max(0.0, float(cost))

    def snapshot(self) -> dict[str, float | int]:
        snapshot = {
            "graph_resolution_accuracy": self.graph_resolution_correct / self.graph_resolution_total
            if self.graph_resolution_total else 0.0,
            "retrieval_recall": self.retrieval_relevant_hits / self.retrieval_relevant_total
            if self.retrieval_relevant_total else 0.0,
            "skill_reuse_success_rate": self.skill_reuse_success / self.skill_reuse_total
            if self.skill_reuse_total else 0.0,
            "operation_count": self.operation_count,
            "operation_latency_ms_avg": self.operation_latency_ms_total / self.operation_count
            if self.operation_count else 0.0,
            "operation_cost_total": self.operation_cost_total,
            "tokenizer_abs_error_avg": self.tokenizer_abs_error_total / self.tokenizer_samples
            if self.tokenizer_samples else 0.0,
        }
        try:
            from executors.tokenizer_calibration import reports
            snapshot["tokenizer_configured_models"] = len(reports())
        except Exception:
            snapshot["tokenizer_configured_models"] = 0
        return snapshot


memory_evaluation = MemoryEvaluation()


def export_memory_evaluation() -> Mapping[str, float | int]:
    """Return a bounded snapshot suitable for structured IPC metrics."""
    return dict(memory_evaluation.snapshot())
