"""Framework-neutral Match engine over canonical Agent IQ descriptors."""

from __future__ import annotations

from dataclasses import dataclass

from agent_iq.catalog.assets import AgentCatalogAsset
from agent_iq.config import Settings, get_settings
from agent_iq.contracts import (
    AgentDescriptor,
    CandidateScore,
    MatchRequest,
    ScoreComponent,
    SelectionDecision,
)

from .document import MatchEvidence, explain_public_matches, search_document
from .fusion import HybridRanker, RawRankedAgent
from .prefilter import (
    DevelopmentEligibilityPolicy,
    EligibilityPolicy,
    prefilter_agents,
)
from .selector import (
    DeterministicSelector,
    SelectableCandidate,
    derive_query,
)
from .semantic import (
    AzureEmbeddingClient,
    SemanticMode,
    build_semantic_index,
)


@dataclass(frozen=True, slots=True)
class _RankedCandidate:
    descriptor: AgentDescriptor
    raw: RawRankedAgent
    contract: CandidateScore
    evidence: MatchEvidence

    def selectable(self) -> SelectableCandidate:
        return SelectableCandidate(
            agent_id=self.descriptor.agent_id,
            agent_name=self.descriptor.name,
            fused_score=self.raw.fused_score,
            lexical_score=self.raw.lexical_score,
            semantic_score=self.raw.semantic_score,
            matched_skills=self.evidence.skill_ids,
            matched_tags=self.evidence.tags,
            policy_facts=self.contract.eligibility.policy_facts,
        )


class MatchEngine:
    """Prefilter, retrieve, fuse, explain, and select eligible agents."""

    def __init__(
        self,
        catalog: AgentCatalogAsset,
        *,
        semantic_mode: SemanticMode = "auto",
        settings: Settings | None = None,
        policy: EligibilityPolicy | None = None,
        embedding_client: AzureEmbeddingClient | None = None,
    ):
        self.catalog = catalog
        self.semantic_mode = semantic_mode
        self.settings = settings or get_settings()
        self.policy = policy or DevelopmentEligibilityPolicy()
        self.embedding_client = embedding_client
        self._rankers: dict[tuple[str, ...], HybridRanker] = {}
        self.semantic_backend = "uninitialized"

    def rank(
        self,
        request: MatchRequest,
        *,
        query: str | None = None,
        top_k: int | None = None,
    ) -> tuple[CandidateScore, ...]:
        ranked = self._rank_internal(request, query or derive_query(request.intent))
        selected = ranked if top_k is None else ranked[:top_k]
        return tuple(candidate.contract for candidate in selected)

    def match(self, request: MatchRequest) -> SelectionDecision:
        ranked = self._rank_internal(request, derive_query(request.intent))
        selector = DeterministicSelector(max_agents=request.filters.max_selected)

        def rank_clause(clause: str) -> tuple[SelectableCandidate, ...]:
            return tuple(
                candidate.selectable() for candidate in self._rank_internal(request, clause)
            )

        reasons = selector.select(
            request.intent,
            tuple(candidate.selectable() for candidate in ranked),
            rank_clause,
        )
        initially_visible = list(ranked[: request.filters.max_candidates])
        visible_by_id = {
            candidate.descriptor.agent_id: candidate for candidate in initially_visible
        }
        full_by_id = {candidate.descriptor.agent_id: candidate for candidate in ranked}
        for reason in reasons:
            if reason.agent_id not in visible_by_id:
                visible_by_id[reason.agent_id] = full_by_id[reason.agent_id]

        selected_ids = {reason.agent_id for reason in reasons}
        visible = sorted(visible_by_id.values(), key=lambda item: item.contract.rank)
        if len(visible) > request.filters.max_candidates:
            required = [item for item in visible if item.descriptor.agent_id in selected_ids]
            optional = [item for item in visible if item.descriptor.agent_id not in selected_ids]
            visible = (required + optional)[: request.filters.max_candidates]
            visible.sort(key=lambda item: item.contract.rank)

        return SelectionDecision(
            request_id=request.request_id,
            candidates=tuple(candidate.contract for candidate in visible),
            selected=reasons,
            selector=selector.name,
            created_at=request.requested_at,
        )

    def _rank_internal(
        self,
        request: MatchRequest,
        query: str,
    ) -> tuple[_RankedCandidate, ...]:
        filtered = prefilter_agents(request, self.catalog.agents, self.policy)
        if not filtered.eligible:
            return ()
        ranker = self._ranker_for(filtered.eligible)
        descriptors = {agent.agent_id: agent for agent in filtered.eligible}
        catalog_version = f"{self.catalog.catalog_id}:{self.catalog.published_at.isoformat()}"
        candidates: list[_RankedCandidate] = []
        for rank, raw in enumerate(ranker.search(query), start=1):
            descriptor = descriptors[raw.agent_id]
            evidence = explain_public_matches(query, descriptor)
            contract = CandidateScore(
                agent_id=raw.agent_id,
                rank=rank,
                fused_score=raw.fused_score,
                components=(
                    ScoreComponent(
                        name="lexical",
                        rank=raw.lexical_rank,
                        score=raw.lexical_score,
                        matched_fields=evidence.fields,
                    ),
                    ScoreComponent(
                        name="semantic",
                        rank=raw.semantic_rank,
                        score=max(0.0, raw.semantic_score),
                        matched_fields=evidence.fields,
                    ),
                ),
                eligibility=filtered.decisions[raw.agent_id],
                matched_fields=evidence.fields,
                explanation=(
                    f"Ranked #{rank} by lexical and semantic matches on public "
                    "agent-card fields after eligibility filtering."
                ),
                catalog_version=catalog_version,
            )
            candidates.append(
                _RankedCandidate(
                    descriptor=descriptor,
                    raw=raw,
                    contract=contract,
                    evidence=evidence,
                )
            )
        return tuple(candidates)

    def _ranker_for(
        self,
        agents: tuple[AgentDescriptor, ...],
    ) -> HybridRanker:
        key = tuple(agent.agent_id for agent in agents)
        ranker = self._rankers.get(key)
        if ranker is None:
            documents = {agent.agent_id: search_document(agent) for agent in agents}
            semantic = build_semantic_index(
                documents,
                mode=self.semantic_mode,
                settings=self.settings,
                client=self.embedding_client,
            )
            ranker = HybridRanker(documents, semantic)
            self._rankers[key] = ranker
        self.semantic_backend = ranker.backend_name
        return ranker
