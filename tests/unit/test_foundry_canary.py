"""Native Foundry A2A canary certification tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from a2a.types import AgentCard
from google.protobuf.json_format import ParseDict

from agent_iq.deployments import foundry_canary

AGENT_ID = "consumer-formulation-rd"
PROJECT = "https://example.services.ai.azure.com/api/projects/test"
ACCOUNT_NAME = "example"
PROJECT_NAME = "test"
AGENT_VERSION = "2"
AGENT_URL = f"{PROJECT}/agents/{AGENT_ID}"
VERSION_URL = f"{AGENT_URL}/versions/{AGENT_VERSION}"
INTERFACE_URL = f"{PROJECT}/agents/{AGENT_ID}/endpoint/protocols/a2a"
ANSWER = (
    "BrightWash cold-water cleaning uses protease, amylase, mannanase, and lipase. "
    "Source: BrightWash Cold active system. [ref_id:0]"
)
SYNTHETIC_TOKEN = "synthetic-runtime-token"
CARD = {
    "name": AGENT_ID,
    "description": "Grounded formulation agent.",
    "version": "1.0.0",
    "supportedInterfaces": [
        {
            "url": INTERFACE_URL,
            "protocolBinding": "JSONRPC",
            "protocolVersion": "1.0",
        }
    ],
    "skills": [
        {
            "id": "formulation",
            "name": "Formulation",
            "description": "Answers formulation questions.",
            "tags": ["brightwash"],
            "examples": ["Which enzymes clean in cold water?"],
        }
    ],
}
PLATFORM_CARD = {
    **CARD,
    "supportedInterfaces": [
        *CARD["supportedInterfaces"],
        {
            "url": INTERFACE_URL,
            "protocolBinding": "JSONRPC",
            "protocolVersion": "0.3",
        },
        {
            "url": INTERFACE_URL,
            "protocolBinding": "HTTP+JSON",
            "protocolVersion": "0.3",
        },
    ],
}


def test_trusted_invocation_card_accepts_platform_protocol_variants() -> None:
    card = ParseDict(PLATFORM_CARD, AgentCard())

    trusted = foundry_canary._trusted_invocation_card(
        card,
        PLATFORM_CARD,
        expected_agent_id=AGENT_ID,
        expected_interface_url=INTERFACE_URL,
    )

    assert len(trusted.supported_interfaces) == 1
    assert trusted.supported_interfaces[0].protocol_binding == "JSONRPC"
    assert trusted.supported_interfaces[0].protocol_version == "1.0"


def test_trusted_invocation_card_rejects_unknown_platform_interface() -> None:
    payload = {
        **PLATFORM_CARD,
        "supportedInterfaces": [
            *PLATFORM_CARD["supportedInterfaces"],
            {
                "url": INTERFACE_URL,
                "protocolBinding": "GRPC",
                "protocolVersion": "1.0",
            },
        ],
    }
    card = ParseDict(payload, AgentCard())

    with pytest.raises(foundry_canary.CanaryProbeError, match="unsupported protocol"):
        foundry_canary._trusted_invocation_card(
            card,
            payload,
            expected_agent_id=AGENT_ID,
            expected_interface_url=INTERFACE_URL,
        )


def _set_adversarial_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.setenv(name, "http://proxy.attacker.test:8080")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")


def _capture_async_client_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    real_async_client = httpx.AsyncClient
    configurations: list[dict[str, Any]] = []

    def isolated_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        configurations.append(dict(kwargs))
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", isolated_client)
    return configurations


class FakeResolver:
    http: httpx.AsyncClient
    base: str
    path: str

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        base_url: str,
        agent_card_path: str,
    ) -> None:
        self.http = httpx_client
        self.base = base_url
        self.path = agent_card_path

    async def get_agent_card(self) -> object:
        response = await self.http.get(f"{self.base}/{self.path}")
        response.raise_for_status()
        return ParseDict(response.json(), AgentCard())


class FakeClient:
    def __init__(self, http: httpx.AsyncClient, interface_url: str) -> None:
        self.http = http
        self.interface_url = interface_url
        self.closed = False

    async def send_message(self, request: object) -> Any:
        del request
        response = await self.http.post(
            self.interface_url,
            json={},
        )
        response.raise_for_status()
        yield object()

    async def close(self) -> None:
        self.closed = True
        await self.http.aclose()


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    card_path = tmp_path / "agent-card.json"
    card_path.write_text(
        json.dumps({key: CARD[key] for key in ("description", "version", "skills")})
    )
    build_path = tmp_path / ".agent-iq-build.json"
    build_path.write_text(
        json.dumps({"buildHash": "a" * 64, "sourceSampleCommit": "b" * 40}),
        encoding="utf-8",
    )
    return card_path, build_path


def _active_agent_details() -> dict[str, Any]:
    return {
        "agent_endpoint": {
            "version_selector": {
                "version_selection_rules": [
                    {
                        "type": "FixedRatio",
                        "agent_version": AGENT_VERSION,
                        "traffic_percentage": 100,
                    }
                ]
            }
        }
    }


def _active_agent_version() -> dict[str, Any]:
    return {
        "version": AGENT_VERSION,
        "status": "active",
        "instance_identity": {"principal_id": "identity"},
        "definition": {
            "kind": "hosted",
            "protocol_versions": [
                {
                    "protocol": "responses",
                    "version": "2.0.0",
                }
            ],
            "environment_variables": {
                "AZURE_AI_MODEL_DEPLOYMENT_NAME": "gpt-5.4-mini",
                "TOOLBOX_NAME": f"{AGENT_ID}-knowledge",
            },
            "code_configuration": {
                "content_hash": "content",
                "runtime": "python_3_13",
            },
        },
    }


def test_probe_certifies_native_grounded_answer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card_path, build_path = _write_inputs(tmp_path)
    authorization_headers: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        authorization_headers.append(request.headers["Authorization"])
        if request.url.path == f"/api/projects/test/agents/{AGENT_ID}":
            return httpx.Response(200, json=_active_agent_details())
        if request.url.path.endswith(f"/versions/{AGENT_VERSION}"):
            return httpx.Response(200, json=_active_agent_version())
        if request.url.path.endswith("/agentCard/v1.0"):
            return httpx.Response(200, json=CARD)
        return httpx.Response(200, json={})

    async def fake_create_client(card: object, client_config: Any) -> FakeClient:
        assert isinstance(card, AgentCard)
        assert len(card.supported_interfaces) == 1
        return FakeClient(client_config.httpx_client, card.supported_interfaces[0].url)

    monkeypatch.setattr(foundry_canary, "A2ACardResolver", FakeResolver)
    monkeypatch.setattr(foundry_canary, "create_client", fake_create_client)
    monkeypatch.setattr(foundry_canary, "_event_text", lambda event: ANSWER)

    result = asyncio.run(
        foundry_canary.probe_canary(
            project_endpoint=PROJECT,
            account_name=ACCOUNT_NAME,
            project_name=PROJECT_NAME,
            agent_id=AGENT_ID,
            agent_version=AGENT_VERSION,
            token=SYNTHETIC_TOKEN,
            canonical_card_path=card_path,
            build_record_path=build_path,
            transport=httpx.MockTransport(handler),
        )
    )

    assert result["status"] == "pass"
    assert result["agent"]["version"] == AGENT_VERSION
    assert result["agent"]["imageDigestStatus"] == "not_exposed_for_code_deploy"
    assert result["card"]["matchesCanonical"] is True
    assert result["transport"]["fallbackUsed"] is False
    assert result["invocation"]["missingTerms"] == []
    assert result["invocation"]["missingSources"] == []
    assert {event["operation"] for event in result["trace"]["events"]} == {
        "foundry.agent.get",
        "foundry.agent.version.get",
        "foundry.a2a.card.fetch",
        "foundry.a2a.message.send",
    }
    trace_text = json.dumps(result["trace"])
    assert SYNTHETIC_TOKEN not in trace_text
    assert foundry_canary.DEFAULT_PROMPT not in trace_text
    for header in authorization_headers:
        scheme, value = header.split(" ", maxsplit=1)
        assert scheme == "Bearer"
        assert value == SYNTHETIC_TOKEN


def test_probe_owned_client_bypasses_environment_proxies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card_path, build_path = _write_inputs(tmp_path)
    _set_adversarial_proxy_environment(monkeypatch)
    configurations = _capture_async_client_configuration(monkeypatch)
    direct_requests: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        direct_requests.append((request.method, request.url.path))
        if request.url.path == f"/api/projects/test/agents/{AGENT_ID}":
            return httpx.Response(200, json=_active_agent_details())
        if request.url.path.endswith(f"/versions/{AGENT_VERSION}"):
            return httpx.Response(200, json=_active_agent_version())
        if request.url.path.endswith("/agentCard/v1.0"):
            return httpx.Response(200, json=CARD)
        return httpx.Response(200, json={})

    async def fake_create_client(card: object, client_config: Any) -> FakeClient:
        assert isinstance(card, AgentCard)
        return FakeClient(client_config.httpx_client, card.supported_interfaces[0].url)

    monkeypatch.setattr(foundry_canary, "A2ACardResolver", FakeResolver)
    monkeypatch.setattr(foundry_canary, "create_client", fake_create_client)
    monkeypatch.setattr(foundry_canary, "_event_text", lambda event: ANSWER)

    result = asyncio.run(
        foundry_canary.probe_canary(
            project_endpoint=PROJECT,
            account_name=ACCOUNT_NAME,
            project_name=PROJECT_NAME,
            agent_id=AGENT_ID,
            agent_version=AGENT_VERSION,
            token=SYNTHETIC_TOKEN,
            canonical_card_path=card_path,
            build_record_path=build_path,
            transport=httpx.MockTransport(handler),
        )
    )

    assert result["status"] == "pass"
    assert [method for method, _path in direct_requests] == [
        "GET",
        "GET",
        "GET",
        "POST",
        "GET",
    ]
    assert len(configurations) == 1
    assert configurations[0]["trust_env"] is False
    assert configurations[0]["follow_redirects"] is False


def test_probe_rejects_traffic_drift_after_native_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card_path, build_path = _write_inputs(tmp_path)
    agent_reads = 0
    invocation_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal agent_reads, invocation_count
        if request.url.path == f"/api/projects/test/agents/{AGENT_ID}":
            agent_reads += 1
            if agent_reads == 1:
                return httpx.Response(200, json=_active_agent_details())
            drifted = _active_agent_details()
            rules = drifted["agent_endpoint"]["version_selector"]["version_selection_rules"]
            rules[0]["agent_version"] = "3"
            return httpx.Response(200, json=drifted)
        if request.url.path.endswith(f"/versions/{AGENT_VERSION}"):
            return httpx.Response(200, json=_active_agent_version())
        if request.url.path.endswith("/agentCard/v1.0"):
            return httpx.Response(200, json=CARD)
        invocation_count += 1
        return httpx.Response(200, json={})

    async def fake_create_client(card: object, client_config: Any) -> FakeClient:
        assert isinstance(card, AgentCard)
        return FakeClient(client_config.httpx_client, card.supported_interfaces[0].url)

    monkeypatch.setattr(foundry_canary, "A2ACardResolver", FakeResolver)
    monkeypatch.setattr(foundry_canary, "create_client", fake_create_client)
    monkeypatch.setattr(foundry_canary, "_event_text", lambda event: ANSWER)

    with pytest.raises(foundry_canary.CanaryProbeError, match="not bound 100%"):
        asyncio.run(
            foundry_canary.probe_canary(
                project_endpoint=PROJECT,
                account_name=ACCOUNT_NAME,
                project_name=PROJECT_NAME,
                agent_id=AGENT_ID,
                agent_version=AGENT_VERSION,
                token=SYNTHETIC_TOKEN,
                canonical_card_path=card_path,
                build_record_path=build_path,
                transport=httpx.MockTransport(handler),
            )
        )

    assert agent_reads == 2
    assert invocation_count == 1


def test_probe_reports_card_and_grounding_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card_path, _ = _write_inputs(tmp_path)
    bad_card = {**CARD, "version": "0.3.0"}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/api/projects/test/agents/{AGENT_ID}":
            return httpx.Response(200, json=_active_agent_details())
        if request.url.path.endswith(f"/versions/{AGENT_VERSION}"):
            return httpx.Response(
                200,
                json={
                    "version": AGENT_VERSION,
                    "status": "failed",
                    "definition": {"code_configuration": {}},
                },
            )
        if request.url.path.endswith("/agentCard/v1.0"):
            return httpx.Response(200, json=bad_card)
        return httpx.Response(200, json={})

    async def fake_create_client(card: object, client_config: Any) -> FakeClient:
        assert isinstance(card, AgentCard)
        return FakeClient(client_config.httpx_client, card.supported_interfaces[0].url)

    monkeypatch.setattr(foundry_canary, "A2ACardResolver", FakeResolver)
    monkeypatch.setattr(foundry_canary, "create_client", fake_create_client)
    monkeypatch.setattr(foundry_canary, "_event_text", lambda event: "Unsupported answer")

    result = asyncio.run(
        foundry_canary.probe_canary(
            project_endpoint=PROJECT,
            account_name=ACCOUNT_NAME,
            project_name=PROJECT_NAME,
            agent_id=AGENT_ID,
            agent_version=AGENT_VERSION,
            token=SYNTHETIC_TOKEN,
            canonical_card_path=card_path,
            transport=httpx.MockTransport(handler),
        )
    )

    assert result["status"] == "fail"
    assert "agentActive" in result["failures"]
    assert "cardVersionIsV1" in result["failures"]
    assert "requiredTermsPresent" in result["failures"]
    assert "sourceTitlePolicySatisfied" in result["failures"]
    assert "sourceReferencePresent" in result["failures"]


def test_probe_rejects_attacker_endpoint_before_http(tmp_path: Path) -> None:
    card_path, _ = _write_inputs(tmp_path)
    http_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        del request
        http_calls += 1
        return httpx.Response(200, json={})

    with pytest.raises(foundry_canary.CanaryProbeError, match="host must end"):
        asyncio.run(
            foundry_canary.probe_canary(
                project_endpoint=(
                    "https://example.services.ai.azure.com.attacker.test/api/projects/test"
                ),
                account_name=ACCOUNT_NAME,
                project_name=PROJECT_NAME,
                agent_id=AGENT_ID,
                agent_version=AGENT_VERSION,
                token=SYNTHETIC_TOKEN,
                canonical_card_path=card_path,
                transport=httpx.MockTransport(handler),
            )
        )

    assert http_calls == 0


MALICIOUS_INTERFACE_URLS = [
    pytest.param(
        f"https://attacker.example/api/projects/test/agents/{AGENT_ID}/endpoint/protocols/a2a",
        id="arbitrary-host",
    ),
    pytest.param(
        f"https://user@example.services.ai.azure.com/api/projects/test/agents/{AGENT_ID}"
        "/endpoint/protocols/a2a",
        id="userinfo",
    ),
    pytest.param(f"{INTERFACE_URL.replace('/api/', ':8443/api/')}", id="port"),
    pytest.param(
        f"https://example%2eservices.ai.azure.com/api/projects/test/agents/{AGENT_ID}"
        "/endpoint/protocols/a2a",
        id="encoded-host",
    ),
    pytest.param(f"{INTERFACE_URL}%2f..%2fother", id="encoded-path"),
    pytest.param(
        f"{PROJECT}/agents/other-agent/endpoint/protocols/a2a",
        id="wrong-agent",
    ),
    pytest.param(f"{INTERFACE_URL}/agentCard/v1.0", id="wrong-path"),
    pytest.param(f"{INTERFACE_URL}?target=https://attacker.example", id="query"),
    pytest.param(f"{INTERFACE_URL}#https://attacker.example", id="fragment"),
    pytest.param(
        f"https://example.services.ai.azure.com\\@attacker.example/api/projects/test/agents/"
        f"{AGENT_ID}/endpoint/protocols/a2a",
        id="backslash-authority",
    ),
    pytest.param(f"{INTERFACE_URL}\n", id="control-character"),
]


@pytest.mark.parametrize("interface_url", MALICIOUS_INTERFACE_URLS)
def test_bearer_auth_rejects_unapproved_target_before_token_or_http(
    interface_url: str,
) -> None:
    token_calls = 0
    http_calls = 0

    def token_provider() -> str:
        nonlocal token_calls
        token_calls += 1
        return SYNTHETIC_TOKEN

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        del request
        http_calls += 1
        return httpx.Response(200)

    async def send() -> None:
        allowed = foundry_canary._allowed_foundry_requests(
            f"{AGENT_URL}?api-version=v1",
            f"{VERSION_URL}?api-version=v1",
            INTERFACE_URL,
            f"{INTERFACE_URL}/{foundry_canary.CARD_PATH}",
        )
        async with httpx.AsyncClient(
            auth=foundry_canary._FoundryBearerAuth(token_provider, allowed),
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
            trust_env=False,
        ) as http:
            await http.post(interface_url)

    with pytest.raises((foundry_canary.CanaryProbeError, httpx.InvalidURL)):
        asyncio.run(send())

    assert token_calls == 0
    assert http_calls == 0


@pytest.mark.parametrize("interface_url", MALICIOUS_INTERFACE_URLS)
def test_probe_rejects_malicious_card_before_invocation(
    interface_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card_path, _ = _write_inputs(tmp_path)
    malicious_card = {
        **CARD,
        "supportedInterfaces": [
            {
                "url": interface_url,
                "protocolBinding": "JSONRPC",
                "protocolVersion": "1.0",
            }
        ],
    }
    token_calls = 0
    requests: list[tuple[str, str]] = []
    client_calls = 0

    def token_provider() -> str:
        nonlocal token_calls
        token_calls += 1
        return SYNTHETIC_TOKEN

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, str(request.url)))
        if request.url.path == f"/api/projects/test/agents/{AGENT_ID}":
            return httpx.Response(200, json=_active_agent_details())
        if request.url.path.endswith(f"/versions/{AGENT_VERSION}"):
            return httpx.Response(200, json=_active_agent_version())
        if request.url.path.endswith("/agentCard/v1.0"):
            return httpx.Response(200, json=malicious_card)
        raise AssertionError("No invocation request may be sent for a malicious Agent Card.")

    async def fake_create_client(card: object, client_config: Any) -> FakeClient:
        nonlocal client_calls
        del card, client_config
        client_calls += 1
        raise AssertionError("The A2A client must not be created for a malicious Agent Card.")

    monkeypatch.setattr(foundry_canary, "A2ACardResolver", FakeResolver)
    monkeypatch.setattr(foundry_canary, "create_client", fake_create_client)

    with pytest.raises(foundry_canary.CanaryProbeError, match="supportedInterfaces URL"):
        asyncio.run(
            foundry_canary.probe_canary(
                project_endpoint=PROJECT,
                account_name=ACCOUNT_NAME,
                project_name=PROJECT_NAME,
                agent_id=AGENT_ID,
                agent_version=AGENT_VERSION,
                canonical_card_path=card_path,
                token_provider=token_provider,
                transport=httpx.MockTransport(handler),
            )
        )

    assert token_calls == 3
    assert [method for method, _url in requests] == ["GET", "GET", "GET"]
    assert client_calls == 0


@pytest.mark.parametrize("status_code", [302, 307, 401, 503])
def test_probe_fails_redirects_and_non_success_without_following(
    status_code: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card_path, _ = _write_inputs(tmp_path)
    requests: list[str] = []
    token_calls = 0

    def token_provider() -> str:
        nonlocal token_calls
        token_calls += 1
        return SYNTHETIC_TOKEN

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == f"/api/projects/test/agents/{AGENT_ID}":
            return httpx.Response(200, json=_active_agent_details())
        if request.url.path.endswith(f"/versions/{AGENT_VERSION}"):
            return httpx.Response(200, json=_active_agent_version())
        if request.url.path.endswith("/agentCard/v1.0"):
            return httpx.Response(200, json=CARD)
        if str(request.url) == INTERFACE_URL:
            return httpx.Response(
                status_code,
                headers={"Location": "https://attacker.example/collect"},
            )
        raise AssertionError("Redirect target must never receive an HTTP request.")

    async def fake_create_client(card: object, client_config: Any) -> FakeClient:
        assert isinstance(card, AgentCard)
        return FakeClient(client_config.httpx_client, card.supported_interfaces[0].url)

    monkeypatch.setattr(foundry_canary, "A2ACardResolver", FakeResolver)
    monkeypatch.setattr(foundry_canary, "create_client", fake_create_client)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(
            foundry_canary.probe_canary(
                project_endpoint=PROJECT,
                account_name=ACCOUNT_NAME,
                project_name=PROJECT_NAME,
                agent_id=AGENT_ID,
                agent_version=AGENT_VERSION,
                canonical_card_path=card_path,
                token_provider=token_provider,
                transport=httpx.MockTransport(handler),
            )
        )

    assert requests[-1] == INTERFACE_URL
    assert "https://attacker.example/collect" not in requests
    assert token_calls == 4


def test_card_projection_and_metadata_validation() -> None:
    assert foundry_canary.card_projection(CARD)["skills"][0]["id"] == "formulation"
    with pytest.raises(foundry_canary.CanaryProbeError, match="skills"):
        foundry_canary.card_projection({})
    with pytest.raises(foundry_canary.CanaryProbeError, match="skill"):
        foundry_canary.card_projection({"skills": ["bad"]})
    with pytest.raises(foundry_canary.CanaryProbeError, match="missing"):
        foundry_canary._agent_metadata({}, AGENT_ID, AGENT_VERSION)
    with pytest.raises(foundry_canary.CanaryProbeError, match="not expected"):
        foundry_canary._agent_metadata({"version": "3"}, AGENT_ID, AGENT_VERSION)
    with pytest.raises(foundry_canary.CanaryProbeError, match="exactly one"):
        foundry_canary._assert_exact_version_traffic({}, AGENT_VERSION)


@pytest.mark.parametrize("percentage", [100.0, "100", True, False])
def test_exact_version_traffic_rejects_noninteger_percentages(percentage: object) -> None:
    payload = _active_agent_details()
    rules = payload["agent_endpoint"]["version_selector"]["version_selection_rules"]
    rules[0]["traffic_percentage"] = percentage

    with pytest.raises(foundry_canary.CanaryProbeError, match="not bound 100%"):
        foundry_canary._assert_exact_version_traffic(payload, AGENT_VERSION)


def test_container_metadata_preserves_logical_agent_and_image_digest() -> None:
    deployment_id = "consumer-formulation-rd-host-canary"
    digest = f"sha256:{'c' * 64}"
    image = f"example.azurecr.io/agent-iq/host@{digest}"
    metadata = foundry_canary._agent_metadata(
        {
            "version": "7",
            "status": "active",
            "metadata": {
                "agentIqLogicalAgentId": AGENT_ID,
                "agentIqRuntimeFamily": "langgraph",
                "agentIqDescriptorSha256": f"sha256:{'d' * 64}",
            },
            "instance_identity": {"principal_id": "identity"},
            "definition": {
                "kind": "hosted",
                "protocol_versions": [
                    {
                        "protocol": "responses",
                        "version": "2.0.0",
                    }
                ],
                "environment_variables": {
                    "AZURE_AI_MODEL_DEPLOYMENT_NAME": "gpt-5.4-mini",
                    "TOOLBOX_NAME": f"{AGENT_ID}-knowledge",
                    "AIQ_HOST_LOGICAL_AGENT_ID": AGENT_ID,
                    "AIQ_HOST_RUNTIME_FAMILY": "langgraph",
                    "AIQ_HOST_DESCRIPTOR_SHA256": f"sha256:{'d' * 64}",
                },
                "container_configuration": {"image": image},
            },
        },
        deployment_id,
        "7",
    )

    assert metadata["deploymentMode"] == "container"
    assert metadata["logicalAgentId"] == AGENT_ID
    assert metadata["toolboxName"] == f"{AGENT_ID}-knowledge"
    assert metadata["image"] == image
    assert metadata["imageDigest"] == digest
    assert metadata["imageDigestStatus"] == "pinned_in_image_reference"


def test_source_title_matching_tolerates_citation_rendering() -> None:
    assert foundry_canary.source_title_present(
        "BrightWash Cold active system",
        "Source: BrightWash Cold active system [ref_id:0]",
    )
    assert foundry_canary.source_title_present(
        "Brand equity & purpose-led campaigns",
        "Brand equity and purpose-led campaigns [ref_id:1]",
    )
    assert not foundry_canary.source_title_present(
        "ClearWave cold-water enzyme system",
        "Source: BrightWash Cold active system [ref_id:0]",
    )
    assert foundry_canary.required_term_present(
        "OTIF|on-time-in-full",
        "Protect on-time-in-full service.",
    )
    assert not foundry_canary.required_term_present("OTIF|on-time-in-full", "Lower cost.")


def test_event_text_handles_all_a2a_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(foundry_canary, "get_message_text", lambda value, delimiter: value)
    monkeypatch.setattr(foundry_canary, "get_artifact_text", lambda value, delimiter: value)

    def event(which: str, **values: Any) -> Any:
        return SimpleNamespace(WhichOneof=lambda name: which, **values)

    assert foundry_canary._event_text(event("message", message="message")) == "message"
    task = SimpleNamespace(
        artifacts=["a", "b"],
        status=SimpleNamespace(HasField=lambda name: False),
    )
    assert foundry_canary._event_text(event("task", task=task)) == "a b"
    empty_task = SimpleNamespace(
        artifacts=[],
        status=SimpleNamespace(
            HasField=lambda name: True,
            message="status",
        ),
    )
    assert foundry_canary._event_text(event("task", task=empty_task)) == "status"
    update = SimpleNamespace(artifact="artifact")
    assert (
        foundry_canary._event_text(event("artifact_update", artifact_update=update)) == "artifact"
    )
    status = SimpleNamespace(status=empty_task.status)
    assert foundry_canary._event_text(event("status_update", status_update=status)) == "status"
    assert foundry_canary._event_text(event("unknown")) == ""


def test_cli_writes_result_and_returns_probe_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card_path, build_path = _write_inputs(tmp_path)
    output = tmp_path / "result.json"
    acquired_token = "synthetic-acquired-token"
    credential = SimpleNamespace(
        get_token=lambda scope: SimpleNamespace(token=acquired_token),
        close=lambda: None,
    )
    raw_answer = "SENTINEL_RAW_ANSWER"
    raw_prompt = "SENTINEL_RAW_PROMPT"
    raw_source = "SENTINEL_RAW_SOURCE_MARKDOWN"
    result: dict[str, Any] = {
        "schemaVersion": "1.0",
        "status": "pass",
        "agent": {"version": "2"},
        "card": {"servedHash": "sha256:" + ("c" * 64)},
        "invocation": {
            "answer": raw_answer,
            "prompt": raw_prompt,
            "requiredTerms": ["term"],
            "missingTerms": [],
            "requiredSources": [raw_source],
            "missingSources": [],
            "sourceTitleMode": "required",
        },
        "trace": {"events": [], "payloadsCaptured": False},
        "checks": {"answerNonempty": True},
    }

    async def fake_probe(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["token_provider"]() == acquired_token
        return result

    monkeypatch.setattr(
        foundry_canary,
        "DefaultAzureCredential",
        lambda **kwargs: credential,
    )
    monkeypatch.setattr(foundry_canary, "probe_canary", fake_probe)

    assert (
        foundry_canary.cli_probe(
            [
                "--project-endpoint",
                PROJECT,
                "--account-name",
                ACCOUNT_NAME,
                "--project-name",
                PROJECT_NAME,
                "--agent-version",
                AGENT_VERSION,
                "--canonical-card",
                str(card_path),
                "--build-record",
                str(build_path),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    report_text = output.read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert report["invocation"]["answerHash"] == (
        f"sha256:{hashlib.sha256(raw_answer.encode()).hexdigest()}"
    )
    assert report["invocation"]["answerLength"] == len(raw_answer)
    assert report["invocation"]["requiredSourceCount"] == 1
    assert raw_answer not in report_text
    assert raw_prompt not in report_text
    assert raw_source not in report_text
    assert not list(tmp_path.glob("*.tmp"))


def test_cli_rejects_attacker_endpoint_before_credential_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card_path, _ = _write_inputs(tmp_path)
    credential_factories = 0
    probe_calls = 0

    def credential_factory() -> object:
        nonlocal credential_factories
        credential_factories += 1
        raise AssertionError("Credential provider must not be constructed.")

    async def fake_probe(**kwargs: Any) -> dict[str, Any]:
        nonlocal probe_calls
        del kwargs
        probe_calls += 1
        return {}

    monkeypatch.setattr(foundry_canary, "DefaultAzureCredential", credential_factory)
    monkeypatch.setattr(foundry_canary, "probe_canary", fake_probe)

    with pytest.raises(SystemExit):
        foundry_canary.cli_probe(
            [
                "--project-endpoint",
                "https://attacker.example/api/projects/test",
                "--account-name",
                ACCOUNT_NAME,
                "--project-name",
                PROJECT_NAME,
                "--agent-version",
                AGENT_VERSION,
                "--canonical-card",
                str(card_path),
                "--output",
                str(tmp_path / "result.json"),
            ]
        )

    assert credential_factories == 0
    assert probe_calls == 0


def test_probe_rejects_valid_but_untrusted_project_before_token_or_http(
    tmp_path: Path,
) -> None:
    card_path, _ = _write_inputs(tmp_path)
    token_calls = 0
    http_calls = 0

    def token_provider() -> str:
        nonlocal token_calls
        token_calls += 1
        return SYNTHETIC_TOKEN

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        del request
        http_calls += 1
        return httpx.Response(200, json={})

    with pytest.raises(foundry_canary.CanaryProbeError, match="does not match"):
        asyncio.run(
            foundry_canary.probe_canary(
                project_endpoint="https://other.services.ai.azure.com/api/projects/test",
                account_name=ACCOUNT_NAME,
                project_name=PROJECT_NAME,
                agent_id=AGENT_ID,
                agent_version=AGENT_VERSION,
                token_provider=token_provider,
                canonical_card_path=card_path,
                transport=httpx.MockTransport(handler),
            )
        )

    assert token_calls == 0
    assert http_calls == 0
