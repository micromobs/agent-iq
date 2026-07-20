"""One-way, content-safe projection from Agent IQ lifecycle events to AG-UI."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Literal, Never

from agent_iq.contracts.agui import (
    AgUiActivityContent,
    AgUiActivitySnapshotEvent,
    AgUiActivityStatus,
    AgUiApprovalState,
    AgUiCandidate,
    AgUiCanvasState,
    AgUiEvent,
    AgUiInvocationState,
    AgUiJsonPatchOperation,
    AgUiPlanState,
    AgUiPlanStepState,
    AgUiProjection,
    AgUiProjectionEnvelope,
    AgUiRunErrorEvent,
    AgUiRunFinishedEvent,
    AgUiRunResult,
    AgUiRunStartedEvent,
    AgUiScoreComponent,
    AgUiSelection,
    AgUiStateDeltaEvent,
    AgUiStateSnapshotEvent,
    AgUiStepFinishedEvent,
    AgUiStepStartedEvent,
    AgUiTaskState,
    AgUiTextMessageContentEvent,
    AgUiTextMessageEndEvent,
    AgUiTextMessageStartEvent,
    AgUiToolCallEndEvent,
    AgUiToolCallStartEvent,
)
from agent_iq.contracts.events import (
    AgentsSelectedPayload,
    ApprovalRequestedPayload,
    ApprovalResolvedPayload,
    CandidatesRankedPayload,
    CatalogSearchedPayload,
    EventEnvelope,
    EvidenceReceivedPayload,
    ExecutionCompletedPayload,
    InvocationCompletedPayload,
    InvocationDeltaPayload,
    InvocationFailedPayload,
    InvocationStartedPayload,
    PlanPreparedPayload,
    RequestAcceptedPayload,
    SynthesisCompletedPayload,
    TaskCompletedPayload,
    TaskHandedOffPayload,
)


class AgUiProjectionError(ValueError):
    """Raised when canonical events cannot be projected without ambiguity."""


class UnsupportedCanonicalEventError(AgUiProjectionError):
    """Raised when a canonical event has no explicitly governed projection."""


class _ProjectionBuilder:
    def __init__(self, *, execution_id: str, thread_id: str) -> None:
        self.execution_id = execution_id
        self.thread_id = thread_id
        self.events: list[AgUiProjectionEnvelope] = []
        self.state: AgUiCanvasState | None = None
        self.open_messages: set[str] = set()

    def emit(self, source: EventEnvelope, event: AgUiEvent) -> None:
        projection_index = sum(item.source_sequence == source.sequence for item in self.events)
        self.events.append(
            AgUiProjectionEnvelope(
                projection_id=_stable_id(
                    "projection",
                    source.event_id,
                    str(projection_index),
                ),
                source_event_id=source.event_id,
                source_sequence=source.sequence,
                projection_index=projection_index,
                execution_id=source.execution_id,
                trace_id=source.trace_id,
                event=event,
            )
        )

    def update_state(self, source: EventEnvelope, **changes: object) -> None:
        if self.state is None:
            raise AgUiProjectionError("state cannot be updated before request acceptance")
        source_state = self.state.model_dump(mode="python", by_alias=False)
        source_state.update(changes)
        next_state = AgUiCanvasState.model_validate(source_state)
        serialized = next_state.model_dump(mode="json", by_alias=True)
        operations: list[AgUiJsonPatchOperation] = []
        for field_name in changes:
            field = AgUiCanvasState.model_fields[field_name]
            alias = field.serialization_alias or field.alias or field_name
            operations.append(
                AgUiJsonPatchOperation(
                    op="replace",
                    path=f"/{_escape_pointer(alias)}",
                    value=serialized[alias],
                )
            )
        self.state = next_state
        self.emit(
            source,
            AgUiStateDeltaEvent(
                delta=tuple(operations),
                timestamp=_timestamp_ms(source),
            ),
        )

    def activity(
        self,
        source: EventEnvelope,
        *,
        stage: str,
        label: str,
        status: AgUiActivityStatus,
        agent_id: str | None = None,
        invocation_id: str | None = None,
        evidence_ids: tuple[str, ...] = (),
    ) -> None:
        self.emit(
            source,
            AgUiActivitySnapshotEvent(
                message_id=_stable_id("activity", self.execution_id),
                content=AgUiActivityContent(
                    stage=stage,
                    label=label,
                    status=status,
                    source_event_type=source.payload.type,
                    agent_id=agent_id,
                    invocation_id=invocation_id,
                    evidence_ids=evidence_ids,
                ),
                timestamp=_timestamp_ms(source),
            ),
        )


class AgUiProjector:
    """Project one complete canonical event prefix into the governed AG-UI subset."""

    def project(
        self,
        events: Iterable[EventEnvelope],
        *,
        thread_id: str,
    ) -> AgUiProjection:
        source_events = tuple(events)
        _validate_source_events(source_events)
        execution_id = source_events[0].execution_id
        builder = _ProjectionBuilder(execution_id=execution_id, thread_id=thread_id)
        for source in source_events:
            self._project_event(builder, source)
        return AgUiProjection(
            execution_id=execution_id,
            thread_id=thread_id,
            canonical_event_count=len(source_events),
            event_count=len(builder.events),
            events=tuple(builder.events),
        )

    def _project_event(self, builder: _ProjectionBuilder, source: EventEnvelope) -> None:
        payload = source.payload
        timestamp = _timestamp_ms(source)

        if isinstance(payload, RequestAcceptedPayload):
            builder.emit(
                source,
                AgUiRunStartedEvent(
                    thread_id=builder.thread_id,
                    run_id=builder.execution_id,
                    timestamp=timestamp,
                ),
            )
            builder.state = AgUiCanvasState(execution_id=builder.execution_id)
            builder.emit(
                source,
                AgUiStateSnapshotEvent(snapshot=builder.state, timestamp=timestamp),
            )
            builder.activity(
                source,
                stage="accepted",
                label="Request accepted",
                status="complete",
            )
            return

        if isinstance(payload, CatalogSearchedPayload):
            builder.update_state(
                source,
                stage="catalog_searched",
                candidate_count=payload.candidate_count,
                catalog_version=payload.catalog_version,
            )
            builder.activity(
                source,
                stage="catalog_searched",
                label=f"Found {payload.candidate_count} eligible candidates",
                status="complete",
            )
            return

        if isinstance(payload, CandidatesRankedPayload):
            candidates = tuple(
                AgUiCandidate(
                    agent_id=candidate.agent_id,
                    rank=candidate.rank,
                    fused_score=candidate.fused_score,
                    explanation="Eligible public card signals matched this request.",
                    matched_fields=candidate.matched_fields,
                    components=tuple(
                        AgUiScoreComponent(
                            name=component.name,
                            rank=component.rank,
                            score=component.score,
                            matched_fields=component.matched_fields,
                        )
                        for component in candidate.components
                    ),
                )
                for candidate in payload.candidates
            )
            builder.update_state(
                source,
                stage="candidates_ranked",
                candidates=candidates,
            )
            builder.activity(
                source,
                stage="candidates_ranked",
                label="Ranked eligible specialists",
                status="complete",
            )
            return

        if isinstance(payload, AgentsSelectedPayload):
            selections = tuple(
                AgUiSelection(
                    agent_id=selection.agent_id,
                    summary="Selected from eligible public card and policy signals.",
                    matched_skills=selection.matched_skills,
                    matched_tags=selection.matched_tags,
                    policy_facts=selection.policy_facts,
                    fused_score=selection.fused_score,
                )
                for selection in payload.decision.selected
            )
            builder.update_state(
                source,
                stage="agents_selected",
                selections=selections,
            )
            builder.activity(
                source,
                stage="agents_selected",
                label=f"Selected {len(selections)} specialist(s)",
                status="complete",
            )
            return

        if isinstance(payload, PlanPreparedPayload):
            plan = AgUiPlanState(
                plan_id=payload.plan_id,
                steps=tuple(
                    AgUiPlanStepState(
                        step_id=step.step_id,
                        agent_id=step.agent_id,
                        depends_on=step.depends_on,
                        context_kinds=tuple(kind.value for kind in step.context_kinds),
                        expected_output_count=step.expected_output_count,
                        invocation_mode=step.invocation_mode.value,
                        approval_required=step.approval_required,
                    )
                    for step in payload.steps
                ),
                max_concurrency=payload.max_concurrency,
            )
            builder.update_state(source, stage="plan_prepared", plan=plan)
            builder.activity(
                source,
                stage="plan_prepared",
                label=f"Prepared a {len(payload.steps)}-step execution plan",
                status="complete",
            )
            return

        if isinstance(payload, ApprovalRequestedPayload):
            state = _required_state(builder)
            if state.plan is None:
                raise AgUiProjectionError("approval request appeared before plan preparation")
            approvals = dict(state.approvals)
            if payload.approval_id in approvals:
                raise AgUiProjectionError("approval request reused an existing approval ID")
            plan_steps = {step.step_id: step for step in state.plan.steps}
            for step_id in payload.step_ids:
                step = plan_steps.get(step_id)
                if step is None:
                    raise AgUiProjectionError("approval request references an unknown plan step")
                if not step.approval_required:
                    raise AgUiProjectionError(
                        "approval request references a step that does not require approval"
                    )
                if any(
                    step_id in approval.step_ids and approval.status == "requested"
                    for approval in approvals.values()
                ):
                    raise AgUiProjectionError("plan step already has a pending approval request")
            approvals[payload.approval_id] = AgUiApprovalState(
                approval_id=payload.approval_id,
                step_ids=payload.step_ids,
                scope_summary="Approval required for the governed plan scope.",
                status="requested",
            )
            builder.update_state(
                source,
                stage="approval_requested",
                approvals=approvals,
            )
            builder.activity(
                source,
                stage="approval_requested",
                label="Approval required before continuing",
                status="pending",
            )
            return

        if isinstance(payload, ApprovalResolvedPayload):
            state = _required_state(builder)
            approval = state.approvals.get(payload.approval_id)
            if approval is None:
                raise AgUiProjectionError("approval resolution has no matching request")
            if approval.status != "requested":
                raise AgUiProjectionError("approval resolution reused an already resolved approval")
            approvals = dict(state.approvals)
            approvals[payload.approval_id] = approval.model_copy(update={"status": payload.status})
            builder.update_state(
                source,
                stage="approval_resolved",
                approvals=approvals,
            )
            builder.activity(
                source,
                stage="approval_resolved",
                label=f"Approval {payload.status}",
                status=(
                    "complete"
                    if payload.status == "approved"
                    else "cancelled"
                    if payload.status == "cancelled"
                    else "failed"
                ),
            )
            return

        if isinstance(payload, InvocationStartedPayload):
            state = _required_state(builder)
            invocations = dict(state.invocations)
            if payload.invocation_id in invocations:
                raise AgUiProjectionError("invocation start reused an existing invocation ID")
            if state.plan is not None:
                plan_step = next(
                    (step for step in state.plan.steps if step.step_id == payload.step_id),
                    None,
                )
                if plan_step is None:
                    raise AgUiProjectionError("invocation references an unknown plan step")
                if plan_step.agent_id != payload.agent_id:
                    raise AgUiProjectionError("invocation agent does not match its plan step")
                if plan_step.approval_required and not any(
                    payload.step_id in approval.step_ids and approval.status == "approved"
                    for approval in state.approvals.values()
                ):
                    raise AgUiProjectionError(
                        "approval-required invocation started without an approved approval"
                    )
            invocations[payload.invocation_id] = AgUiInvocationState(
                invocation_id=payload.invocation_id,
                step_id=payload.step_id,
                agent_id=payload.agent_id,
                status="running",
                transport=payload.transport.value,
                protocol_version=payload.protocol_version,
            )
            builder.update_state(source, stage="invoking", invocations=invocations)
            builder.emit(
                source,
                AgUiStepStartedEvent(step_name=payload.step_id, timestamp=timestamp),
            )
            builder.emit(
                source,
                AgUiToolCallStartEvent(
                    tool_call_id=payload.invocation_id,
                    tool_call_name="invoke_a2a_agent",
                    timestamp=timestamp,
                ),
            )
            builder.activity(
                source,
                stage="invoking",
                label=f"Invoking {payload.agent_id}",
                status="running",
                agent_id=payload.agent_id,
                invocation_id=payload.invocation_id,
            )
            return

        if isinstance(payload, InvocationDeltaPayload):
            invocation = _required_invocation_step(
                builder,
                payload.invocation_id,
                payload.step_id,
            )
            message_id = _invocation_message_id(payload.invocation_id)
            if message_id not in builder.open_messages:
                builder.emit(
                    source,
                    AgUiTextMessageStartEvent(message_id=message_id, timestamp=timestamp),
                )
                builder.open_messages.add(message_id)
            builder.emit(
                source,
                AgUiTextMessageContentEvent(
                    message_id=message_id,
                    delta=payload.text,
                    timestamp=timestamp,
                ),
            )
            builder.activity(
                source,
                stage="invoking",
                label="Receiving specialist response",
                status="running",
                agent_id=invocation.agent_id,
                invocation_id=payload.invocation_id,
            )
            return

        if isinstance(payload, EvidenceReceivedPayload):
            state = _required_state(builder)
            invocation = _required_invocation_step(
                builder,
                payload.invocation_id,
                payload.step_id,
            )
            evidence_ids = _ordered_union(invocation.evidence_ids, payload.evidence_ids)
            invocations = dict(state.invocations)
            invocations[payload.invocation_id] = invocation.model_copy(
                update={"evidence_ids": evidence_ids}
            )
            builder.update_state(
                source,
                stage="evidence_received",
                invocations=invocations,
                evidence_ids=_ordered_union(state.evidence_ids, payload.evidence_ids),
            )
            builder.activity(
                source,
                stage="evidence_received",
                label=f"Received {len(payload.evidence_ids)} governed evidence record(s)",
                status="complete",
                agent_id=invocation.agent_id,
                invocation_id=payload.invocation_id,
                evidence_ids=payload.evidence_ids,
            )
            return

        if isinstance(payload, InvocationCompletedPayload):
            self._finish_invocation(
                builder,
                source,
                invocation_id=payload.invocation_id,
                step_id=payload.step_id,
                status="completed",
                duration_ms=payload.duration_ms,
                error_code=None,
            )
            return

        if isinstance(payload, InvocationFailedPayload):
            self._finish_invocation(
                builder,
                source,
                invocation_id=payload.invocation_id,
                step_id=payload.step_id,
                status="failed",
                duration_ms=None,
                error_code=payload.error.code.value,
            )
            return

        if isinstance(payload, SynthesisCompletedPayload):
            builder.update_state(
                source,
                stage="synthesis_completed",
                answer=payload.answer,
                citation_numbers=payload.citation_numbers,
            )
            message_id = _stable_id("answer", builder.execution_id)
            builder.emit(
                source,
                AgUiTextMessageStartEvent(message_id=message_id, timestamp=timestamp),
            )
            builder.emit(
                source,
                AgUiTextMessageContentEvent(
                    message_id=message_id,
                    delta=payload.answer,
                    timestamp=timestamp,
                ),
            )
            builder.emit(
                source,
                AgUiTextMessageEndEvent(message_id=message_id, timestamp=timestamp),
            )
            builder.activity(
                source,
                stage="synthesis_completed",
                label="Built the cited answer",
                status="complete",
            )
            return

        if isinstance(payload, TaskHandedOffPayload):
            state = _required_state(builder)
            tasks = dict(state.tasks)
            tasks[payload.task_id] = AgUiTaskState(
                task_id=payload.task_id,
                agent_id=payload.agent_id,
                callback_modes=tuple(mode.value for mode in payload.callback_modes),
                status="handed_off",
            )
            builder.update_state(source, stage="task_handed_off", tasks=tasks)
            builder.activity(
                source,
                stage="task_handed_off",
                label="Durable task handed off",
                status="running",
                agent_id=payload.agent_id,
            )
            return

        if isinstance(payload, TaskCompletedPayload):
            state = _required_state(builder)
            task = state.tasks.get(payload.task_id)
            if task is None:
                raise AgUiProjectionError("task completion has no matching handoff")
            tasks = dict(state.tasks)
            tasks[payload.task_id] = task.model_copy(update={"status": payload.status.value})
            builder.update_state(source, stage="task_completed", tasks=tasks)
            builder.activity(
                source,
                stage="task_completed",
                label="Durable task status updated",
                status=_task_activity_status(payload.status.value),
                agent_id=task.agent_id,
            )
            return

        if isinstance(payload, ExecutionCompletedPayload):
            builder.update_state(
                source,
                stage=payload.status,
                status=payload.status,
                duration_ms=payload.duration_ms,
            )
            terminal_status: AgUiActivityStatus = (
                "failed"
                if payload.status == "failed"
                else "cancelled"
                if payload.status == "cancelled"
                else "complete"
            )
            builder.activity(
                source,
                stage=payload.status,
                label=_terminal_label(payload.status),
                status=terminal_status,
            )
            if payload.status == "failed":
                builder.emit(
                    source,
                    AgUiRunErrorEvent(
                        message="The execution failed. Review the governed Agent IQ activity log.",
                        code="execution_failed",
                        timestamp=timestamp,
                    ),
                )
            else:
                builder.emit(
                    source,
                    AgUiRunFinishedEvent(
                        thread_id=builder.thread_id,
                        run_id=builder.execution_id,
                        result=AgUiRunResult(
                            status=payload.status,
                            duration_ms=payload.duration_ms,
                        ),
                        timestamp=timestamp,
                    ),
                )
            return

        _raise_unsupported(payload)

    def _finish_invocation(
        self,
        builder: _ProjectionBuilder,
        source: EventEnvelope,
        *,
        invocation_id: str,
        step_id: str,
        status: Literal["completed", "failed"],
        duration_ms: int | None,
        error_code: str | None,
    ) -> None:
        state = _required_state(builder)
        invocation = _required_invocation_step(builder, invocation_id, step_id)
        if invocation.status != "running":
            raise AgUiProjectionError("invocation terminal event reused a completed invocation")
        invocations = dict(state.invocations)
        invocations[invocation_id] = invocation.model_copy(
            update={
                "status": status,
                "duration_ms": duration_ms,
                "error_code": error_code,
            }
        )
        builder.update_state(source, stage="invocation_finished", invocations=invocations)
        message_id = _invocation_message_id(invocation_id)
        if message_id in builder.open_messages:
            builder.emit(
                source,
                AgUiTextMessageEndEvent(
                    message_id=message_id,
                    timestamp=_timestamp_ms(source),
                ),
            )
            builder.open_messages.remove(message_id)
        builder.emit(
            source,
            AgUiToolCallEndEvent(
                tool_call_id=invocation_id,
                timestamp=_timestamp_ms(source),
            ),
        )
        builder.emit(
            source,
            AgUiStepFinishedEvent(
                step_name=invocation.step_id,
                timestamp=_timestamp_ms(source),
            ),
        )
        builder.activity(
            source,
            stage="invocation_finished",
            label=(
                f"Completed {invocation.agent_id}"
                if status == "completed"
                else f"{invocation.agent_id} did not complete"
            ),
            status="complete" if status == "completed" else "failed",
            agent_id=invocation.agent_id,
            invocation_id=invocation_id,
        )


def replay_agui_state(projection: AgUiProjection) -> AgUiCanvasState:
    """Apply projected snapshots and RFC 6902 deltas to reconstruct Canvas state."""

    state: AgUiCanvasState | None = None
    for envelope in projection.events:
        event = envelope.event
        if isinstance(event, AgUiStateSnapshotEvent):
            state = event.snapshot
            continue
        if not isinstance(event, AgUiStateDeltaEvent):
            continue
        if state is None:
            raise AgUiProjectionError("STATE_DELTA appeared before STATE_SNAPSHOT")
        document = state.model_dump(mode="json", by_alias=True)
        for operation in event.delta:
            key = _top_level_pointer(operation.path)
            if operation.op == "remove":
                if key not in document:
                    raise AgUiProjectionError("state patch tried to remove a missing field")
                del document[key]
            else:
                if operation.op == "replace" and key not in document:
                    raise AgUiProjectionError("state patch tried to replace a missing field")
                document[key] = operation.value
        state = AgUiCanvasState.model_validate_json(json.dumps(document))
    if state is None:
        raise AgUiProjectionError("projection did not contain a state snapshot")
    return state


def _validate_source_events(events: tuple[EventEnvelope, ...]) -> None:
    if not events:
        raise AgUiProjectionError("at least one canonical event is required")
    execution_id = events[0].execution_id
    for expected_sequence, event in enumerate(events):
        if event.execution_id != execution_id:
            raise AgUiProjectionError("canonical events must use one execution_id")
        if event.sequence != expected_sequence:
            raise AgUiProjectionError("canonical event sequences must be contiguous from zero")
        if event.trace_id is None:
            raise AgUiProjectionError("canonical events require trace correlation")
    if not isinstance(events[0].payload, RequestAcceptedPayload):
        raise AgUiProjectionError("the canonical prefix must start with request.accepted")


def _required_state(builder: _ProjectionBuilder) -> AgUiCanvasState:
    if builder.state is None:
        raise AgUiProjectionError("canonical event appeared before request acceptance")
    return builder.state


def _required_invocation(
    builder: _ProjectionBuilder,
    invocation_id: str,
) -> AgUiInvocationState:
    invocation = _required_state(builder).invocations.get(invocation_id)
    if invocation is None:
        raise AgUiProjectionError("invocation event has no matching start")
    return invocation


def _required_invocation_step(
    builder: _ProjectionBuilder,
    invocation_id: str,
    step_id: str,
) -> AgUiInvocationState:
    invocation = _required_invocation(builder, invocation_id)
    if invocation.step_id != step_id:
        raise AgUiProjectionError("invocation event step does not match its start")
    return invocation


def _timestamp_ms(source: EventEnvelope) -> int:
    return int(source.occurred_at.timestamp() * 1000)


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _invocation_message_id(invocation_id: str) -> str:
    return _stable_id("message", invocation_id)


def _ordered_union(existing: tuple[str, ...], additions: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*existing, *additions)))


def _escape_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _top_level_pointer(path: str) -> str:
    if not path.startswith("/") or "/" in path[1:]:
        raise AgUiProjectionError("only top-level state patches are supported")
    return path[1:].replace("~1", "/").replace("~0", "~")


def _terminal_label(status: str) -> str:
    return {
        "completed": "Execution completed",
        "partial": "Execution completed with visible gaps",
        "declined": "Execution declined",
        "failed": "Execution failed",
        "cancelled": "Execution cancelled",
    }[status]


def _task_activity_status(status: str) -> AgUiActivityStatus:
    if status in {"failed", "expired"}:
        return "failed"
    if status == "cancelled":
        return "cancelled"
    if status == "completed":
        return "complete"
    return "running"


def _raise_unsupported(payload: object) -> Never:
    raise UnsupportedCanonicalEventError(
        f"canonical event class {type(payload).__name__!r} has no safe AG-UI projection"
    )
