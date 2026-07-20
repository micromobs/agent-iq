from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import pytest

from agent_iq.artifacts import CompanionArtifactStore
from agent_iq.catalog import load_consumer_assets
from agent_iq.catalog.assets import AgentCatalogAsset
from agent_iq.config import Settings
from agent_iq.contracts import (
    AgentDescriptor,
    ArtifactKind,
    ArtifactProducerIdentity,
    CallerContext,
    ContextInput,
    ContextKind,
    DemandScenario,
    EligibilityDecision,
    ErrorCode,
    EvidenceEnvelope,
    EvidenceFetchRequest,
    EvidenceLocator,
    EvidenceRecord,
    ExecutionStatus,
    ExecutionSummary,
    MatchRequest,
    StepExecutionResult,
    StepExecutionStatus,
)
from agent_iq.errors import operation_error
from agent_iq.evidence import CompanionEvidenceStore, EvidenceResolver
from agent_iq.match import MatchEngine
from agent_iq.match.prefilter import EligibilityPolicy
from agent_iq.orchestrate import (
    ConflictDetector,
    ContextCandidate,
    DagExecutor,
    ExecutionPlanner,
    GroundedSynthesizer,
    OrchestrationEngine,
    PlannerPolicy,
)
from agent_iq.runtime import NativeA2ARequest, NativeA2AResult

NOW = datetime(2026, 7, 11, 2, 0, tzinfo=UTC)


class FakeDispatcher:
    def __init__(
        self,
        *,
        behaviors: dict[str, str] | None = None,
        artifact_store: CompanionArtifactStore | None = None,
        producers: dict[str, ArtifactProducerIdentity] | None = None,
    ) -> None:
        handles = iter(f"evh-{index}" for index in range(1, 50))
        self._evidence = CompanionEvidenceStore(
            clock=lambda: NOW,
            handle_factory=lambda: next(handles),
        )
        self.behaviors = behaviors or {}
        self.artifact_store = artifact_store
        self.producers = producers or {}
        self.calls: list[str] = []
        self.prompts: dict[str, str] = {}
        self.timeline: list[tuple[str, str]] = []
        self.active = 0
        self.max_active = 0

    async def dispatch(
        self,
        request: NativeA2ARequest,
        *,
        caller: CallerContext,
        cancel_event: asyncio.Event | None = None,
    ) -> NativeA2AResult:
        assert request.invocation_id is not None
        self.calls.append(request.agent_id)
        self.prompts[request.agent_id] = request.prompt
        self.timeline.append(("start", request.agent_id))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        behavior = self.behaviors.get(request.agent_id, "success")
        try:
            if behavior == "slow":
                await asyncio.sleep(10)
            else:
                await asyncio.sleep(0.02)
            if cancel_event is not None and cancel_event.is_set():
                raise operation_error(
                    ErrorCode.CANCELLED,
                    "Fake invocation observed cancellation.",
                    correlation_id=request.correlation_id,
                )
            if behavior == "failed":
                raise operation_error(
                    ErrorCode.INVOCATION_FAILED,
                    "Fake agent failed.",
                    correlation_id=request.correlation_id,
                )
            if behavior == "timed_out":
                raise operation_error(
                    ErrorCode.INVOCATION_TIMEOUT,
                    "Fake agent timed out.",
                    correlation_id=request.correlation_id,
                )

            if request.agent_id in self.producers:
                assert self.artifact_store is not None
                assert request.enable_artifact_publication is True
                assert request.correlation_id is not None
                producer = self.producers[request.agent_id]
                ticket = self.artifact_store.claim_publication_ticket(
                    invocation_id=request.invocation_id,
                    correlation_id=request.correlation_id,
                    producer_identity_id=producer.identity_id,
                )
                self.artifact_store.publish_with_ticket(
                    ticket.secret,
                    DemandScenario(
                        artifact_id="demand-germany-october",
                        product_id="brightwash-cold",
                        market="Germany",
                        horizon_start=date(2026, 10, 1),
                        horizon_end=date(2026, 10, 31),
                        unit="cases",
                        baseline_quantity=1000,
                        forecast_quantity=1250,
                        confidence=0.84,
                        evidence_ids=(f"evidence-{request.agent_id}",),
                        created_at=NOW,
                    ),
                    producer=producer,
                )

            answer = f"{request.agent_id} grounded finding [ref_id:0]"
            reference = None
            if behavior != "no_evidence":
                record = EvidenceRecord(
                    evidence_id=f"evidence-{request.agent_id}",
                    invocation_id=request.invocation_id,
                    agent_id=request.agent_id,
                    source_id=f"source-{request.agent_id}",
                    title=f"Source for {request.agent_id}",
                    locator=EvidenceLocator(document_key=f"source-{request.agent_id}"),
                    excerpt=f"Grounding for {request.agent_id}.",
                    local_reference_id="0",
                    knowledge_source=f"kb-{request.agent_id}",
                    retrieved_at=NOW,
                    content_hash=f"sha256:{'a' * 64}",
                )
                reference = self._evidence.publish(
                    (record,),
                    caller=caller,
                    correlation_id=request.correlation_id or request.invocation_id,
                )
            return NativeA2AResult(
                invocation_id=(
                    "inv-wrong" if behavior == "wrong_identity" else request.invocation_id
                ),
                agent_id=(
                    "consumer-formulation-rd" if behavior == "wrong_identity" else request.agent_id
                ),
                agent_version="1.0.0",
                protocol_version="1.0",
                message_id=f"msg-{request.invocation_id}",
                answer=answer,
                duration_ms=20,
                attempt_count=1,
                correlation_id=(
                    "execution-wrong" if behavior == "wrong_correlation" else request.correlation_id
                ),
                evidence=reference,
            )
        finally:
            self.active -= 1
            self.timeline.append(("end", request.agent_id))

    def fetch_evidence(
        self,
        request: EvidenceFetchRequest,
        *,
        caller: CallerContext,
    ) -> EvidenceEnvelope:
        return self._evidence.fetch(request, caller=caller)


class MarkAllConflicting:
    def detect(self, steps: tuple[StepExecutionResult, ...]) -> frozenset[str]:
        return frozenset(
            step.step_id for step in steps if step.status is StepExecutionStatus.SUCCEEDED
        )


class UnknownConflict:
    def detect(self, steps: tuple[StepExecutionResult, ...]) -> frozenset[str]:
        del steps
        return frozenset({"step-unknown"})


class DenyAtInvocation:
    def evaluate(
        self,
        request: MatchRequest,
        agent: AgentDescriptor,
    ) -> EligibilityDecision:
        del request, agent
        return EligibilityDecision(
            eligible=False,
            blocked_reasons=("agent was revoked after selection",),
            policy_version="deny-v1",
        )


class LiveProducerResolver:
    def __init__(self, producer: ArtifactProducerIdentity) -> None:
        self.producer = producer
        self.calls: list[str] = []

    async def resolve(self, descriptor: AgentDescriptor) -> ArtifactProducerIdentity:
        self.calls.append(descriptor.agent_id)
        return self.producer


def _request(intent: str, request_id: str = "request-1") -> MatchRequest:
    return MatchRequest(
        request_id=request_id,
        intent=intent,
        caller=CallerContext(subject_id="caller-1", tenant_id="tenant-1"),
        requested_at=NOW,
    )


def _artifact_catalog() -> tuple[
    AgentCatalogAsset,
    ArtifactProducerIdentity,
]:
    assets = load_consumer_assets()
    agents = []
    producer: ArtifactProducerIdentity | None = None
    for agent in assets.catalog.agents:
        if agent.agent_id != "consumer-demand-forecasting":
            agents.append(agent)
            continue
        identity = "identity-demand"
        capabilities = tuple(
            capability.model_copy(update={"artifact_kinds": (ArtifactKind.DEMAND_SCENARIO,)})
            for capability in agent.capabilities
        )
        updated = agent.model_copy(
            update={
                "capabilities": capabilities,
                "trust": agent.trust.model_copy(update={"identity_id": identity}),
            }
        )
        agents.append(updated)
        producer = ArtifactProducerIdentity(
            agent_id=updated.agent_id,
            agent_version=updated.version.version,
            identity_id=identity,
            framework=updated.implementation.framework,
        )
    assert producer is not None
    return assets.catalog.model_copy(update={"agents": tuple(agents)}), producer


def _engine(
    *,
    catalog: AgentCatalogAsset | None = None,
    dispatcher: FakeDispatcher | None = None,
    artifact_store: CompanionArtifactStore | None = None,
    artifact_producer_resolver: LiveProducerResolver | None = None,
    conflict_detector: ConflictDetector | None = None,
    eligibility_policy: EligibilityPolicy | None = None,
    planner_policy: PlannerPolicy | None = None,
) -> tuple[OrchestrationEngine, FakeDispatcher]:
    assets = load_consumer_assets()
    selected_catalog = catalog or assets.catalog
    fake = dispatcher or FakeDispatcher()
    matcher = MatchEngine(
        selected_catalog,
        semantic_mode="offline",
        settings=Settings(_env_file=None),
    )
    planner = ExecutionPlanner(
        selected_catalog,
        policy=planner_policy,
        clock=lambda: NOW,
        plan_id_factory=lambda: "plan-fixed",
    )
    invocation_ids = iter(f"inv-{index}" for index in range(1, 20))
    executor = DagExecutor(
        selected_catalog,
        fake,
        GroundedSynthesizer(EvidenceResolver("https://agent-iq.example.test", clock=lambda: NOW)),
        eligibility_policy=eligibility_policy,
        artifact_broker=artifact_store,
        artifact_producer_resolver=artifact_producer_resolver,
        conflict_detector=conflict_detector,
        clock=lambda: NOW,
        execution_id_factory=lambda: "exec-fixed",
        invocation_id_factory=lambda: next(invocation_ids),
    )
    return OrchestrationEngine(matcher, planner, executor), fake


def test_independent_steps_run_concurrently_with_typed_artifact_and_minimum_prompts() -> None:
    catalog, producer = _artifact_catalog()
    handles = iter(("ath-1", "ath-2"))
    artifact_store = CompanionArtifactStore(
        clock=lambda: NOW,
        handle_factory=lambda: next(handles),
    )
    dispatcher = FakeDispatcher(
        artifact_store=artifact_store,
        producers={"consumer-demand-forecasting": producer},
    )
    engine, fake = _engine(
        catalog=catalog,
        dispatcher=dispatcher,
        artifact_store=artifact_store,
    )
    request = _request(
        "Improve forecast accuracy with demand sensing and raise OEE on the bottling line"
    )

    result = asyncio.run(
        engine.orchestrate(
            request,
            context_candidates=(
                ContextCandidate(
                    context=ContextInput(
                        context_id="demand-context",
                        kind=ContextKind.WORK_IQ,
                        source_uri="https://example.test/demand",
                        approved=True,
                    ),
                    allowed_agent_ids=("consumer-demand-forecasting",),
                ),
            ),
        )
    )

    assert result.status is ExecutionStatus.COMPLETED
    assert [step.agent_id for step in result.steps] == [
        "consumer-demand-forecasting",
        "consumer-manufacturing-operations",
    ]
    assert fake.max_active == 2
    assert len(result.steps[0].artifacts) == 1
    assert result.steps[0].artifacts[0].artifact.kind is ArtifactKind.DEMAND_SCENARIO
    assert result.steps[1].artifacts == ()
    assert "OEE" not in fake.prompts["consumer-demand-forecasting"]
    assert "demand-context (work_iq)" in fake.prompts["consumer-demand-forecasting"]
    assert "demand-context" not in fake.prompts["consumer-manufacturing-operations"]
    assert "forecast" not in fake.prompts["consumer-manufacturing-operations"].casefold()
    assert len(result.synthesis.evidence.citations) == 2


def test_artifact_grant_uses_live_foundry_identity_instead_of_authored_trust() -> None:
    catalog, static_producer = _artifact_catalog()
    live_producer = static_producer.model_copy(
        update={
            "agent_version": "7",
            "identity_id": "live-foundry-principal",
        }
    )
    catalog = catalog.model_copy(
        update={
            "agents": tuple(
                agent.model_copy(
                    update={"trust": agent.trust.model_copy(update={"identity_id": None})}
                )
                if agent.agent_id == live_producer.agent_id
                else agent
                for agent in catalog.agents
            )
        }
    )
    artifact_store = CompanionArtifactStore(clock=lambda: NOW)
    resolver = LiveProducerResolver(live_producer)
    dispatcher = FakeDispatcher(
        artifact_store=artifact_store,
        producers={live_producer.agent_id: live_producer},
    )
    engine, _ = _engine(
        catalog=catalog,
        dispatcher=dispatcher,
        artifact_store=artifact_store,
        artifact_producer_resolver=resolver,
    )

    result = asyncio.run(
        engine.orchestrate(_request("Improve forecast accuracy with demand sensing"))
    )

    assert result.status is ExecutionStatus.COMPLETED
    assert resolver.calls == [live_producer.agent_id]
    assert result.steps[0].artifacts[0].producer == live_producer


def test_dependent_step_waits_for_and_receives_only_successful_prerequisite() -> None:
    engine, fake = _engine()
    request = _request(
        "Improve forecast accuracy with demand sensing, then raise OEE on the bottling line"
    )

    result = asyncio.run(engine.orchestrate(request))

    assert result.status is ExecutionStatus.COMPLETED
    assert fake.max_active == 1
    assert fake.timeline.index(("end", "consumer-demand-forecasting")) < fake.timeline.index(
        ("start", "consumer-manufacturing-operations")
    )
    manufacturing_prompt = fake.prompts["consumer-manufacturing-operations"]
    assert "Required prior-step results:" in manufacturing_prompt
    assert "consumer-demand-forecasting grounded finding" in manufacturing_prompt
    assert "Keep the answer under 140 words" in manufacturing_prompt
    assert "Preserve every [ref_id:n] marker used" in manufacturing_prompt


def test_dependency_failure_skips_downstream_work_without_erasing_failure() -> None:
    fake = FakeDispatcher(behaviors={"consumer-demand-forecasting": "failed"})
    engine, _ = _engine(dispatcher=fake)
    request = _request(
        "Improve forecast accuracy with demand sensing, then raise OEE on the bottling line"
    )

    result = asyncio.run(engine.orchestrate(request))

    assert result.status is ExecutionStatus.FAILED
    assert [step.status for step in result.steps] == [
        StepExecutionStatus.FAILED,
        StepExecutionStatus.SKIPPED,
    ]
    assert fake.calls == ["consumer-demand-forecasting"]
    assert result.steps[1].error is not None
    assert result.steps[1].error.code is ErrorCode.DEPENDENCY_FAILED


@pytest.mark.parametrize(
    ("behavior", "expected"),
    (
        ("failed", StepExecutionStatus.FAILED),
        ("timed_out", StepExecutionStatus.TIMED_OUT),
    ),
)
def test_independent_failure_or_timeout_returns_grounded_partial_result(
    behavior: str,
    expected: StepExecutionStatus,
) -> None:
    fake = FakeDispatcher(behaviors={"consumer-manufacturing-operations": behavior})
    engine, _ = _engine(dispatcher=fake)
    request = _request(
        "Improve forecast accuracy with demand sensing and raise OEE on the bottling line"
    )

    result = asyncio.run(engine.orchestrate(request))

    assert result.status is ExecutionStatus.PARTIAL
    assert [step.status for step in result.steps] == [
        StepExecutionStatus.SUCCEEDED,
        expected,
    ]
    assert len(result.synthesis.evidence.records) == 1
    assert "Gaps and partial results" in result.synthesis.answer


def test_missing_evidence_declines_instead_of_exposing_ungrounded_answer() -> None:
    fake = FakeDispatcher(behaviors={"consumer-formulation-rd": "no_evidence"})
    engine, _ = _engine(dispatcher=fake)

    result = asyncio.run(
        engine.orchestrate(
            _request("Which enzymes drive BrightWash's cold-water cleaning performance?")
        )
    )

    assert result.status is ExecutionStatus.DECLINED
    assert result.steps[0].status is StepExecutionStatus.DECLINED
    assert result.steps[0].answer is None
    assert result.synthesis.evidence.records == ()


def test_user_cancellation_stops_active_invocation_and_propagates_status() -> None:
    cancel_event = asyncio.Event()
    fake = FakeDispatcher(behaviors={"consumer-formulation-rd": "slow"})
    engine, _ = _engine(dispatcher=fake)

    async def invoke() -> ExecutionSummary:
        task = asyncio.create_task(
            engine.orchestrate(
                _request("Which enzymes drive BrightWash's cold-water cleaning performance?"),
                cancel_event=cancel_event,
            )
        )
        await asyncio.sleep(0.02)
        cancel_event.set()
        return await task

    result = asyncio.run(invoke())
    assert result.status is ExecutionStatus.CANCELLED
    assert result.steps[0].status is StepExecutionStatus.CANCELLED
    assert fake.active == 0


def test_pre_cancelled_execution_never_invokes_an_agent() -> None:
    cancel_event = asyncio.Event()
    cancel_event.set()
    engine, fake = _engine()

    result = asyncio.run(
        engine.orchestrate(
            _request("Which enzymes drive BrightWash's cold-water cleaning performance?"),
            cancel_event=cancel_event,
        )
    )

    assert result.status is ExecutionStatus.CANCELLED
    assert fake.calls == []


def test_conflict_detector_marks_grounded_result_partial_and_rejects_unknown_steps() -> None:
    request = _request(
        "Improve forecast accuracy with demand sensing and raise OEE on the bottling line"
    )
    engine, _ = _engine(conflict_detector=MarkAllConflicting())
    result = asyncio.run(engine.orchestrate(request))

    assert result.status is ExecutionStatus.PARTIAL
    assert len(result.synthesis.conflict_claim_ids) == 2
    assert "Conflicts" in result.synthesis.answer

    invalid, _ = _engine(conflict_detector=UnknownConflict())
    with pytest.raises(ValueError, match="unknown step"):
        asyncio.run(invalid.orchestrate(request))


def test_pre_invocation_reauthorization_and_native_identity_mismatch_fail_closed() -> None:
    denied, denied_fake = _engine(eligibility_policy=DenyAtInvocation())
    denied_result = asyncio.run(
        denied.orchestrate(
            _request("Which enzymes drive BrightWash's cold-water cleaning performance?")
        )
    )
    assert denied_result.status is ExecutionStatus.FAILED
    assert denied_result.steps[0].error is not None
    assert denied_result.steps[0].error.code is ErrorCode.AGENT_INELIGIBLE
    assert denied_fake.calls == []

    wrong_fake = FakeDispatcher(behaviors={"consumer-demand-forecasting": "wrong_identity"})
    wrong, _ = _engine(dispatcher=wrong_fake)
    wrong_result = asyncio.run(
        wrong.orchestrate(
            _request("Improve forecast accuracy using demand sensing across POS signals")
        )
    )
    assert wrong_result.status is ExecutionStatus.FAILED
    assert wrong_result.steps[0].error is not None
    assert wrong_result.steps[0].error.code is ErrorCode.INVOCATION_FAILED

    correlation_fake = FakeDispatcher(
        behaviors={"consumer-demand-forecasting": "wrong_correlation"}
    )
    correlation, _ = _engine(dispatcher=correlation_fake)
    correlation_result = asyncio.run(
        correlation.orchestrate(
            _request("Improve forecast accuracy using demand sensing across POS signals")
        )
    )
    assert correlation_result.steps[0].error is not None
    assert correlation_result.steps[0].error.code is ErrorCode.INVOCATION_FAILED


def test_artifact_capability_requires_broker_and_trusted_identity() -> None:
    catalog, _ = _artifact_catalog()
    no_broker, no_broker_fake = _engine(catalog=catalog)
    result = asyncio.run(
        no_broker.orchestrate(
            _request("Improve forecast accuracy using demand sensing across POS signals")
        )
    )
    assert result.steps[0].error is not None
    assert result.steps[0].error.code is ErrorCode.ARTIFACT_UNAVAILABLE
    assert no_broker_fake.calls == []

    demand = next(
        agent for agent in catalog.agents if agent.agent_id == "consumer-demand-forecasting"
    )
    missing_identity = demand.model_copy(
        update={"trust": demand.trust.model_copy(update={"identity_id": None})}
    )
    no_identity_catalog = catalog.model_copy(
        update={
            "agents": tuple(
                missing_identity if agent.agent_id == demand.agent_id else agent
                for agent in catalog.agents
            )
        }
    )
    store = CompanionArtifactStore(clock=lambda: NOW)
    no_identity, no_identity_fake = _engine(
        catalog=no_identity_catalog,
        artifact_store=store,
    )
    result = asyncio.run(
        no_identity.orchestrate(
            _request("Improve forecast accuracy using demand sensing across POS signals")
        )
    )
    assert result.steps[0].error is not None
    assert result.steps[0].error.code is ErrorCode.AGENT_INELIGIBLE
    assert no_identity_fake.calls == []

    _, producer = _artifact_catalog()
    conflict_store = CompanionArtifactStore(clock=lambda: NOW)
    conflict_store.authorize_invocation(
        invocation_id="inv-1",
        correlation_id="another-execution",
        caller=_request("Intent.").caller,
        producer=producer,
        allowed_kinds=(ArtifactKind.DEMAND_SCENARIO,),
    )
    conflict, conflict_fake = _engine(
        catalog=catalog,
        artifact_store=conflict_store,
    )
    result = asyncio.run(
        conflict.orchestrate(
            _request("Improve forecast accuracy using demand sensing across POS signals")
        )
    )
    assert result.steps[0].error is not None
    assert result.steps[0].error.code is ErrorCode.ARTIFACT_CONFLICT
    assert conflict_fake.calls == []


def test_overall_deadline_cancels_unfinished_native_work() -> None:
    fake = FakeDispatcher(behaviors={"consumer-formulation-rd": "slow"})
    engine, _ = _engine(
        dispatcher=fake,
        planner_policy=PlannerPolicy(overall_deadline_seconds=1),
    )

    result = asyncio.run(
        engine.orchestrate(
            _request("Which enzymes drive BrightWash's cold-water cleaning performance?")
        )
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.steps[0].status is StepExecutionStatus.TIMED_OUT
    assert result.steps[0].error is not None
    assert result.steps[0].error.code is ErrorCode.INVOCATION_TIMEOUT
    assert fake.active == 0
