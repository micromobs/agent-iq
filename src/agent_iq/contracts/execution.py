"""Protocol-neutral orchestration execution results."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from .agent import TransportProtocol
from .artifacts import ArtifactEnvelope
from .base import ContractModel, Identifier, NonEmptyText, SchemaVersion, ShortText, Timestamp
from .errors import AgentIqError
from .match import SelectionDecision
from .plan import ExecutionPlan
from .synthesis import SynthesisResult, SynthesisStatus


class StepExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    DECLINED = "declined"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class ExecutionStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    DECLINED = "declined"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepExecutionResult(ContractModel):
    step_id: Identifier
    agent_id: Identifier
    status: StepExecutionStatus
    transport: TransportProtocol = TransportProtocol.A2A
    invocation_id: Identifier | None = None
    protocol_version: ShortText | None = None
    duration_ms: int = Field(ge=0)
    answer: NonEmptyText | None = None
    evidence_ids: tuple[Identifier, ...] = ()
    artifacts: tuple[ArtifactEnvelope, ...] = ()
    error: AgentIqError | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> StepExecutionResult:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("Step evidence IDs must be unique.")
        artifact_ids = [artifact.artifact_id for artifact in self.artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("Step artifact IDs must be unique.")
        if self.status is StepExecutionStatus.SUCCEEDED:
            if self.invocation_id is None or self.protocol_version is None or self.answer is None:
                raise ValueError("A successful step requires invocation metadata and an answer.")
            if not self.evidence_ids:
                raise ValueError("A successful step requires structured evidence.")
            if self.error is not None:
                raise ValueError("A successful step cannot contain an error.")
            for artifact in self.artifacts:
                if (
                    artifact.invocation_id != self.invocation_id
                    or artifact.producer.agent_id != self.agent_id
                ):
                    raise ValueError("A step artifact must match its invocation and agent.")
        else:
            if self.answer is not None or self.evidence_ids or self.artifacts:
                raise ValueError("A non-successful step cannot expose untrusted output.")
            if self.error is None:
                raise ValueError("A non-successful step requires a typed error.")
        return self


class ExecutionSummary(ContractModel):
    schema_version: SchemaVersion = "1.0"
    execution_id: Identifier
    request_id: Identifier
    status: ExecutionStatus
    plan: ExecutionPlan
    selection: SelectionDecision
    steps: tuple[StepExecutionResult, ...] = Field(min_length=1)
    synthesis: SynthesisResult
    started_at: Timestamp
    completed_at: Timestamp
    duration_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_execution(self) -> ExecutionSummary:
        if self.completed_at < self.started_at:
            raise ValueError("Execution completion cannot precede its start.")
        if self.request_id != self.plan.request_id or self.request_id != self.selection.request_id:
            raise ValueError("Execution request IDs must match the plan and selection.")
        if self.execution_id != self.synthesis.execution_id:
            raise ValueError("Execution and synthesis IDs must match.")

        plan_steps = [(step.step_id, step.agent_id) for step in self.plan.steps]
        result_steps = [(step.step_id, step.agent_id) for step in self.steps]
        if result_steps != plan_steps:
            raise ValueError("Execution step results must match plan order and agent assignments.")
        selected_agents = {reason.agent_id for reason in self.selection.selected}
        if selected_agents != {step.agent_id for step in self.plan.steps}:
            raise ValueError("The plan must execute exactly the selected agents.")

        succeeded = [step for step in self.steps if step.status is StepExecutionStatus.SUCCEEDED]
        unsuccessful = [
            step for step in self.steps if step.status is not StepExecutionStatus.SUCCEEDED
        ]
        if self.status is ExecutionStatus.COMPLETED:
            if unsuccessful or self.synthesis.status is not SynthesisStatus.COMPLETE:
                raise ValueError(
                    "Completed execution requires every step and synthesis to complete."
                )
        elif self.status is ExecutionStatus.PARTIAL:
            if not succeeded or (
                not unsuccessful and self.synthesis.status is not SynthesisStatus.PARTIAL
            ):
                raise ValueError("Partial execution requires usable output plus a visible gap.")
        elif self.status is ExecutionStatus.DECLINED:
            if succeeded or self.synthesis.status is not SynthesisStatus.DECLINED:
                raise ValueError("Declined execution cannot contain successful steps.")
            if any(step.status is not StepExecutionStatus.DECLINED for step in self.steps):
                raise ValueError("Declined execution requires every step to decline.")
        elif self.status is ExecutionStatus.CANCELLED:
            if succeeded or not any(
                step.status is StepExecutionStatus.CANCELLED for step in self.steps
            ):
                raise ValueError(
                    "Cancelled execution requires cancellation and no successful step."
                )
        else:
            if succeeded or any(
                step.status is StepExecutionStatus.CANCELLED for step in self.steps
            ):
                raise ValueError("Failed execution cannot contain success or cancellation.")
            if not any(
                step.status
                in {
                    StepExecutionStatus.FAILED,
                    StepExecutionStatus.TIMED_OUT,
                    StepExecutionStatus.SKIPPED,
                }
                for step in self.steps
            ):
                raise ValueError("Failed execution requires a failure, timeout, or skipped step.")
        return self
