"""RRF + MMR Hybrid Search & Rerank Engine (Cormack et al. / Carbonell & Goldstein ported algorithm).

Ported core algorithms:
1. Reciprocal Rank Fusion (RRF):
   RRF_Score(d) = sum_{m in M} 1 / (k + r_m(d)), k=60
2. Maximal Marginal Relevance (MMR):
   MMR = argmax_{d_i in R \\ S} [ lambda * Sim_1(d_i, q) - (1-lambda) * max_{d_j in S} Sim_2(d_i, d_j) ], lambda=0.7
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence


class HybridRerankEngine:
    """Audit-grade RRF + MMR Hybrid Search and Diversity Reranking Engine."""

    def __init__(self, rrf_k: int = 60, mmr_lambda: float = 0.7) -> None:
        self.rrf_k = rrf_k
        self.mmr_lambda = mmr_lambda

    def rrf_fusion(
        self, rank_lists: Sequence[Sequence[Mapping[str, Any]]]
    ) -> list[dict[str, Any]]:
        """Combine multiple ranked candidate lists using Reciprocal Rank Fusion (RRF).

        Formula: RRF_Score(d) = sum_{m in M} 1.0 / (k + r_m(d))
        """
        scores: dict[str, float] = {}
        candidate_map: dict[str, dict[str, Any]] = {}

        for rank_list in rank_lists:
            for rank, item in enumerate(rank_list, start=1):
                item_id = str(item.get("id") or item.get("record_id") or item.get("content"))
                candidate_map[item_id] = dict(item)
                rrf_increment = 1.0 / (self.rrf_k + rank)
                scores[item_id] = scores.get(item_id, 0.0) + rrf_increment

        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
        results: list[dict[str, Any]] = []
        for item_id in sorted_ids:
            elem = candidate_map[item_id]
            elem["rrf_score"] = scores[item_id]
            results.append(elem)

        return results

    def mmr_diversify(
        self,
        candidates: Sequence[Mapping[str, Any]],
        query: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Diversify search results using Maximal Marginal Relevance (MMR).

        Formula: MMR = argmax_{d_i} [ lambda * Sim1(d_i, q) - (1-lambda) * max_{d_j} Sim2(d_i, d_j) ]
        """
        if not candidates:
            return []

        query_tokens = set(re.findall(r"\w+", (query or "").lower()))
        unselected = list(candidates)
        selected: list[dict[str, Any]] = []

        max_rrf = max((float(c.get("rrf_score") or c.get("score") or 0.0) for c in candidates), default=1.0) or 1.0

        while unselected and len(selected) < top_k:
            best_item = None
            best_mmr_score = -float("inf")

            for candidate in unselected:
                cand_text = str(candidate.get("content") or "")
                cand_tokens = set(re.findall(r"\w+", cand_text.lower()))

                # Sim1: Weighted combination of RRF rank score and lexical token similarity
                raw_rrf = float(candidate.get("rrf_score") or candidate.get("score") or 0.0)
                norm_rrf = raw_rrf / max_rrf
                jaccard = (len(query_tokens & cand_tokens) / float(len(query_tokens | cand_tokens))) if (query_tokens and cand_tokens) else 0.0
                sim1 = 0.5 * norm_rrf + 0.5 * jaccard

                # Sim2: Max similarity to already selected items
                sim2 = 0.0
                if selected:
                    sim2_scores = []
                    for sel in selected:
                        sel_text = str(sel.get("content") or "")
                        sel_tokens = set(re.findall(r"\w+", sel_text.lower()))
                        if cand_tokens and sel_tokens:
                            s = len(cand_tokens & sel_tokens) / float(len(cand_tokens | sel_tokens))
                        else:
                            s = 0.0
                        sim2_scores.append(s)
                    sim2 = max(sim2_scores) if sim2_scores else 0.0

                mmr_score = self.mmr_lambda * sim1 - (1.0 - self.mmr_lambda) * sim2
                if mmr_score > best_mmr_score:
                    best_mmr_score = mmr_score
                    best_item = candidate

            if best_item is not None:
                selected.append(dict(best_item))
                unselected.remove(best_item)
            else:
                break

        return selected

    def rerank(
        self,
        keyword_hits: Sequence[Mapping[str, Any]],
        vector_hits: Sequence[Mapping[str, Any]],
        query: str,
        top_k: int = 5,
        cluster_hits: Sequence[Mapping[str, Any]] = (),
        graph_hits: Sequence[Mapping[str, Any]] = (),
    ) -> list[dict[str, Any]]:
        """Execute complete RRF Fusion + MMR Diversification pipeline."""
        rank_lists = [keyword_hits, vector_hits]
        if cluster_hits:
            rank_lists.append(cluster_hits)
        if graph_hits:
            rank_lists.append(graph_hits)
        fused = self.rrf_fusion(rank_lists)
        diversified = self.mmr_diversify(fused, query, top_k=top_k)
        return diversified
