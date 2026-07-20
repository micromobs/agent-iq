"""Conformance tests for Foundry catalog access and native A2A invocation."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, cast

import httpx
import pytest
from a2a.client.errors import A2AClientError, AgentCardResolutionError
from a2a.utils.errors import InternalError
from azure.identity import CredentialUnavailableError
from google.protobuf.json_format import ParseError

import agent_iq.catalog.adapters.foundry as foundry_adapter_module
import agent_iq.runtime.adapters.native_a2a as native_a2a_module
from agent_iq.catalog import (
    FoundryCatalogAdapter,
    foundry_card_projection,
    load_consumer_assets,
)
from agent_iq.contracts import ErrorCode, HealthStatus
from agent_iq.errors import AgentIqOperationError
from agent_iq.runtime import (
    IdempotencyPolicy,
    MemoryRuntimeTelemetry,
    NativeA2ARequest,
    NativeA2ARuntime,
)

PROJECT = "https://example.services.ai.azure.com/api/projects/test"
AGENT_ID = "consumer-formulation-rd"
ANSWER = "Grounded native A2A answer. [ref_id:0]"
CARD = {
    "name": AGENT_ID,
    "description": "Grounded formulation agent.",
    "version": "1.0.0",
    "skills": [],
}


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


class FakeTokenProvider:
    def __init__(self) -> None:
        self.scopes: list[str] = []
        self.closed = False

    async def get_token(self, scope: str) -> str:
        self.scopes.append(scope)
        return "test-token"

    async def close(self) -> None:
        self.closed = True


class FakeResolver:
    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        base_url: str,
        agent_card_path: str,
    ) -> None:
        self.http = httpx_client
        self.url = f"{base_url}/{agent_card_path}"

    async def get_agent_card(self) -> Mapping[str, Any]:
        response = await self.http.get(self.url)
        response.raise_for_status()
        return cast(Mapping[str, Any], response.json())


class FakeClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        sent_message_ids: list[str],
        sent_prompts: list[str] | None = None,
        sent_context_ids: list[str] | None = None,
        *,
        delay_seconds: float = 0,
    ) -> None:
        self.http = http
        self.sent_message_ids = sent_message_ids
        self.sent_prompts = sent_prompts
        self.sent_context_ids = sent_context_ids
        self.delay_seconds = delay_seconds
        self.closed = False

    async def send_message(self, request: Any) -> Any:
        self.sent_message_ids.append(request.message.message_id)
        if self.sent_prompts is not None:
            self.sent_prompts.append(request.message.parts[0].text)
        if self.sent_context_ids is not None:
            self.sent_context_ids.append(request.message.context_id)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        response = await self.http.post(
            f"{PROJECT}/agents/{AGENT_ID}/endpoint/protocols/a2a",
            json={},
        )
        response.raise_for_status()
        yield object()

    async def close(self) -> None:
        self.closed = True


def _agent(agent_id: str = AGENT_ID, *, status: str = "active") -> dict[str, Any]:
    return {
        "id": agent_id,
        "status": status,
        # Inventory "latest" is mutable and intentionally untrusted.
        "versions": {
            "latest": {
                "version": "mutable-latest-must-be-ignored",
            }
        },
    }


def _agent_details(
    agent_id: str = AGENT_ID,
    *,
    version: str = "2",
    traffic_percentage: object = 100,
) -> dict[str, Any]:
    return {
        "id": agent_id,
        "agentEndpoint": {
            "versionSelector": {
                "versionSelectionRules": [
                    {
                        "type": "FixedRatio",
                        "agentVersion": version,
                        "trafficPercentage": traffic_percentage,
                    }
                ]
            }
        },
    }


def _agent_version(
    agent_id: str = AGENT_ID,
    *,
    version: str = "2",
    status: str = "active",
) -> dict[str, Any]:
    return {
        "id": agent_id,
        "version": version,
        "status": status,
        "definition": {
            "kind": "hosted",
            "protocol_versions": [{"protocol": "responses", "version": "2.0.0"}],
            "environment_variables": {
                "AZURE_AI_MODEL_DEPLOYMENT_NAME": "gpt-5.4-mini",
                "TOOLBOX_NAME": f"{agent_id}-knowledge",
            },
            "code_configuration": {
                "content_hash": "abc123",
                "runtime": "python_3_13",
            },
        },
    }


def _inventory_response(*agents: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"data": list(agents), "has_more": False})


def _metadata_response(
    request: httpx.Request,
    *agent_ids: str,
    status: str = "active",
) -> httpx.Response | None:
    for agent_id in agent_ids or (AGENT_ID,):
        if request.url.path.endswith(f"/agents/{agent_id}/versions/2"):
            return httpx.Response(200, json=_agent_version(agent_id, status=status))
        if request.url.path.endswith(f"/agents/{agent_id}"):
            return httpx.Response(200, json=_agent_details(agent_id))
    return None


def _standard_foundry_response(
    request: httpx.Request,
    *,
    status: str = "active",
    card: Mapping[str, Any] = CARD,
) -> httpx.Response | None:
    if request.url.path.endswith("/agents"):
        return _inventory_response(_agent())
    metadata = _metadata_response(request, status=status)
    if metadata is not None:
        return metadata
    if request.method == "GET":
        return httpx.Response(200, json=card)
    return None


def _catalog_handler(request: httpx.Request, *, status: str = "active") -> httpx.Response:
    response = _standard_foundry_response(request, status=status)
    if response is None:
        raise AssertionError(f"Unexpected Foundry request: {request.method} {request.url}")
    return response


def _runtime(
    handler: httpx.MockTransport,
    *,
    telemetry: MemoryRuntimeTelemetry | None = None,
    sent_message_ids: list[str] | None = None,
    sent_prompts: list[str] | None = None,
    sent_context_ids: list[str] | None = None,
    client_delay_seconds: float = 0,
) -> NativeA2ARuntime:
    ids = sent_message_ids if sent_message_ids is not None else []

    async def client_factory(card: object, client_config: Any) -> FakeClient:
        del card
        return FakeClient(
            client_config.httpx_client,
            ids,
            sent_prompts,
            sent_context_ids,
            delay_seconds=client_delay_seconds,
        )

    return NativeA2ARuntime(
        PROJECT,
        token_provider=FakeTokenProvider(),
        transport=handler,
        telemetry=telemetry,
        resolver_factory=FakeResolver,
        client_factory=client_factory,
        card_mapper=lambda card: card,
        event_text=lambda event: ANSWER,
    )


def test_catalog_paginates_and_caches() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.headers["Authorization"] == "Bearer test-token"
        metadata = _metadata_response(request, "consumer-one", "consumer-two")
        if metadata is not None:
            return metadata
        after = request.url.params.get("after")
        if after is None:
            return httpx.Response(
                200,
                json={
                    "data": [_agent("consumer-one")],
                    "has_more": True,
                    "last_id": "consumer-one",
                },
            )
        assert after == "consumer-one"
        return _inventory_response(_agent("consumer-two"))

    async def run() -> None:
        adapter = FoundryCatalogAdapter(
            PROJECT,
            token_provider=FakeTokenProvider(),
            transport=httpx.MockTransport(handler),
        )
        first = await adapter.list_agents()
        second = await adapter.list_agents()
        assert [agent.agent_id for agent in first] == ["consumer-one", "consumer-two"]
        assert first == second
        assert len(calls) == 6
        await adapter.close()

    asyncio.run(run())


def test_catalog_inventory_does_not_resolve_mutable_versions() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        after = request.url.params.get("after")
        if after is None:
            return httpx.Response(
                200,
                json={
                    "data": [_agent("consumer-one")],
                    "has_more": True,
                    "last_id": "consumer-one",
                },
            )
        assert after == "consumer-one"
        return _inventory_response(_agent("consumer-two", status="disabled"))

    async def run() -> None:
        adapter = FoundryCatalogAdapter(
            PROJECT,
            token_provider=FakeTokenProvider(),
            transport=httpx.MockTransport(handler),
        )
        first = await adapter.list_inventory()
        second = await adapter.list_inventory()
        assert [(item.agent_id, item.status) for item in first] == [
            ("consumer-one", "active"),
            ("consumer-two", "disabled"),
        ]
        assert first == second
        await adapter.close()

    asyncio.run(run())

    assert calls == [
        f"{PROJECT}/agents?api-version=v1&limit=100",
        f"{PROJECT}/agents?api-version=v1&limit=100&after=consumer-one",
    ]


@pytest.mark.parametrize("refresh", [False, True])
def test_catalog_resolves_one_agent_without_enumerating_unrelated_agents(
    refresh: bool,
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        metadata = _metadata_response(request)
        if metadata is not None:
            return metadata
        raise AssertionError(f"Unexpected Foundry request: {request.method} {request.url}")

    async def run() -> None:
        adapter = FoundryCatalogAdapter(
            PROJECT,
            token_provider=FakeTokenProvider(),
            transport=httpx.MockTransport(handler),
        )
        agent = await adapter.get_agent(AGENT_ID, refresh=refresh)
        assert agent.agent_id == AGENT_ID
        assert agent.version == "2"
        await adapter.close()

    asyncio.run(run())

    assert calls == [
        f"{PROJECT}/agents/{AGENT_ID}?api-version=v1",
        f"{PROJECT}/agents/{AGENT_ID}/versions/2?api-version=v1",
    ]


def test_catalog_owned_client_bypasses_environment_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_adversarial_proxy_environment(monkeypatch)
    configurations = _capture_async_client_configuration(monkeypatch)
    direct_requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        direct_requests.append(str(request.url))
        return _catalog_handler(request)

    async def run() -> None:
        adapter = FoundryCatalogAdapter(
            PROJECT,
            token_provider=FakeTokenProvider(),
            transport=httpx.MockTransport(handler),
        )
        await adapter.list_agents()
        await adapter.close()

    asyncio.run(run())

    assert direct_requests == [
        f"{PROJECT}/agents?api-version=v1&limit=100",
        f"{PROJECT}/agents/{AGENT_ID}?api-version=v1",
        f"{PROJECT}/agents/{AGENT_ID}/versions/2?api-version=v1",
    ]
    assert len(configurations) == 1
    assert configurations[0]["trust_env"] is False
    assert configurations[0]["follow_redirects"] is False


def test_catalog_rejects_endpoint_before_credentials_or_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dependency_calls: list[str] = []

    def forbidden_dependency(*args: Any, **kwargs: Any) -> object:
        del args, kwargs
        dependency_calls.append("called")
        raise AssertionError("No credential or HTTP client may be constructed.")

    monkeypatch.setattr(
        foundry_adapter_module,
        "DefaultAzureBearerTokenProvider",
        forbidden_dependency,
    )
    monkeypatch.setattr(httpx, "AsyncClient", forbidden_dependency)

    with pytest.raises(ValueError, match="host must end"):
        FoundryCatalogAdapter("https://attacker.example/api/projects/test")

    assert dependency_calls == []


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, ErrorCode.AUTHENTICATION_REQUIRED, False),
        (403, ErrorCode.AUTHORIZATION_DENIED, False),
        (429, ErrorCode.CATALOG_UNAVAILABLE, True),
        (500, ErrorCode.CATALOG_UNAVAILABLE, True),
    ],
)
def test_catalog_maps_http_failures(
    status: int,
    code: ErrorCode,
    retryable: bool,
) -> None:
    async def run() -> None:
        adapter = FoundryCatalogAdapter(
            PROJECT,
            token_provider=FakeTokenProvider(),
            transport=httpx.MockTransport(lambda _: httpx.Response(status)),
        )
        with pytest.raises(AgentIqOperationError) as caught:
            await adapter.list_agents()
        assert caught.value.error.code is code
        assert caught.value.error.retryable is retryable

    asyncio.run(run())


def test_catalog_rejects_missing_and_malformed_agents() -> None:
    async def missing() -> None:
        adapter = FoundryCatalogAdapter(
            PROJECT,
            token_provider=FakeTokenProvider(),
            transport=httpx.MockTransport(lambda _: httpx.Response(404)),
        )
        with pytest.raises(AgentIqOperationError) as caught:
            await adapter.get_agent(AGENT_ID)
        assert caught.value.error.code is ErrorCode.AGENT_NOT_FOUND

    async def malformed() -> None:
        adapter = FoundryCatalogAdapter(
            PROJECT,
            token_provider=FakeTokenProvider(),
            transport=httpx.MockTransport(
                lambda _: _inventory_response({"id": AGENT_ID, "versions": {}})
            ),
        )
        with pytest.raises(AgentIqOperationError) as caught:
            await adapter.list_agents()
        assert caught.value.error.code is ErrorCode.CATALOG_UNAVAILABLE

        invalid_fields = FoundryCatalogAdapter(
            PROJECT,
            token_provider=FakeTokenProvider(),
            transport=httpx.MockTransport(
                lambda _: _inventory_response(
                    {
                        "id": AGENT_ID,
                        "versions": {"latest": {"definition": {}}},
                    }
                )
            ),
        )
        with pytest.raises(AgentIqOperationError) as invalid:
            await invalid_fields.list_agents()
        assert invalid.value.error.code is ErrorCode.CATALOG_UNAVAILABLE

    asyncio.run(missing())
    asyncio.run(malformed())


def test_catalog_rejects_malformed_json() -> None:
    async def run() -> None:
        adapter = FoundryCatalogAdapter(
            PROJECT,
            token_provider=FakeTokenProvider(),
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    content=b"{not-json",
                    headers={"content-type": "application/json"},
                )
            ),
        )
        with pytest.raises(AgentIqOperationError) as caught:
            await adapter.list_agents()
        assert caught.value.error.code is ErrorCode.CATALOG_UNAVAILABLE

    asyncio.run(run())


@pytest.mark.parametrize(
    "details",
    [
        {"agentEndpoint": {"versionSelector": {"versionSelectionRules": []}}},
        {
            "agentEndpoint": {
                "versionSelector": {
                    "versionSelectionRules": [
                        {
                            "type": "FixedRatio",
                            "agentVersion": "2",
                            "trafficPercentage": 50,
                        }
                    ]
                }
            }
        },
        {
            "agentEndpoint": {
                "versionSelector": {
                    "versionSelectionRules": [
                        {
                            "type": "Weighted",
                            "agentVersion": "2",
                            "trafficPercentage": 100,
                        }
                    ]
                }
            }
        },
        _agent_details(traffic_percentage=100.0),
        _agent_details(traffic_percentage="100"),
        _agent_details(traffic_percentage=True),
        _agent_details(traffic_percentage=False),
    ],
)
def test_catalog_rejects_nonfixed_traffic(details: Mapping[str, Any]) -> None:
    exact_version_requested = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal exact_version_requested
        if request.url.path.endswith("/agents"):
            return _inventory_response(_agent())
        if "/versions/" in request.url.path:
            exact_version_requested = True
            return httpx.Response(200, json=_agent_version())
        return httpx.Response(200, json=details)

    async def run() -> None:
        adapter = FoundryCatalogAdapter(
            PROJECT,
            token_provider=FakeTokenProvider(),
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(AgentIqOperationError) as caught:
            await adapter.list_agents()
        assert caught.value.error.code is ErrorCode.CATALOG_UNAVAILABLE

    asyncio.run(run())
    assert exact_version_requested is False


def test_catalog_marks_canonical_card_drift_unhealthy() -> None:
    descriptor = next(
        agent for agent in load_consumer_assets().catalog.agents if agent.agent_id == AGENT_ID
    )
    payload = {"name": AGENT_ID, **foundry_card_projection(descriptor)}

    async def run() -> None:
        adapter = FoundryCatalogAdapter(
            PROJECT,
            token_provider=FakeTokenProvider(),
            transport=httpx.MockTransport(_catalog_handler),
            expected_descriptors=(descriptor,),
        )
        agent = await adapter.get_agent(AGENT_ID)
        healthy = adapter.reconcile_card(agent, payload)
        drifted = adapter.reconcile_card(
            agent,
            {**payload, "description": "Drifted description."},
        )
        assert healthy.health is HealthStatus.HEALTHY
        assert healthy.served_hash == healthy.expected_hash
        assert healthy.platform_omitted_fields == ("capabilities.extensions",)
        assert drifted.health is HealthStatus.UNHEALTHY
        assert drifted.drifted_fields == ("description",)

        unverified_adapter = FoundryCatalogAdapter(
            PROJECT,
            token_provider=FakeTokenProvider(),
        )
        unverified = unverified_adapter.reconcile_card(agent, payload)
        assert unverified.health is HealthStatus.UNKNOWN
        assert unverified.expected_hash is None

    asyncio.run(run())


def test_runtime_negotiates_v1_and_invokes() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        standard = _standard_foundry_response(request)
        if standard is not None:
            return standard
        return httpx.Response(200, json={})

    async def run() -> None:
        runtime = _runtime(httpx.MockTransport(handler))
        result = await runtime.invoke(
            NativeA2ARequest(
                invocation_id="inv-coordinator",
                agent_id=AGENT_ID,
                prompt="Which enzymes clean in cold water?",
            )
        )
        assert result.invocation_id == "inv-coordinator"
        assert result.agent_id == AGENT_ID
        assert result.protocol_version == "1.0"
        assert result.answer == ANSWER
        assert result.attempt_count == 1
        await runtime.close()

    asyncio.run(run())
    assert any(path.endswith("/agentCard/v1.0") for path in paths)
    assert not any(path.endswith("/agentCard/v0.3") for path in paths)


def test_runtime_owned_clients_bypass_environment_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_adversarial_proxy_environment(monkeypatch)
    configurations = _capture_async_client_configuration(monkeypatch)
    direct_requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        direct_requests.append((request.method, request.url.path))
        standard = _standard_foundry_response(request)
        if standard is not None:
            return standard
        return httpx.Response(200, json={})

    async def run() -> None:
        runtime = _runtime(httpx.MockTransport(handler))
        await runtime.invoke(NativeA2ARequest(agent_id=AGENT_ID, prompt="Use native A2A."))
        await runtime.close()

    asyncio.run(run())

    assert [method for method, _path in direct_requests] == [
        "GET",
        "GET",
        "GET",
        "GET",
        "POST",
        "GET",
    ]
    assert len(configurations) == 3
    assert all(config["trust_env"] is False for config in configurations)
    assert all(config["follow_redirects"] is False for config in configurations)


def test_runtime_rejects_traffic_drift_after_native_invocation() -> None:
    details_reads = 0
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal details_reads, post_count
        if request.url.path.endswith("/agents"):
            return _inventory_response(_agent())
        if request.url.path.endswith(f"/agents/{AGENT_ID}/versions/2"):
            return httpx.Response(200, json=_agent_version())
        if request.url.path.endswith(f"/agents/{AGENT_ID}"):
            details_reads += 1
            routed_version = "3" if details_reads == 3 else "2"
            return httpx.Response(200, json=_agent_details(version=routed_version))
        if request.method == "GET":
            return httpx.Response(200, json=CARD)
        post_count += 1
        return httpx.Response(200)

    async def run() -> None:
        runtime = _runtime(httpx.MockTransport(handler))
        with pytest.raises(AgentIqOperationError) as caught:
            await runtime.invoke(NativeA2ARequest(agent_id=AGENT_ID, prompt="Reject drift."))
        assert caught.value.error.code is ErrorCode.CATALOG_UNAVAILABLE
        assert "expected immutable version" in caught.value.error.message

    asyncio.run(run())
    assert details_reads == 3
    assert post_count == 1


def test_runtime_rejects_endpoint_before_credentials_catalog_or_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dependency_calls: list[str] = []

    def forbidden_dependency(*args: Any, **kwargs: Any) -> object:
        del args, kwargs
        dependency_calls.append("called")
        raise AssertionError("No credential, catalog, or HTTP client may be constructed.")

    monkeypatch.setattr(
        native_a2a_module,
        "DefaultAzureBearerTokenProvider",
        forbidden_dependency,
    )
    monkeypatch.setattr(native_a2a_module, "FoundryCatalogAdapter", forbidden_dependency)
    monkeypatch.setattr(httpx, "AsyncClient", forbidden_dependency)

    with pytest.raises(ValueError, match="host must end"):
        NativeA2ARuntime("https://attacker.example/api/projects/test")

    assert dependency_calls == []


def test_runtime_falls_back_to_v03_card() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/agents"):
            return _inventory_response(_agent())
        metadata = _metadata_response(request)
        if metadata is not None:
            return metadata
        if request.url.path.endswith("/agentCard/v1.0"):
            return httpx.Response(404)
        if request.method == "GET":
            return httpx.Response(200, json=CARD)
        return httpx.Response(200, json={})

    async def run() -> None:
        runtime = _runtime(httpx.MockTransport(handler))
        result = await runtime.invoke(NativeA2ARequest(agent_id=AGENT_ID, prompt="Use native A2A."))
        assert result.protocol_version == "0.3"

    asyncio.run(run())
    assert any(path.endswith("/agentCard/v0.3") for path in paths)


def test_runtime_rejects_unsupported_protocol_without_fallback() -> None:
    client_created = False

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/agents"):
            return _inventory_response(_agent())
        metadata = _metadata_response(request)
        if metadata is not None:
            return metadata
        return httpx.Response(404)

    async def client_factory(card: object, client_config: Any) -> FakeClient:
        nonlocal client_created
        del card
        client_created = True
        return FakeClient(client_config.httpx_client, [])

    async def run() -> None:
        runtime = NativeA2ARuntime(
            PROJECT,
            token_provider=FakeTokenProvider(),
            transport=httpx.MockTransport(handler),
            resolver_factory=FakeResolver,
            client_factory=client_factory,
            card_mapper=lambda card: card,
        )
        with pytest.raises(AgentIqOperationError) as caught:
            await runtime.invoke(NativeA2ARequest(agent_id=AGENT_ID, prompt="Invoke."))
        assert caught.value.error.code is ErrorCode.PROTOCOL_UNSUPPORTED

    asyncio.run(run())
    assert client_created is False


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, ErrorCode.AUTHENTICATION_REQUIRED),
        (403, ErrorCode.AUTHORIZATION_DENIED),
        (500, ErrorCode.CARD_INVALID),
    ],
)
def test_runtime_maps_card_http_failures(status: int, code: ErrorCode) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/agents"):
            return _inventory_response(_agent())
        metadata = _metadata_response(request)
        if metadata is not None:
            return metadata
        return httpx.Response(status)

    async def run() -> None:
        runtime = _runtime(httpx.MockTransport(handler))
        with pytest.raises(AgentIqOperationError) as caught:
            await runtime.negotiate_card(AGENT_ID)
        assert caught.value.error.code is code

    asyncio.run(run())


def test_runtime_marks_card_transport_failure_retryable() -> None:
    class TransportErrorResolver:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs

        async def get_agent_card(self) -> object:
            request = httpx.Request("GET", f"{PROJECT}/card")
            cause = httpx.ConnectError("unavailable", request=request)
            raise AgentCardResolutionError("unavailable") from cause

    async def run() -> None:
        runtime = NativeA2ARuntime(
            PROJECT,
            token_provider=FakeTokenProvider(),
            transport=httpx.MockTransport(_catalog_handler),
            resolver_factory=TransportErrorResolver,
        )
        with pytest.raises(AgentIqOperationError) as caught:
            await runtime.negotiate_card(AGENT_ID)
        assert caught.value.error.code is ErrorCode.CARD_INVALID
        assert caught.value.error.retryable is True

    asyncio.run(run())


def test_runtime_rejects_malformed_and_ineligible_agents() -> None:
    def wrong_card(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/agents"):
            return _inventory_response(_agent())
        metadata = _metadata_response(request)
        if metadata is not None:
            return metadata
        return httpx.Response(200, json={**CARD, "name": "consumer-wrong"})

    async def malformed() -> None:
        runtime = _runtime(httpx.MockTransport(wrong_card))
        with pytest.raises(AgentIqOperationError) as caught:
            await runtime.negotiate_card(AGENT_ID)
        assert caught.value.error.code is ErrorCode.CARD_INVALID

    async def inactive() -> None:
        runtime = _runtime(
            httpx.MockTransport(lambda request: _catalog_handler(request, status="failed"))
        )
        with pytest.raises(AgentIqOperationError) as caught:
            await runtime.invoke(NativeA2ARequest(agent_id=AGENT_ID, prompt="Invoke."))
        assert caught.value.error.code is ErrorCode.AGENT_INELIGIBLE

    asyncio.run(malformed())
    asyncio.run(inactive())


def test_runtime_rejects_card_drift_as_ineligible() -> None:
    descriptor = next(
        agent for agent in load_consumer_assets().catalog.agents if agent.agent_id == AGENT_ID
    )
    drifted_card = {
        "name": AGENT_ID,
        **foundry_card_projection(descriptor),
        "description": "Drifted description.",
    }

    def drift_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/agents"):
            return _inventory_response(_agent())
        metadata = _metadata_response(request)
        if metadata is not None:
            return metadata
        return httpx.Response(200, json=drifted_card)

    transport = httpx.MockTransport(drift_handler)

    async def run() -> None:
        token_provider = FakeTokenProvider()
        catalog = FoundryCatalogAdapter(
            PROJECT,
            token_provider=token_provider,
            transport=transport,
            expected_descriptors=(descriptor,),
        )
        runtime = NativeA2ARuntime(
            PROJECT,
            token_provider=token_provider,
            catalog=catalog,
            transport=transport,
            resolver_factory=FakeResolver,
            card_mapper=lambda card: card,
        )
        with pytest.raises(AgentIqOperationError) as caught:
            await runtime.invoke(NativeA2ARequest(agent_id=AGENT_ID, prompt="Reject drift."))
        assert caught.value.error.code is ErrorCode.AGENT_INELIGIBLE
        assert caught.value.error.details[0].field == "agentCard"

    asyncio.run(run())


def test_runtime_enforces_deadline_and_cancellation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        standard = _standard_foundry_response(request)
        if standard is not None:
            return standard
        return httpx.Response(200)

    async def timeout() -> None:
        runtime = _runtime(
            httpx.MockTransport(handler),
            client_delay_seconds=1,
        )
        with pytest.raises(AgentIqOperationError) as caught:
            await runtime.invoke(
                NativeA2ARequest(
                    agent_id=AGENT_ID,
                    prompt="Timeout.",
                    deadline_seconds=0.1,
                )
            )
        assert caught.value.error.code is ErrorCode.INVOCATION_TIMEOUT

    async def cancellation() -> None:
        runtime = _runtime(
            httpx.MockTransport(handler),
            client_delay_seconds=1,
        )
        cancel_event = asyncio.Event()
        asyncio.get_running_loop().call_later(0.02, cancel_event.set)
        with pytest.raises(AgentIqOperationError) as caught:
            await runtime.invoke(
                NativeA2ARequest(agent_id=AGENT_ID, prompt="Cancel."),
                cancel_event=cancel_event,
            )
        assert caught.value.error.code is ErrorCode.CANCELLED

    asyncio.run(timeout())
    asyncio.run(cancellation())


def test_runtime_rejects_invalid_deadline_and_preexisting_cancellation() -> None:
    async def run() -> None:
        runtime = NativeA2ARuntime(
            PROJECT,
            token_provider=FakeTokenProvider(),
            transport=httpx.MockTransport(lambda _: _inventory_response(_agent())),
        )
        with pytest.raises(AgentIqOperationError) as invalid:
            await runtime.negotiate_card(AGENT_ID, deadline_seconds=0)
        assert invalid.value.error.code is ErrorCode.INVALID_REQUEST

        cancel_event = asyncio.Event()
        cancel_event.set()
        with pytest.raises(AgentIqOperationError) as card_cancelled:
            await runtime.negotiate_card(AGENT_ID, cancel_event=cancel_event)
        assert card_cancelled.value.error.code is ErrorCode.CANCELLED
        with pytest.raises(AgentIqOperationError) as invocation_cancelled:
            await runtime.invoke(
                NativeA2ARequest(agent_id=AGENT_ID, prompt="Cancelled."),
                cancel_event=cancel_event,
            )
        assert invocation_cancelled.value.error.code is ErrorCode.CANCELLED

    asyncio.run(run())


def test_retry_requires_key_and_reuses_message_id() -> None:
    post_count = 0
    message_ids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        standard = _standard_foundry_response(request)
        if standard is not None:
            return standard
        post_count += 1
        return httpx.Response(503 if post_count == 1 else 200)

    async def retry_with_key() -> None:
        runtime = _runtime(
            httpx.MockTransport(handler),
            sent_message_ids=message_ids,
        )
        result = await runtime.invoke(
            NativeA2ARequest(
                agent_id=AGENT_ID,
                prompt="Retry safely.",
                idempotency_policy=IdempotencyPolicy.RETRY_WITH_KEY,
                idempotency_key="stable-key",
                max_attempts=2,
            )
        )
        assert result.attempt_count == 2

    asyncio.run(retry_with_key())
    assert post_count == 2
    assert len(message_ids) == 2
    assert message_ids[0] == message_ids[1]


def test_runtime_carries_non_secret_publication_context_in_a2a_context_id() -> None:
    prompts: list[str] = []
    context_ids: list[str] = []
    telemetry = MemoryRuntimeTelemetry()

    def handler(request: httpx.Request) -> httpx.Response:
        standard = _standard_foundry_response(request)
        if standard is not None:
            return standard
        return httpx.Response(200)

    async def run() -> None:
        runtime = _runtime(
            httpx.MockTransport(handler),
            telemetry=telemetry,
            sent_prompts=prompts,
            sent_context_ids=context_ids,
        )
        await runtime.invoke(
            NativeA2ARequest(
                invocation_id="inv-publication",
                agent_id=AGENT_ID,
                prompt="Publish the typed result.",
                correlation_id="corr-publication",
                enable_artifact_publication=True,
            )
        )

    asyncio.run(run())
    assert prompts == ["Publish the typed result."]
    assert context_ids == ["aiq-pub-v1|inv-publication|corr-publication"]
    assert "apt-" not in prompts[0]
    prepared = next(
        event for event in telemetry.events if event.name == "invocation.request_prepared"
    )
    assert dict(prepared.attributes) == {
        "artifact_context_transport": "a2a_context_id",
        "artifact_context_present": True,
        "artifact_context_length": len(context_ids[0]),
        "prompt_length": len(prompts[0]),
        "publication_marker_in_prompt": False,
    }


def test_runtime_adds_evidence_contract_without_changing_request_prompt() -> None:
    prompts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        standard = _standard_foundry_response(request)
        if standard is not None:
            return standard
        return httpx.Response(200)

    request = NativeA2ARequest(
        agent_id=AGENT_ID,
        prompt="Ground the operational answer.",
        preserve_evidence_markers=True,
    )

    async def run() -> None:
        runtime = _runtime(httpx.MockTransport(handler), sent_prompts=prompts)
        await runtime.invoke(request)

    asyncio.run(run())
    assert request.prompt == "Ground the operational answer."
    assert prompts[0].startswith(request.prompt)
    assert "Preserve every inline source marker" in prompts[0]
    assert "Do not create a source marker" in prompts[0]


def test_transient_send_is_not_retried_without_idempotency_key() -> None:
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        standard = _standard_foundry_response(request)
        if standard is not None:
            return standard
        post_count += 1
        return httpx.Response(503)

    async def run() -> None:
        runtime = _runtime(httpx.MockTransport(handler))
        with pytest.raises(AgentIqOperationError) as caught:
            await runtime.invoke(NativeA2ARequest(agent_id=AGENT_ID, prompt="Do not retry."))
        assert caught.value.error.code is ErrorCode.INVOCATION_FAILED
        assert caught.value.error.retryable is True

    asyncio.run(run())
    assert post_count == 1


def test_runtime_maps_malformed_response_to_typed_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        standard = _standard_foundry_response(request)
        if standard is not None:
            return standard
        return httpx.Response(200)

    def malformed_event(event: object) -> str:
        del event
        raise ValueError("malformed")

    async def client_factory(card: object, client_config: Any) -> FakeClient:
        del card
        return FakeClient(client_config.httpx_client, [])

    async def run() -> None:
        runtime = NativeA2ARuntime(
            PROJECT,
            token_provider=FakeTokenProvider(),
            transport=httpx.MockTransport(handler),
            resolver_factory=FakeResolver,
            client_factory=client_factory,
            card_mapper=lambda card: card,
            event_text=malformed_event,
        )
        with pytest.raises(AgentIqOperationError) as caught:
            await runtime.invoke(NativeA2ARequest(agent_id=AGENT_ID, prompt="Malformed."))
        assert caught.value.error.code is ErrorCode.INVOCATION_FAILED
        assert "malformed response" in caught.value.error.message

    asyncio.run(run())


def test_runtime_maps_sdk_parse_failure_to_typed_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        response = _standard_foundry_response(request)
        if response is None:
            raise AssertionError(f"Unexpected Foundry request: {request.method} {request.url}")
        return response

    class ParseErrorClient:
        async def send_message(self, request: object) -> Any:
            if request is None:
                yield object()
            raise ParseError("malformed response")

        async def close(self) -> None:
            return None

    async def client_factory(card: object, client_config: Any) -> ParseErrorClient:
        del card, client_config
        return ParseErrorClient()

    async def run() -> None:
        runtime = NativeA2ARuntime(
            PROJECT,
            token_provider=FakeTokenProvider(),
            transport=httpx.MockTransport(handler),
            resolver_factory=FakeResolver,
            client_factory=client_factory,
            card_mapper=lambda card: card,
        )
        with pytest.raises(AgentIqOperationError) as caught:
            await runtime.invoke(NativeA2ARequest(agent_id=AGENT_ID, prompt="Malformed."))
        assert caught.value.error.code is ErrorCode.INVOCATION_FAILED
        assert "malformed response" in caught.value.error.message

    asyncio.run(run())


def test_runtime_maps_a2a_protocol_error_to_typed_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        response = _standard_foundry_response(request)
        if response is None:
            raise AssertionError(f"Unexpected Foundry request: {request.method} {request.url}")
        return response

    class ProtocolErrorClient:
        async def send_message(self, request: object) -> Any:
            if request is None:
                yield object()
            raise InternalError("Internal error")

        async def close(self) -> None:
            return None

    async def client_factory(card: object, client_config: Any) -> ProtocolErrorClient:
        del card, client_config
        return ProtocolErrorClient()

    async def run() -> None:
        runtime = NativeA2ARuntime(
            PROJECT,
            token_provider=FakeTokenProvider(),
            transport=httpx.MockTransport(handler),
            resolver_factory=FakeResolver,
            client_factory=client_factory,
            card_mapper=lambda card: card,
        )
        with pytest.raises(AgentIqOperationError) as caught:
            await runtime.invoke(NativeA2ARequest(agent_id=AGENT_ID, prompt="Invoke."))
        assert caught.value.error.code is ErrorCode.INVOCATION_FAILED
        assert caught.value.error.retryable is False
        assert "protocol error" in caught.value.error.message

    asyncio.run(run())


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, ErrorCode.AUTHENTICATION_REQUIRED, False),
        (403, ErrorCode.AUTHORIZATION_DENIED, False),
        (429, ErrorCode.INVOCATION_FAILED, True),
        (500, ErrorCode.INVOCATION_FAILED, True),
    ],
)
def test_runtime_unwraps_official_sdk_http_failures(
    status: int,
    code: ErrorCode,
    retryable: bool,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        response = _standard_foundry_response(request)
        if response is None:
            raise AssertionError(f"Unexpected Foundry request: {request.method} {request.url}")
        return response

    class WrappedErrorClient:
        async def send_message(self, request: object) -> Any:
            del request
            if status < 0:
                yield object()
            http_request = httpx.Request("POST", f"{PROJECT}/a2a")
            response = httpx.Response(status, request=http_request)
            cause = httpx.HTTPStatusError(
                "failed",
                request=http_request,
                response=response,
            )
            raise A2AClientError("wrapped") from cause

        async def close(self) -> None:
            return None

    async def client_factory(card: object, client_config: Any) -> WrappedErrorClient:
        del card, client_config
        return WrappedErrorClient()

    async def run() -> None:
        runtime = NativeA2ARuntime(
            PROJECT,
            token_provider=FakeTokenProvider(),
            transport=httpx.MockTransport(handler),
            resolver_factory=FakeResolver,
            client_factory=client_factory,
            card_mapper=lambda card: card,
        )
        with pytest.raises(AgentIqOperationError) as caught:
            await runtime.invoke(NativeA2ARequest(agent_id=AGENT_ID, prompt="Invoke."))
        assert caught.value.error.code is code
        assert caught.value.error.retryable is retryable

    asyncio.run(run())


def test_runtime_maps_entra_credential_failure() -> None:
    class FailingSecondTokenProvider(FakeTokenProvider):
        async def get_token(self, scope: str) -> str:
            self.scopes.append(scope)
            if len(self.scopes) > 1:
                raise CredentialUnavailableError("credential unavailable")
            return "test-token"

    def handler(request: httpx.Request) -> httpx.Response:
        return _catalog_handler(request)

    async def run() -> None:
        runtime = NativeA2ARuntime(
            PROJECT,
            token_provider=FailingSecondTokenProvider(),
            transport=httpx.MockTransport(handler),
            resolver_factory=FakeResolver,
            card_mapper=lambda card: card,
        )
        with pytest.raises(AgentIqOperationError) as caught:
            await runtime.invoke(NativeA2ARequest(agent_id=AGENT_ID, prompt="Invoke."))
        assert caught.value.error.code is ErrorCode.AUTHENTICATION_REQUIRED

    asyncio.run(run())


def test_runtime_telemetry_excludes_sensitive_content() -> None:
    telemetry = MemoryRuntimeTelemetry()

    def handler(request: httpx.Request) -> httpx.Response:
        standard = _standard_foundry_response(request)
        if standard is not None:
            return standard
        return httpx.Response(200)

    async def run() -> None:
        runtime = _runtime(
            httpx.MockTransport(handler),
            telemetry=telemetry,
        )
        await runtime.invoke(
            NativeA2ARequest(
                agent_id=AGENT_ID,
                prompt="sensitive prompt text",
                idempotency_key=None,
            )
        )

    asyncio.run(run())
    serialized = repr(telemetry.events)
    assert "sensitive prompt text" not in serialized
    assert "test-token" not in serialized
    assert ANSWER not in serialized
    assert {"card.negotiated", "invocation.started", "invocation.completed"} <= {
        event.name for event in telemetry.events
    }
