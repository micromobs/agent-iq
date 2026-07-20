from __future__ import annotations

import pytest
from azure.monitor import opentelemetry as azure_monitor
from opentelemetry.sdk.resources import Resource

import agent_iq.telemetry.configure as configure_module


def test_telemetry_is_optional_and_configured_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.setattr(configure_module, "_CONFIGURED", False)
    assert not configure_module.configure_agent_iq_telemetry()

    calls: list[dict[str, object]] = []

    def configure_azure_monitor(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(azure_monitor, "configure_azure_monitor", configure_azure_monitor)
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "InstrumentationKey=test")

    assert configure_module.configure_agent_iq_telemetry()
    assert configure_module.configure_agent_iq_telemetry()
    assert calls[0]["connection_string"] == "InstrumentationKey=test"
    resource = calls[0]["resource"]
    assert isinstance(resource, Resource)
    assert resource.attributes["service.name"] == "agent-iq-orchestrator"
    monkeypatch.setattr(configure_module, "_CONFIGURED", False)
