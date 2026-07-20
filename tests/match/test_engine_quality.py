"""The 20-agent consumer Match quality and determinism gates."""

from __future__ import annotations

from datetime import UTC, datetime

from agent_iq.catalog import load_consumer_assets
from agent_iq.config import Settings
from agent_iq.contracts import CallerContext, MatchFilters, MatchRequest
from agent_iq.match import MatchEngine, evaluate


def test_all_33_discovery_cases_meet_the_quality_bar() -> None:
    assets = load_consumer_assets()
    result = evaluate(
        MatchEngine(
            assets.catalog,
            semantic_mode="offline",
            settings=Settings(_env_file=None),
        ),
        assets.discovery_cases,
    )

    assert result.semantic_backend == "tfidf-char-ngram"
    assert result.recall_at_k == 1.0
    assert result.mean_reciprocal_rank == 1.0
    assert result.exact_accuracy == 1.0
    assert result.false_positives == 0
    assert result.passes
    assert result.p95_latency_ms < 250


def test_offline_selection_is_stable_across_100_runs() -> None:
    assets = load_consumer_assets()
    engine = MatchEngine(
        assets.catalog,
        semantic_mode="offline",
        settings=Settings(_env_file=None),
    )
    request = MatchRequest(
        request_id="determinism",
        intent=("Improve forecast accuracy with demand sensing and raise OEE on the bottling line"),
        caller=CallerContext(subject_id="user", tenant_id="tenant"),
        filters=MatchFilters(max_candidates=20, max_selected=5),
        requested_at=datetime(2026, 7, 10, tzinfo=UTC),
    )
    expected = engine.match(request).model_dump_json()

    assert all(engine.match(request).model_dump_json() == expected for _ in range(100))


def test_selection_explanation_contains_public_facts_not_hidden_reasoning() -> None:
    assets = load_consumer_assets()
    engine = MatchEngine(
        assets.catalog,
        semantic_mode="offline",
        settings=Settings(_env_file=None),
    )
    case = assets.discovery_cases[0]
    decision = engine.match(
        MatchRequest(
            request_id=case.case_id,
            intent=case.intent,
            caller=CallerContext(subject_id="user", tenant_id="tenant"),
            filters=MatchFilters(max_candidates=20),
            requested_at=datetime(2026, 7, 10, tzinfo=UTC),
        )
    )
    wire = decision.model_dump(mode="json", by_alias=True)
    text = str(wire)

    assert decision.selected[0].matched_skills
    assert decision.selected[0].policy_facts
    assert "chainOfThought" not in text
    assert "reasoning" not in text
