"""Foundry capability-ledger tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol, cast

import httpx
import pytest
from azure.core.exceptions import ClientAuthenticationError

from agent_iq.contracts.capabilities import (
    CapabilityLedger,
    ProbeMode,
    ProbeStatus,
)
from agent_iq.deployments import foundry_capabilities as capabilities

PROJECT = "https://example.services.ai.azure.com/api/projects/example"


class _Collection:
    def __init__(self, values: list[object] | None = None) -> None:
        self._values = values or []

    def list(self, **_: object) -> list[object]:
        return self._values


class _Agents(_Collection):
    def list_sessions(self, *_: object, **__: object) -> list[object]:
        return [object()]

    def get(self, *_: object, **__: object) -> dict[str, object]:
        return {
            "state": "enabled",
            "versions": {
                "latest": {
                    "status": "active",
                    "version": "3",
                    "definition": {
                        "kind": "hosted",
                        "protocol_versions": [{"protocol": "responses", "version": "2.0.0"}],
                    },
                }
            },
            "agent_endpoint": {"protocols": ["responses", "a2a"]},
            "instance_identity": {"configured": True},
        }


class _FakeProjectClient:
    def __init__(self) -> None:
        self.agents = _Agents([object()])
        self.toolboxes = _Collection([object()])
        self.beta = SimpleNamespace(
            memory_stores=_Collection(),
            routines=_Collection(),
            schedules=_Collection(),
            skills=_Collection(),
            models=_Collection(),
        )
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeReader:
    def get(self, url: str) -> dict[str, object]:
        if "/locations/" in url and "/usages?" in url:
            return _quota_payload()
        if "/models?" in url:
            return _model_payload()
        if "/deployments?" in url:
            return {
                "value": [
                    {
                        "properties": {
                            "model": {"name": "gpt-current", "version": "1"},
                            "provisioningState": "Succeeded",
                        },
                        "sku": {"name": "GlobalStandard"},
                    }
                ]
            }
        if "/agentCard/" in url:
            return {
                "capabilities": {
                    "streaming": False,
                    "pushNotifications": False,
                },
                "supportedInterfaces": [{}, {}, {}],
            }
        return {
            "location": "example-region",
            "kind": "AIServices",
            "identity": {"type": "SystemAssigned"},
            "properties": {
                "publicNetworkAccess": "Disabled",
                "networkAcls": {
                    "defaultAction": "Deny",
                    "virtualNetworkRules": [{}],
                },
                "privateEndpointConnections": [{}],
            },
        }

    def close(self) -> None:
        return None


class _TransportSettings(Protocol):
    _use_env_settings: bool


class _RedirectSettings(Protocol):
    allow: bool


class _FakeCredential:
    def __init__(self, **kwargs: object) -> None:
        if kwargs:
            transport = cast(_TransportSettings, kwargs["transport"])
            redirect_policy = cast(_RedirectSettings, kwargs["redirect_policy"])
            assert transport._use_env_settings is False
            assert redirect_policy.allow is False
        self.closed = False

    def get_token(self, *_: object, **__: object) -> SimpleNamespace:
        return SimpleNamespace(token="test-token")

    def close(self) -> None:
        self.closed = True


class _Closeable:
    def __init__(self, *_: object, **__: object) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _model_payload() -> dict[str, object]:
    return {
        "value": [
            {
                "name": model,
                "version": "2026-07-09",
                "properties": {
                    "lifecycleStatus": "GenerallyAvailable",
                    "capabilities": {
                        "agentsV2": "true",
                        "responses": "true",
                        "chatCompletion": "true",
                    },
                    "deprecation": {"inference": "2027-07-09"},
                },
                "skus": [
                    {
                        "name": "GlobalStandard",
                        "capacity": {"default": 1, "minimum": 1, "maximum": 100},
                    },
                    {
                        "name": "DataZoneStandard",
                        "capacity": {"default": 1, "minimum": 1, "maximum": 50},
                    },
                ],
                "unlistedField": "must-not-survive",
            }
            for model in capabilities.DEFAULT_TARGET_MODELS
        ]
    }


def _quota_payload() -> dict[str, object]:
    return {
        "value": [
            {
                "name": {"value": f"OpenAI.{sku}.{model}"},
                "currentValue": 0,
                "limit": limit,
                "unit": "Count",
                "rawError": "must-not-survive",
            }
            for model in capabilities.DEFAULT_TARGET_MODELS
            for sku, limit in (
                ("GlobalStandard", 1000),
                ("DataZoneStandard", 333),
            )
        ]
    }


def test_model_and_quota_normalizers_are_allowlisted() -> None:
    models = capabilities._target_model_details(
        _model_payload(),
        capabilities.DEFAULT_TARGET_MODELS,
    )
    quotas = capabilities._normalize_quota(
        _quota_payload(),
        capabilities.DEFAULT_TARGET_MODELS,
    )
    serialized = json.dumps({"models": models, "quotas": quotas})

    assert all(details["listed"] for details in models.values())
    assert quotas["modelsWithPositiveQuota"] == 3
    assert "must-not-survive" not in serialized
    assert "unlistedField" not in serialized
    assert "rawError" not in serialized


def test_placement_requires_catalog_support_and_positive_quota() -> None:
    models = capabilities._target_model_details(
        _model_payload(),
        capabilities.DEFAULT_TARGET_MODELS,
    )
    quotas = capabilities._target_quota_records(
        _quota_payload(),
        capabilities.DEFAULT_TARGET_MODELS,
    )

    global_observation = capabilities._placement_observation(
        "global",
        sku_prefixes=("Global",),
        model_details=models,
        quota_records=quotas,
    )
    regional_observation = capabilities._placement_observation(
        "regional",
        sku_prefixes=("Standard", "Regional"),
        model_details=models,
        quota_records=quotas,
    )

    assert global_observation.observed_status is ProbeStatus.AVAILABLE
    assert regional_observation.observed_status is ProbeStatus.UNAVAILABLE


def test_error_classification_never_persists_raw_error_text() -> None:
    restricted_text = "restricted-content-marker"
    exc = capabilities.ProbeFailure(status_code=403, error_code="Forbidden")
    exc.args = (restricted_text,)

    observation = capabilities._failed_observation(
        "test.capability",
        category="test",
        documented_status="preview",
        probe_mode=ProbeMode.ARM,
        required=True,
        exc=exc,
    )
    serialized = observation.model_dump_json(by_alias=True)

    assert observation.observed_status is ProbeStatus.UNAUTHORIZED
    assert observation.details["httpStatus"] == 403
    assert restricted_text not in serialized


def test_certificate_evidence_keeps_only_protocol_facts(tmp_path: Path) -> None:
    path = tmp_path / "certificate.json"
    path.write_text(
        json.dumps(
            {
                "status": "pass",
                "checkedAt": "2026-07-12T00:00:00Z",
                "plan": {
                    "projectEndpoint": "https://private.example",
                    "agentCard": {"description": "private prompt-like content"},
                },
                "responses": {
                    "status": "pass",
                    "transport": "foundry-responses-sse",
                    "answer": "private answer",
                },
                "a2a": {
                    "status": "pass",
                    "transport": {
                        "protocolVersion": "1.0",
                        "fallbackUsed": False,
                    },
                    "answer": "another private answer",
                },
            }
        ),
        encoding="utf-8",
    )

    evidence = capabilities._certificate_evidence(path)
    serialized = json.dumps(evidence)

    assert evidence["responsesStatus"] == "pass"
    assert evidence["a2aProtocolVersion"] == "1.0"
    assert "private" not in serialized
    assert "answer" not in serialized


def test_summary_fails_required_gap_but_allows_classified_preview() -> None:
    observations = [
        capabilities._observation(
            "required.available",
            category="test",
            documented_status="ga",
            observed_status=ProbeStatus.AVAILABLE,
            probe_mode=ProbeMode.SDK,
            required=True,
        ),
        capabilities._classified_observation(
            "optional.mutation",
            category="test",
            documented_status="preview",
            status=ProbeStatus.MUTATION_REQUIRED,
            reason_code="mutation_needed",
        ),
    ]
    status, summary = capabilities._summary(observations)
    assert status == "pass"
    assert summary["requiredFailures"] == []

    observations[0] = observations[0].model_copy(
        update={"observed_status": ProbeStatus.UNAVAILABLE}
    )
    status, summary = capabilities._summary(observations)
    assert status == "fail"
    assert summary["requiredFailures"] == ["required.available"]


def test_content_safety_rejects_resource_coordinates() -> None:
    config = capabilities.CapabilityProbeConfig(
        project_endpoint="https://private-project.services.ai.azure.com/api/projects/private",
        subscription_id="private-subscription-coordinate",
        resource_group="private-resource-group",
        account_name="private-project",
        project_name="private",
        location="example-region",
        canary_agent_name="private-canary-agent",
    )
    ledger = CapabilityLedger(
        status="pass",
        checked_at="2026-07-12T00:00:00Z",
        scope={
            "location": "example-region",
            "targetModels": [],
            "projectConfigured": True,
            "canaryConfigured": True,
        },
        content_policy={},
        sdk={},
        summary={
            "capabilityCount": 0,
            "requiredCount": 0,
            "requiredAvailableCount": 0,
            "requiredFailures": [],
            "probeErrors": [],
            "statusCounts": {},
        },
        capabilities=[],
    )
    capabilities._validate_content_safety(ledger, config)

    leaked = ledger.model_copy(update={"sdk": {"leaked-resource": config.account_name}})
    try:
        capabilities._validate_content_safety(leaked, config)
    except RuntimeError as exc:
        assert "content-safety" in str(exc)
    else:
        raise AssertionError("Expected the content-safety guard to reject a resource name.")


def _write_certificate(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "status": "pass",
                "checkedAt": "2026-07-12T00:00:00Z",
                "responses": {
                    "status": "pass",
                    "transport": "foundry-responses-sse",
                },
                "a2a": {
                    "status": "pass",
                    "transport": {
                        "protocolVersion": "1.0",
                        "fallbackUsed": False,
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def _collect_test_ledger(tmp_path: Path) -> CapabilityLedger:
    certificate = tmp_path / "certificate.json"
    _write_certificate(certificate)
    config = capabilities.CapabilityProbeConfig(
        project_endpoint=PROJECT,
        subscription_id="subscription-coordinate",
        resource_group="resource-group-coordinate",
        account_name="example",
        project_name="example",
        location="example-region",
        canary_agent_name="canary-coordinate",
        certificate_path=certificate,
    )
    return capabilities.collect_capability_ledger(
        config,
        project_client=cast(Any, _FakeProjectClient()),
        arm_reader=cast(Any, _FakeReader()),
        foundry_reader=cast(Any, _FakeReader()),
    )


def test_collect_revalidates_tampered_config_before_any_probe() -> None:
    config = capabilities.CapabilityProbeConfig(
        project_endpoint=PROJECT,
        subscription_id="subscription-coordinate",
        resource_group="resource-group-coordinate",
        account_name="example",
        project_name="example",
        location="example-region",
        canary_agent_name="canary-coordinate",
    )
    object.__setattr__(
        config,
        "project_endpoint",
        "https://attacker.example/api/projects/example",
    )

    with pytest.raises(ValueError, match="host must end"):
        capabilities.collect_capability_ledger(
            config,
            project_client=cast(Any, object()),
            arm_reader=cast(Any, object()),
            foundry_reader=cast(Any, object()),
        )


def test_collect_capability_ledger_covers_every_read_only_surface(
    tmp_path: Path,
) -> None:
    ledger = _collect_test_ledger(tmp_path)
    observed = {observation.capability_id: observation for observation in ledger.capabilities}

    assert ledger.status == "pass"
    assert ledger.summary.required_available_count == ledger.summary.required_count
    assert observed["hosted.agents"].observed_status is ProbeStatus.AVAILABLE
    assert observed["memory.stores"].details["configured"] is False
    assert observed["models.gpt-5.6-sol"].details["lifecycleStatus"] == ("GenerallyAvailable")
    assert observed["models.quota"].details["modelsWithPositiveQuota"] == 3
    assert observed["placement.global"].observed_status is ProbeStatus.AVAILABLE
    assert observed["placement.data_zone"].observed_status is ProbeStatus.AVAILABLE
    assert observed["placement.regional"].observed_status is ProbeStatus.UNAVAILABLE
    assert observed["network.private_endpoint"].observed_status is ProbeStatus.AVAILABLE
    assert observed["hosted.responses"].details["certified"] is True
    assert observed["hosted.a2a"].details["fallbackUsed"] is False
    assert observed["hosted.invocations"].observed_status is ProbeStatus.MUTATION_REQUIRED


def test_authenticated_reader_handles_success_and_sanitized_failures() -> None:
    credential = cast(Any, _FakeCredential())

    def success_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(200, json={"value": []})

    success_client = httpx.Client(transport=httpx.MockTransport(success_handler))
    reader = capabilities.AuthenticatedJsonReader(
        credential=credential,
        scope="https://scope.example/.default",
        client=success_client,
    )
    assert reader.get("https://api.example/items") == {"value": []}
    reader.close()
    success_client.close()

    forbidden_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                403,
                json={
                    "error": {
                        "code": "Forbidden",
                        "message": "restricted response text",
                    }
                },
            )
        )
    )
    reader = capabilities.AuthenticatedJsonReader(
        credential=credential,
        scope="https://scope.example/.default",
        client=forbidden_client,
    )
    with pytest.raises(capabilities.ProbeFailure) as caught:
        reader.get("https://api.example/items")
    assert caught.value.status_code == 403
    assert caught.value.error_code is None
    assert "restricted response text" not in str(caught.value)
    forbidden_client.close()

    invalid_client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text="not-json"))
    )
    reader = capabilities.AuthenticatedJsonReader(
        credential=credential,
        scope="https://scope.example/.default",
        client=invalid_client,
    )
    with pytest.raises(capabilities.ProbeFailure):
        reader.get("https://api.example/items")
    invalid_client.close()

    owned_reader = capabilities.AuthenticatedJsonReader(
        credential=credential,
        scope="https://scope.example/.default",
    )
    assert owned_reader._client.follow_redirects is False
    assert owned_reader._client.trust_env is False
    owned_reader.close()


def test_foundry_reader_requires_binding_and_rejects_non_object_json() -> None:
    credential = cast(Any, _FakeCredential())
    with pytest.raises(ValueError, match="bound to a project endpoint"):
        capabilities.AuthenticatedJsonReader(
            credential=credential,
            scope=capabilities.FOUNDRY_SCOPE,
        )

    client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=["not-an-object"]))
    )
    reader = capabilities.AuthenticatedJsonReader(
        credential=credential,
        scope=capabilities.FOUNDRY_SCOPE,
        client=client,
        foundry_project_endpoint=PROJECT,
    )
    with pytest.raises(capabilities.ProbeFailure):
        reader.get(f"{PROJECT}/agents?api-version=v1")
    client.close()


def test_normalization_helpers_fail_closed_on_alternate_and_malformed_shapes() -> None:
    assert capabilities._values({"data": [{"id": "one"}, "invalid"]}) == [{"id": "one"}]
    assert capabilities._values({"data": "invalid"}) == []
    assert capabilities._normalize_capacity("invalid") == {}

    class MappingLike:
        def as_dict(self) -> dict[str, str]:
            return {"status": "ready"}

    class InvalidMappingLike:
        def as_dict(self) -> list[str]:
            return ["invalid"]

    assert capabilities._mapping(MappingLike()) == {"status": "ready"}
    with pytest.raises(capabilities.ProbeFailure):
        capabilities._mapping(InvalidMappingLike())

    status, details = capabilities._error_facts(capabilities.ProbeFailure(status_code=404))
    assert status is ProbeStatus.UNAVAILABLE
    assert details["httpStatus"] == 404


def test_authenticated_reader_rejects_redirect_json_without_following() -> None:
    credential = cast(Any, _FakeCredential())
    paths: list[str] = []

    def redirect_handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/redirect":
            return httpx.Response(
                302,
                headers={"Location": "https://api.example/target"},
                json={"value": [{"redirected": True}]},
            )
        return httpx.Response(200, json={"value": [{"redirected": True}]})

    client = httpx.Client(
        transport=httpx.MockTransport(redirect_handler),
        follow_redirects=True,
    )
    reader = capabilities.AuthenticatedJsonReader(
        credential=credential,
        scope="https://scope.example/.default",
        client=client,
    )

    observation, payload = capabilities._probe_mapping(
        "redirected.capability",
        category="test",
        documented_status="preview",
        probe_mode=ProbeMode.DATA_PLANE,
        required=True,
        loader=lambda: reader.get("https://api.example/redirect"),
        normalizer=lambda value: value,
    )

    assert payload is None
    assert observation.observed_status is ProbeStatus.PROBE_ERROR
    assert observation.details["httpStatus"] == 302
    assert paths == ["/redirect"]
    client.close()


def test_foundry_reader_rejects_attacker_url_before_token_or_http() -> None:
    token_calls = 0
    http_calls = 0

    class Credential:
        def get_token(self, *_: object, **__: object) -> SimpleNamespace:
            nonlocal token_calls
            token_calls += 1
            return SimpleNamespace(token="must-not-be-used")

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        del request
        http_calls += 1
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    reader = capabilities.AuthenticatedJsonReader(
        credential=cast(Any, Credential()),
        scope=capabilities.FOUNDRY_SCOPE,
        client=client,
        foundry_project_endpoint=PROJECT,
    )

    with pytest.raises(ValueError, match="outside the bound project"):
        reader.get("https://attacker.example/api/projects/example/agents")

    assert token_calls == 0
    assert http_calls == 0
    client.close()


def test_probe_failures_and_unconfigured_protocols_are_explicit(
    tmp_path: Path,
) -> None:
    unavailable = capabilities._probe_collection(
        "test.collection",
        category="test",
        documented_status="preview",
        required=False,
        loader=lambda: [],
    )

    def denied() -> list[object]:
        raise ClientAuthenticationError("denied")

    unauthorized = capabilities._probe_collection(
        "test.denied",
        category="test",
        documented_status="preview",
        required=False,
        loader=denied,
    )
    canary = capabilities._observation(
        "hosted.canary_definition",
        category="hosted_runtime",
        documented_status="ga",
        observed_status=ProbeStatus.AVAILABLE,
        probe_mode=ProbeMode.SDK,
        details={"endpointProtocols": ["responses", "a2a"]},
    )
    card = capabilities._observation(
        "hosted.a2a_card",
        category="hosted_runtime",
        documented_status="preview",
        observed_status=ProbeStatus.AVAILABLE,
        probe_mode=ProbeMode.DATA_PLANE,
    )
    protocols = capabilities._protocol_observations(
        canary_observation=canary,
        card_observation=card,
        certificate=capabilities._certificate_evidence(tmp_path / "missing.json"),
    )

    assert unavailable.observed_status is ProbeStatus.AVAILABLE
    assert unavailable.details["configured"] is False
    assert unauthorized.observed_status is ProbeStatus.UNAUTHORIZED
    assert all(
        observation.observed_status is ProbeStatus.NOT_CONFIGURED for observation in protocols
    )


def test_main_writes_a_versioned_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _collect_test_ledger(tmp_path)
    output = tmp_path / "capabilities.json"
    closeable = _Closeable()

    def return_closeable(*_: object, **__: object) -> _Closeable:
        return closeable

    def return_ledger(*_: object, **__: object) -> CapabilityLedger:
        return ledger

    monkeypatch.setattr(capabilities, "DefaultAzureCredential", _FakeCredential)
    monkeypatch.setattr(capabilities, "AIProjectClient", return_closeable)
    monkeypatch.setattr(capabilities, "AuthenticatedJsonReader", return_closeable)
    monkeypatch.setattr(capabilities, "collect_capability_ledger", return_ledger)

    exit_code = capabilities.main(
        [
            "--project-endpoint",
            PROJECT,
            "--subscription-id",
            "subscription-coordinate",
            "--resource-group",
            "resource-group-coordinate",
            "--account-name",
            "example",
            "--project-name",
            "example",
            "--location",
            "example-region",
            "--canary-agent",
            "canary-coordinate",
            "--out",
            str(output),
        ]
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert payload["schemaVersion"] == "1.0"
    assert payload["status"] == "pass"
    assert closeable.closed is True


def test_main_rejects_attacker_endpoint_before_credential_or_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential_factories = 0
    client_factories = 0

    def credential_factory() -> object:
        nonlocal credential_factories
        credential_factories += 1
        raise AssertionError("Credential provider must not be constructed.")

    def client_factory(*args: object, **kwargs: object) -> object:
        nonlocal client_factories
        del args, kwargs
        client_factories += 1
        raise AssertionError("HTTP-capable client must not be constructed.")

    monkeypatch.setattr(capabilities, "DefaultAzureCredential", credential_factory)
    monkeypatch.setattr(capabilities, "AIProjectClient", client_factory)

    with pytest.raises(SystemExit, match="host must end"):
        capabilities.main(
            [
                "--project-endpoint",
                "https://example.services.ai.azure.com.attacker.test/api/projects/example",
                "--subscription-id",
                "subscription-coordinate",
                "--resource-group",
                "resource-group-coordinate",
                "--account-name",
                "example",
                "--project-name",
                "example",
                "--location",
                "example-region",
                "--canary-agent",
                "canary-coordinate",
                "--out",
                str(tmp_path / "capabilities.json"),
            ]
        )

    assert credential_factories == 0
    assert client_factories == 0


def test_config_rejects_valid_but_untrusted_project_coordinates() -> None:
    with pytest.raises(ValueError, match="does not match"):
        capabilities.CapabilityProbeConfig(
            project_endpoint="https://other.services.ai.azure.com/api/projects/example",
            subscription_id="subscription-coordinate",
            resource_group="resource-group-coordinate",
            account_name="example",
            project_name="example",
            location="example-region",
            canary_agent_name="canary-coordinate",
        )
