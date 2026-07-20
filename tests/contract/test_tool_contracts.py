"""Governed tool and SAP provenance contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_iq.catalog import load_consumer_runtime_assets
from agent_iq.contracts import (
    ArtifactKind,
    SapReadResult,
    SapSourceMode,
    ToolBackend,
    ToolDescriptor,
    ToolEffect,
    ToolIdempotency,
    ToolParameter,
)


def _tool(**overrides: object) -> ToolDescriptor:
    values: dict[str, object] = {
        "tool_id": "lookup-record",
        "name": "Lookup record",
        "description": "Retrieve one authorized record.",
        "backend": ToolBackend.SAP,
        "parameters": (
            ToolParameter(
                parameter_id="record_id",
                description="Stable record identifier.",
                required=True,
            ),
        ),
        "authorization_scopes": ("sap.record.read",),
        "effect": ToolEffect.READ,
        "approval_required": False,
        "idempotency": ToolIdempotency.SAFE,
        "produces_evidence": True,
        "artifact_kinds": (ArtifactKind.DEMAND_SCENARIO,),
    }
    values.update(overrides)
    return ToolDescriptor.model_validate(values)


def test_read_tool_governance_round_trips_on_the_wire() -> None:
    tool = _tool()
    wire = tool.model_dump(mode="json", by_alias=True)

    assert ToolDescriptor.model_validate(wire) == tool
    assert wire["authorizationScopes"] == ["sap.record.read"]
    assert wire["producesEvidence"] is True
    assert wire["telemetry"]["captureLatency"] is True
    assert wire["telemetry"]["captureArguments"] is False


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"parameters": ()}, "at least 1"),
        (
            {
                "parameters": (
                    ToolParameter(parameter_id="record_id", description="First."),
                    ToolParameter(parameter_id="record_id", description="Duplicate."),
                )
            },
            "unique",
        ),
        (
            {"authorization_scopes": ("sap.record.read", "SAP.RECORD.READ")},
            "unique",
        ),
        (
            {
                "effect": ToolEffect.WRITE,
                "approval_required": False,
            },
            "explicit approval",
        ),
        (
            {
                "effect": ToolEffect.WRITE,
                "approval_required": True,
                "idempotency": ToolIdempotency.SAFE,
            },
            "unconditional retry safety",
        ),
    ],
)
def test_tool_governance_rejects_unsafe_or_ambiguous_metadata(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _tool(**overrides)


def test_sap_fixture_result_preserves_visible_provenance() -> None:
    assets = load_consumer_runtime_assets()
    result = SapReadResult(
        retrieved_at=assets.sap_fixtures.generated_at,
        authorization_scope=assets.sap_fixtures.records[0].reference.authorization_scope,
        source_mode=SapSourceMode.FIXTURE,
        source_label="DETERMINISTIC FIXTURE - NOT LIVE SAP",
        resource_kind=assets.sap_fixtures.records[0].resource_kind,
        records=(assets.sap_fixtures.records[0],),
    )

    wire = result.model_dump(mode="json", by_alias=True)
    assert wire["sourceMode"] == "fixture"
    assert "NOT LIVE SAP" in wire["sourceLabel"]
    assert wire["records"][0]["reference"]["deepLink"].startswith(
        "https://fixture.sap.example.invalid/"
    )


def test_sap_fixture_result_rejects_a_live_looking_label() -> None:
    assets = load_consumer_runtime_assets()
    record = assets.sap_fixtures.records[0]

    with pytest.raises(ValidationError, match="NOT LIVE SAP"):
        SapReadResult(
            retrieved_at=assets.sap_fixtures.generated_at,
            authorization_scope=record.reference.authorization_scope,
            source_mode=SapSourceMode.FIXTURE,
            source_label="SAP",
            resource_kind=record.resource_kind,
            records=(record,),
        )
