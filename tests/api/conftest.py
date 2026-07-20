from __future__ import annotations

import asyncio
import itertools
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from agent_iq.api import AgentIqService
from agent_iq.catalog import load_consumer_assets
from agent_iq.config import Settings
from agent_iq.contracts import (
    CallerContext,
    ErrorCode,
    EvidenceEnvelope,
    EvidenceFetchRequest,
    EvidenceLocator,
    EvidenceRecord,
    MatchRequest,
    OrchestrationCommand,
)
from agent_iq.errors import operation_error
from agent_iq.evidence import CompanionEvidenceStore, EvidenceResolver
from agent_iq.match import MatchEngine
from agent_iq.orchestrate import (
    DagExecutor,
    ExecutionPlanner,
    GroundedSynthesizer,
    OrchestrationEngine,
)
from agent_iq.runtime import NativeA2ARequest, NativeA2AResult

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
CONSUMER_DOCUMENTS = {
    document.document_id: document for document in load_consumer_assets().documents
}
VACATION_DOCUMENT = CONSUMER_DOCUMENTS["employee-vacation-balance-2026"]
VPN_DOCUMENT = CONSUMER_DOCUMENTS["employee-vpn-recovery-guidance"]
IDENTITY_DOCUMENT = CONSUMER_DOCUMENTS["employee-identity-security-guide"]
EXPENSE_DOCUMENT = CONSUMER_DOCUMENTS["employee-client-dinner-expense"]


class ApiFakeDispatcher:
    def __init__(
        self,
        *,
        delay_seconds: float = 0.01,
        failed_agent_id: str | None = None,
        malformed_vpn_payload: bool = False,
        extra_employee_evidence: bool = False,
    ) -> None:
        handles = (f"evh-{index}" for index in itertools.count(1))
        self._evidence = CompanionEvidenceStore(
            clock=lambda: NOW,
            handle_factory=lambda: next(handles),
        )
        self.delay_seconds = delay_seconds
        self.failed_agent_id = failed_agent_id
        self.malformed_vpn_payload = malformed_vpn_payload
        self.extra_employee_evidence = extra_employee_evidence

    async def dispatch(
        self,
        request: NativeA2ARequest,
        *,
        caller: CallerContext,
        cancel_event: asyncio.Event | None = None,
    ) -> NativeA2AResult:
        if request.invocation_id is None:
            raise RuntimeError("The test dispatcher requires an invocation ID.")
        if cancel_event is not None:
            try:
                await asyncio.wait_for(cancel_event.wait(), timeout=self.delay_seconds)
            except TimeoutError:
                pass
            else:
                raise operation_error(
                    ErrorCode.CANCELLED,
                    "The test invocation was cancelled.",
                    correlation_id=request.correlation_id,
                )
        else:
            await asyncio.sleep(self.delay_seconds)
        if request.agent_id == self.failed_agent_id:
            raise operation_error(
                ErrorCode.INVOCATION_FAILED,
                "The specialist is temporarily unavailable.",
                correlation_id=request.correlation_id,
            )

        vacation = request.agent_id == "employee-vacation-leave"
        vpn = request.agent_id == "employee-it-helpdesk"
        identity = request.agent_id == "employee-identity-security"
        expense = request.agent_id == "employee-expense"
        formulation = request.agent_id == "consumer-formulation-rd"
        comparison = formulation and "clearwave" in request.prompt.casefold()
        if vacation:
            source_id = VACATION_DOCUMENT.document_id
            title = VACATION_DOCUMENT.title
            excerpt = VACATION_DOCUMENT.content
        elif vpn:
            source_id = VPN_DOCUMENT.document_id
            title = VPN_DOCUMENT.title
            excerpt = VPN_DOCUMENT.content
            if self.malformed_vpn_payload:
                excerpt = excerpt.replace("VPN_RECOVERY_JSON_END", "", 1)
        elif identity:
            source_id = IDENTITY_DOCUMENT.document_id
            title = IDENTITY_DOCUMENT.title
            excerpt = IDENTITY_DOCUMENT.content
        elif expense:
            source_id = EXPENSE_DOCUMENT.document_id
            title = EXPENSE_DOCUMENT.title
            excerpt = EXPENSE_DOCUMENT.content
        elif formulation:
            source_id = "consumer-brightwash-cold"
            title = "BrightWash Cold active system"
            excerpt = "The package uses protease, amylase, mannanase, and lipase."
        else:
            source_id = f"source-{request.agent_id}"
            title = f"Source for {request.agent_id}"
            excerpt = f"Grounding for {request.agent_id}."
        content_hash_character = {
            "employee-it-helpdesk": "c",
            "employee-expense": "d",
        }.get(request.agent_id, "a")
        records = [
            EvidenceRecord(
                evidence_id=f"evidence-{request.invocation_id}-0",
                invocation_id=request.invocation_id,
                agent_id=request.agent_id,
                source_id=source_id,
                title=title,
                locator=EvidenceLocator(document_key=source_id),
                excerpt=excerpt,
                local_reference_id="0",
                knowledge_source=f"kb-{request.agent_id}",
                retrieved_at=NOW,
                content_hash=f"sha256:{content_hash_character * 64}",
            )
        ]
        if comparison:
            records.append(
                EvidenceRecord(
                    evidence_id=f"evidence-{request.invocation_id}-1",
                    invocation_id=request.invocation_id,
                    agent_id=request.agent_id,
                    source_id="consumer-clearwave-cold",
                    title="ClearWave Cold Clean active system",
                    locator=EvidenceLocator(document_key="consumer-clearwave-cold"),
                    excerpt=(
                        "ClearWave uses protease, amylase, mannanase, lipase, and pectate lyase."
                    ),
                    local_reference_id="1",
                    knowledge_source=f"kb-{request.agent_id}",
                    retrieved_at=NOW,
                    content_hash=f"sha256:{'b' * 64}",
                )
            )
        if self.extra_employee_evidence and (vacation or vpn):
            records.insert(
                0,
                EvidenceRecord(
                    evidence_id=f"evidence-{request.invocation_id}-extra",
                    invocation_id=request.invocation_id,
                    agent_id=request.agent_id,
                    source_id=(
                        "employee-vacation-policy-context"
                        if vacation
                        else "employee-remote-access-policy-context"
                    ),
                    title=(
                        "Vacation policy context" if vacation else "Remote access policy context"
                    ),
                    locator=EvidenceLocator(document_key="employee-policy-context"),
                    excerpt="Additional authenticated policy context.",
                    local_reference_id="1",
                    knowledge_source=f"kb-{request.agent_id}",
                    retrieved_at=NOW,
                    content_hash=f"sha256:{'e' * 64}",
                ),
            )
        answer = (
            "You have 12 vacation days remaining [ref_id:0]."
            if vacation
            else (
                (
                    "Aggregate guidance indicates reconnecting the VPN once before escalating "
                    "[ref_id:0]."
                )
                if vpn
                else (
                    (
                        "Do not approve the prompt; review recent sign-ins and contact the "
                        "security team if the prompts continue [ref_id:0]."
                    )
                    if identity
                    else (
                        "The client dinner is ready for review before the AMEX cutoff [ref_id:0]."
                        if expense
                        else (
                            (
                                "BrightWash uses protease, amylase, mannanase, and lipase "
                                "[ref_id:0]. ClearWave adds pectate lyase to the same core "
                                "[ref_id:1]."
                            )
                            if comparison
                            else (
                                "BrightWash uses protease, amylase, mannanase, and "
                                "lipase [ref_id:0]."
                                if formulation
                                else f"{request.agent_id} grounded finding [ref_id:0]"
                            )
                        )
                    )
                )
            )
        )
        if self.extra_employee_evidence and (vacation or vpn):
            answer = f"{answer} Additional policy context [ref_id:1]."
        reference = self._evidence.publish(
            tuple(records),
            caller=caller,
            correlation_id=request.correlation_id or request.invocation_id,
        )
        return NativeA2AResult(
            invocation_id=request.invocation_id,
            agent_id=request.agent_id,
            agent_version="1.0.0",
            protocol_version="1.0",
            message_id=f"msg-{request.invocation_id}",
            answer=answer,
            duration_ms=10,
            attempt_count=1,
            correlation_id=request.correlation_id,
            evidence=reference,
        )

    def fetch_evidence(
        self,
        request: EvidenceFetchRequest,
        *,
        caller: CallerContext,
    ) -> EvidenceEnvelope:
        return self._evidence.fetch(request, caller=caller)


def build_test_service(
    *,
    delay_seconds: float = 0.01,
    failed_agent_id: str | None = None,
    malformed_vpn_payload: bool = False,
    extra_employee_evidence: bool = False,
) -> AgentIqService:
    assets = load_consumer_assets()
    dispatcher = ApiFakeDispatcher(
        delay_seconds=delay_seconds,
        failed_agent_id=failed_agent_id,
        malformed_vpn_payload=malformed_vpn_payload,
        extra_employee_evidence=extra_employee_evidence,
    )
    matcher = MatchEngine(
        assets.catalog,
        semantic_mode="offline",
        settings=Settings(_env_file=None),
    )
    plans = (f"plan-{index}" for index in itertools.count(1))
    invocations = (f"inv-{index}" for index in itertools.count(1))
    executions = (f"exec-{index}" for index in itertools.count(1))
    events = (f"evt-{index}" for index in itertools.count(1))
    planner = ExecutionPlanner(
        assets.catalog,
        clock=lambda: NOW,
        plan_id_factory=lambda: next(plans),
    )
    executor = DagExecutor(
        assets.catalog,
        dispatcher,
        GroundedSynthesizer(EvidenceResolver("https://agent-iq.example.test", clock=lambda: NOW)),
        clock=lambda: NOW,
        invocation_id_factory=lambda: next(invocations),
    )
    return AgentIqService(
        OrchestrationEngine(matcher, planner, executor),
        clock=lambda: NOW,
        execution_id_factory=lambda: next(executions),
        event_id_factory=lambda: next(events),
    )


@pytest.fixture
def service_factory() -> Callable[..., AgentIqService]:
    return build_test_service


@pytest.fixture
def orchestration_command() -> OrchestrationCommand:
    return OrchestrationCommand(
        request=MatchRequest(
            request_id="request-api-1",
            intent="Which enzymes improve BrightWash cold-water cleaning?",
            caller=CallerContext(subject_id="caller-1", tenant_id="tenant-1"),
            requested_at=NOW,
        )
    )
