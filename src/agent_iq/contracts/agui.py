"""Safe AG-UI wire contracts projected from the canonical Agent IQ ledger."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from .base import ContractModel, Identifier, NonEmptyText, ShortText, VersionText

AG_UI_REFERENCE_REVISION = "e0d48fd100c15448553cf4ef91218f4d0bbfff29"
AG_UI_PROJECTION_VERSION = "1.0.0"

AgUiRunStatus = Literal["running", "completed", "partial", "declined", "failed", "cancelled"]
AgUiActivityStatus = Literal["pending", "running", "complete", "failed", "cancelled"]


class AgUiScoreComponent(ContractModel):
    """Public retrieval evidence for one candidate."""

    name: Identifier
    rank: Annotated[int, Field(ge=1)]
    score: float
    matched_fields: tuple[ShortText, ...] = ()


class AgUiCandidate(ContractModel):
    """Security-trimmed candidate safe for the internal command center."""

    agent_id: Identifier
    rank: Annotated[int, Field(ge=1)]
    fused_score: float
    explanation: NonEmptyText
    matched_fields: tuple[ShortText, ...] = ()
    components: tuple[AgUiScoreComponent, ...] = ()


class AgUiSelection(ContractModel):
    """Public selection facts without work-item text or hidden reasoning."""

    agent_id: Identifier
    summary: NonEmptyText
    matched_skills: tuple[ShortText, ...] = ()
    matched_tags: tuple[ShortText, ...] = ()
    policy_facts: tuple[ShortText, ...] = ()
    fused_score: float


class AgUiPlanStepState(ContractModel):
    """Safe execution-DAG facts without work-item text or context identifiers."""

    step_id: Identifier
    agent_id: Identifier
    depends_on: tuple[Identifier, ...] = ()
    context_kinds: tuple[ShortText, ...] = ()
    expected_output_count: Annotated[int, Field(ge=1)]
    invocation_mode: ShortText
    approval_required: bool


class AgUiPlanState(ContractModel):
    """Safe plan shape used to render order, parallelism, and approvals."""

    plan_id: Identifier
    steps: tuple[AgUiPlanStepState, ...] = Field(min_length=1)
    max_concurrency: Annotated[int, Field(ge=1, le=20)]


class AgUiInvocationState(ContractModel):
    """Safe invocation state. Arguments, prompts, and provider messages are excluded."""

    invocation_id: Identifier
    step_id: Identifier
    agent_id: Identifier
    status: Literal["running", "completed", "failed"]
    transport: ShortText
    protocol_version: ShortText
    duration_ms: Annotated[int | None, Field(default=None, ge=0)]
    evidence_ids: tuple[Identifier, ...] = ()
    error_code: Identifier | None = None


class AgUiApprovalState(ContractModel):
    """Approval state without private policy metadata."""

    approval_id: Identifier
    step_ids: tuple[Identifier, ...] = Field(min_length=1)
    scope_summary: NonEmptyText
    status: Literal["requested", "approved", "declined", "expired", "cancelled"]


class AgUiTaskState(ContractModel):
    """Durable handoff/task status without destination coordinates."""

    task_id: Identifier
    agent_id: Identifier
    callback_modes: tuple[ShortText, ...]
    status: ShortText


class AgUiCanvasState(ContractModel):
    """Reconstructable safe state consumed by internal Agent IQ canvases."""

    execution_id: Identifier
    status: AgUiRunStatus = "running"
    stage: Identifier = "accepted"
    catalog_version: ShortText | None = None
    candidate_count: Annotated[int, Field(ge=0)] = 0
    candidates: tuple[AgUiCandidate, ...] = ()
    selections: tuple[AgUiSelection, ...] = ()
    plan: AgUiPlanState | None = None
    invocations: dict[Identifier, AgUiInvocationState] = Field(default_factory=dict)
    approvals: dict[Identifier, AgUiApprovalState] = Field(default_factory=dict)
    tasks: dict[Identifier, AgUiTaskState] = Field(default_factory=dict)
    evidence_ids: tuple[Identifier, ...] = ()
    answer: NonEmptyText | None = None
    citation_numbers: tuple[Annotated[int, Field(ge=1)], ...] = ()
    duration_ms: Annotated[int | None, Field(default=None, ge=0)]


class AgUiRunResult(ContractModel):
    """Terminal run result safe for a presentation client."""

    status: Literal["completed", "partial", "declined", "cancelled"]
    duration_ms: Annotated[int, Field(ge=0)]


class AgUiRunStartedEvent(ContractModel):
    type: Literal["RUN_STARTED"] = "RUN_STARTED"
    thread_id: Identifier
    run_id: Identifier
    timestamp: Annotated[int, Field(ge=0)]


class AgUiRunFinishedEvent(ContractModel):
    type: Literal["RUN_FINISHED"] = "RUN_FINISHED"
    thread_id: Identifier
    run_id: Identifier
    result: AgUiRunResult
    timestamp: Annotated[int, Field(ge=0)]


class AgUiRunErrorEvent(ContractModel):
    type: Literal["RUN_ERROR"] = "RUN_ERROR"
    message: NonEmptyText
    code: Identifier
    timestamp: Annotated[int, Field(ge=0)]


class AgUiStepStartedEvent(ContractModel):
    type: Literal["STEP_STARTED"] = "STEP_STARTED"
    step_name: Identifier
    timestamp: Annotated[int, Field(ge=0)]


class AgUiStepFinishedEvent(ContractModel):
    type: Literal["STEP_FINISHED"] = "STEP_FINISHED"
    step_name: Identifier
    timestamp: Annotated[int, Field(ge=0)]


class AgUiTextMessageStartEvent(ContractModel):
    type: Literal["TEXT_MESSAGE_START"] = "TEXT_MESSAGE_START"
    message_id: Identifier
    role: Literal["assistant"] = "assistant"
    timestamp: Annotated[int, Field(ge=0)]


class AgUiTextMessageContentEvent(ContractModel):
    type: Literal["TEXT_MESSAGE_CONTENT"] = "TEXT_MESSAGE_CONTENT"
    message_id: Identifier
    delta: NonEmptyText
    timestamp: Annotated[int, Field(ge=0)]


class AgUiTextMessageEndEvent(ContractModel):
    type: Literal["TEXT_MESSAGE_END"] = "TEXT_MESSAGE_END"
    message_id: Identifier
    timestamp: Annotated[int, Field(ge=0)]


class AgUiToolCallStartEvent(ContractModel):
    type: Literal["TOOL_CALL_START"] = "TOOL_CALL_START"
    tool_call_id: Identifier
    tool_call_name: Identifier
    timestamp: Annotated[int, Field(ge=0)]


class AgUiToolCallEndEvent(ContractModel):
    type: Literal["TOOL_CALL_END"] = "TOOL_CALL_END"
    tool_call_id: Identifier
    timestamp: Annotated[int, Field(ge=0)]


class AgUiJsonPatchOperation(ContractModel):
    """RFC 6902 operation used by the safe state projection."""

    op: Literal["add", "remove", "replace"]
    path: Annotated[str, Field(min_length=1, max_length=512, pattern=r"^/")]
    value: Any = None

    @model_validator(mode="after")
    def validate_value(self) -> AgUiJsonPatchOperation:
        if self.op in {"add", "replace"} and self.value is None:
            raise ValueError("add and replace operations require a value")
        if self.op == "remove" and self.value is not None:
            raise ValueError("remove operations cannot include a value")
        return self


class AgUiStateSnapshotEvent(ContractModel):
    type: Literal["STATE_SNAPSHOT"] = "STATE_SNAPSHOT"
    snapshot: AgUiCanvasState
    timestamp: Annotated[int, Field(ge=0)]


class AgUiStateDeltaEvent(ContractModel):
    type: Literal["STATE_DELTA"] = "STATE_DELTA"
    delta: Annotated[tuple[AgUiJsonPatchOperation, ...], Field(min_length=1)]
    timestamp: Annotated[int, Field(ge=0)]


class AgUiActivityContent(ContractModel):
    """Governed activity content rendered by the Agent IQ command center."""

    stage: Identifier
    label: NonEmptyText
    status: AgUiActivityStatus
    source_event_type: Identifier
    agent_id: Identifier | None = None
    invocation_id: Identifier | None = None
    evidence_ids: tuple[Identifier, ...] = ()


class AgUiActivitySnapshotEvent(ContractModel):
    type: Literal["ACTIVITY_SNAPSHOT"] = "ACTIVITY_SNAPSHOT"
    message_id: Identifier
    activity_type: Literal["agent_iq.lifecycle"] = "agent_iq.lifecycle"
    content: AgUiActivityContent
    replace: bool = True
    timestamp: Annotated[int, Field(ge=0)]


AgUiEvent = (
    AgUiRunStartedEvent
    | AgUiRunFinishedEvent
    | AgUiRunErrorEvent
    | AgUiStepStartedEvent
    | AgUiStepFinishedEvent
    | AgUiTextMessageStartEvent
    | AgUiTextMessageContentEvent
    | AgUiTextMessageEndEvent
    | AgUiToolCallStartEvent
    | AgUiToolCallEndEvent
    | AgUiStateSnapshotEvent
    | AgUiStateDeltaEvent
    | AgUiActivitySnapshotEvent
)


class AgUiProjectionEnvelope(ContractModel):
    """Correlation wrapper around one projected AG-UI event."""

    projection_id: Identifier
    source_event_id: Identifier
    source_sequence: Annotated[int, Field(ge=0)]
    projection_index: Annotated[int, Field(ge=0)]
    execution_id: Identifier
    trace_id: Identifier
    event: AgUiEvent = Field(discriminator="type")


class AgUiProjection(ContractModel):
    """Deterministic one-way projection of a complete canonical event prefix."""

    schema_version: VersionText = AG_UI_PROJECTION_VERSION
    ag_ui_reference_revision: Literal["e0d48fd100c15448553cf4ef91218f4d0bbfff29"] = (
        "e0d48fd100c15448553cf4ef91218f4d0bbfff29"
    )
    execution_id: Identifier
    thread_id: Identifier
    canonical_event_count: Annotated[int, Field(ge=1)]
    event_count: Annotated[int, Field(ge=1)]
    events: Annotated[tuple[AgUiProjectionEnvelope, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_projection(self) -> AgUiProjection:
        if self.event_count != len(self.events):
            raise ValueError("event_count must equal the projected event count")
        if self.events[0].event.type != "RUN_STARTED":
            raise ValueError("the first projected event must be RUN_STARTED")

        source_sequences: list[int] = []
        expected_index: dict[int, int] = {}
        run_started = 0
        terminal_events = 0
        for envelope in self.events:
            if envelope.execution_id != self.execution_id:
                raise ValueError("projection envelopes must use one execution_id")
            if envelope.projection_index != expected_index.get(envelope.source_sequence, 0):
                raise ValueError("projection_index must be contiguous for each source event")
            expected_index[envelope.source_sequence] = envelope.projection_index + 1
            if envelope.source_sequence not in source_sequences:
                source_sequences.append(envelope.source_sequence)

            event = envelope.event
            if isinstance(event, AgUiRunStartedEvent):
                run_started += 1
                if event.run_id != self.execution_id or event.thread_id != self.thread_id:
                    raise ValueError("RUN_STARTED identifiers must match the projection")
            elif isinstance(event, AgUiRunFinishedEvent):
                terminal_events += 1
                if event.run_id != self.execution_id or event.thread_id != self.thread_id:
                    raise ValueError("RUN_FINISHED identifiers must match the projection")
            elif isinstance(event, AgUiRunErrorEvent):
                terminal_events += 1

        if run_started != 1:
            raise ValueError("a projection must contain exactly one RUN_STARTED event")
        if terminal_events > 1:
            raise ValueError("a projection cannot contain multiple terminal run events")
        if source_sequences != list(range(self.canonical_event_count)):
            raise ValueError("source event sequences must form one contiguous prefix")
        if terminal_events and self.events[-1].event.type not in {"RUN_FINISHED", "RUN_ERROR"}:
            raise ValueError("a terminal run event must be the final projected event")
        return self
