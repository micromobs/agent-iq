from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from agent_iq.contracts import (
    ArtifactEnvelope,
    ArtifactProducerIdentity,
    DemandScenario,
    ExecutionStatus,
    ExecutionSummary,
    GroundingCoverage,
    StepExecutionResult,
    StepExecutionStatus,
    SynthesisResult,
    SynthesisStatus,
)

from .samples import DIGEST, NOW, error, evidence, plan, selection


def _successful_step() -> StepExecutionResult:
    return StepExecutionResult(
        step_id="step-1",
        agent_id="consumer-formulation-rd",
        status=StepExecutionStatus.SUCCEEDED,
        invocation_id="invocation-1",
        protocol_version="1.0",
        duration_ms=25,
        answer="Grounded answer [1].",
        evidence_ids=("evidence-1",),
    )


def _synthesis(status: SynthesisStatus = SynthesisStatus.COMPLETE) -> SynthesisResult:
    return SynthesisResult(
        execution_id="execution-1",
        status=status,
        answer="Grounded answer [1].",
        evidence=evidence(),
        coverage=GroundingCoverage(total_claims=1, grounded_claims=1, ratio=1),
        sources_markdown="## Sources\n1. Source",
    )


def _summary() -> ExecutionSummary:
    return ExecutionSummary(
        execution_id="execution-1",
        request_id="request-1",
        status=ExecutionStatus.COMPLETED,
        plan=plan(),
        selection=selection(),
        steps=(_successful_step(),),
        synthesis=_synthesis(),
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
        duration_ms=1000,
    )


@pytest.mark.parametrize(
    "update",
    (
        {"invocation_id": None},
        {"protocol_version": None},
        {"answer": None},
        {"evidence_ids": ()},
        {"error": error()},
        {"evidence_ids": ("evidence-1", "evidence-1")},
    ),
)
def test_successful_step_requires_complete_trusted_output(update: dict[str, object]) -> None:
    payload = _successful_step().model_dump()
    payload.update(update)
    with pytest.raises(ValidationError):
        StepExecutionResult.model_validate(payload)


def test_non_successful_step_rejects_output_and_requires_error() -> None:
    with pytest.raises(ValidationError, match="untrusted"):
        StepExecutionResult(
            step_id="step-1",
            agent_id="agent-1",
            status=StepExecutionStatus.FAILED,
            duration_ms=1,
            answer="Do not expose me.",
            error=error(),
        )
    with pytest.raises(ValidationError, match="typed error"):
        StepExecutionResult(
            step_id="step-1",
            agent_id="agent-1",
            status=StepExecutionStatus.FAILED,
            duration_ms=1,
        )


def test_step_artifacts_must_match_agent_and_invocation() -> None:
    artifact = ArtifactEnvelope(
        artifact_id="demand-1",
        invocation_id="invocation-other",
        correlation_id="execution-1",
        tenant_id="tenant-1",
        caller_subject_id="caller-1",
        producer=ArtifactProducerIdentity(
            agent_id="consumer-demand-forecasting",
            agent_version="1",
            identity_id="identity-demand",
        ),
        artifact=DemandScenario(
            artifact_id="demand-1",
            product_id="product-1",
            market="Germany",
            horizon_start=date(2026, 10, 1),
            horizon_end=date(2026, 10, 31),
            unit="cases",
            baseline_quantity=100,
            forecast_quantity=120,
            confidence=0.8,
            evidence_ids=("evidence-1",),
            created_at=datetime(2026, 7, 11, tzinfo=UTC),
        ),
        content_hash=DIGEST,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    with pytest.raises(ValidationError, match="match"):
        StepExecutionResult(
            step_id="step-1",
            agent_id="consumer-formulation-rd",
            status=StepExecutionStatus.SUCCEEDED,
            invocation_id="invocation-1",
            protocol_version="1.0",
            duration_ms=1,
            answer="Grounded [1].",
            evidence_ids=("evidence-1",),
            artifacts=(artifact,),
        )


@pytest.mark.parametrize(
    ("update", "message"),
    (
        ({"completed_at": NOW - timedelta(seconds=1)}, "precede"),
        ({"request_id": "other"}, "request IDs"),
        ({"execution_id": "other"}, "synthesis IDs"),
        ({"steps": []}, "at least 1"),
        ({"status": ExecutionStatus.PARTIAL}, "Partial"),
        ({"status": ExecutionStatus.DECLINED}, "Declined"),
        ({"status": ExecutionStatus.CANCELLED}, "Cancelled"),
        ({"status": ExecutionStatus.FAILED}, "Failed"),
    ),
)
def test_execution_summary_rejects_inconsistent_terminal_state(
    update: dict[str, object],
    message: str,
) -> None:
    payload = _summary().model_dump(by_alias=False)
    payload.update(update)
    with pytest.raises(ValidationError, match=message):
        ExecutionSummary.model_validate(payload)
