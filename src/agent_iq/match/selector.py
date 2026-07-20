"""Deterministic single- and multi-clause agent selection."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from agent_iq.contracts import SelectionReason

from .lexical import tokenize

_CLAUSE_SPLIT = re.compile(
    r"\band\b|\bplus\b|\bthen\b|\bnext\b|\bafter\s+that\b|"
    r"\bfollowed\s+by\b|\bbefore\b|\bafter\b|[;,]",
    re.IGNORECASE,
)


def derive_query(intent: str) -> str:
    """Build a stable retrieval query from unique salient intent tokens."""

    unique: list[str] = []
    for token in tokenize(intent):
        if token not in unique:
            unique.append(token)
    return " ".join(unique[:16]) or intent


def split_clauses(intent: str, minimum_words: int = 2) -> tuple[str, ...]:
    """Split only on explicit task conjunctions and discard tiny fragments."""

    return tuple(
        part.strip()
        for part in _CLAUSE_SPLIT.split(intent)
        if len(part.strip().split()) >= minimum_words
    )


@dataclass(frozen=True, slots=True)
class SelectableCandidate:
    agent_id: str
    agent_name: str
    fused_score: float
    lexical_score: float
    semantic_score: float
    matched_skills: tuple[str, ...]
    matched_tags: tuple[str, ...]
    policy_facts: tuple[str, ...]


class DeterministicSelector:
    """Select a primary agent and one additional agent per distinct task clause."""

    name = "deterministic-v1"

    def __init__(
        self,
        *,
        max_agents: int,
        lexical_floor: float = 1.2,
        semantic_floor: float = 0.06,
    ):
        self.max_agents = max_agents
        self.lexical_floor = lexical_floor
        self.semantic_floor = semantic_floor

    def select(
        self,
        intent: str,
        ranked: tuple[SelectableCandidate, ...],
        rank_clause: Callable[[str], tuple[SelectableCandidate, ...]],
    ) -> tuple[SelectionReason, ...]:
        if not ranked or not self._clears_floor(ranked[0]):
            return ()

        selected: list[tuple[SelectableCandidate, str]] = [(ranked[0], intent)]
        clauses = split_clauses(intent)
        if len(clauses) >= 2:
            for clause in clauses:
                if len(selected) >= self.max_agents:
                    break
                clause_ranked = rank_clause(clause)
                if not clause_ranked:
                    continue
                candidate = clause_ranked[0]
                if not self._clears_floor(candidate):
                    continue
                existing = next(
                    (
                        index
                        for index, (item, _) in enumerate(selected)
                        if item.agent_id == candidate.agent_id
                    ),
                    None,
                )
                if existing is not None:
                    if selected[existing][1] == intent:
                        selected[existing] = (candidate, clause)
                    continue
                selected.append((candidate, clause))

        return tuple(
            SelectionReason(
                agent_id=candidate.agent_id,
                work_item_id=f"work-{index}",
                work_item=work_item,
                summary=f"Selected {candidate.agent_name} for: {work_item}",
                matched_skills=candidate.matched_skills,
                matched_tags=candidate.matched_tags,
                fused_score=candidate.fused_score,
                policy_facts=candidate.policy_facts,
            )
            for index, (candidate, work_item) in enumerate(selected, start=1)
        )

    def _clears_floor(self, candidate: SelectableCandidate) -> bool:
        return (
            candidate.lexical_score >= self.lexical_floor
            and candidate.semantic_score >= self.semantic_floor
        )
