"""Protocol-neutral northbound Agent IQ command and result contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AnyUrl, Field, field_validator, model_validator

from .base import (
    ContractModel,
    Identifier,
    NonEmptyText,
    SchemaVersion,
    ShortText,
    Timestamp,
    require_unique_text,
)
from .errors import AgentIqError
from .events import EventEnvelope
from .execution import ExecutionSummary
from .match import CandidateScore, MatchRequest
from .plan import ContextKind


class ContextReference(ContractModel):
    """Permission-bound reference; source content never crosses this API contract."""

    context_id: Identifier
    kind: ContextKind
    source_uri: AnyUrl
    subject_id: ShortText
    tenant_id: ShortText
    permission_scope: ShortText
    approved: bool = False
    sensitivity: ShortText | None = None
    allowed_agent_ids: tuple[Identifier, ...] = ()
    relevance_terms: tuple[ShortText, ...] = ()
    priority: int = Field(default=100, ge=0, le=10_000)

    @field_validator("allowed_agent_ids", "relevance_terms")
    @classmethod
    def validate_unique_bindings(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Context bindings")


class OrchestrationCommand(ContractModel):
    """The one host-independent command consumed by every northbound adapter."""

    schema_version: SchemaVersion = "1.0"
    request: MatchRequest
    context_references: tuple[ContextReference, ...] = ()

    @model_validator(mode="after")
    def validate_context_authority(self) -> OrchestrationCommand:
        context_ids = [reference.context_id for reference in self.context_references]
        if len(context_ids) != len(set(context_ids)):
            raise ValueError("Context reference IDs must be unique.")
        for reference in self.context_references:
            if (
                reference.subject_id != self.request.caller.subject_id
                or reference.tenant_id != self.request.caller.tenant_id
            ):
                raise ValueError("Context references must be bound to the request caller.")
        return self


class AgentSearchResult(ContractModel):
    schema_version: SchemaVersion = "1.0"
    request_id: Identifier
    candidates: tuple[CandidateScore, ...]


class OrchestrationResult(ContractModel):
    """Normalized terminal result returned by MCP, HTTP, and incoming A2A."""

    schema_version: SchemaVersion = "1.0"
    summary: ExecutionSummary
    events: tuple[EventEnvelope, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_identity_and_sequence(self) -> OrchestrationResult:
        if any(event.execution_id != self.summary.execution_id for event in self.events):
            raise ValueError("Every lifecycle event must belong to the result execution.")
        if tuple(event.sequence for event in self.events) != tuple(range(len(self.events))):
            raise ValueError("Lifecycle event sequences must be contiguous and zero-based.")
        if self.events[-1].payload.type != "execution.completed":
            raise ValueError("The final lifecycle event must complete the execution.")
        return self


class ExecutionLifecycleStatus(StrEnum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    DECLINED = "declined"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionSnapshot(ContractModel):
    schema_version: SchemaVersion = "1.0"
    execution_id: Identifier
    request_id: Identifier
    status: ExecutionLifecycleStatus
    created_at: Timestamp
    updated_at: Timestamp
    events: tuple[EventEnvelope, ...] = ()
    summary: ExecutionSummary | None = None
    error: AgentIqError | None = None

    @model_validator(mode="after")
    def validate_snapshot(self) -> ExecutionSnapshot:
        if self.updated_at < self.created_at:
            raise ValueError("Execution update time cannot precede creation.")
        terminal = {
            ExecutionLifecycleStatus.COMPLETED,
            ExecutionLifecycleStatus.PARTIAL,
            ExecutionLifecycleStatus.DECLINED,
            ExecutionLifecycleStatus.CANCELLED,
        }
        if self.status in terminal and self.summary is None:
            raise ValueError("A successful terminal execution requires its summary.")
        if (
            self.status is ExecutionLifecycleStatus.FAILED
            and self.summary is None
            and self.error is None
        ):
            raise ValueError("A failed execution requires a summary or typed error.")
        if self.status in {
            ExecutionLifecycleStatus.ACCEPTED,
            ExecutionLifecycleStatus.RUNNING,
        } and (self.summary is not None or self.error is not None):
            raise ValueError("A running execution cannot contain a terminal outcome.")
        if self.summary is not None and (
            self.summary.execution_id != self.execution_id
            or self.summary.request_id != self.request_id
        ):
            raise ValueError("Snapshot and execution summary identities must match.")
        if any(event.execution_id != self.execution_id for event in self.events):
            raise ValueError("Snapshot events must belong to the execution.")
        return self


class FeedbackRating(StrEnum):
    HELPFUL = "helpful"
    NOT_HELPFUL = "not_helpful"


class FeedbackSubmission(ContractModel):
    schema_version: SchemaVersion = "1.0"
    feedback_id: Identifier
    execution_id: Identifier
    subject_id: ShortText
    tenant_id: ShortText
    rating: FeedbackRating
    comment: NonEmptyText | None = None
    submitted_at: Timestamp


class FeedbackReceipt(ContractModel):
    schema_version: SchemaVersion = "1.0"
    feedback_id: Identifier
    execution_id: Identifier
    accepted: bool = True


class ProductCapabilities(ContractModel):
    schema_version: SchemaVersion = "1.0"
    product: ShortText = "Agent IQ"
    ingress_transports: tuple[ShortText, ...]
    tools: tuple[Identifier, ...]
    southbound_transport: ShortText = "native_a2a"
    lifecycle_events: bool = True
    lifecycle_sse: bool = True
    ag_ui_projection: bool = True
    token_streaming: bool = False
    anonymous_canary: bool
    artifact_publication: bool
