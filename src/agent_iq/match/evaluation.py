"""Quantitative routing evaluation over labelled discovery cases."""

from __future__ import annotations

import math
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from agent_iq.catalog.assets import DiscoveryCase
from agent_iq.contracts import CallerContext, MatchFilters, MatchRequest

from .engine import MatchEngine


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    expected: tuple[str, ...]
    ranked: tuple[str, ...]
    selected: tuple[str, ...]
    recall_at_k: float
    reciprocal_rank: float
    false_positives: tuple[str, ...]
    exact: bool
    duration_ms: float


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    cases: tuple[CaseResult, ...]
    recall_at_k: float
    mean_reciprocal_rank: float
    exact_accuracy: float
    false_positives: int
    p95_latency_ms: float
    semantic_backend: str

    @property
    def passes(self) -> bool:
        return (
            self.recall_at_k == 1.0
            and self.mean_reciprocal_rank == 1.0
            and self.exact_accuracy == 1.0
            and self.false_positives == 0
        )


def evaluate(
    engine: MatchEngine,
    cases: tuple[DiscoveryCase, ...],
    *,
    top_k: int = 3,
) -> EvaluationResult:
    results: list[CaseResult] = []
    total_agents = len(engine.catalog.agents)

    for case in cases:
        request = MatchRequest(
            request_id=case.case_id,
            intent=case.intent,
            caller=CallerContext(
                subject_id="eval-user",
                tenant_id="eval-tenant",
            ),
            filters=MatchFilters(
                max_candidates=total_agents,
                max_selected=5,
            ),
            requested_at=datetime(2026, 7, 10, tzinfo=UTC),
        )
        started = time.perf_counter()
        decision = engine.match(request)
        ranked = engine.rank(request)
        duration_ms = (time.perf_counter() - started) * 1_000
        ranked_ids = tuple(candidate.agent_id for candidate in ranked)
        selected_ids = tuple(reason.agent_id for reason in decision.selected)
        expected = case.expected_agent_ids
        false_positives = tuple(agent_id for agent_id in selected_ids if agent_id not in expected)
        results.append(
            CaseResult(
                case_id=case.case_id,
                expected=expected,
                ranked=ranked_ids,
                selected=selected_ids,
                recall_at_k=_recall(expected, ranked_ids[:top_k]),
                reciprocal_rank=_reciprocal_rank(expected, ranked_ids),
                false_positives=false_positives,
                exact=set(selected_ids) == set(expected),
                duration_ms=duration_ms,
            )
        )

    count = len(results)
    return EvaluationResult(
        cases=tuple(results),
        recall_at_k=_mean(result.recall_at_k for result in results),
        mean_reciprocal_rank=_mean(result.reciprocal_rank for result in results),
        exact_accuracy=_mean(1.0 if result.exact else 0.0 for result in results),
        false_positives=sum(len(result.false_positives) for result in results),
        p95_latency_ms=_percentile(
            [result.duration_ms for result in results],
            0.95,
        ),
        semantic_backend=engine.semantic_backend if count else "uninitialized",
    )


def _recall(expected: tuple[str, ...], ranked: tuple[str, ...]) -> float:
    return sum(agent_id in ranked for agent_id in expected) / len(expected)


def _reciprocal_rank(
    expected: tuple[str, ...],
    ranked: tuple[str, ...],
) -> float:
    for rank, agent_id in enumerate(ranked, start=1):
        if agent_id in expected:
            return 1.0 / rank
    return 0.0


def _mean(values: Iterable[float]) -> float:
    numbers = list(values)
    return sum(numbers) / len(numbers) if numbers else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]
