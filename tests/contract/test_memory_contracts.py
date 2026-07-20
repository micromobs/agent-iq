"""Frozen WP-01 Memory and squad-decision contract invariants."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agent_iq.contracts import (
    MEMORY_BASE_CANDIDATE_LIMIT,
    MEMORY_DELTA_CAP,
    MEMORY_NEAR_TIE_MAX_GAP,
    MEMORY_SCOPE_PRECEDENCE,
    CandidateMemoryPrior,
    CandidateScore,
    EligibilityDecision,
    EpisodeReceipt,
    EpisodeWriteStatus,
    ExecutionMemoryCandidate,
    ExecutionMemoryIntent,
    ExecutionMemoryRecord,
    ExecutionMemorySummary,
    FeedbackSignal,
    IqSourceModes,
    MemoryOutcomeStatus,
    MemoryRecordType,
    MemoryScope,
    OutcomeMemorySummary,
    SelectionDecision,
    SelectionReason,
    SourceMode,
    SquadDecisionEnvelope,
    SquadOverride,
)

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
HASH = f"sha256:{'a' * 64}"


def _selection(request_id: str, agent_ids: tuple[str, ...]) -> SelectionDecision:
    candidates = tuple(
        CandidateScore(
            agent_id=agent_id,
            rank=rank,
            fused_score=1 / rank,
            eligibility=EligibilityDecision(
                eligible=True,
                policy_facts=("development eligibility",),
            ),
            explanation="Public agent-card evidence.",
            catalog_version="catalog:1",
        )
        for rank, agent_id in enumerate(("agent-a", "agent-b", "agent-c"), start=1)
    )
    return SelectionDecision(
        request_id=request_id,
        candidates=candidates,
        selected=tuple(
            SelectionReason(
                agent_id=agent_id,
                work_item_id=f"work-{index}",
                work_item=f"Work item {index}",
                summary="Selected using public capability evidence.",
                fused_score=next(
                    candidate.fused_score
                    for candidate in candidates
                    if candidate.agent_id == agent_id
                ),
            )
            for index, agent_id in enumerate(agent_ids, start=1)
        ),
        selector="deterministic-smallest-squad",
        created_at=NOW,
    )


def _override() -> SquadOverride:
    return SquadOverride(
        override_id="override-1",
        request_id="request-1",
        execution_id="execution-1",
        tenant_id="tenant-1",
        actor_subject_id="user-1",
        project_id="project-1",
        proposed_agent_ids=("agent-a",),
        authorized_agent_ids=("agent-a", "agent-b"),
        reason_code="missing_project_constraint",
        reason_hash=HASH,
        created_at=NOW,
    )


def test_memory_constants_and_scope_precedence_are_frozen() -> None:
    assert MEMORY_DELTA_CAP == 0.15
    assert MEMORY_NEAR_TIE_MAX_GAP == 0.10
    assert MEMORY_BASE_CANDIDATE_LIMIT == 10
    assert MEMORY_SCOPE_PRECEDENCE[:4] == (
        MemoryScope.USER,
        MemoryScope.PROJECT,
        MemoryScope.DEPARTMENT,
        MemoryScope.ORGANIZATION,
    )


def test_provisional_override_requires_exact_scope_and_near_tie() -> None:
    values = {
        "agent_id": "agent-b",
        "base_rank": 4,
        "scope": MemoryScope.USER,
        "intent_affinity": 0.9,
        "reputation": 0.8,
        "confidence": 0.7,
        "recency": 1.0,
        "decision_delta": MEMORY_DELTA_CAP,
        "provisional_override_applied": True,
        "inclusion_boundary_gap": MEMORY_NEAR_TIE_MAX_GAP,
        "same_subject": True,
        "same_project": True,
        "same_neighborhood": True,
    }
    assert CandidateMemoryPrior(**values).decision_delta == MEMORY_DELTA_CAP

    with pytest.raises(ValidationError, match="near-tie"):
        CandidateMemoryPrior(**{**values, "inclusion_boundary_gap": 0.11})


def test_squad_decision_envelope_requires_explicit_override() -> None:
    proposal = _selection("request-1", ("agent-a",))
    authorized = _selection("request-1", ("agent-a", "agent-b"))
    envelope = SquadDecisionEnvelope(
        decision_id="decision-1",
        request_id="request-1",
        execution_id="execution-1",
        proposal=proposal,
        override=_override(),
        execution_decision=authorized,
        created_at=NOW,
    )
    assert envelope.override is not None

    with pytest.raises(ValidationError, match="explicit override"):
        SquadDecisionEnvelope(
            decision_id="decision-2",
            request_id="request-1",
            execution_id="execution-1",
            proposal=proposal,
            execution_decision=authorized,
            created_at=NOW,
        )


def test_execution_memory_record_is_content_safe_and_idempotent() -> None:
    record = ExecutionMemoryRecord(
        idempotency_key="execution-1:execution",
        execution_id="execution-1",
        trace_id="trace-1",
        correlation_id="correlation-1",
        tenant_id="tenant-1",
        subject_id="user-1",
        project_id="project-1",
        intent=ExecutionMemoryIntent(
            text_hash=HASH,
            intent_class="supply.disruption",
            neighborhood_id="intent-neighborhood:supply-resilience",
        ),
        eligible_candidates=(
            ExecutionMemoryCandidate(agent_id="agent-a", base_rank=1),
            ExecutionMemoryCandidate(agent_id="agent-b", base_rank=2),
        ),
        proposed_squad=("agent-a",),
        executed_squad=("agent-a", "agent-b"),
        override=_override(),
        execution=ExecutionMemorySummary(
            status=MemoryOutcomeStatus.SUCCESS,
            latency_ms=125,
            evidence_count=4,
        ),
        outcome=OutcomeMemorySummary(
            status=MemoryOutcomeStatus.SUCCESS,
            business_signal="mitigation_accepted",
        ),
        source_modes=IqSourceModes(
            foundry_iq=SourceMode.LIVE,
            work_iq=SourceMode.SEEDED,
            fabric_iq=SourceMode.SEEDED,
            web_iq=SourceMode.SEEDED,
        ),
        policy_version="supply-policy-v3",
        recorded_at=NOW,
    )
    wire = record.model_dump(mode="json", by_alias=True)

    assert wire["recordType"] == "execution"
    assert "intent" in wire and set(wire["intent"]) == {
        "textHash",
        "intentClass",
        "neighborhoodId",
    }
    assert "prompt" not in str(wire).casefold()
    assert "answer" not in str(wire).casefold()

    with pytest.raises(ValidationError, match="idempotency key"):
        ExecutionMemoryRecord.model_validate({**wire, "idempotencyKey": "wrong-key"})


def test_episode_receipt_reports_duplicate_delivery_honestly() -> None:
    receipt = EpisodeReceipt(
        record_type=MemoryRecordType.FEEDBACK,
        execution_id="execution-1",
        idempotency_key="execution-1:feedback",
        graph_id="user:user-1",
        episode_id="episode-1",
        status=EpisodeWriteStatus.DUPLICATE,
        duplicate=True,
        received_at=NOW,
    )
    assert receipt.duplicate
    assert FeedbackSignal.HELPFUL.value == "helpful"
