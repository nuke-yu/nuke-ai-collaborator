"""EverOS Case Clustering Engine (Semantic & Geometric Time Decay).

Ported from EverOS (everalgo) Case Management:
- Calculate dual-distance metrics (Semantic Similarity S_sem x Geometric Time Decay D_time).
- Group related cases into thematic clusters for experience distillation.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from typing import Sequence

from memory.adapters.algorithms.everos_case_engine import ExtractedCase


@dataclass(frozen=True, slots=True)
class CaseCluster:
    cluster_id: str
    cases: tuple[ExtractedCase, ...]
    centroid_signature: str
    updated_at: float


class EverOSClusteringEngine:
    """Audit-grade Case Clustering Engine with Dual-Distance (Semantic + Geometric Time)."""

    def __init__(self, half_life_days: float = 7.0, threshold: float = 0.5) -> None:
        self.half_life_seconds = half_life_days * 86400.0
        self.decay_lambda = math.log(2) / self.half_life_seconds if self.half_life_seconds > 0 else 0.0
        self.threshold = threshold

    def calculate_semantic_similarity(
        self,
        task_a: str,
        task_b: str,
        tools_a: Sequence[str] = (),
        tools_b: Sequence[str] = (),
    ) -> float:
        """Calculate Jaccard similarity across normalized token sets and tool sequences."""
        tokens_a = set(re.findall(r"\w+", (task_a or "").lower()))
        tokens_b = set(re.findall(r"\w+", (task_b or "").lower()))

        if not tokens_a or not tokens_b:
            task_sim = 0.0
        else:
            task_sim = len(tokens_a & tokens_b) / float(len(tokens_a | tokens_b))

        set_tools_a = set(tools_a)
        set_tools_b = set(tools_b)
        if not set_tools_a or not set_tools_b:
            tool_sim = 1.0 if not set_tools_a and not set_tools_b else 0.5
        else:
            tool_sim = len(set_tools_a & set_tools_b) / float(len(set_tools_a | set_tools_b))

        return 0.7 * task_sim + 0.3 * tool_sim

    def calculate_time_decay(self, timestamp_a: float, timestamp_b: float) -> float:
        """Calculate exponential geometric time decay factor e^(-lambda * delta_t)."""
        delta_t = abs(timestamp_a - timestamp_b)
        return math.exp(-self.decay_lambda * delta_t)

    def calculate_combined_score(
        self,
        case_a: ExtractedCase,
        time_a: float,
        case_b: ExtractedCase,
        time_b: float,
    ) -> float:
        """Score = SemanticSimilarity * GeometricTimeDecay."""
        sem = self.calculate_semantic_similarity(
            case_a.task, case_b.task, case_a.tools_used, case_b.tools_used
        )
        decay = self.calculate_time_decay(time_a, time_b)
        return sem * decay

    def cluster_cases(
        self,
        cases_with_timestamps: Sequence[tuple[ExtractedCase, float]],
    ) -> list[CaseCluster]:
        """Cluster cases into thematic clusters based on dual-distance metric threshold."""
        if not cases_with_timestamps:
            return []

        clusters: list[list[tuple[ExtractedCase, float]]] = []

        for case, ts in cases_with_timestamps:
            assigned = False
            for cluster in clusters:
                # Check score against centroid (first item in cluster)
                centroid_case, centroid_ts = cluster[0]
                score = self.calculate_combined_score(case, ts, centroid_case, centroid_ts)
                if score >= self.threshold:
                    cluster.append((case, ts))
                    assigned = True
                    break

            if not assigned:
                clusters.append([(case, ts)])

        result: list[CaseCluster] = []
        for idx, cluster_items in enumerate(clusters):
            case_tuple = tuple(item[0] for item in cluster_items)
            max_ts = max(item[1] for item in cluster_items)
            centroid_sig = cluster_items[0][0].task_signature
            result.append(
                CaseCluster(
                    cluster_id=f"cluster:{idx+1}:{centroid_sig[:8]}",
                    cases=case_tuple,
                    centroid_signature=centroid_sig,
                    updated_at=max_ts,
                )
            )

        return result
