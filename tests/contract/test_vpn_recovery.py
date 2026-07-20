"""Strict aggregate VPN recovery contract tests."""

from __future__ import annotations

import json
from typing import cast

import pytest

from agent_iq.catalog import load_consumer_assets
from agent_iq.contracts import (
    VPN_RECOVERY_JSON_END,
    VPN_RECOVERY_JSON_START,
    parse_vpn_recovery,
)


def _document(document_id: str) -> str:
    return next(
        document.content
        for document in load_consumer_assets().documents
        if document.document_id == document_id
    )


def _payload(answer: str) -> dict[str, object]:
    marked = answer.split(VPN_RECOVERY_JSON_START, maxsplit=1)[1]
    encoded = marked.split(VPN_RECOVERY_JSON_END, maxsplit=1)[0]
    return cast(dict[str, object], json.loads(encoded))


def _marked(payload: dict[str, object]) -> str:
    return (
        f"{VPN_RECOVERY_JSON_START}\n"
        f"{json.dumps(payload, separators=(',', ':'))}\n"
        f"{VPN_RECOVERY_JSON_END}"
    )


def test_parse_vpn_recovery_accepts_canonical_evidence() -> None:
    guidance = parse_vpn_recovery(_document("employee-vpn-recovery-guidance"))

    assert guidance.agent_id == "employee-it-helpdesk"
    assert guidance.population == 2400
    assert guidance.read_only is True
    assert guidance.guidance_note == "Aggregate recovery guidance only; not a device diagnosis."


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("agentId", "employee-expense"),
        ("scope", "Regional"),
        ("readOnly", False),
    ),
)
def test_vpn_guidance_rejects_wrong_agent_scope_or_write_state(
    field: str,
    value: object,
) -> None:
    payload = _payload(_document("employee-vpn-recovery-guidance"))
    payload[field] = value

    with pytest.raises(ValueError, match="does not satisfy"):
        parse_vpn_recovery(_marked(payload))


def test_vpn_guidance_requires_population_to_match_outcomes() -> None:
    payload = _payload(_document("employee-vpn-recovery-guidance"))
    payload["population"] = 10

    with pytest.raises(ValueError, match="does not satisfy"):
        parse_vpn_recovery(_marked(payload))


@pytest.mark.parametrize(
    "answer",
    (
        f"{VPN_RECOVERY_JSON_START}\n{{bad json}}\n{VPN_RECOVERY_JSON_END}",
        f"{VPN_RECOVERY_JSON_START}\n{VPN_RECOVERY_JSON_END}",
        (
            f"{VPN_RECOVERY_JSON_START}\n{{}}\n{VPN_RECOVERY_JSON_END}\n"
            f"{VPN_RECOVERY_JSON_START}\n{{}}\n{VPN_RECOVERY_JSON_END}"
        ),
    ),
)
def test_parse_vpn_recovery_rejects_malformed_markers(answer: str) -> None:
    with pytest.raises(ValueError):
        parse_vpn_recovery(answer)
