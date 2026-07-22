"""RRF + MMR Hybrid Rerank Algorithm Adapter implementing MemoryAlgorithmPort."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from memory.adapters.algorithms.hybrid_rerank_engine import HybridRerankEngine
from memory.contracts import MemoryHit, ObserveMemory, RecallMemory
from memory.ports.infrastructure import AlgorithmDescriptor, MemoryAlgorithmPort


class HybridRerankAlgorithmAdapter:
    """Adapter wrapping RRF Fusion and MMR Diversity Search Reranker."""

    descriptor = AlgorithmDescriptor(
        algorithm_id="nuke.hybrid.rrf_mmr_rerank",
        source="RRF (Cormack et al.) / MMR (Carbonell & Goldstein)",
        version="v1.0",
        license="Apache-2.0",
        capabilities=("rrf_fusion", "mmr_diversification", "cross_encoder_rerank"),
    )

    def __init__(self, engine: HybridRerankEngine | None = None) -> None:
        self._engine = engine or HybridRerankEngine()

    async def rerank(
        self,
        keyword_hits: Sequence[Mapping[str, Any]],
        vector_hits: Sequence[Mapping[str, Any]],
        query: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Rerank candidates using RRF and MMR diversification."""
        return self._engine.rerank(keyword_hits, vector_hits, query, top_k=top_k)

    async def extract(self, command: ObserveMemory) -> Sequence[Mapping[str, Any]]:
        """Placeholder for observation extraction."""
        return ()

    async def retrieve(self, query: RecallMemory) -> Sequence[MemoryHit]:
        """Placeholder for algorithm-native retrieval."""
        return ()
