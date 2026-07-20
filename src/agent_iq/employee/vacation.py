"""Compact Cowork projection for a vacation-balance request."""

from __future__ import annotations

from decimal import Decimal

from agent_iq.contracts import (
    Citation,
    EvidenceRecord,
    OrchestrationResult,
    StepExecutionStatus,
    VacationBalance,
    parse_vacation_balance,
)

from .contracts import (
    EmployeeAgentGroup,
    EmployeeSelectedAgentPresentation,
    ScenarioPhaseStatus,
    ScenarioSourceRef,
    VacationBalancePresentation,
)

VACATION_AGENT_ID = "employee-vacation-leave"
VACATION_AGENT_NAME = "Vacation & Leave Agent"


def _days(value: Decimal) -> str:
    rendered = format(value.normalize(), "f")
    return rendered if "." in rendered else str(int(value))


def _authenticated_balances(
    result: OrchestrationResult,
) -> tuple[tuple[VacationBalance, EvidenceRecord, Citation], ...]:
    evidence = result.summary.synthesis.evidence
    citation_by_evidence = {citation.evidence_id: citation for citation in evidence.citations}
    matches: list[tuple[VacationBalance, EvidenceRecord, Citation]] = []
    for record in evidence.records:
        if record.agent_id != VACATION_AGENT_ID:
            continue
        try:
            balance = parse_vacation_balance(record.excerpt)
        except ValueError:
            continue
        citation = citation_by_evidence.get(record.evidence_id)
        if (
            citation is not None
            and record.source_id == balance.source_record_id
            and record.title == balance.source_title
        ):
            matches.append((balance, record, citation))
    return tuple(matches)


def select_vacation_balance(result: OrchestrationResult) -> VacationBalance:
    """Select one uniquely authenticated marked balance from multi-source evidence."""

    matches = _authenticated_balances(result)
    if len(matches) != 1:
        raise ValueError(
            "The vacation balance requires exactly one authenticated marked balance record."
        )
    return matches[0][0]


def _validated_source(
    balance: VacationBalance,
    result: OrchestrationResult,
) -> ScenarioSourceRef:
    matches = [match for match in _authenticated_balances(result) if match[0] == balance]
    if len(matches) != 1:
        raise ValueError(
            "The vacation balance source provenance does not match authenticated evidence."
        )
    _, record, citation = matches[0]
    return ScenarioSourceRef(
        source_id=record.source_id,
        title=f"Vacation balance {balance.balance_year}",
        knowledge_source_id=record.knowledge_source,
        summary=f"Vacation balance as of {balance.as_of.strftime('%d %B %Y')}.",
        url=citation.url,
    )


def build_vacation_experience(
    balance: VacationBalance,
    result: OrchestrationResult,
) -> VacationBalancePresentation:
    """Build one compact ring from validated live execution data."""

    selected = result.summary.selection.selected
    steps = result.summary.steps
    if len(selected) != 1 or selected[0].agent_id != VACATION_AGENT_ID:
        raise ValueError("The vacation balance must select exactly the Vacation & Leave Agent.")
    if (
        len(steps) != 1
        or steps[0].agent_id != VACATION_AGENT_ID
        or steps[0].status is not StepExecutionStatus.SUCCEEDED
        or steps[0].protocol_version != "1.0"
    ):
        raise ValueError("The vacation balance requires one successful native A2A v1.0 invocation.")
    source = _validated_source(balance, result)
    selected_reason = selected[0]
    agent = EmployeeSelectedAgentPresentation(
        agent_id=VACATION_AGENT_ID,
        display_name=VACATION_AGENT_NAME,
        glyph="VL",
        group=EmployeeAgentGroup.CORPORATE_SERVICES,
        reason=selected_reason.summary,
    )
    return VacationBalancePresentation(
        execution_id=result.summary.execution_id,
        balance_year=balance.balance_year,
        title=f"{_days(balance.remaining_days)} vacation days remaining",
        supporting_line=(
            f"{_days(balance.taken_days)} taken | "
            f"{_days(balance.booked_days)} booked | "
            f"{_days(balance.entitlement_days)} total"
        ),
        status=ScenarioPhaseStatus.COMPLETED,
        entitlement_days=balance.entitlement_days,
        taken_days=balance.taken_days,
        booked_days=balance.booked_days,
        remaining_days=balance.remaining_days,
        selected_agents=(agent,),
        sources=(source,),
    )
