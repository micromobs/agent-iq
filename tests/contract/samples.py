"""Representative valid Agent IQ contract instances."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_iq.contracts import (
    AgentDescriptor,
    AgentImplementation,
    AgentIqError,
    AgentTask,
    AgentVersion,
    CallbackMode,
    CallbackTarget,
    CallerContext,
    CandidateScore,
    CatalogAuthority,
    CatalogKind,
    CatalogSource,
    Citation,
    Claim,
    ContextInput,
    ContextKind,
    EligibilityDecision,
    ErrorCode,
    EventEnvelope,
    EvidenceBundle,
    EvidenceLocator,
    EvidenceRecord,
    ExecutionPlan,
    ExpectedOutput,
    HealthRecord,
    HealthStatus,
    InteractionMode,
    InvocationCapability,
    MatchRequest,
    PlanStep,
    ProviderDescriptor,
    RiskLevel,
    ScoreComponent,
    SelectionDecision,
    SelectionReason,
    SkillDescriptor,
    TaskCheckpoint,
    TaskMode,
    TaskStatus,
    TransportProtocol,
    TrustMetadata,
    TrustState,
)
from agent_iq.contracts.events import InvocationStartedPayload

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)
DIGEST = "sha256:" + ("a" * 64)


def agent() -> AgentDescriptor:
    return AgentDescriptor(
        agent_id="consumer-formulation-rd",
        name="Fabrikam Consumer Products (synthetic demo) Formulation R&D",
        description=(
            "Grounded formulation and ingredient science for "
            "Fabrikam Consumer Products (synthetic demo) products."
        ),
        provider=ProviderDescriptor(name="Fabrikam Consumer Products (synthetic demo)"),
        version=AgentVersion(version="1.0.0", card_hash=DIGEST, published_at=NOW),
        skills=(
            SkillDescriptor(
                skill_id="formulation",
                name="Formulation science",
                description="Answer formulation and ingredient questions.",
                tags=("BrightWash", "enzymes"),
                examples=("Which enzymes power BrightWash cold-water cleaning?",),
            ),
        ),
        capabilities=(
            InvocationCapability(
                protocol=TransportProtocol.A2A,
                protocol_version="1.0.0",
                endpoint="https://example.invalid/agents/consumer-formulation-rd/a2a",
            ),
        ),
        source=CatalogSource(
            source_id="foundry-canary",
            kind=CatalogKind.FOUNDRY,
            authority=CatalogAuthority.PROJECTION,
            source_agent_id="consumer-formulation-rd",
            source_uri="https://example.invalid/agents/consumer-formulation-rd",
            fetched_at=NOW,
            expires_at=LATER,
        ),
        trust=TrustMetadata(
            owner_ids=("owner-1",),
            state=TrustState.VERIFIED,
            admin_approved=True,
            signed=True,
            risk_level=RiskLevel.LOW,
            policy_version="1",
        ),
        health=HealthRecord(
            status=HealthStatus.HEALTHY,
            checked_at=NOW,
            expires_at=LATER,
        ),
        implementation=AgentImplementation(
            framework="LangGraph",
            hosting_platform="Microsoft Foundry",
        ),
        tags=("Fabrikam Consumer Products (synthetic demo)", "formulation"),
        knowledge_binding_ids=("consumer-product-innovation-kb",),
    )


def match_request() -> MatchRequest:
    return MatchRequest(
        request_id="request-1",
        intent="Which enzymes power BrightWash cold-water cleaning?",
        caller=CallerContext(subject_id="user-1", tenant_id="tenant-1"),
        requested_at=NOW,
    )


def candidate(*, eligible: bool = True) -> CandidateScore:
    return CandidateScore(
        agent_id="consumer-formulation-rd",
        rank=1,
        fused_score=0.032,
        components=(
            ScoreComponent(
                name="lexical",
                rank=1,
                score=4.2,
                matched_fields=("skills.tags",),
            ),
        ),
        eligibility=EligibilityDecision(
            eligible=eligible,
            policy_facts=("admin approved",) if eligible else (),
            blocked_reasons=() if eligible else ("agent blocked",),
            policy_version="1",
        ),
        matched_fields=("skills.tags",),
        explanation="Matched BrightWash and enzyme expertise on an eligible agent card.",
        catalog_version="catalog-1",
    )


def selection() -> SelectionDecision:
    return SelectionDecision(
        request_id="request-1",
        candidates=(candidate(),),
        selected=(
            SelectionReason(
                agent_id="consumer-formulation-rd",
                work_item_id="work-1",
                work_item="Answer the formulation question.",
                summary="Answer the formulation question.",
                matched_skills=("formulation",),
                matched_tags=("BrightWash", "enzymes"),
                fused_score=0.032,
                policy_facts=("admin approved",),
            ),
        ),
        selector="deterministic-v1",
        created_at=NOW,
    )


def plan() -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="plan-1",
        request_id="request-1",
        objective="Answer the user's grounded formulation question.",
        public_summary="Ask the formulation agent and cite its evidence.",
        steps=(
            PlanStep(
                step_id="step-1",
                agent_id="consumer-formulation-rd",
                work_item="Find the relevant formulation evidence.",
                context_inputs=(
                    ContextInput(
                        context_id="context-1",
                        kind=ContextKind.FOUNDRY_IQ,
                        approved=True,
                    ),
                ),
                expected_outputs=(
                    ExpectedOutput(
                        output_id="answer",
                        description="A grounded answer with evidence.",
                    ),
                ),
                invocation_mode=InteractionMode.SYNC,
            ),
        ),
        created_at=NOW,
    )


def evidence() -> EvidenceBundle:
    return EvidenceBundle(
        execution_id="execution-1",
        records=(
            EvidenceRecord(
                evidence_id="evidence-1",
                invocation_id="invocation-1",
                agent_id="consumer-formulation-rd",
                source_id="consumer-brightwash-cold",
                title="BrightWash Cold active system",
                locator=EvidenceLocator(
                    uri="https://example.invalid/source/consumer-brightwash-cold",
                    document_key="consumer-brightwash-cold",
                ),
                excerpt="The enzyme package includes protease, amylase, mannanase, and lipase.",
                knowledge_source="consumer-product-innovation-kb",
                retrieved_at=NOW,
                confidence=0.98,
                content_hash=DIGEST,
            ),
        ),
        claims=(
            Claim(
                claim_id="claim-1",
                text="BrightWash uses a multi-enzyme package.",
                evidence_ids=("evidence-1",),
                agent_ids=("consumer-formulation-rd",),
            ),
        ),
        citations=(
            Citation(
                number=1,
                evidence_id="evidence-1",
                label="BrightWash Cold active system",
                url="https://example.invalid/source/consumer-brightwash-cold",
            ),
        ),
    )


def task() -> AgentTask:
    return AgentTask(
        task_id="task-1",
        execution_id="execution-1",
        step_id="step-1",
        agent_id="consumer-formulation-rd",
        mode=TaskMode.ATTENDED,
        status=TaskStatus.RUNNING,
        created_at=NOW,
        updated_at=LATER,
        deadline_at=NOW + timedelta(hours=1),
        callback_targets=(CallbackTarget(mode=CallbackMode.POLL, destination="execution-1"),),
        checkpoints=(
            TaskCheckpoint(
                sequence=0,
                status=TaskStatus.RUNNING,
                summary="Invocation started.",
                recorded_at=NOW,
            ),
        ),
    )


def error() -> AgentIqError:
    return AgentIqError(
        error_id="error-1",
        code=ErrorCode.INVOCATION_TIMEOUT,
        message="The agent did not answer before its deadline.",
        retryable=True,
        retry_after_seconds=5,
        correlation_id="execution-1",
    )


def event() -> EventEnvelope:
    return EventEnvelope(
        event_id="event-1",
        execution_id="execution-1",
        sequence=3,
        occurred_at=NOW,
        trace_id="0123456789abcdef0123456789abcdef",
        payload=InvocationStartedPayload(
            invocation_id="invocation-1",
            step_id="step-1",
            agent_id="consumer-formulation-rd",
            transport=TransportProtocol.A2A,
            protocol_version="1.0.0",
        ),
    )
