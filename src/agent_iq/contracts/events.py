"""Typed lifecycle events shared by Cowork, HTTP, SSE, and Canvas."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .agent import CallbackMode, InteractionMode, TransportProtocol
from .base import (
    ContractModel,
    Identifier,
    NonEmptyText,
    SchemaVersion,
    Sha256Digest,
    ShortText,
    Timestamp,
)
from .errors import AgentIqError
from .match import CandidateScore, SelectionDecision
from .plan import ContextKind
from .task import TaskStatus


class RequestAcceptedPayload(ContractModel):
    type: Literal["request.accepted"] = "request.accepted"
    request_id: Identifier
    intent_summary: NonEmptyText


class CatalogSearchedPayload(ContractModel):
    type: Literal["catalog.searched"] = "catalog.searched"
    query: NonEmptyText
    candidate_count: int = Field(ge=0)
    catalog_version: ShortText


class CandidatesRankedPayload(ContractModel):
    type: Literal["candidates.ranked"] = "candidates.ranked"
    candidates: tuple[CandidateScore, ...]


class AgentsSelectedPayload(ContractModel):
    type: Literal["agents.selected"] = "agents.selected"
    decision: SelectionDecision


class SquadProposedPayload(ContractModel):
    type: Literal["squad.proposed"] = "squad.proposed"
    request_id: Identifier
    decision_id: Identifier
    agent_ids: tuple[Identifier, ...] = Field(min_length=1)
    selector: ShortText

    @model_validator(mode="after")
    def validate_agent_ids(self) -> SquadProposedPayload:
        if len(self.agent_ids) != len(set(self.agent_ids)):
            raise ValueError("Proposed squad agent IDs must be unique.")
        return self


class SquadOverriddenPayload(ContractModel):
    type: Literal["squad.overridden"] = "squad.overridden"
    request_id: Identifier
    decision_id: Identifier
    override_id: Identifier
    proposed_agent_ids: tuple[Identifier, ...] = Field(min_length=1)
    authorized_agent_ids: tuple[Identifier, ...] = Field(min_length=1)
    reason_code: Identifier
    reason_hash: Sha256Digest

    @model_validator(mode="after")
    def validate_override(self) -> SquadOverriddenPayload:
        if len(self.proposed_agent_ids) != len(set(self.proposed_agent_ids)):
            raise ValueError("Proposed squad agent IDs must be unique.")
        if len(self.authorized_agent_ids) != len(set(self.authorized_agent_ids)):
            raise ValueError("Authorized squad agent IDs must be unique.")
        if self.proposed_agent_ids == self.authorized_agent_ids:
            raise ValueError("A squad override event must change the proposed squad.")
        return self


class SquadAuthorizedPayload(ContractModel):
    type: Literal["squad.authorized"] = "squad.authorized"
    request_id: Identifier
    decision_id: Identifier
    agent_ids: tuple[Identifier, ...] = Field(min_length=1)
    override_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_agent_ids(self) -> SquadAuthorizedPayload:
        if len(self.agent_ids) != len(set(self.agent_ids)):
            raise ValueError("Authorized squad agent IDs must be unique.")
        return self


class PreparedPlanStep(ContractModel):
    step_id: Identifier
    agent_id: Identifier
    depends_on: tuple[Identifier, ...] = ()
    context_kinds: tuple[ContextKind, ...] = ()
    expected_output_count: int = Field(ge=1)
    invocation_mode: InteractionMode
    approval_required: bool


class PlanPreparedPayload(ContractModel):
    type: Literal["plan.prepared"] = "plan.prepared"
    plan_id: Identifier
    steps: tuple[PreparedPlanStep, ...] = Field(min_length=1)
    max_concurrency: int = Field(ge=1, le=20)


class ApprovalRequestedPayload(ContractModel):
    type: Literal["approval.requested"] = "approval.requested"
    approval_id: Identifier
    step_ids: tuple[Identifier, ...] = Field(min_length=1)
    reason: NonEmptyText

    @model_validator(mode="after")
    def validate_step_ids(self) -> ApprovalRequestedPayload:
        if len(set(self.step_ids)) != len(self.step_ids):
            raise ValueError("Approval step IDs must be unique.")
        return self


class ApprovalResolvedPayload(ContractModel):
    type: Literal["approval.resolved"] = "approval.resolved"
    approval_id: Identifier
    status: Literal["approved", "declined", "expired", "cancelled"]


class InvocationStartedPayload(ContractModel):
    type: Literal["invocation.started"] = "invocation.started"
    invocation_id: Identifier
    step_id: Identifier
    agent_id: Identifier
    transport: TransportProtocol
    protocol_version: ShortText


class InvocationDeltaPayload(ContractModel):
    type: Literal["invocation.delta"] = "invocation.delta"
    invocation_id: Identifier
    step_id: Identifier
    text: NonEmptyText


class EvidenceReceivedPayload(ContractModel):
    type: Literal["evidence.received"] = "evidence.received"
    invocation_id: Identifier
    step_id: Identifier
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)


class InvocationCompletedPayload(ContractModel):
    type: Literal["invocation.completed"] = "invocation.completed"
    invocation_id: Identifier
    step_id: Identifier
    duration_ms: int = Field(ge=0)


class InvocationFailedPayload(ContractModel):
    type: Literal["invocation.failed"] = "invocation.failed"
    invocation_id: Identifier
    step_id: Identifier
    error: AgentIqError


class SynthesisCompletedPayload(ContractModel):
    type: Literal["synthesis.completed"] = "synthesis.completed"
    answer: NonEmptyText
    citation_numbers: tuple[int, ...] = ()


class TaskHandedOffPayload(ContractModel):
    type: Literal["task.handed_off"] = "task.handed_off"
    task_id: Identifier
    agent_id: Identifier
    callback_modes: tuple[CallbackMode, ...]


class TaskCompletedPayload(ContractModel):
    type: Literal["task.completed"] = "task.completed"
    task_id: Identifier
    status: TaskStatus
    summary: NonEmptyText

    @model_validator(mode="after")
    def validate_terminal_status(self) -> TaskCompletedPayload:
        terminal = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
        if self.status not in terminal:
            raise ValueError("Task completion events require a terminal status.")
        return self


class ExecutionCompletedPayload(ContractModel):
    type: Literal["execution.completed"] = "execution.completed"
    status: Literal["completed", "partial", "declined", "failed", "cancelled"]
    duration_ms: int = Field(ge=0)


EventPayload = Annotated[
    RequestAcceptedPayload
    | CatalogSearchedPayload
    | CandidatesRankedPayload
    | AgentsSelectedPayload
    | SquadProposedPayload
    | SquadOverriddenPayload
    | SquadAuthorizedPayload
    | PlanPreparedPayload
    | ApprovalRequestedPayload
    | ApprovalResolvedPayload
    | InvocationStartedPayload
    | InvocationDeltaPayload
    | EvidenceReceivedPayload
    | InvocationCompletedPayload
    | InvocationFailedPayload
    | SynthesisCompletedPayload
    | TaskHandedOffPayload
    | TaskCompletedPayload
    | ExecutionCompletedPayload,
    Field(discriminator="type"),
]


class EventEnvelope(ContractModel):
    schema_version: SchemaVersion = "1.0"
    event_id: Identifier
    execution_id: Identifier
    sequence: int = Field(ge=0)
    occurred_at: Timestamp
    trace_id: ShortText | None = None
    payload: EventPayload
