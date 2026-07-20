"""Contract round-trip, invariant, and public-explanation tests."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import BaseModel, ValidationError

from agent_iq.contracts import (
    AgentDescriptor,
    AgentIqError,
    AgentTask,
    Citation,
    CitationPresentation,
    Claim,
    EligibilityDecision,
    ErrorCode,
    EventEnvelope,
    EvidenceBundle,
    ExecutionPlan,
    HealthRecord,
    MatchFilters,
    OrchestrationPresentation,
    PlanStep,
    SelectionDecision,
    SkillDescriptor,
    TaskCheckpoint,
    TaskStatus,
)
from agent_iq.contracts.events import TaskCompletedPayload
from agent_iq.contracts.schemas import generate_contract_schemas

from .samples import (
    LATER,
    NOW,
    agent,
    candidate,
    error,
    event,
    evidence,
    match_request,
    plan,
    selection,
    task,
)


@pytest.mark.parametrize(
    "model",
    [
        agent(),
        match_request(),
        selection(),
        plan(),
        evidence(),
        task(),
        error(),
        event(),
    ],
)
def test_contracts_round_trip_camel_case(model: BaseModel) -> None:
    wire = model.model_dump(mode="json", by_alias=True)
    parsed = type(model).model_validate(wire)

    assert parsed == model
    assert "schemaVersion" in wire
    assert "schema_version" not in wire


def test_unknown_fields_are_rejected() -> None:
    wire = agent().model_dump(mode="json", by_alias=True)
    wire["unexpected"] = True

    with pytest.raises(ValidationError, match="extra_forbidden"):
        AgentDescriptor.model_validate(wire)


def test_compact_orchestration_requires_sources_for_citations() -> None:
    with pytest.raises(ValidationError, match="Sources markdown"):
        OrchestrationPresentation(
            execution_id="exec-1",
            request_id="request-1",
            status="completed",
            duration_ms=1,
            answer="Grounded answer [1].",
            citations=(
                CitationPresentation(
                    number=1,
                    title="Source",
                    source_id="source-1",
                ),
            ),
        )


def test_agent_framework_is_informational_metadata() -> None:
    descriptor = agent()

    assert descriptor.implementation.framework == "LangGraph"
    assert all("framework" not in field_name for field_name in descriptor.tags)


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: SkillDescriptor(
                skill_id="skill",
                name="Skill",
                description="Description",
                tags=("BrightWash", "brightwash"),
            ),
            "duplicates",
        ),
        (
            lambda: MatchFilters(
                allowed_agent_ids=("agent-1",),
                excluded_agent_ids=("agent-1",),
            ),
            "must not overlap",
        ),
        (
            lambda: MatchFilters(max_candidates=1, max_selected=2),
            "cannot exceed",
        ),
        (
            lambda: EligibilityDecision(eligible=True, blocked_reasons=("blocked",)),
            "cannot have blocked reasons",
        ),
        (
            lambda: EligibilityDecision(eligible=False),
            "require a public blocked reason",
        ),
        (
            lambda: HealthRecord(status="healthy", checked_at=LATER, expires_at=NOW),
            "expiry",
        ),
        (
            lambda: AgentIqError(
                error_id="error-2",
                code=ErrorCode.INTERNAL,
                message="Internal error.",
                retry_after_seconds=5,
            ),
            "Retry delay",
        ),
    ],
)
def test_common_invalid_contracts_fail(factory: object, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        assert callable(factory)
        factory()


def test_selection_rejects_missing_ineligible_and_duplicate_agents() -> None:
    reason = selection().selected[0]

    with pytest.raises(ValidationError, match="scored candidate"):
        SelectionDecision(
            request_id="request-1",
            candidates=(),
            selected=(reason,),
            selector="test",
            created_at=NOW,
        )

    with pytest.raises(ValidationError, match="ineligible"):
        SelectionDecision(
            request_id="request-1",
            candidates=(candidate(eligible=False),),
            selected=(reason,),
            selector="test",
            created_at=NOW,
        )

    with pytest.raises(ValidationError, match="must be unique"):
        SelectionDecision(
            request_id="request-1",
            candidates=(candidate(),),
            selected=(reason, reason),
            selector="test",
            created_at=NOW,
        )


def test_plan_rejects_missing_self_and_cyclic_dependencies() -> None:
    output = plan().steps[0].expected_outputs

    with pytest.raises(ValidationError, match="cannot depend on itself"):
        ExecutionPlan(
            plan_id="plan-self",
            request_id="request-1",
            objective="Test.",
            public_summary="Test.",
            steps=(
                PlanStep(
                    step_id="step-1",
                    agent_id="agent-1",
                    work_item="Test.",
                    depends_on=("step-1",),
                    expected_outputs=output,
                ),
            ),
            created_at=NOW,
        )

    with pytest.raises(ValidationError, match="does not exist"):
        ExecutionPlan(
            plan_id="plan-missing",
            request_id="request-1",
            objective="Test.",
            public_summary="Test.",
            steps=(
                PlanStep(
                    step_id="step-1",
                    agent_id="agent-1",
                    work_item="Test.",
                    depends_on=("missing",),
                    expected_outputs=output,
                ),
            ),
            created_at=NOW,
        )

    with pytest.raises(ValidationError, match="acyclic"):
        ExecutionPlan(
            plan_id="plan-cycle",
            request_id="request-1",
            objective="Test.",
            public_summary="Test.",
            steps=(
                PlanStep(
                    step_id="step-1",
                    agent_id="agent-1",
                    work_item="Test.",
                    depends_on=("step-2",),
                    expected_outputs=output,
                ),
                PlanStep(
                    step_id="step-2",
                    agent_id="agent-2",
                    work_item="Test.",
                    depends_on=("step-1",),
                    expected_outputs=output,
                ),
            ),
            created_at=NOW,
        )


def test_plan_rejects_duplicate_steps() -> None:
    current = plan()
    with pytest.raises(ValidationError, match="step IDs"):
        ExecutionPlan(
            plan_id="duplicate",
            request_id=current.request_id,
            objective=current.objective,
            public_summary=current.public_summary,
            steps=(current.steps[0], current.steps[0]),
            created_at=NOW,
        )


def test_evidence_rejects_unstructured_references_and_bad_numbering() -> None:
    current = evidence()

    with pytest.raises(ValidationError, match="Citation numbers"):
        EvidenceBundle(
            execution_id=current.execution_id,
            records=current.records,
            citations=(Citation(number=2, evidence_id="evidence-1", label="Source"),),
        )

    with pytest.raises(ValidationError, match="citation must reference"):
        EvidenceBundle(
            execution_id=current.execution_id,
            records=current.records,
            citations=(Citation(number=1, evidence_id="missing", label="Source"),),
        )

    with pytest.raises(ValidationError, match="claim must reference"):
        EvidenceBundle(
            execution_id=current.execution_id,
            records=current.records,
            claims=(
                Claim(
                    claim_id="claim-2",
                    text="Unsupported claim.",
                    evidence_ids=("missing",),
                    agent_ids=("agent-1",),
                ),
            ),
        )

    with pytest.raises(ValidationError, match="Evidence IDs"):
        EvidenceBundle(
            execution_id=current.execution_id,
            records=(current.records[0], current.records[0]),
        )


def test_task_rejects_invalid_timeline_and_checkpoint_order() -> None:
    current = task()

    with pytest.raises(ValidationError, match="update time"):
        AgentTask(
            **{
                **current.model_dump(by_alias=False),
                "updated_at": NOW - timedelta(seconds=1),
            }
        )

    with pytest.raises(ValidationError, match="deadline"):
        AgentTask(
            **{
                **current.model_dump(by_alias=False),
                "deadline_at": NOW,
            }
        )

    with pytest.raises(ValidationError, match="checkpoint sequences"):
        AgentTask(
            **{
                **current.model_dump(by_alias=False),
                "checkpoints": (
                    TaskCheckpoint(
                        sequence=1,
                        status=TaskStatus.RUNNING,
                        summary="Later.",
                        recorded_at=LATER,
                    ),
                    TaskCheckpoint(
                        sequence=0,
                        status=TaskStatus.PENDING,
                        summary="Earlier.",
                        recorded_at=NOW,
                    ),
                ),
            }
        )


def test_task_completed_event_requires_terminal_status() -> None:
    with pytest.raises(ValidationError, match="terminal status"):
        TaskCompletedPayload(
            task_id="task-1",
            status=TaskStatus.RUNNING,
            summary="Still running.",
        )


def test_event_payload_uses_a_discriminator() -> None:
    wire = event().model_dump(mode="json", by_alias=True)
    parsed = EventEnvelope.model_validate(wire)

    assert parsed.payload.type == "invocation.started"


def test_public_schemas_exclude_hidden_reasoning_fields() -> None:
    text = str(generate_contract_schemas())

    assert "chainOfThought" not in text
    assert '"reasoning"' not in text
