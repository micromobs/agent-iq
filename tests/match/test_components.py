"""Lexical, semantic, fusion, selection, and prefilter component tests."""

from __future__ import annotations

from datetime import UTC, datetime

from agent_iq.catalog import load_consumer_assets
from agent_iq.catalog.assets import AgentCatalogAsset
from agent_iq.config import Settings
from agent_iq.contracts import (
    CallerContext,
    MatchFilters,
    MatchRequest,
    TrustMetadata,
    TrustState,
)
from agent_iq.match import MatchEngine
from agent_iq.match.fusion import HybridRanker
from agent_iq.match.lexical import BM25Index, tokenize
from agent_iq.match.selector import derive_query, split_clauses
from agent_iq.match.semantic import OfflineSemanticIndex


def request(intent: str, filters: MatchFilters | None = None) -> MatchRequest:
    return MatchRequest(
        request_id="test-request",
        intent=intent,
        caller=CallerContext(subject_id="user", tenant_id="tenant"),
        filters=filters or MatchFilters(),
        requested_at=datetime(2026, 7, 10, tzinfo=UTC),
    )


def test_tokenizer_removes_stopwords_and_preserves_domain_terms() -> None:
    assert tokenize("Please compare BrightWash enzymes and surfactants") == [
        "compare",
        "brightwash",
        "enzymes",
        "surfactants",
    ]
    assert (
        derive_query("BrightWash enzymes and BrightWash surfactants")
        == "brightwash enzymes surfactants"
    )


def test_bm25_prefers_exact_domain_overlap() -> None:
    index = BM25Index(
        {
            "formulation": "BrightWash enzymes detergent formulation",
            "marketing": "brand campaign advertising",
        }
    )
    scores = index.scores("BrightWash detergent enzymes")

    assert scores["formulation"] > scores["marketing"]
    assert scores["marketing"] == 0


def test_offline_semantic_recovers_morphology() -> None:
    index = OfflineSemanticIndex(
        {
            "formulation": "enzyme formulation chemistry",
            "marketing": "advertising campaign",
        }
    )
    scores = index.scores("enzymes and formulations")

    assert scores["formulation"] > scores["marketing"]


def test_rrf_is_deterministic_on_ties() -> None:
    documents = {"agent-b": "same", "agent-a": "same"}
    ranker = HybridRanker(documents, OfflineSemanticIndex(documents))

    first = ranker.search("unmatched")
    second = ranker.search("unmatched")

    assert first == second
    assert [result.agent_id for result in first] == ["agent-a", "agent-b"]


def test_rrf_cross_rank_tie_prefers_stronger_lexical_evidence() -> None:
    class ReverseSemantic:
        backend_name = "test"

        def scores(self, query: str) -> dict[str, float]:
            del query
            return {"lexical-agent": 0.2, "semantic-agent": 0.9}

    documents = {
        "lexical-agent": "LAS nonionic surfactants grease chemistry formulation",
        "semantic-agent": "brand comparison",
    }
    results = HybridRanker(documents, ReverseSemantic()).search("LAS nonionic surfactants grease")

    assert results[0].agent_id == "lexical-agent"


def test_clause_split_does_not_split_brand_versus_brand() -> None:
    assert split_clauses("Compare BrightWash vs ClearWave cold-water") == (
        "Compare BrightWash vs ClearWave cold-water",
    )
    assert split_clauses("Improve forecast accuracy and raise OEE") == (
        "Improve forecast accuracy",
        "raise OEE",
    )


def test_prefilter_removes_blocked_agent_before_ranking() -> None:
    assets = load_consumer_assets()
    base = assets.catalog.agents[0]
    blocked = base.model_copy(
        update={
            "agent_id": "blocked-perfect-match",
            "name": "Exact Secret Launch Campaign Agent",
            "description": "Exact secret launch campaign.",
            "trust": TrustMetadata(state=TrustState.BLOCKED),
        }
    )
    catalog = AgentCatalogAsset(
        catalog_id=assets.catalog.catalog_id,
        published_at=assets.catalog.published_at,
        agents=(*assets.catalog.agents, blocked),
    )
    engine = MatchEngine(
        catalog,
        semantic_mode="offline",
        settings=Settings(_env_file=None),
    )
    ranked = engine.rank(request("Exact secret launch campaign"))

    assert "blocked-perfect-match" not in {candidate.agent_id for candidate in ranked}


def test_request_filters_apply_before_ranking() -> None:
    assets = load_consumer_assets()
    engine = MatchEngine(
        assets.catalog,
        semantic_mode="offline",
        settings=Settings(_env_file=None),
    )
    ranked = engine.rank(
        request(
            "Which enzymes power BrightWash?",
            MatchFilters(excluded_agent_ids=("consumer-formulation-rd",)),
        )
    )

    assert "consumer-formulation-rd" not in {candidate.agent_id for candidate in ranked}
