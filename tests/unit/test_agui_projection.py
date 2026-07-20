"""AG-UI projection, redaction, and deterministic replay tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel

from agent_iq.contracts import (
    AgentIqError,
    CallbackMode,
    CandidateScore,
    EligibilityDecision,
    ErrorCode,
    EventEnvelope,
    ScoreComponent,
    SelectionDecision,
    SelectionReason,
    TaskStatus,
    TransportProtocol,
)
from agent_iq.contracts.events import (
    AgentsSelectedPayload,
    ApprovalRequestedPayload,
    ApprovalResolvedPayload,
    CandidatesRankedPayload,
    CatalogSearchedPayload,
    EventPayload,
    EvidenceReceivedPayload,
    ExecutionCompletedPayload,
    InvocationCompletedPayload,
    InvocationDeltaPayload,
    InvocationFailedPayload,
    InvocationStartedPayload,
    PlanPreparedPayload,
    PreparedPlanStep,
    RequestAcceptedPayload,
    SynthesisCompletedPayload,
    TaskCompletedPayload,
    TaskHandedOffPayload,
)
from agent_iq.presentation import (
    AgUiProjectionError,
    AgUiProjector,
    UnsupportedCanonicalEventError,
    replay_agui_state,
)

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
TRACE_ID = "0123456789abcdef0123456789abcdef"
EXECUTION_ID = "execution-1"
THREAD_ID = "thread-1"
AGENT_ID = "consumer-formulation-rd"
INVOCATION_ID = "invocation-1"
STEP_ID = "step-1"


def _candidate() -> CandidateScore:
    return CandidateScore(
        agent_id=AGENT_ID,
        rank=1,
        fused_score=0.032,
        components=(
            ScoreComponent(
                name="lexical",
                rank=1,
                score=4.2,
                matched_fields=("skills.tags",),
            ),
        ),
        eligibility=EligibilityDecision(
            eligible=True,
            policy_facts=("admin approved",),
            policy_version="policy-1",
        ),
        matched_fields=("skills.tags",),
        explanation="private-candidate-explanation-zeta-must-not-project",
        catalog_version="catalog-1",
    )


def _selection() -> SelectionDecision:
    candidate = _candidate()
    return SelectionDecision(
        request_id="request-1",
        candidates=(candidate,),
        selected=(
            SelectionReason(
                agent_id=AGENT_ID,
                work_item_id="work-1",
                work_item="private-work-item-must-not-project",
                summary="private-selection-summary-eta-must-not-project",
                matched_skills=("formulation",),
                matched_tags=("BrightWash", "enzymes"),
                fused_score=candidate.fused_score,
                policy_facts=("admin approved",),
            ),
        ),
        selector="deterministic-v1",
        created_at=NOW,
    )


def _event(sequence: int, payload: EventPayload) -> EventEnvelope:
    return EventEnvelope(
        event_id=f"event-{sequence}",
        execution_id=EXECUTION_ID,
        sequence=sequence,
        occurred_at=NOW + timedelta(milliseconds=sequence),
        trace_id=TRACE_ID,
        payload=payload,
    )


def _complete_events() -> tuple[EventEnvelope, ...]:
    candidate = _candidate()
    return (
        _event(
            0,
            RequestAcceptedPayload(
                request_id="request-1",
                intent_summary="private-intent-omega-must-not-project",
            ),
        ),
        _event(
            1,
            CatalogSearchedPayload(
                query="private-query-sigma-must-not-project",
                candidate_count=1,
                catalog_version="catalog-1",
            ),
        ),
        _event(2, CandidatesRankedPayload(candidates=(candidate,))),
        _event(3, AgentsSelectedPayload(decision=_selection())),
        _event(
            4,
            PlanPreparedPayload(
                plan_id="plan-1",
                steps=(
                    PreparedPlanStep(
                        step_id=STEP_ID,
                        agent_id=AGENT_ID,
                        context_kinds=("foundry_iq",),
                        expected_output_count=1,
                        invocation_mode="sync",
                        approval_required=True,
                    ),
                ),
                max_concurrency=1,
            ),
        ),
        _event(
            5,
            ApprovalRequestedPayload(
                approval_id="approval-1",
                step_ids=(STEP_ID,),
                reason="private-approval-reason-theta-must-not-project",
            ),
        ),
        _event(
            6,
            ApprovalResolvedPayload(
                approval_id="approval-1",
                status="approved",
            ),
        ),
        _event(
            7,
            InvocationStartedPayload(
                invocation_id=INVOCATION_ID,
                step_id=STEP_ID,
                agent_id=AGENT_ID,
                transport=TransportProtocol.A2A,
                protocol_version="1.0",
            ),
        ),
        _event(
            8,
            InvocationDeltaPayload(
                invocation_id=INVOCATION_ID,
                step_id=STEP_ID,
                text="Protease and amylase are supported by the governed source. ",
            ),
        ),
        _event(
            9,
            EvidenceReceivedPayload(
                invocation_id=INVOCATION_ID,
                step_id=STEP_ID,
                evidence_ids=("evidence-1",),
            ),
        ),
        _event(
            10,
            InvocationCompletedPayload(
                invocation_id=INVOCATION_ID,
                step_id=STEP_ID,
                duration_ms=125,
            ),
        ),
        _event(
            11,
            TaskHandedOffPayload(
                task_id="task-1",
                agent_id=AGENT_ID,
                callback_modes=(CallbackMode.POLL,),
            ),
        ),
        _event(
            12,
            TaskCompletedPayload(
                task_id="task-1",
                status=TaskStatus.COMPLETED,
                summary="Task completed with governed evidence.",
            ),
        ),
        _event(
            13,
            SynthesisCompletedPayload(
                answer="The governed answer cites the product source [1].",
                citation_numbers=(1,),
            ),
        ),
        _event(
            14,
            ExecutionCompletedPayload(status="completed", duration_ms=240),
        ),
    )


def test_projection_is_deterministic_correlated_and_replayable() -> None:
    projector = AgUiProjector()

    first = projector.project(_complete_events(), thread_id=THREAD_ID)
    second = projector.project(_complete_events(), thread_id=THREAD_ID)

    assert first.model_dump_json(by_alias=True) == second.model_dump_json(by_alias=True)
    assert first.canonical_event_count == 15
    assert first.event_count == len(first.events)
    assert first.events[0].event.type == "RUN_STARTED"
    assert first.events[-1].event.type == "RUN_FINISHED"
    assert {event.trace_id for event in first.events} == {TRACE_ID}
    assert {event.execution_id for event in first.events} == {EXECUTION_ID}
    assert {event.source_sequence for event in first.events} == set(range(15))

    event_types = [item.event.type for item in first.events]
    assert "TOOL_CALL_START" in event_types
    assert "TOOL_CALL_END" in event_types
    assert "TOOL_CALL_ARGS" not in event_types
    assert not any(event_type.startswith("REASONING_") for event_type in event_types)
    assert "RAW" not in event_types
    assert "CUSTOM" not in event_types

    state = replay_agui_state(first)
    assert state.status == "completed"
    assert state.stage == "completed"
    assert state.catalog_version == "catalog-1"
    assert state.candidate_count == 1
    assert state.selections[0].agent_id == AGENT_ID
    assert state.plan is not None
    assert state.plan.steps[0].context_kinds == ("foundry_iq",)
    assert state.plan.steps[0].approval_required
    assert state.approvals["approval-1"].status == "approved"
    assert state.approvals["approval-1"].step_ids == (STEP_ID,)
    assert state.approvals["approval-1"].scope_summary == (
        "Approval required for the governed plan scope."
    )
    assert state.invocations[INVOCATION_ID].status == "completed"
    assert state.invocations[INVOCATION_ID].evidence_ids == ("evidence-1",)
    assert state.tasks["task-1"].status == "completed"
    assert state.answer == "The governed answer cites the product source [1]."
    assert state.citation_numbers == (1,)
    assert state.duration_ms == 240

    wire = first.model_dump(mode="json", by_alias=True)
    step = next(item["event"] for item in wire["events"] if item["event"]["type"] == "STEP_STARTED")
    assert step["stepName"] == STEP_ID
    assert "step_name" not in step


def test_projection_omits_private_canonical_fields() -> None:
    serialized = (
        AgUiProjector()
        .project(
            _complete_events(),
            thread_id=THREAD_ID,
        )
        .model_dump_json(by_alias=True)
    )

    assert "private-intent-omega-must-not-project" not in serialized
    assert "private-query-sigma-must-not-project" not in serialized
    assert "private-work-item-must-not-project" not in serialized
    assert "private-candidate-explanation-zeta-must-not-project" not in serialized
    assert "private-selection-summary-eta-must-not-project" not in serialized
    assert "private-approval-reason-theta-must-not-project" not in serialized
    assert "request-1" not in serialized
    assert "work-1" not in serialized
    assert "toolCallArgs" not in serialized
    assert "rawEvent" not in serialized


def test_failure_projection_keeps_code_but_redacts_provider_message() -> None:
    events = (
        _event(
            0,
            RequestAcceptedPayload(
                request_id="request-1",
                intent_summary="private-intent",
            ),
        ),
        _event(
            1,
            InvocationStartedPayload(
                invocation_id=INVOCATION_ID,
                step_id=STEP_ID,
                agent_id=AGENT_ID,
                transport=TransportProtocol.A2A,
                protocol_version="1.0",
            ),
        ),
        _event(
            2,
            InvocationFailedPayload(
                invocation_id=INVOCATION_ID,
                step_id=STEP_ID,
                error=AgentIqError(
                    error_id="error-1",
                    code=ErrorCode.INVOCATION_FAILED,
                    message="provider-raw-message-must-not-project",
                    retryable=False,
                    correlation_id=EXECUTION_ID,
                ),
            ),
        ),
        _event(
            3,
            ExecutionCompletedPayload(status="failed", duration_ms=10),
        ),
    )

    projection = AgUiProjector().project(events, thread_id=THREAD_ID)
    serialized = projection.model_dump_json(by_alias=True)

    assert projection.events[-1].event.type == "RUN_ERROR"
    assert "provider-raw-message-must-not-project" not in serialized
    assert replay_agui_state(projection).invocations[INVOCATION_ID].error_code == (
        "invocation_failed"
    )


class _UnknownPayload(BaseModel):
    type: str = "reasoning.trace"
    content: str = "hidden-reasoning-must-not-project"


def test_unknown_canonical_event_fails_closed() -> None:
    events = list(_complete_events()[:1])
    events.append(
        EventEnvelope.model_construct(
            event_id="event-1",
            execution_id=EXECUTION_ID,
            sequence=1,
            occurred_at=NOW,
            trace_id=TRACE_ID,
            payload=_UnknownPayload(),  # type: ignore[arg-type]
        )
    )

    with pytest.raises(UnsupportedCanonicalEventError, match="no safe AG-UI projection"):
        AgUiProjector().project(events, thread_id=THREAD_ID)


def test_invalid_event_order_and_missing_invocation_fail_closed() -> None:
    with pytest.raises(AgUiProjectionError, match="contiguous"):
        AgUiProjector().project(
            (_complete_events()[0], _complete_events()[2]),
            thread_id=THREAD_ID,
        )

    events = (
        _complete_events()[0],
        _event(
            1,
            EvidenceReceivedPayload(
                invocation_id=INVOCATION_ID,
                step_id=STEP_ID,
                evidence_ids=("evidence-1",),
            ),
        ),
    )
    with pytest.raises(AgUiProjectionError, match="no matching start"):
        AgUiProjector().project(events, thread_id=THREAD_ID)


def test_invalid_approval_ordering_fails_closed() -> None:
    plan_prefix = _complete_events()[:5]
    invocation_before_approval = (
        *plan_prefix,
        _event(
            5,
            InvocationStartedPayload(
                invocation_id=INVOCATION_ID,
                step_id=STEP_ID,
                agent_id=AGENT_ID,
                transport=TransportProtocol.A2A,
                protocol_version="1.0",
            ),
        ),
    )
    with pytest.raises(AgUiProjectionError, match="without an approved approval"):
        AgUiProjector().project(invocation_before_approval, thread_id=THREAD_ID)

    resolution_without_request = (
        _complete_events()[0],
        _event(
            1,
            ApprovalResolvedPayload(
                approval_id="approval-1",
                status="approved",
            ),
        ),
    )
    with pytest.raises(AgUiProjectionError, match="no matching request"):
        AgUiProjector().project(resolution_without_request, thread_id=THREAD_ID)

    duplicate_resolution = (
        *_complete_events()[:7],
        _event(
            7,
            ApprovalResolvedPayload(
                approval_id="approval-1",
                status="approved",
            ),
        ),
    )
    with pytest.raises(AgUiProjectionError, match="already resolved"):
        AgUiProjector().project(duplicate_resolution, thread_id=THREAD_ID)
