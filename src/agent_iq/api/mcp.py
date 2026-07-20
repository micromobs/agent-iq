"""Cowork-facing MCP tools over the protocol-neutral Agent IQ service."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, assert_never
from urllib.parse import urlsplit
from uuid import uuid4

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from agent_iq.api.mcp_apps import (
    EMPLOYEE_OPERATIONS_MIME_TYPE,
    EMPLOYEE_OPERATIONS_RESOURCE_URI,
    FORMATION_MIME_TYPE,
    FORMATION_RESOURCE_URI,
    HISTORICAL_RESOURCE_TEMPLATE_URI,
    LEGACY_VACATION_RING_RESOURCE_URI,
    VACATION_RING_MIME_TYPE,
    VACATION_RING_RESOURCE_URI,
    employee_operations_widget_html,
    formation_widget_html,
    historical_widget_html,
    vacation_ring_widget_html,
)
from agent_iq.api.presentation import public_answer, public_text
from agent_iq.catalog import LiveDemoInventory
from agent_iq.contracts import (
    VACATION_BALANCE_JSON_END,
    VACATION_BALANCE_JSON_START,
    VPN_RECOVERY_JSON_END,
    VPN_RECOVERY_JSON_START,
    AgentCandidatePresentation,
    AgentSearchPresentation,
    CallerContext,
    CitationPresentation,
    ErrorCode,
    ExecutionIssuePresentation,
    ExecutionSnapshot,
    ExecutionSummary,
    FeedbackReceipt,
    FeedbackSubmission,
    MatchFilters,
    MatchRequest,
    OrchestrationCommand,
    OrchestrationPresentation,
    OrchestrationResult,
    ProductCapabilities,
    SelectedAgentPresentation,
    SynthesisIssueStatus,
    TransportProtocol,
    VpnRecoveryGuidance,
    VpnRecoveryPresentation,
    parse_vacation_balance,
    parse_vpn_recovery,
)
from agent_iq.contracts.events import (
    AgentsSelectedPayload,
    ApprovalRequestedPayload,
    ApprovalResolvedPayload,
    CandidatesRankedPayload,
    CatalogSearchedPayload,
    EventPayload,
    EvidenceReceivedPayload,
    ExecutionCompletedPayload,
    InvocationCompletedPayload,
    InvocationDeltaPayload,
    InvocationFailedPayload,
    InvocationStartedPayload,
    PlanPreparedPayload,
    RequestAcceptedPayload,
    SquadAuthorizedPayload,
    SquadOverriddenPayload,
    SquadProposedPayload,
    SynthesisCompletedPayload,
    TaskCompletedPayload,
    TaskHandedOffPayload,
)
from agent_iq.employee import (
    EmployeeActionPreparationRequest,
    EmployeeActionReceipt,
    EmployeeExperiencePresentation,
    EmployeeScenarioCatalogPresentation,
    EmployeeScenarioRequest,
    VacationBalancePresentation,
    build_vacation_experience,
    resolve_employee_request,
    resolve_employee_scenario,
    select_vacation_balance,
)
from agent_iq.employee import (
    list_employee_scenarios as list_employee_scenarios_presentation,
)
from agent_iq.employee import (
    prepare_employee_action as prepare_employee_action_receipt,
)
from agent_iq.errors import operation_error

from .service import PRODUCT_TOOLS, AgentIqService

EMPLOYEE_MCP_TOOLS = (
    "check_vacation_balance",
    "run_vacation_balance_demo",
    "list_employee_scenarios",
    "resolve_employee_intent",
    "open_employee_experience",
    "prepare_employee_action",
)
MCP_TOOLS = (*PRODUCT_TOOLS, *EMPLOYEE_MCP_TOOLS)
SINGLE_AGENT_DEMO_PROMPT = (
    "What four enzymes power BrightWash Cold, and which fifth enzyme differentiates "
    "ClearWave Cold Clean? Cite both product sources."
)
VACATION_BALANCE_PROMPT = "How much vacation do I have remaining this year?"
SingleAgentDemoPreflight = Callable[[], Awaitable[LiveDemoInventory]]

_READ_ONLY_CATALOG = ToolAnnotations(
    title="Preview an Agent IQ recommendation",
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_READ_ONLY_ORCHESTRATION = ToolAnnotations(
    title="Run a read-only Agent IQ analysis",
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
_VACATION_BALANCE = ToolAnnotations(
    title="Check vacation balance",
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
_READ_ONLY_EXECUTION = ToolAnnotations(
    title="View Agent IQ execution status",
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_CANCEL_EXECUTION = ToolAnnotations(
    title="Cancel an Agent IQ execution",
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=False,
)
_SUBMIT_FEEDBACK = ToolAnnotations(
    title="Submit Agent IQ feedback",
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_READ_ONLY_CAPABILITIES = ToolAnnotations(
    title="View Agent IQ capabilities",
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_READ_ONLY_EMPLOYEE_CATALOG = ToolAnnotations(
    title="Browse Employee Operations",
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_READ_ONLY_EMPLOYEE_EXPERIENCE = ToolAnnotations(
    title="Open an Employee Operations experience",
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_PREPARE_EMPLOYEE_ACTION = ToolAnnotations(
    title="Prepare an employee action without executing it",
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


@dataclass(frozen=True, slots=True)
class _ProgressUpdate:
    key: str
    progress: float
    message: str


@dataclass(slots=True)
class _ProgressState:
    emitted: set[str] = field(default_factory=set)
    progress: float = 0.0

    def accept(self, update: _ProgressUpdate | None) -> _ProgressUpdate | None:
        if update is None or update.key in self.emitted:
            return None
        self.emitted.add(update.key)
        self.progress = max(self.progress, update.progress)
        return _ProgressUpdate(update.key, self.progress, update.message)


def _formation_meta() -> dict[str, object]:
    return {
        "ui": {
            "resourceUri": FORMATION_RESOURCE_URI,
            "visibility": ["model", "app"],
        }
    }


def _formation_resource_meta() -> dict[str, object]:
    return {
        "ui": {
            "csp": {
                "connectDomains": [],
                "resourceDomains": [],
                "frameDomains": [],
                "baseUriDomains": [],
            },
            "prefersBorder": False,
        }
    }


def _employee_operations_meta() -> dict[str, object]:
    return {
        "ui": {
            "resourceUri": EMPLOYEE_OPERATIONS_RESOURCE_URI,
            "visibility": ["model", "app"],
        }
    }


def _employee_operations_resource_meta() -> dict[str, object]:
    return {
        "ui": {
            "csp": {
                "connectDomains": [],
                "resourceDomains": [],
                "frameDomains": [],
                "baseUriDomains": [],
            },
            "prefersBorder": False,
        }
    }


def _vacation_ring_meta() -> dict[str, object]:
    return {
        "ui": {
            "resourceUri": VACATION_RING_RESOURCE_URI,
            "visibility": ["model", "app"],
        }
    }


def _vacation_ring_resource_meta() -> dict[str, object]:
    return _employee_operations_resource_meta()


def create_mcp_server(
    service: AgentIqService,
    *,
    public_base_url: str | None = None,
    deployment_probe_base_url: str | None = None,
    single_agent_demo_service: AgentIqService | None = None,
    single_agent_demo_preflight: SingleAgentDemoPreflight | None = None,
    vacation_demo_service: AgentIqService | None = None,
    vacation_demo_preflight: SingleAgentDemoPreflight | None = None,
) -> FastMCP[None]:
    """Expose bounded Agent IQ orchestration and employee-showcase operations."""

    transport_security = None
    if public_base_url is not None:
        public_url = urlsplit(public_base_url)
        if not public_url.scheme or not public_url.netloc:
            raise ValueError("public_base_url must be an absolute URL.")
        allowed_urls = [public_url]
        if deployment_probe_base_url is not None:
            probe_url = urlsplit(deployment_probe_base_url)
            if (
                probe_url.scheme != "https"
                or not probe_url.netloc
                or probe_url.path not in {"", "/"}
                or probe_url.query
                or probe_url.fragment
            ):
                raise ValueError("deployment_probe_base_url must be an absolute HTTPS origin.")
            allowed_urls.append(probe_url)
        transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[
                "127.0.0.1:*",
                "localhost:*",
                "[::1]:*",
                *(url.netloc for url in allowed_urls),
            ],
            allowed_origins=[
                "http://127.0.0.1:*",
                "http://localhost:*",
                "http://[::1]:*",
                *(f"{url.scheme}://{url.netloc}" for url in allowed_urls),
            ],
        )

    mcp: FastMCP[None] = FastMCP(
        name="agent-iq",
        instructions=(
            "Discover and orchestrate eligible A2A agents. Preserve selected-agent "
            "reasons, lifecycle status, grounded citations, and explicit failures. "
            "When the user asks to run or show a focused single-agent analysis, call "
            "run_single_agent_demo exactly once; it performs real catalog discovery, "
            "selects exactly one specialist, and invokes that hosted Foundry agent over "
            "native A2A. "
            "For a clearly personal remaining-vacation request, call "
            "check_vacation_balance exactly once; it selects the Vacation & Leave Agent "
            "and renders one compact cited vacation ring. If the audience is ambiguous, ask "
            "whether the request is for the user or their team before calling a tool. For a "
            "manager request, clarify the authorized team and region before using orchestrate. "
            "For a VPN or remote-access request, call orchestrate exactly once; Agent IQ can "
            "select one eligible IT specialist, validate one aggregate guidance source, and "
            "render a compact read-only recovery flow. "
            "For other employee-service requests, resolve the matching Employee Operations "
            "scenario and keep every business action prepared, "
            "review-required, or not-executed."
        ),
        streamable_http_path="/mcp",
        json_response=False,
        stateless_http=True,
        transport_security=transport_security,
    )

    @mcp.resource(
        FORMATION_RESOURCE_URI,
        name="agent-iq-formation",
        title="Agent IQ formation",
        description="Accessible specialist formation, scope, and cited-result view.",
        mime_type=FORMATION_MIME_TYPE,
        meta=_formation_resource_meta(),
    )
    async def agent_iq_formation() -> str:
        return formation_widget_html()

    @mcp.resource(
        VACATION_RING_RESOURCE_URI,
        name="agent-iq-vacation-ring",
        title="Agent IQ vacation balance",
        description=(
            "Compact Apple-Watch-style vacation ring with one selected agent and one source."
        ),
        mime_type=VACATION_RING_MIME_TYPE,
        meta=_vacation_ring_resource_meta(),
    )
    async def agent_iq_vacation_ring() -> str:
        return vacation_ring_widget_html()

    @mcp.resource(
        LEGACY_VACATION_RING_RESOURCE_URI,
        name="agent-iq-vacation-ring-legacy",
        title="Agent IQ vacation balance",
        description="Compatibility resource for previously saved vacation conversations.",
        mime_type=VACATION_RING_MIME_TYPE,
        meta=_vacation_ring_resource_meta(),
    )
    async def agent_iq_legacy_vacation_ring() -> str:
        return vacation_ring_widget_html()

    @mcp.resource(
        EMPLOYEE_OPERATIONS_RESOURCE_URI,
        name="agent-iq-employee-operations",
        title="Agent IQ Employee Operations",
        description=(
            "Schema-driven employee-services command center with 30 workflows, "
            "visible specialist work, rich UI, sources, and safe actions."
        ),
        mime_type=EMPLOYEE_OPERATIONS_MIME_TYPE,
        meta=_employee_operations_resource_meta(),
    )
    async def agent_iq_employee_operations() -> str:
        return employee_operations_widget_html()

    @mcp.resource(
        HISTORICAL_RESOURCE_TEMPLATE_URI,
        name="agent-iq-historical-result",
        title="Historical Agent IQ result",
        description="Safe compatibility view for a retired saved-conversation resource.",
        mime_type=FORMATION_MIME_TYPE,
        meta=_formation_resource_meta(),
    )
    async def agent_iq_historical_result(legacy_widget: str) -> str:
        del legacy_widget
        return historical_widget_html()

    @mcp.tool(
        name="search_agents",
        title="Preview an Agent IQ recommendation",
        description="Rank eligible catalog agents for an intent without invoking them.",
        annotations=_READ_ONLY_CATALOG,
        meta=_formation_meta(),
        structured_output=True,
    )
    async def search_agents(command: OrchestrationCommand) -> AgentSearchPresentation:
        result = service.search_agents(command)
        return AgentSearchPresentation(
            request_id=result.request_id,
            total_candidates=len(result.candidates),
            candidates=tuple(
                AgentCandidatePresentation(
                    agent_id=candidate.agent_id,
                    display_name=public_text(service.agent_name(candidate.agent_id)),
                    rank=candidate.rank,
                    eligible=candidate.eligibility.eligible,
                    reason=public_text(candidate.explanation),
                    matched_fields=tuple(public_text(field) for field in candidate.matched_fields),
                )
                for candidate in result.candidates[:5]
            ),
        )

    @mcp.tool(
        name="orchestrate",
        title="Run a read-only Agent IQ analysis",
        description=(
            "Select and invoke the minimum trusted A2A squad, then return one grounded "
            "result. This can call external specialists but cannot change business systems."
        ),
        annotations=_READ_ONLY_ORCHESTRATION,
        meta=_formation_meta(),
        structured_output=True,
    )
    async def orchestrate(
        command: OrchestrationCommand,
        ctx: Context[Any, Any, Any],
    ) -> OrchestrationPresentation:
        return await _run_orchestration(service, command, ctx)

    @mcp.tool(
        name="run_single_agent_demo",
        title="Show the live formulation answer",
        description=(
            "Use this for a focused one-agent formulation analysis. One call verifies the Foundry "
            "inventory, performs real Agent IQ catalog discovery across at least 20 active A2A "
            "candidates, selects exactly one specialist, invokes it through native A2A, and "
            "returns an evidence-backed answer with cited source excerpts for the rich response "
            "card. The "
            "optional question defaults to the proven BrightWash-versus-ClearWave enzyme prompt; "
            "do not call search_agents or orchestrate before or after this tool."
        ),
        annotations=_READ_ONLY_ORCHESTRATION,
        meta=_formation_meta(),
        structured_output=True,
    )
    async def run_single_agent_demo(
        ctx: Context[Any, Any, Any],
        question: str = SINGLE_AGENT_DEMO_PROMPT,
    ) -> OrchestrationPresentation:
        if single_agent_demo_preflight is not None:
            await single_agent_demo_preflight()
        intent = question.strip() or SINGLE_AGENT_DEMO_PROMPT
        command = OrchestrationCommand(
            request=MatchRequest(
                request_id=f"cowork-single-agent-{uuid4().hex}",
                intent=intent,
                caller=CallerContext(
                    subject_id="cowork-single-agent-analysis",
                    tenant_id="agent-iq-cowork",
                ),
                filters=MatchFilters(
                    max_candidates=23,
                    max_selected=1,
                    required_protocols=(TransportProtocol.A2A,),
                ),
                requested_at=datetime.now(UTC),
            )
        )
        demo_service = single_agent_demo_service or service
        result = await _execute_orchestration(
            demo_service,
            command,
            ctx,
        )
        return _present_orchestration(result, demo_service)

    async def _check_vacation_balance(
        ctx: Context[Any, Any, Any],
        question: str = VACATION_BALANCE_PROMPT,
    ) -> VacationBalancePresentation:
        if vacation_demo_preflight is None:
            raise operation_error(
                ErrorCode.CATALOG_UNAVAILABLE,
                "The vacation inventory preflight is not configured.",
            )
        await vacation_demo_preflight()
        command = OrchestrationCommand(
            request=MatchRequest(
                request_id=f"cowork-vacation-{uuid4().hex}",
                intent=question.strip() or VACATION_BALANCE_PROMPT,
                caller=CallerContext(
                    subject_id="cowork-vacation",
                    tenant_id="agent-iq-cowork",
                ),
                filters=MatchFilters(
                    max_candidates=23,
                    max_selected=1,
                    required_protocols=(TransportProtocol.A2A,),
                ),
                requested_at=datetime.now(UTC),
            )
        )
        result = await _execute_orchestration(
            vacation_demo_service or service,
            command,
            ctx,
        )
        if len(result.summary.steps) != 1:
            raise operation_error(
                ErrorCode.INVOCATION_FAILED,
                "The vacation balance did not produce exactly one agent invocation.",
            )
        answer = result.summary.steps[0].answer
        if answer is None:
            raise operation_error(
                ErrorCode.INVOCATION_FAILED,
                "The Vacation & Leave Agent did not return an answer.",
            )
        try:
            balance = select_vacation_balance(result)
        except ValueError as exc:
            raise operation_error(
                ErrorCode.EVIDENCE_UNAVAILABLE,
                "The authenticated vacation evidence did not contain one unique marked balance.",
            ) from exc
        return build_vacation_experience(balance, result)

    @mcp.tool(
        name="check_vacation_balance",
        title="Check vacation balance",
        description=(
            "Use only for a clearly personal vacation, PTO, holiday, or annual-leave balance. "
            "One call discovers and selects the Vacation & Leave Agent, invokes it through "
            "native A2A, validates the balance and its source, and returns one compact ring "
            "with one supporting line, one selected-agent chip, and one citation. Clarify "
            "self versus team before calling when the audience is ambiguous. Do not call "
            "search_agents or orchestrate before or after this personal-balance tool."
        ),
        annotations=_VACATION_BALANCE,
        meta=_vacation_ring_meta(),
        structured_output=True,
    )
    async def check_vacation_balance(
        ctx: Context[Any, Any, Any],
        question: str = VACATION_BALANCE_PROMPT,
    ) -> VacationBalancePresentation:
        return await _check_vacation_balance(ctx, question)

    @mcp.tool(
        name="run_vacation_balance_demo",
        title="Check vacation balance (compatibility)",
        description=(
            "Compatibility alias for cached Agent IQ clients. New clients must use "
            "check_vacation_balance. When invoked, this performs the same read-only Vacation & "
            "Leave Agent discovery, native A2A call, evidence validation, and compact cited "
            "balance response."
        ),
        annotations=_VACATION_BALANCE,
        meta=_vacation_ring_meta(),
        structured_output=True,
    )
    async def run_vacation_balance_demo(
        ctx: Context[Any, Any, Any],
        question: str = VACATION_BALANCE_PROMPT,
    ) -> VacationBalancePresentation:
        return await _check_vacation_balance(ctx, question)

    @mcp.tool(
        name="get_execution",
        title="View Agent IQ execution status",
        description="Get one execution's current state and persisted lifecycle events.",
        annotations=_READ_ONLY_EXECUTION,
        structured_output=True,
    )
    async def get_execution(execution_id: str) -> ExecutionSnapshot:
        return await service.get_execution(execution_id)

    @mcp.tool(
        name="cancel_execution",
        title="Cancel an Agent IQ execution",
        description="Request cancellation of an accepted or running execution.",
        annotations=_CANCEL_EXECUTION,
        structured_output=True,
    )
    async def cancel_execution(execution_id: str) -> ExecutionSnapshot:
        return await service.cancel_execution(execution_id)

    @mcp.tool(
        name="submit_feedback",
        title="Submit Agent IQ feedback",
        description="Attach caller-bound helpful or not-helpful feedback to an execution.",
        annotations=_SUBMIT_FEEDBACK,
        structured_output=True,
    )
    async def submit_feedback(submission: FeedbackSubmission) -> FeedbackReceipt:
        return await service.submit_feedback(submission)

    @mcp.tool(
        name="capabilities",
        title="View Agent IQ capabilities",
        description="Return Agent IQ ingress, lifecycle, and transport capabilities.",
        annotations=_READ_ONLY_CAPABILITIES,
        structured_output=True,
    )
    async def capabilities() -> ProductCapabilities:
        return service.capabilities()

    @mcp.tool(
        name="list_employee_scenarios",
        title="Browse Employee Operations scenarios",
        description=(
            "Return the compact catalog of 30 HR, IT, Payroll, Finance, Expense, "
            "corporate-services, workplace, and governance workflows."
        ),
        annotations=_READ_ONLY_EMPLOYEE_CATALOG,
        meta=_employee_operations_meta(),
        structured_output=True,
    )
    async def list_employee_scenarios() -> EmployeeScenarioCatalogPresentation:
        return list_employee_scenarios_presentation()

    @mcp.tool(
        name="resolve_employee_intent",
        title="Resolve an employee-services intent",
        description=(
            "Deterministically match a natural-language employee-services intent to the "
            "best workflow and return its complete rich experience."
        ),
        annotations=_READ_ONLY_EMPLOYEE_EXPERIENCE,
        meta=_employee_operations_meta(),
        structured_output=True,
    )
    async def resolve_employee_intent(intent: str) -> EmployeeExperiencePresentation:
        result = resolve_employee_request(EmployeeScenarioRequest(intent=intent))
        if not isinstance(result, EmployeeExperiencePresentation):
            raise TypeError("Employee intent resolution did not return an experience.")
        return result

    @mcp.tool(
        name="open_employee_experience",
        title="Open an Employee Operations scenario",
        description=(
            "Open one exact Employee Operations workflow by scenario ID and "
            "return selected agents, public phases, rich sections, sources, and actions."
        ),
        annotations=_READ_ONLY_EMPLOYEE_EXPERIENCE,
        meta=_employee_operations_meta(),
        structured_output=True,
    )
    async def open_employee_experience(scenario_id: str) -> EmployeeExperiencePresentation:
        return resolve_employee_scenario(scenario_id)

    @mcp.tool(
        name="prepare_employee_action",
        title="Prepare an employee action without executing it",
        description=(
            "Prepare or route an approval-bound draft from an Employee Operations "
            "scenario. This tool never writes to an enterprise system and always returns "
            "executed=false."
        ),
        annotations=_PREPARE_EMPLOYEE_ACTION,
        meta=_employee_operations_meta(),
        structured_output=True,
    )
    async def prepare_employee_action(
        request: EmployeeActionPreparationRequest,
    ) -> EmployeeActionReceipt:
        return prepare_employee_action_receipt(request)

    return mcp


async def _run_orchestration(
    service: AgentIqService,
    command: OrchestrationCommand,
    ctx: Context[Any, Any, Any],
) -> OrchestrationPresentation:
    result = await _execute_orchestration(service, command, ctx)
    return _present_orchestration(result, service)


async def _execute_orchestration(
    service: AgentIqService,
    command: OrchestrationCommand,
    ctx: Context[Any, Any, Any],
) -> OrchestrationResult:
    started = await service.start(command)
    progress_state = _ProgressState()
    async for event in service.watch_events(started.execution_id):
        meta = ctx.request_context.meta
        progress_token = meta.progressToken if meta is not None else None
        update = progress_state.accept(_progress_update(event.payload, service))
        if progress_token is not None and update is not None:
            # Route progress onto this stateless POST's SSE stream.
            await ctx.session.send_progress_notification(
                progress_token=progress_token,
                progress=update.progress,
                total=100.0,
                message=update.message,
                related_request_id=ctx.request_id,
            )
    return await service.wait_for_presentation(started.execution_id)


def _present_orchestration(
    result: OrchestrationResult,
    service: AgentIqService,
) -> OrchestrationPresentation:
    summary = result.summary
    records = {record.evidence_id: record for record in summary.synthesis.evidence.records}
    citations = []
    for citation in summary.synthesis.evidence.citations:
        record = records[citation.evidence_id]
        citations.append(
            CitationPresentation(
                number=citation.number,
                title=citation.label,
                source_id=record.source_id,
                excerpt=_public_evidence_excerpt(record.excerpt),
            )
        )
    issues = [
        ExecutionIssuePresentation(
            agent_id=issue.agent_id,
            status=issue.status,
            message=issue.message,
        )
        for issue in summary.synthesis.issues
    ]
    answer = _public_orchestration_answer(summary.synthesis.answer)
    vpn_recovery = None
    selected_agents = tuple(
        SelectedAgentPresentation(
            agent_id=reason.agent_id,
            display_name=public_text(service.agent_name(reason.agent_id)),
            reason=public_text(reason.summary),
            matched_skills=reason.matched_skills,
            matched_tags=reason.matched_tags,
        )
        for reason in summary.selection.selected
    )
    if (
        len(selected_agents) == 1
        and selected_agents[0].agent_id == "employee-it-helpdesk"
        and "vpn-recovery-guidance" in selected_agents[0].matched_skills
        and _is_vpn_recovery_intent(summary.plan.objective)
    ):
        try:
            guidance, source_citation = _authenticated_vpn_guidance(summary, citations)
            first_line = (
                f"{guidance.population:,} resolved VPN recoveries across {guidance.scope} "
                f"from {guidance.period_start.strftime('%d %b %Y')} "
                f"to {guidance.period_end.strftime('%d %b %Y')}."
            )
            second_line = (
                f"Start with {public_text(guidance.recommended_first_action).lower()}. "
                f"{public_text(guidance.escalation_boundary)}"
            )
            vpn_recovery = VpnRecoveryPresentation.model_validate(
                {
                    "status": "available",
                    "title": public_text(guidance.title),
                    "summaryLines": (first_line, second_line),
                    "population": guidance.population,
                    "periodLabel": (
                        f"{guidance.period_start.strftime('%d %b %Y')} - "
                        f"{guidance.period_end.strftime('%d %b %Y')}"
                    ),
                    "scopeLabel": public_text(guidance.scope),
                    "recommendedFirstAction": public_text(guidance.recommended_first_action),
                    "escalationBoundary": public_text(guidance.escalation_boundary),
                    "freshnessLabel": (
                        f"Freshness: {guidance.freshness.strftime('%d %b %Y %H:%M UTC')}"
                    ),
                    "guidanceNote": public_text(guidance.guidance_note),
                    "selectedAgent": selected_agents[0].model_dump(mode="json", by_alias=True),
                    "source": source_citation.model_dump(mode="json", by_alias=True),
                    "outcomes": tuple(
                        {
                            "outcomeId": outcome.outcome_id,
                            "label": public_text(outcome.label),
                            "count": outcome.count,
                            "share": outcome.count / guidance.population,
                            "recommended": outcome.outcome_id.value == "reconnect",
                        }
                        for outcome in guidance.outcomes
                    ),
                }
            )
            answer = " ".join(vpn_recovery.summary_lines)
        except ValueError as exc:
            issues.append(
                ExecutionIssuePresentation(
                    agent_id="employee-it-helpdesk",
                    status=SynthesisIssueStatus.OMITTED_UNGROUNDED,
                    message=str(exc),
                )
            )
            vpn_recovery = VpnRecoveryPresentation.model_validate(
                {
                    "status": "unavailable",
                    "title": "VPN guidance unavailable",
                    "summaryLines": ("Authenticated VPN guidance was unavailable for this run.",),
                }
            )
            answer = "Authenticated VPN guidance was unavailable for this run."
    return OrchestrationPresentation(
        execution_id=summary.execution_id,
        request_id=summary.request_id,
        status=summary.status,
        duration_ms=summary.duration_ms,
        selected_agents=selected_agents,
        answer=answer,
        citations=tuple(citations),
        sources_markdown=_public_sources_markdown(citations),
        issues=tuple(issues),
        next_action=("Review the reported gaps before relying on this result." if issues else None),
        vpn_recovery=vpn_recovery,
    )


def _authenticated_vpn_guidance(
    summary: ExecutionSummary,
    citations: list[CitationPresentation],
) -> tuple[VpnRecoveryGuidance, CitationPresentation]:
    citation_number_by_evidence = {
        citation.evidence_id: citation.number for citation in summary.synthesis.evidence.citations
    }
    presentation_by_number = {citation.number: citation for citation in citations}
    matches: list[tuple[VpnRecoveryGuidance, CitationPresentation]] = []
    for record in summary.synthesis.evidence.records:
        if record.agent_id != "employee-it-helpdesk":
            continue
        try:
            guidance = parse_vpn_recovery(record.excerpt)
        except ValueError:
            continue
        citation_number = citation_number_by_evidence.get(record.evidence_id)
        if citation_number is None:
            continue
        source_citation = presentation_by_number.get(citation_number)
        if (
            source_citation is not None
            and record.source_id == guidance.source_record_id
            and record.title == guidance.source_title
        ):
            matches.append((guidance, source_citation))
    if len(matches) != 1:
        raise ValueError(
            "Authenticated VPN guidance requires one unique marked and cited source record."
        )
    return matches[0]


def _public_evidence_excerpt(excerpt: str) -> str | None:
    if VPN_RECOVERY_JSON_START in excerpt or VPN_RECOVERY_JSON_END in excerpt:
        try:
            return public_text(parse_vpn_recovery(excerpt).guidance_note)
        except ValueError:
            return None
    if VACATION_BALANCE_JSON_START in excerpt or VACATION_BALANCE_JSON_END in excerpt:
        try:
            balance = parse_vacation_balance(excerpt)
        except ValueError:
            return None
        return f"Vacation balance as of {balance.as_of.strftime('%d %B %Y')}."
    return public_text(excerpt)


def _public_sources_markdown(citations: list[CitationPresentation]) -> str | None:
    if not citations:
        return None
    lines = ["**Sources**"]
    lines.extend(
        f"{citation.number}. {citation.title} - `{citation.source_id}`" for citation in citations
    )
    return "\n".join(lines)


def _public_orchestration_answer(answer: str) -> str:
    return public_answer(answer)


def _is_vpn_recovery_intent(intent: str) -> bool:
    normalized = " ".join(intent.casefold().split())
    tokens = set(re.findall(r"[a-z0-9]+", normalized))
    if "vpn" in tokens:
        return True
    if any(
        phrase in normalized
        for phrase in (
            "cannot connect remotely",
            "can't connect remotely",
            "unable to connect remotely",
        )
    ):
        return True
    remote_problem = bool(tokens & {"remote", "connectivity"}) and bool(
        tokens & {"down", "fail", "failed", "failing", "issue", "problem", "working"}
    )
    escalation_only = any(token.startswith("escalat") for token in tokens)
    return remote_problem and not escalation_only


def _progress_update(
    payload: EventPayload,
    service: AgentIqService,
) -> _ProgressUpdate | None:
    if isinstance(
        payload,
        (RequestAcceptedPayload, CatalogSearchedPayload, CandidatesRankedPayload),
    ):
        return _ProgressUpdate("matching", 10.0, "Matching eligible specialists")
    if isinstance(payload, AgentsSelectedPayload):
        names = [
            public_text(service.agent_name(reason.agent_id)) for reason in payload.decision.selected
        ]
        signals = [
            signal
            for reason in payload.decision.selected
            for signal in (*reason.matched_skills, *reason.matched_tags)
        ][:4]
        message = f"Selected {', '.join(names)}"
        if signals:
            message += f" - matched {', '.join(signals)}"
        return _ProgressUpdate("selected", 30.0, message)
    if isinstance(payload, SquadProposedPayload):
        return _ProgressUpdate("squad-proposed", 32.0, "Proposed the smallest justified squad")
    if isinstance(payload, SquadOverriddenPayload):
        return _ProgressUpdate("squad-overridden", 33.0, "Adjusted the squad composition")
    if isinstance(payload, SquadAuthorizedPayload):
        return _ProgressUpdate("squad-authorized", 34.0, "Authorized the squad")
    if isinstance(payload, PlanPreparedPayload):
        return _ProgressUpdate("planned", 35.0, "Prepared the governed execution plan")
    if isinstance(payload, ApprovalRequestedPayload):
        return _ProgressUpdate("review", 40.0, "Review required before continuing")
    if isinstance(payload, ApprovalResolvedPayload):
        return _ProgressUpdate(
            f"review:{payload.status}",
            45.0,
            f"Review {payload.status}",
        )
    if isinstance(payload, InvocationStartedPayload):
        name = public_text(service.agent_name(payload.agent_id))
        return _ProgressUpdate(
            f"retrieving:{payload.agent_id}",
            55.0,
            f"Retrieving approved evidence from {name}",
        )
    if isinstance(payload, InvocationDeltaPayload):
        return _ProgressUpdate(
            f"streaming:{payload.step_id}",
            70.0,
            "Receiving live specialist updates",
        )
    if isinstance(payload, EvidenceReceivedPayload):
        return None
    if isinstance(payload, InvocationFailedPayload):
        return _ProgressUpdate(
            f"failed:{payload.step_id}",
            80.0,
            "Continuing with the available specialist results",
        )
    if isinstance(payload, SynthesisCompletedPayload):
        return _ProgressUpdate("synthesizing", 85.0, "Building the cited answer")
    if isinstance(payload, InvocationCompletedPayload):
        return None
    if isinstance(payload, TaskHandedOffPayload):
        return _ProgressUpdate("handoff", 90.0, "Handing off the long-running task")
    if isinstance(payload, TaskCompletedPayload):
        return _ProgressUpdate("task-completed", 95.0, "Long-running task completed")
    if isinstance(payload, ExecutionCompletedPayload):
        return _ProgressUpdate("completed", 100.0, "Completed")
    assert_never(payload)
