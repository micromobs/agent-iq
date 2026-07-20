"""Host-neutral presentation copy derived from canonical catalog records."""

from __future__ import annotations

from typing import Any

from agent_iq.contracts import (
    VACATION_BALANCE_JSON_END,
    VACATION_BALANCE_JSON_START,
    VPN_RECOVERY_JSON_END,
    VPN_RECOVERY_JSON_START,
    AgentSearchResult,
    ExecutionSnapshot,
    ExecutionSummary,
    SelectionDecision,
)

_MACHINE_PAYLOAD_DELIMITERS = (
    (VACATION_BALANCE_JSON_START, VACATION_BALANCE_JSON_END),
    (VPN_RECOVERY_JSON_START, VPN_RECOVERY_JSON_END),
)


def public_text(value: str) -> str:
    """Remove internal fixture qualifiers and machine payloads from public copy."""

    sanitized = value
    contained_machine_payload = any(
        start_marker in sanitized or end_marker in sanitized
        for start_marker, end_marker in _MACHINE_PAYLOAD_DELIMITERS
    )
    for start_marker, end_marker in _MACHINE_PAYLOAD_DELIMITERS:
        sanitized = _strip_machine_payload(sanitized, start_marker, end_marker)
    sanitized = sanitized.replace(" (synthetic demo)", "").replace("(synthetic demo) ", "")
    if contained_machine_payload and not sanitized.strip():
        return "The selected agent returned no human-readable guidance."
    return sanitized


def public_answer(value: str) -> str:
    """Return a non-empty human-readable answer for public adapters."""

    sanitized = public_text(value).strip()
    return sanitized or "The selected agent returned no human-readable guidance."


def public_agent_search_result(result: AgentSearchResult) -> AgentSearchResult:
    """Project a search result without changing its canonical catalog source."""

    return AgentSearchResult.model_validate(_public_value(result.model_dump(mode="json")))


def public_selection_decision(decision: SelectionDecision) -> SelectionDecision:
    """Project public selection facts while preserving scores and identities."""

    return SelectionDecision.model_validate(_public_value(decision.model_dump(mode="json")))


def public_execution_summary(summary: ExecutionSummary) -> ExecutionSummary:
    """Project a terminal result consistently across MCP, HTTP, and A2A."""

    return ExecutionSummary.model_validate(_public_value(summary.model_dump(mode="json")))


def public_execution_snapshot(snapshot: ExecutionSnapshot) -> ExecutionSnapshot:
    """Project an execution ledger snapshot without exposing trusted machine payloads."""

    return ExecutionSnapshot.model_validate(_public_value(snapshot.model_dump(mode="json")))


def _public_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _public_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_public_value(item) for item in value]
    if isinstance(value, str):
        return public_text(value)
    return value


def _strip_machine_payload(value: str, start_marker: str, end_marker: str) -> str:
    sanitized = value
    while start_marker in sanitized or end_marker in sanitized:
        start = sanitized.find(start_marker)
        end = sanitized.find(end_marker)
        if end >= 0 and (start < 0 or end < start):
            sanitized = sanitized[end + len(end_marker) :]
            continue
        if start >= 0:
            paired_end = sanitized.find(end_marker, start + len(start_marker))
            if paired_end < 0:
                return sanitized[:start]
            sanitized = sanitized[:start] + sanitized[paired_end + len(end_marker) :]
    return sanitized
