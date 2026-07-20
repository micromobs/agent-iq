"""Reciprocal-rank fusion of lexical and semantic retrieval."""

from __future__ import annotations

from dataclasses import dataclass

from .lexical import BM25Index
from .semantic import SemanticIndex


@dataclass(frozen=True, slots=True)
class RawRankedAgent:
    agent_id: str
    fused_score: float
    lexical_score: float
    semantic_score: float
    lexical_rank: int
    semantic_rank: int


class HybridRanker:
    """Fuse BM25 and semantic ranks without comparing incompatible score scales."""

    def __init__(
        self,
        documents: dict[str, str],
        semantic: SemanticIndex,
        *,
        rrf_k: float = 60.0,
        lexical_weight: float = 0.5,
        semantic_weight: float = 0.5,
    ):
        self._ids = tuple(documents)
        self._lexical = BM25Index(documents)
        self._semantic = semantic
        self.backend_name = semantic.backend_name
        self._rrf_k = rrf_k
        self._lexical_weight = lexical_weight
        self._semantic_weight = semantic_weight

    def search(self, query: str) -> tuple[RawRankedAgent, ...]:
        lexical_scores = self._lexical.scores(query)
        semantic_scores = self._semantic.scores(query)
        lexical_ranks = _ranks(lexical_scores)
        semantic_ranks = _ranks(semantic_scores)

        results = [
            RawRankedAgent(
                agent_id=agent_id,
                fused_score=(
                    self._lexical_weight / (self._rrf_k + lexical_ranks[agent_id])
                    + self._semantic_weight / (self._rrf_k + semantic_ranks[agent_id])
                ),
                lexical_score=lexical_scores.get(agent_id, 0.0),
                semantic_score=semantic_scores.get(agent_id, 0.0),
                lexical_rank=lexical_ranks[agent_id],
                semantic_rank=semantic_ranks[agent_id],
            )
            for agent_id in self._ids
        ]
        return tuple(
            sorted(
                results,
                key=lambda result: (
                    -result.fused_score,
                    -result.lexical_score,
                    -result.semantic_score,
                    result.agent_id,
                ),
            )
        )


def _ranks(scores: dict[str, float]) -> dict[str, int]:
    order = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return {agent_id: index for index, (agent_id, _) in enumerate(order, start=1)}
