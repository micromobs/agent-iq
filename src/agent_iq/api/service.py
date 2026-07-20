"""Host-independent orchestration service with lifecycle persistence."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from opentelemetry import metrics, trace
from opentelemetry.trace import Span, Status, StatusCode

from agent_iq.api.presentation import (
    public_agent_search_result,
    public_answer,
    public_execution_snapshot,
    public_execution_summary,
    public_selection_decision,
    public_text,
)
from agent_iq.contracts import (
    AgentIqError,
    AgentSearchResult,
    AgUiProjection,
    ContextInput,
    ContextReference,
    ErrorCode,
    EventEnvelope,
    ExecutionLifecycleStatus,
    ExecutionSnapshot,
    ExecutionStatus,
    ExecutionSummary,
    FeedbackReceipt,
    FeedbackSubmission,
    OrchestrationCommand,
    OrchestrationResult,
    PlanStep,
    ProductCapabilities,
    StepExecutionResult,
    StepExecutionStatus,
    TransportProtocol,
)
from agent_iq.contracts.events import (
    AgentsSelectedPayload,
    CandidatesRankedPayload,
    CatalogSearchedPayload,
    EventPayload,
    EvidenceReceivedPayload,
    ExecutionCompletedPayload,
    InvocationCompletedPayload,
    InvocationFailedPayload,
    InvocationStartedPayload,
    PlanPreparedPayload,
    PreparedPlanStep,
    RequestAcceptedPayload,
    SynthesisCompletedPayload,
)
from agent_iq.errors import AgentIqOperationError, operation_error
from agent_iq.orchestrate import (
    ContextCandidate,
    ExecutionObserver,
    OrchestrationEngine,
)
from agent_iq.presentation import AgUiProjector

LOGGER = logging.getLogger(__name__)
TRACER = trace.get_tracer("agent_iq.api")
METER = metrics.get_meter("agent_iq.api")
EXECUTION_COUNTER = METER.create_counter(
    "agent_iq.executions",
    description="Agent IQ orchestration executions by terminal status.",
)
EXECUTION_DURATION = METER.create_histogram(
    "agent_iq.execution.duration",
    unit="ms",
    description="End-to-end Agent IQ orchestration duration.",
)
INVOCATION_COUNTER = METER.create_counter(
    "agent_iq.a2a.invocations",
    description="Southbound native A2A invocations by terminal status.",
)

PRODUCT_TOOLS = (
    "search_agents",
    "orchestrate",
    "run_single_agent_demo",
    "get_execution",
    "cancel_execution",
    "submit_feedback",
    "capabilities",
)


@dataclass(slots=True)
class _ExecutionRecord:
    command: OrchestrationCommand
    execution_id: str
    created_at: datetime
    updated_at: datetime
    status: ExecutionLifecycleStatus = ExecutionLifecycleStatus.ACCEPTED
    events: list[EventEnvelope] = field(default_factory=list)
    summary: ExecutionSummary | None = None
    error: AgentIqError | None = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task[None] | None = None


class ExecutionLedger:
    """Concurrent execution/event ledger for one canary process."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._records: dict[str, _ExecutionRecord] = {}
        self._feedback: dict[str, FeedbackSubmission] = {}
        self._records_lock = asyncio.Lock()

    async def create(
        self,
        execution_id: str,
        command: OrchestrationCommand,
    ) -> _ExecutionRecord:
        now = self._clock()
        record = _ExecutionRecord(
            command=command,
            execution_id=execution_id,
            created_at=now,
            updated_at=now,
        )
        async with self._records_lock:
            if execution_id in self._records:
                raise ValueError("Execution IDs must be unique.")
            self._records[execution_id] = record
        return record

    async def attach_task(self, execution_id: str, task: asyncio.Task[None]) -> None:
        record = await self._record(execution_id)
        async with record.condition:
            record.task = task
            record.updated_at = self._clock()

    async def set_running(self, execution_id: str) -> None:
        record = await self._record(execution_id)
        async with record.condition:
            record.status = ExecutionLifecycleStatus.RUNNING
            record.updated_at = self._clock()
            record.condition.notify_all()

    async def append(
        self,
        execution_id: str,
        payload: EventPayload,
        *,
        event_id: str,
        trace_id: str | None = None,
    ) -> EventEnvelope:
        record = await self._record(execution_id)
        async with record.condition:
            event = EventEnvelope(
                event_id=event_id,
                execution_id=execution_id,
                sequence=len(record.events),
                occurred_at=self._clock(),
                trace_id=trace_id,
                payload=payload,
            )
            record.events.append(event)
            record.updated_at = self._clock()
            record.condition.notify_all()
            return event

    async def complete(self, execution_id: str, summary: ExecutionSummary) -> None:
        record = await self._record(execution_id)
        async with record.condition:
            record.summary = summary
            record.status = _lifecycle_status(summary.status)
            record.updated_at = self._clock()
            record.condition.notify_all()

    async def fail(self, execution_id: str, error: AgentIqError) -> None:
        record = await self._record(execution_id)
        async with record.condition:
            record.error = error
            record.status = ExecutionLifecycleStatus.FAILED
            record.updated_at = self._clock()
            record.condition.notify_all()

    async def snapshot(self, execution_id: str) -> ExecutionSnapshot:
        record = await self._record(execution_id)
        async with record.condition:
            return ExecutionSnapshot(
                execution_id=record.execution_id,
                request_id=record.command.request.request_id,
                status=record.status,
                created_at=record.created_at,
                updated_at=record.updated_at,
                events=tuple(record.events),
                summary=record.summary,
                error=record.error,
            )

    async def cancel(self, execution_id: str) -> ExecutionSnapshot:
        record = await self._record(execution_id)
        async with record.condition:
            if record.status not in {
                ExecutionLifecycleStatus.ACCEPTED,
                ExecutionLifecycleStatus.RUNNING,
            }:
                raise operation_error(
                    ErrorCode.INVALID_REQUEST,
                    "Only an accepted or running execution can be cancelled.",
                    correlation_id=execution_id,
                )
            record.cancel_event.set()
            record.updated_at = self._clock()
            record.condition.notify_all()
        return await self.snapshot(execution_id)

    async def cancel_event(self, execution_id: str) -> asyncio.Event:
        return (await self._record(execution_id)).cancel_event

    async def task(self, execution_id: str) -> asyncio.Task[None] | None:
        return (await self._record(execution_id)).task

    async def watch(
        self,
        execution_id: str,
        *,
        after_sequence: int = -1,
    ) -> AsyncIterator[EventEnvelope]:
        record = await self._record(execution_id)
        next_sequence = after_sequence + 1
        while True:
            async with record.condition:

                def events_ready(next_sequence: int = next_sequence) -> bool:
                    return len(record.events) > next_sequence or record.status not in {
                        ExecutionLifecycleStatus.ACCEPTED,
                        ExecutionLifecycleStatus.RUNNING,
                    }

                await record.condition.wait_for(events_ready)
                pending = tuple(record.events[next_sequence:])
                terminal = record.status not in {
                    ExecutionLifecycleStatus.ACCEPTED,
                    ExecutionLifecycleStatus.RUNNING,
                }
            for event in pending:
                yield event
                next_sequence = event.sequence + 1
            if terminal and next_sequence >= len(record.events):
                return

    async def submit_feedback(self, submission: FeedbackSubmission) -> FeedbackReceipt:
        record = await self._record(submission.execution_id)
        caller = record.command.request.caller
        if submission.subject_id != caller.subject_id or submission.tenant_id != caller.tenant_id:
            raise operation_error(
                ErrorCode.AUTHORIZATION_DENIED,
                "Feedback identity does not match the execution caller.",
                correlation_id=submission.execution_id,
            )
        existing = self._feedback.get(submission.feedback_id)
        if existing is not None and existing != submission:
            raise operation_error(
                ErrorCode.INVALID_REQUEST,
                "The feedback ID is already bound to different content.",
                correlation_id=submission.execution_id,
            )
        self._feedback[submission.feedback_id] = submission
        return FeedbackReceipt(
            feedback_id=submission.feedback_id,
            execution_id=submission.execution_id,
        )

    async def _record(self, execution_id: str) -> _ExecutionRecord:
        async with self._records_lock:
            record = self._records.get(execution_id)
        if record is None:
            raise operation_error(
                ErrorCode.INVALID_REQUEST,
                "The requested execution does not exist.",
                correlation_id=execution_id,
            )
        return record


class _LifecycleObserver(ExecutionObserver):
    def __init__(
        self,
        ledger: ExecutionLedger,
        event_id_factory: Callable[[], str],
    ) -> None:
        self._ledger = ledger
        self._event_id_factory = event_id_factory
        self._spans: dict[str, Span] = {}

    async def invocation_started(
        self,
        *,
        execution_id: str,
        step: PlanStep,
        invocation_id: str,
        protocol_version: str,
    ) -> None:
        span = TRACER.start_span(
            "agent_iq.a2a.invoke",
            attributes={
                "agent_iq.execution.id": execution_id,
                "agent_iq.step.id": step.step_id,
                "agent_iq.agent.id": step.agent_id,
                "agent_iq.transport": "a2a",
                "agent_iq.protocol.version": protocol_version,
            },
        )
        self._spans[invocation_id] = span
        await self._append(
            execution_id,
            InvocationStartedPayload(
                invocation_id=invocation_id,
                step_id=step.step_id,
                agent_id=step.agent_id,
                transport=TransportProtocol.A2A,
                protocol_version=protocol_version,
            ),
        )

    async def evidence_received(
        self,
        *,
        execution_id: str,
        step: PlanStep,
        invocation_id: str,
        evidence_ids: tuple[str, ...],
    ) -> None:
        span = self._spans.get(invocation_id)
        if span is not None:
            span.set_attribute("agent_iq.evidence.count", len(evidence_ids))
            span.add_event("evidence.received")
        await self._append(
            execution_id,
            EvidenceReceivedPayload(
                invocation_id=invocation_id,
                step_id=step.step_id,
                evidence_ids=evidence_ids,
            ),
        )

    async def invocation_finished(
        self,
        *,
        execution_id: str,
        result: StepExecutionResult,
    ) -> None:
        if result.invocation_id is None:
            return
        status = result.status.value
        if result.status is StepExecutionStatus.SUCCEEDED:
            payload: EventPayload = InvocationCompletedPayload(
                invocation_id=result.invocation_id,
                step_id=result.step_id,
                duration_ms=result.duration_ms,
            )
        else:
            if result.error is None:
                raise RuntimeError("A failed invocation lifecycle event requires a typed error.")
            payload = InvocationFailedPayload(
                invocation_id=result.invocation_id,
                step_id=result.step_id,
                error=result.error,
            )
        await self._append(execution_id, payload)
        INVOCATION_COUNTER.add(
            1,
            {
                "agent_iq.agent.id": result.agent_id,
                "agent_iq.status": status,
            },
        )
        span = self._spans.pop(result.invocation_id, None)
        if span is not None:
            span.set_attribute("agent_iq.status", status)
            span.set_attribute("agent_iq.duration_ms", result.duration_ms)
            if result.error is not None:
                span.set_attribute("agent_iq.error.code", result.error.code.value)
                span.set_status(Status(StatusCode.ERROR, result.error.code.value))
            else:
                span.set_status(Status(StatusCode.OK))
            span.end()

    async def _append(self, execution_id: str, payload: EventPayload) -> None:
        await self._ledger.append(
            execution_id,
            payload,
            event_id=self._event_id_factory(),
            trace_id=_trace_id() or execution_id,
        )


class AgentIqService:
    """One protocol-neutral service composed by every D11 ingress adapter."""

    def __init__(
        self,
        engine: OrchestrationEngine,
        *,
        ledger: ExecutionLedger | None = None,
        anonymous_canary: bool = True,
        artifact_publication: bool = False,
        clock: Callable[[], datetime] | None = None,
        execution_id_factory: Callable[[], str] | None = None,
        event_id_factory: Callable[[], str] | None = None,
        ag_ui_projector: AgUiProjector | None = None,
    ) -> None:
        self._engine = engine
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ledger = ledger or ExecutionLedger(clock=self._clock)
        self._execution_id_factory = execution_id_factory or (lambda: f"exec-{uuid.uuid4().hex}")
        self._event_id_factory = event_id_factory or (lambda: f"evt-{uuid.uuid4().hex}")
        self._ag_ui_projector = ag_ui_projector or AgUiProjector()
        self._anonymous_canary = anonymous_canary
        self._artifact_publication = artifact_publication

    def capabilities(self) -> ProductCapabilities:
        return ProductCapabilities(
            ingress_transports=("mcp", "http_sse", "a2a"),
            tools=PRODUCT_TOOLS,
            anonymous_canary=self._anonymous_canary,
            artifact_publication=self._artifact_publication,
        )

    def agent_name(self, agent_id: str) -> str:
        """Resolve an agent ID without exposing the mutable runtime composition."""

        return public_text(self._engine.agent_name(agent_id))

    def search_agents(self, command: OrchestrationCommand) -> AgentSearchResult:
        with TRACER.start_as_current_span("agent_iq.match") as span:
            selection = self._engine.search(command.request)
            span.set_attribute("agent_iq.request.id", command.request.request_id)
            span.set_attribute("agent_iq.candidate.count", len(selection.candidates))
            span.set_attribute("agent_iq.selected.count", len(selection.selected))
        return public_agent_search_result(
            AgentSearchResult(
                request_id=command.request.request_id,
                candidates=selection.candidates,
            )
        )

    async def start(self, command: OrchestrationCommand) -> ExecutionSnapshot:
        execution_id = self._execution_id_factory()
        await self._ledger.create(execution_id, command)
        await self._append(
            execution_id,
            RequestAcceptedPayload(
                request_id=command.request.request_id,
                intent_summary=command.request.intent,
            ),
        )
        task = asyncio.create_task(
            self._run(execution_id, command),
            name=f"agent-iq:{execution_id}",
        )
        await self._ledger.attach_task(execution_id, task)
        return public_execution_snapshot(await self._ledger.snapshot(execution_id))

    async def orchestrate(self, command: OrchestrationCommand) -> OrchestrationResult:
        started = await self.start(command)
        return await self.wait(started.execution_id)

    async def wait(self, execution_id: str) -> OrchestrationResult:
        internal = await self.wait_for_presentation(execution_id)
        return OrchestrationResult(
            summary=public_execution_summary(internal.summary),
            events=internal.events,
        )

    async def wait_for_presentation(self, execution_id: str) -> OrchestrationResult:
        """Return trusted machine evidence to an in-process presentation adapter."""

        task = await self._ledger.task(execution_id)
        if task is None:
            raise operation_error(
                ErrorCode.INTERNAL,
                "The execution task was not attached.",
                correlation_id=execution_id,
            )
        await asyncio.shield(task)
        snapshot = await self._ledger.snapshot(execution_id)
        if snapshot.error is not None:
            raise AgentIqOperationError(snapshot.error)
        if snapshot.summary is None:
            raise operation_error(
                ErrorCode.INTERNAL,
                "The execution ended without a terminal summary.",
                correlation_id=execution_id,
            )
        return OrchestrationResult(summary=snapshot.summary, events=snapshot.events)

    async def get_execution(self, execution_id: str) -> ExecutionSnapshot:
        return public_execution_snapshot(await self._ledger.snapshot(execution_id))

    async def get_agui_projection(
        self,
        execution_id: str,
        *,
        thread_id: str,
    ) -> AgUiProjection:
        """Return the safe replay projection without replacing the canonical ledger."""

        snapshot = await self._ledger.snapshot(execution_id)
        return self._ag_ui_projector.project(snapshot.events, thread_id=thread_id)

    async def cancel_execution(self, execution_id: str) -> ExecutionSnapshot:
        return public_execution_snapshot(await self._ledger.cancel(execution_id))

    async def submit_feedback(self, submission: FeedbackSubmission) -> FeedbackReceipt:
        return await self._ledger.submit_feedback(submission)

    def watch_events(
        self,
        execution_id: str,
        *,
        after_sequence: int = -1,
    ) -> AsyncIterator[EventEnvelope]:
        return self._ledger.watch(execution_id, after_sequence=after_sequence)

    async def _run(self, execution_id: str, command: OrchestrationCommand) -> None:
        with TRACER.start_as_current_span("agent_iq.orchestrate") as span:
            span.set_attribute("agent_iq.execution.id", execution_id)
            span.set_attribute("agent_iq.request.id", command.request.request_id)
            await self._run_core(execution_id, command)
            snapshot = await self._ledger.snapshot(execution_id)
            span.set_attribute("agent_iq.status", snapshot.status.value)
            duration_ms = snapshot.summary.duration_ms if snapshot.summary is not None else 0
            EXECUTION_COUNTER.add(1, {"agent_iq.status": snapshot.status.value})
            EXECUTION_DURATION.record(
                duration_ms,
                {"agent_iq.status": snapshot.status.value},
            )
            if snapshot.status is ExecutionLifecycleStatus.FAILED:
                code = (
                    snapshot.error.code.value if snapshot.error is not None else "execution_failed"
                )
                span.set_attribute("agent_iq.error.code", code)
                span.set_status(Status(StatusCode.ERROR, code))
            else:
                span.set_status(Status(StatusCode.OK))

    async def _run_core(self, execution_id: str, command: OrchestrationCommand) -> None:
        try:
            await self._ledger.set_running(execution_id)
            context = tuple(
                _context_candidate(reference) for reference in command.context_references
            )
            with TRACER.start_as_current_span("agent_iq.match") as match_span:
                selection = self._engine.search(command.request)
                match_span.set_attribute(
                    "agent_iq.candidate.count",
                    len(selection.candidates),
                )
                match_span.set_attribute(
                    "agent_iq.selected.count",
                    len(selection.selected),
                )
            with TRACER.start_as_current_span("agent_iq.plan") as plan_span:
                plan = self._engine.plan(
                    command.request,
                    selection,
                    context_candidates=context,
                )
                plan_span.set_attribute("agent_iq.plan.step_count", len(plan.steps))
                plan_span.set_attribute(
                    "agent_iq.plan.max_concurrency",
                    plan.max_concurrency,
                )
            public_selection = public_selection_decision(selection)
            catalog_version = (
                selection.candidates[0].catalog_version if selection.candidates else "catalog-empty"
            )
            await self._append(
                execution_id,
                CatalogSearchedPayload(
                    query=command.request.intent,
                    candidate_count=len(selection.candidates),
                    catalog_version=catalog_version,
                ),
            )
            await self._append(
                execution_id,
                CandidatesRankedPayload(candidates=public_selection.candidates),
            )
            await self._append(
                execution_id,
                AgentsSelectedPayload(decision=public_selection),
            )
            await self._append(
                execution_id,
                PlanPreparedPayload(
                    plan_id=plan.plan_id,
                    steps=tuple(
                        PreparedPlanStep(
                            step_id=step.step_id,
                            agent_id=step.agent_id,
                            depends_on=step.depends_on,
                            context_kinds=tuple(
                                dict.fromkeys(
                                    context_input.kind for context_input in step.context_inputs
                                )
                            ),
                            expected_output_count=len(step.expected_outputs),
                            invocation_mode=step.invocation_mode,
                            approval_required=step.approval_required,
                        )
                        for step in plan.steps
                    ),
                    max_concurrency=plan.max_concurrency,
                ),
            )
            cancel_event = await self._ledger.cancel_event(execution_id)
            with TRACER.start_as_current_span("agent_iq.execute_and_synthesize"):
                summary = await self._engine.execute_prepared(
                    command.request,
                    selection,
                    plan,
                    cancel_event=cancel_event,
                    execution_id=execution_id,
                    observer=_LifecycleObserver(self._ledger, self._event_id_factory),
                )
            await self._append(
                execution_id,
                SynthesisCompletedPayload(
                    answer=public_answer(summary.synthesis.answer),
                    citation_numbers=tuple(
                        citation.number for citation in summary.synthesis.evidence.citations
                    ),
                ),
            )
            await self._append(
                execution_id,
                ExecutionCompletedPayload(
                    status=_completed_status(summary.status),
                    duration_ms=summary.duration_ms,
                ),
            )
            await self._ledger.complete(execution_id, summary)
        except AgentIqOperationError as exc:
            await self._record_failure(execution_id, exc.error)
        except Exception:
            LOGGER.exception("Agent IQ execution failed", extra={"execution_id": execution_id})
            error = operation_error(
                ErrorCode.INTERNAL,
                "The orchestration execution failed unexpectedly.",
                correlation_id=execution_id,
            ).error
            await self._record_failure(execution_id, error)

    async def _record_failure(self, execution_id: str, error: AgentIqError) -> None:
        await self._append(
            execution_id,
            ExecutionCompletedPayload(status="failed", duration_ms=0),
        )
        await self._ledger.fail(execution_id, error)

    async def _append(self, execution_id: str, payload: EventPayload) -> None:
        await self._ledger.append(
            execution_id,
            payload,
            event_id=self._event_id_factory(),
            trace_id=_trace_id() or execution_id,
        )


def _context_candidate(reference: ContextReference) -> ContextCandidate:
    return ContextCandidate(
        context=ContextInput(
            context_id=reference.context_id,
            kind=reference.kind,
            source_uri=reference.source_uri,
            sensitivity=reference.sensitivity,
            approved=reference.approved,
        ),
        allowed_agent_ids=reference.allowed_agent_ids,
        relevance_terms=reference.relevance_terms,
        priority=reference.priority,
    )


def _lifecycle_status(status: ExecutionStatus) -> ExecutionLifecycleStatus:
    return {
        ExecutionStatus.COMPLETED: ExecutionLifecycleStatus.COMPLETED,
        ExecutionStatus.PARTIAL: ExecutionLifecycleStatus.PARTIAL,
        ExecutionStatus.DECLINED: ExecutionLifecycleStatus.DECLINED,
        ExecutionStatus.FAILED: ExecutionLifecycleStatus.FAILED,
        ExecutionStatus.CANCELLED: ExecutionLifecycleStatus.CANCELLED,
    }[status]


def _completed_status(
    status: ExecutionStatus,
) -> Literal["completed", "partial", "declined", "failed", "cancelled"]:
    return status.value


def _trace_id() -> str | None:
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None
    return f"{span_context.trace_id:032x}"
