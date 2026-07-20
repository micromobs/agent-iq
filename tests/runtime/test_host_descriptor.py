"""Reusable Foundry host descriptor tests."""

from __future__ import annotations

import json

import pytest

from agent_iq.runtime import host_bootstrap
from agent_iq.runtime.host_descriptor import (
    HOST_DESCRIPTOR_SHA256_ENV,
    HOST_LOGICAL_AGENT_ID_ENV,
    HOST_RUNTIME_FAMILY_ENV,
    HOST_TOOLBOX_NAME_ENV,
    HostDescriptorError,
    descriptor_for_agent,
    descriptor_from_environment,
)

AGENT_ID = "consumer-formulation-rd"


def _environment() -> dict[str, str]:
    descriptor = descriptor_for_agent(AGENT_ID)
    return {
        HOST_LOGICAL_AGENT_ID_ENV: descriptor.logical_agent_id,
        HOST_RUNTIME_FAMILY_ENV: descriptor.runtime_family,
        HOST_DESCRIPTOR_SHA256_ENV: descriptor.descriptor_sha256,
        HOST_TOOLBOX_NAME_ENV: descriptor.toolbox_name,
    }


def test_descriptor_is_deterministic_and_content_safe() -> None:
    first = descriptor_for_agent(AGENT_ID)
    second = descriptor_for_agent(AGENT_ID)

    assert first == second
    assert first.runtime_family == "langgraph"
    assert first.authoring_framework == "LangGraph"
    assert first.knowledge_binding_id == "consumer-product-innovation-kb"
    assert first.toolbox_name == f"{AGENT_ID}-knowledge"
    assert first.descriptor_sha256.startswith("sha256:")
    assert len(first.descriptor_sha256) == 71
    serialized = first.model_dump_json(by_alias=True)
    assert "promptAddendum" not in serialized
    assert "BrightWash" not in serialized


def test_environment_must_match_every_immutable_selector() -> None:
    environment = _environment()
    descriptor = descriptor_from_environment(environment)
    assert descriptor.logical_agent_id == AGENT_ID

    for name, value in (
        (HOST_RUNTIME_FAMILY_ENV, "pydantic-ai"),
        (HOST_DESCRIPTOR_SHA256_ENV, f"sha256:{'0' * 64}"),
        (HOST_TOOLBOX_NAME_ENV, "other-knowledge"),
    ):
        drifted = {**environment, name: value}
        with pytest.raises(HostDescriptorError, match=name):
            descriptor_from_environment(drifted)


def test_environment_rejects_missing_unknown_and_wrong_runtime_family() -> None:
    environment = _environment()
    del environment[HOST_LOGICAL_AGENT_ID_ENV]
    with pytest.raises(HostDescriptorError, match=HOST_LOGICAL_AGENT_ID_ENV):
        descriptor_from_environment(environment)
    with pytest.raises(HostDescriptorError, match="Unknown canonical agent"):
        descriptor_for_agent("consumer-missing")
    with pytest.raises(HostDescriptorError, match="not the LangGraph runtime family"):
        descriptor_for_agent("consumer-demand-forecasting")


def test_network_free_check_prints_only_the_safe_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name, value in _environment().items():
        monkeypatch.setenv(name, value)

    assert host_bootstrap.main(["--check"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["logicalAgentId"] == AGENT_ID
    assert payload["runtimeFamily"] == "langgraph"
    assert set(payload) == {
        "schemaVersion",
        "logicalAgentId",
        "runtimeFamily",
        "authoringFramework",
        "descriptorSha256",
        "knowledgeBindingId",
        "toolboxName",
        "toolIds",
        "artifactOutputKinds",
    }
