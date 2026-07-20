"""Bounded native-A2A execution of validated Agent IQ dependency plans."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from agent_iq.artifacts import ArtifactPublicationTicket
from agent_iq.catalog.assets import AgentCatalogAsset
from agent_iq.contracts import (
    AgentDescriptor,
    AgentIqError,
    ArtifactEnvelope,
    ArtifactInvocationFetchRequest,
    ArtifactKind,
    ArtifactProducerIdentity,
    CallerContext,
    EligibilityDecision,
    ErrorCode,
    EvidenceEnvelope,
    EvidenceFetchRequest,
    EvidenceRecord,
    ExecutionPlan,
    ExecutionStatus,
    ExecutionSummary,
    InteractionMode,
    MatchRequest,
    PlanStep,
    SelectionDecision,
    StepExecutionResult,
    StepExecutionStatus,
    SynthesisStatus,
    TransportProtocol,
)
from agent_iq.errors import AgentIqOperationError, operation_error
from agent_iq.match import MatchEngine
from agent_iq.match.prefilter import DevelopmentEligibilityPolicy, EligibilityPolicy
from agent_iq.runtime import NativeA2ARequest, NativeA2AResult

from .context import ContextCandidate
from .planner import ExecutionPlanner
from .synthesizer import (
    AgentResultStatus,
    AgentSynthesisInput,
    GroundedSynthesizer,
)


class ExecutionDispatcher(Protocol):
    async def dispatch(
        self,
        request: NativeA2ARequest,
        *,
        caller: CallerContext,
        cancel_event: asyncio.Event | None = None,
    ) -> NativeA2AResult: ...

    def fetch_evidence(
        self,
        request: EvidenceFetchRequest,
        *,
        caller: CallerContext,
    ) -> EvidenceEnvelope: ...


class ArtifactBroker(Protocol):
    def authorize_invocation(
        self,
        *,
        invocation_id: str,
        correlation_id: str,
        caller: CallerContext,
        producer: ArtifactProducerIdentity,
        allowed_kinds: Sequence[ArtifactKind],
    ) -> ArtifactPublicationTicket: ...

    def fetch_invocation(
        self,
        request: ArtifactInvocationFetchRequest,
        *,
        caller: CallerContext,
    ) -> tuple[ArtifactEnvelope, ...]: ...

    def revoke_invocation(self, invocation_id: str) -> None: ...


class ArtifactProducerResolver(Protocol):
    async def resolve(self, descriptor: AgentDescriptor) -> ArtifactProducerIdentity: ...


class ConflictDetector(Protocol):
    def detect(self, steps: tuple[StepExecutionResult, ...]) -> frozenset[str]: ...


class ExecutionObserver(Protocol):
    """Receive content-safe invocation lifecycle facts in execution order."""

    async def invocation_started(
        self,
        *,
        execution_id: str,
        step: PlanStep,
        invocation_id: str,
        protocol_version: str,
    ) -> None: ...

    async def evidence_received(
        self,
        *,
        execution_id: str,
        step: PlanStep,
        invocation_id: str,
        evidence_ids: tuple[str, ...],
    ) -> None: ...

    async def invocation_finished(
        self,
        *,
        execution_id: str,
        result: StepExecutionResult,
    ) -> None: ...


class NoConflictDetector:
    def detect(self, steps: tuple[StepExecutionResult, ...]) -> frozenset[str]:
        del steps
        return frozenset()


class CatalogInvocationAuthorizer:
    """Re-check current catalog policy immediately before every invocation."""

    def __init__(
        self,
        catalog: AgentCatalogAsset,
        *,
        policy: EligibilityPolicy | None = None,
    ) -> None:
        self._agents = {agent.agent_id: agent for agent in catalog.agents}
        self._policy = policy or DevelopmentEligibilityPolicy()

    def descriptor(self, agent_id: str) -> AgentDescriptor | None:
        return self._agents.get(agent_id)

    def authorize(self, request: MatchRequest, step: PlanStep) -> EligibilityDecision:
        descriptor = self._agents.get(step.agent_id)
        if descriptor is None:
            return EligibilityDecision(
                eligible=False,
                blocked_reasons=("agent is absent from the current catalog",),
                policy_version="catalog-reauthorization-v1",
            )
        decision = self._policy.evaluate(request, descriptor)
        if not decision.eligible:
            return decision
        if not any(
            capability.protocol is TransportProtocol.A2A
            and InteractionMode.SYNC in capability.interaction_modes
            for capability in descriptor.capabilities
        ):
            return EligibilityDecision(
                eligible=False,
                blocked_reasons=("agent lacks synchronous native A2A",),
                policy_version=decision.policy_version,
            )
        return decision


@dataclass(frozen=True, slots=True)
class _CompletedStep:
    result: StepExecutionResult
    records: tuple[EvidenceRecord, ...] = ()


class DagExecutor:
    """Execute ready DAG stages concurrently and preserve every terminal outcome."""

    def __init__(
        self,
        catalog: AgentCatalogAsset,
        dispatcher: ExecutionDispatcher,
        synthesizer: GroundedSynthesizer,
        *,
        eligibility_policy: EligibilityPolicy | None = None,
        artifact_broker: ArtifactBroker | None = None,
        artifact_producer_resolver: ArtifactProducerResolver | None = None,
        conflict_detector: ConflictDetector | None = None,
        clock: Callable[[], datetime] | None = None,
        execution_id_factory: Callable[[], str] | None = None,
        invocation_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._synthesizer = synthesizer
        self._authorizer = CatalogInvocationAuthorizer(catalog, policy=eligibility_policy)
        self._artifact_broker = artifact_broker
        self._artifact_producer_resolver = artifact_producer_resolver
        self._conflict_detector = conflict_detector or NoConflictDetector()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._execution_id_factory = execution_id_factory or (lambda: f"exec-{uuid.uuid4().hex}")
        self._invocation_id_factory = invocation_id_factory or (lambda: f"inv-{uuid.uuid4().hex}")

    async def execute(
        self,
        plan: ExecutionPlan,
        selection: SelectionDecision,
        request: MatchRequest,
        *,
        cancel_event: asyncio.Event | None = None,
        execution_id: str | None = None,
        observer: ExecutionObserver | None = None,
    ) -> ExecutionSummary:
        self._validate_inputs(plan, selection, request)
        execution_id = execution_id or self._execution_id_factory()
        started_at = self._clock()
        started = time.monotonic()
        deadline_at = started + plan.overall_deadline_seconds
        caller = request.caller
        completed: dict[str, _CompletedStep] = {}
        pending = {step.step_id: step for step in plan.steps}
        semaphore = asyncio.Semaphore(plan.max_concurrency)

        while pending:
            if cancel_event is not None and cancel_event.is_set():
                for step in pending.values():
                    completed[step.step_id] = self._terminal_step(
                        step,
                        StepExecutionStatus.CANCELLED,
                        operation_error(
                            ErrorCode.CANCELLED,
                            "The orchestration request was cancelled.",
                            correlation_id=execution_id,
                        ).error,
                    )
                break

            ready = tuple(
                step
                for step in pending.values()
                if all(dependency in completed for dependency in step.depends_on)
            )
            if not ready:
                raise RuntimeError("A validated execution plan has no runnable step.")

            runnable: list[PlanStep] = []
            for step in ready:
                failed_dependencies = [
                    dependency
                    for dependency in step.depends_on
                    if completed[dependency].result.status is not StepExecutionStatus.SUCCEEDED
                ]
                if failed_dependencies:
                    completed[step.step_id] = self._terminal_step(
                        step,
                        StepExecutionStatus.SKIPPED,
                        operation_error(
                            ErrorCode.DEPENDENCY_FAILED,
                            "The step was skipped because a required earlier step did not succeed.",
                            correlation_id=execution_id,
                        ).error,
                    )
                    del pending[step.step_id]
                else:
                    runnable.append(step)

            if not runnable:
                continue
            remaining = deadline_at - time.monotonic()
            if remaining <= 0:
                self._mark_remaining_timed_out(pending, completed, execution_id)
                break

            tasks = {
                asyncio.create_task(
                    self._execute_step(
                        step,
                        plan=plan,
                        request=request,
                        caller=caller,
                        execution_id=execution_id,
                        completed=completed,
                        semaphore=semaphore,
                        cancel_event=cancel_event,
                        observer=observer,
                    )
                ): step
                for step in runnable
            }
            done, unfinished = await asyncio.wait(tasks, timeout=remaining)
            failed_task = next(
                (task for task in done if task.cancelled() or task.exception() is not None),
                None,
            )
            if failed_task is not None:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if failed_task.cancelled():
                    raise asyncio.CancelledError
                failure = failed_task.exception()
                if failure is None:
                    raise RuntimeError("An orchestration task failed without an exception.")
                raise failure
            for task in done:
                step = tasks[task]
                completed[step.step_id] = task.result()
                del pending[step.step_id]

            if unfinished:
                status = (
                    StepExecutionStatus.CANCELLED
                    if cancel_event is not None and cancel_event.is_set()
                    else StepExecutionStatus.TIMED_OUT
                )
                code = (
                    ErrorCode.CANCELLED
                    if status is StepExecutionStatus.CANCELLED
                    else ErrorCode.INVOCATION_TIMEOUT
                )
                message = (
                    "The orchestration request was cancelled."
                    if status is StepExecutionStatus.CANCELLED
                    else "The overall orchestration deadline expired."
                )
                for task in unfinished:
                    task.cancel()
                await asyncio.gather(*unfinished, return_exceptions=True)
                for task in unfinished:
                    step = tasks[task]
                    completed[step.step_id] = self._terminal_step(
                        step,
                        status,
                        operation_error(
                            code,
                            message,
                            correlation_id=execution_id,
                        ).error,
                    )
                    del pending[step.step_id]
                if status is StepExecutionStatus.TIMED_OUT:
                    self._mark_remaining_timed_out(pending, completed, execution_id)
                    break

        ordered = tuple(completed[step.step_id] for step in plan.steps)
        step_results = tuple(item.result for item in ordered)
        conflicts = self._conflict_detector.detect(step_results)
        unknown_conflicts = conflicts - {step.step_id for step in step_results}
        if unknown_conflicts:
            raise ValueError("Conflict detector returned an unknown step ID.")
        synthesis = self._synthesizer.synthesize(
            execution_id,
            tuple(
                self._synthesis_input(
                    item,
                    conflict=item.result.step_id in conflicts,
                    execution_id=execution_id,
                )
                for item in ordered
            ),
            caller=caller,
        )
        duration_ms = round((time.monotonic() - started) * 1000)
        return ExecutionSummary(
            execution_id=execution_id,
            request_id=request.request_id,
            status=_execution_status(step_results, synthesis.status),
            plan=plan,
            selection=selection,
            steps=step_results,
            synthesis=synthesis,
            started_at=started_at,
            completed_at=self._clock(),
            duration_ms=duration_ms,
        )

    async def _execute_step(
        self,
        step: PlanStep,
        *,
        plan: ExecutionPlan,
        request: MatchRequest,
        caller: CallerContext,
        execution_id: str,
        completed: dict[str, _CompletedStep],
        semaphore: asyncio.Semaphore,
        cancel_event: asyncio.Event | None,
        observer: ExecutionObserver | None,
    ) -> _CompletedStep:
        started = time.monotonic()
        invocation_id: str | None = None
        artifact_authorized = False
        async with semaphore:
            decision = self._authorizer.authorize(request, step)
            if not decision.eligible:
                return self._terminal_step(
                    step,
                    StepExecutionStatus.FAILED,
                    operation_error(
                        ErrorCode.AGENT_INELIGIBLE,
                        decision.blocked_reasons[0],
                        correlation_id=execution_id,
                    ).error,
                    duration_ms=round((time.monotonic() - started) * 1000),
                )

            descriptor = self._authorizer.descriptor(step.agent_id)
            if descriptor is None:
                raise RuntimeError("Authorized agent disappeared from the catalog projection.")
            invocation_id = self._invocation_id_factory()
            artifact_kinds = _artifact_kinds(descriptor)
            if artifact_kinds:
                if self._artifact_broker is None:
                    return self._terminal_step(
                        step,
                        StepExecutionStatus.FAILED,
                        operation_error(
                            ErrorCode.ARTIFACT_UNAVAILABLE,
                            "The agent requires a typed-artifact channel that is unavailable.",
                            correlation_id=execution_id,
                        ).error,
                        invocation_id=invocation_id,
                        duration_ms=round((time.monotonic() - started) * 1000),
                    )
                try:
                    producer = await self._resolve_artifact_producer(descriptor)
                except AgentIqOperationError as exc:
                    return self._terminal_step(
                        step,
                        StepExecutionStatus.FAILED,
                        exc.error,
                        invocation_id=invocation_id,
                        duration_ms=round((time.monotonic() - started) * 1000),
                    )
                try:
                    self._artifact_broker.authorize_invocation(
                        invocation_id=invocation_id,
                        correlation_id=execution_id,
                        caller=caller,
                        producer=producer,
                        allowed_kinds=artifact_kinds,
                    )
                except AgentIqOperationError as exc:
                    return self._terminal_step(
                        step,
                        _step_status(exc.error.code),
                        exc.error,
                        invocation_id=invocation_id,
                        duration_ms=round((time.monotonic() - started) * 1000),
                    )
                artifact_authorized = True

            prompt = _render_prompt(step, completed)
            native_request = NativeA2ARequest(
                invocation_id=invocation_id,
                agent_id=step.agent_id,
                prompt=prompt,
                deadline_seconds=step.deadline_seconds,
                correlation_id=execution_id,
                preserve_evidence_markers=True,
                enable_artifact_publication=artifact_authorized,
            )
            if observer is not None:
                await observer.invocation_started(
                    execution_id=execution_id,
                    step=step,
                    invocation_id=invocation_id,
                    protocol_version=native_request.preferred_card_versions[0],
                )
            try:
                native_result = await _dispatch_with_controls(
                    self._dispatcher,
                    native_request,
                    caller=caller,
                    deadline_seconds=step.deadline_seconds,
                    cancel_event=cancel_event,
                )
                _validate_native_result(native_request, native_result)
                if native_result.evidence is None:
                    raise operation_error(
                        ErrorCode.EVIDENCE_UNAVAILABLE,
                        "The agent returned no authenticated structured evidence.",
                        correlation_id=execution_id,
                    )
                evidence = self._dispatcher.fetch_evidence(
                    EvidenceFetchRequest.from_reference(native_result.evidence),
                    caller=caller,
                )
                if evidence.invocation_id != invocation_id or evidence.agent_id != step.agent_id:
                    raise operation_error(
                        ErrorCode.EVIDENCE_UNAVAILABLE,
                        "The evidence envelope does not match the invoked plan step.",
                        correlation_id=execution_id,
                    )
                evidence_ids = tuple(record.evidence_id for record in evidence.records)
                if observer is not None:
                    await observer.evidence_received(
                        execution_id=execution_id,
                        step=step,
                        invocation_id=invocation_id,
                        evidence_ids=evidence_ids,
                    )
                artifacts = (
                    self._artifact_broker.fetch_invocation(
                        ArtifactInvocationFetchRequest(
                            invocation_id=invocation_id,
                            correlation_id=execution_id,
                        ),
                        caller=caller,
                    )
                    if artifact_authorized and self._artifact_broker is not None
                    else ()
                )
                result = StepExecutionResult(
                    step_id=step.step_id,
                    agent_id=step.agent_id,
                    status=StepExecutionStatus.SUCCEEDED,
                    invocation_id=invocation_id,
                    protocol_version=native_result.protocol_version,
                    duration_ms=round((time.monotonic() - started) * 1000),
                    answer=native_result.answer,
                    evidence_ids=evidence_ids,
                    artifacts=artifacts,
                )
                if observer is not None:
                    await observer.invocation_finished(
                        execution_id=execution_id,
                        result=result,
                    )
                return _CompletedStep(result=result, records=evidence.records)
            except AgentIqOperationError as exc:
                if artifact_authorized and self._artifact_broker is not None:
                    self._artifact_broker.revoke_invocation(invocation_id)
                terminal = self._terminal_step(
                    step,
                    _step_status(exc.error.code),
                    exc.error,
                    invocation_id=invocation_id,
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
                if observer is not None:
                    await observer.invocation_finished(
                        execution_id=execution_id,
                        result=terminal.result,
                    )
                return terminal

    async def _resolve_artifact_producer(
        self,
        descriptor: AgentDescriptor,
    ) -> ArtifactProducerIdentity:
        if self._artifact_producer_resolver is not None:
            producer = await self._artifact_producer_resolver.resolve(descriptor)
            if producer.agent_id != descriptor.agent_id:
                raise operation_error(
                    ErrorCode.AGENT_INELIGIBLE,
                    "The live artifact producer identity does not match the selected agent.",
                )
            return producer
        if descriptor.trust.identity_id is None:
            raise operation_error(
                ErrorCode.AGENT_INELIGIBLE,
                "An artifact-producing agent requires a trusted runtime identity.",
            )
        return ArtifactProducerIdentity(
            agent_id=descriptor.agent_id,
            agent_version=descriptor.version.version,
            identity_id=descriptor.trust.identity_id,
            framework=descriptor.implementation.framework,
        )

    def _terminal_step(
        self,
        step: PlanStep,
        status: StepExecutionStatus,
        error: AgentIqError,
        *,
        invocation_id: str | None = None,
        duration_ms: int = 0,
    ) -> _CompletedStep:
        return _CompletedStep(
            result=StepExecutionResult(
                step_id=step.step_id,
                agent_id=step.agent_id,
                status=status,
                invocation_id=invocation_id,
                duration_ms=duration_ms,
                error=error,
            )
        )

    def _mark_remaining_timed_out(
        self,
        pending: dict[str, PlanStep],
        completed: dict[str, _CompletedStep],
        execution_id: str,
    ) -> None:
        for step in pending.values():
            completed[step.step_id] = self._terminal_step(
                step,
                StepExecutionStatus.TIMED_OUT,
                operation_error(
                    ErrorCode.INVOCATION_TIMEOUT,
                    "The overall orchestration deadline expired.",
                    correlation_id=execution_id,
                ).error,
            )
        pending.clear()

    def _synthesis_input(
        self,
        item: _CompletedStep,
        *,
        conflict: bool,
        execution_id: str,
    ) -> AgentSynthesisInput:
        result = item.result
        descriptor = self._authorizer.descriptor(result.agent_id)
        status = {
            StepExecutionStatus.SUCCEEDED: AgentResultStatus.SUCCESS,
            StepExecutionStatus.DECLINED: AgentResultStatus.DECLINED,
            StepExecutionStatus.FAILED: AgentResultStatus.FAILED,
            StepExecutionStatus.TIMED_OUT: AgentResultStatus.TIMED_OUT,
            StepExecutionStatus.CANCELLED: AgentResultStatus.CANCELLED,
            StepExecutionStatus.SKIPPED: AgentResultStatus.FAILED,
        }[result.status]
        return AgentSynthesisInput(
            agent_id=result.agent_id,
            invocation_id=result.invocation_id or f"{execution_id}:{result.step_id}",
            status=status,
            answer=result.answer,
            records=item.records,
            public_message=result.error.message if result.error is not None else None,
            display_name=descriptor.name if descriptor is not None else result.agent_id,
            conflict=conflict,
        )

    @staticmethod
    def _validate_inputs(
        plan: ExecutionPlan,
        selection: SelectionDecision,
        request: MatchRequest,
    ) -> None:
        if plan.request_id != request.request_id or selection.request_id != request.request_id:
            raise ValueError("Request, selection, and plan IDs must match.")
        selected = {reason.agent_id for reason in selection.selected}
        if selected != {step.agent_id for step in plan.steps}:
            raise ValueError("The plan must execute exactly the selected agents.")
        if any(step.invocation_mode is not InteractionMode.SYNC for step in plan.steps):
            raise ValueError("The native-A2A DAG executor supports synchronous steps only.")
        if any(step.deadline_seconds > 600 for step in plan.steps):
            raise ValueError("Native-A2A step deadlines cannot exceed 600 seconds.")


class OrchestrationEngine:
    """One dynamic Match -> plan -> native-A2A execution composition."""

    def __init__(
        self,
        matcher: MatchEngine,
        planner: ExecutionPlanner,
        executor: DagExecutor,
    ) -> None:
        self._matcher = matcher
        self._planner = planner
        self._executor = executor

    async def orchestrate(
        self,
        request: MatchRequest,
        *,
        context_candidates: tuple[ContextCandidate, ...] = (),
        cancel_event: asyncio.Event | None = None,
        execution_id: str | None = None,
        observer: ExecutionObserver | None = None,
    ) -> ExecutionSummary:
        selection, plan = self.prepare(request, context_candidates=context_candidates)
        return await self.execute_prepared(
            request,
            selection,
            plan,
            cancel_event=cancel_event,
            execution_id=execution_id,
            observer=observer,
        )

    def search(self, request: MatchRequest) -> SelectionDecision:
        return self._matcher.match(request)

    def agent_name(self, agent_id: str) -> str:
        """Resolve a selected catalog ID to its public display name."""

        for descriptor in self._matcher.catalog.agents:
            if descriptor.agent_id == agent_id:
                return descriptor.name
        raise KeyError(f"Unknown catalog agent ID {agent_id!r}.")

    def prepare(
        self,
        request: MatchRequest,
        *,
        context_candidates: tuple[ContextCandidate, ...] = (),
    ) -> tuple[SelectionDecision, ExecutionPlan]:
        selection = self.search(request)
        plan = self.plan(
            request,
            selection,
            context_candidates=context_candidates,
        )
        return selection, plan

    def plan(
        self,
        request: MatchRequest,
        selection: SelectionDecision,
        *,
        context_candidates: tuple[ContextCandidate, ...] = (),
    ) -> ExecutionPlan:
        return self._planner.plan(
            request,
            selection,
            context_candidates=context_candidates,
        )

    async def execute_prepared(
        self,
        request: MatchRequest,
        selection: SelectionDecision,
        plan: ExecutionPlan,
        *,
        cancel_event: asyncio.Event | None = None,
        execution_id: str | None = None,
        observer: ExecutionObserver | None = None,
    ) -> ExecutionSummary:
        return await self._executor.execute(
            plan,
            selection,
            request,
            cancel_event=cancel_event,
            execution_id=execution_id,
            observer=observer,
        )


async def _dispatch_with_controls(
    dispatcher: ExecutionDispatcher,
    request: NativeA2ARequest,
    *,
    caller: CallerContext,
    deadline_seconds: float,
    cancel_event: asyncio.Event | None,
) -> NativeA2AResult:
    dispatch_task = asyncio.create_task(
        dispatcher.dispatch(request, caller=caller, cancel_event=cancel_event)
    )
    cancel_task = asyncio.create_task(cancel_event.wait()) if cancel_event is not None else None
    waiters: set[asyncio.Task[object]] = {dispatch_task}
    if cancel_task is not None:
        waiters.add(cancel_task)
    try:
        done, _ = await asyncio.wait(
            waiters,
            timeout=deadline_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if dispatch_task in done:
            return await dispatch_task
        if cancel_task is not None and cancel_task in done:
            raise operation_error(
                ErrorCode.CANCELLED,
                "The agent invocation was cancelled.",
                correlation_id=request.correlation_id or request.invocation_id,
            )
        raise operation_error(
            ErrorCode.INVOCATION_TIMEOUT,
            "The agent invocation exceeded its step deadline.",
            retryable=True,
            correlation_id=request.correlation_id or request.invocation_id,
        )
    finally:
        if cancel_task is not None and not cancel_task.done():
            cancel_task.cancel()
            with suppress(asyncio.CancelledError):
                await cancel_task
        if not dispatch_task.done():
            dispatch_task.cancel()
            with suppress(asyncio.CancelledError):
                await dispatch_task


def _validate_native_result(
    request: NativeA2ARequest,
    result: NativeA2AResult,
) -> None:
    if result.invocation_id != request.invocation_id or result.agent_id != request.agent_id:
        raise operation_error(
            ErrorCode.INVOCATION_FAILED,
            "The native A2A result identity does not match the requested plan step.",
            correlation_id=request.correlation_id,
        )
    if result.correlation_id not in {None, request.correlation_id}:
        raise operation_error(
            ErrorCode.INVOCATION_FAILED,
            "The native A2A result correlation does not match the execution.",
            correlation_id=request.correlation_id,
        )


def _artifact_kinds(descriptor: AgentDescriptor) -> tuple[ArtifactKind, ...]:
    return tuple(
        dict.fromkeys(
            kind
            for capability in descriptor.capabilities
            if capability.protocol is TransportProtocol.A2A
            for kind in capability.artifact_kinds
        )
    )


def _render_prompt(
    step: PlanStep,
    completed: dict[str, _CompletedStep],
) -> str:
    sections = [
        f"Assigned work item: {step.work_item}",
        "Stay within this assigned work item and return a grounded answer.",
        (
            "Use the smallest sufficient evidence set. Keep the answer under 140 words "
            "unless a compact table is essential. Preserve every [ref_id:n] marker used."
        ),
    ]
    external = [context for context in step.context_inputs if context.kind.value != "prior_step"]
    if external:
        lines = [
            f"- {context.context_id} ({context.kind.value})"
            + (f": {context.source_uri}" if context.source_uri is not None else "")
            for context in external
        ]
        sections.append("Approved context references:\n" + "\n".join(lines))
    if step.depends_on:
        lines = [
            f"- {dependency}: {completed[dependency].result.answer}"
            for dependency in step.depends_on
        ]
        sections.append("Required prior-step results:\n" + "\n".join(lines))
    return "\n\n".join(sections)


def _step_status(code: ErrorCode) -> StepExecutionStatus:
    if code is ErrorCode.CANCELLED:
        return StepExecutionStatus.CANCELLED
    if code is ErrorCode.INVOCATION_TIMEOUT:
        return StepExecutionStatus.TIMED_OUT
    if code is ErrorCode.EVIDENCE_UNAVAILABLE:
        return StepExecutionStatus.DECLINED
    return StepExecutionStatus.FAILED


def _execution_status(
    steps: tuple[StepExecutionResult, ...],
    synthesis_status: SynthesisStatus,
) -> ExecutionStatus:
    succeeded = [step for step in steps if step.status is StepExecutionStatus.SUCCEEDED]
    if succeeded:
        if (
            all(step.status is StepExecutionStatus.SUCCEEDED for step in steps)
            and synthesis_status is SynthesisStatus.COMPLETE
        ):
            return ExecutionStatus.COMPLETED
        return ExecutionStatus.PARTIAL
    if all(step.status is StepExecutionStatus.DECLINED for step in steps):
        return ExecutionStatus.DECLINED
    if any(step.status is StepExecutionStatus.CANCELLED for step in steps):
        return ExecutionStatus.CANCELLED
    return ExecutionStatus.FAILED
