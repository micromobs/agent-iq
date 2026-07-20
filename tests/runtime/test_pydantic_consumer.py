"""PydanticAI hosted hero runtime tests."""

from __future__ import annotations

import asyncio
import json
import ssl
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from pydantic import AnyHttpUrl, SecretStr, ValidationError
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel

from agent_iq.config import Settings
from agent_iq.contracts import (
    ArtifactChannelReference,
    BusinessArtifact,
    DemandScenario,
    RegulatoryClaimDecision,
    RegulatoryClaimStatus,
    RegulatoryDecision,
    RegulatoryDisposition,
)
from agent_iq.publication import (
    attach_publication_context,
    attach_publication_ticket,
    decode_publication_context_id,
    encode_publication_context_id,
    extract_publication_context,
    extract_publication_ticket,
)
from agent_iq.runtime import hosted_consumer, pydantic_consumer
from agent_iq.runtime.pydantic_consumer import (
    PUBLISH_ARTIFACT_TOOL,
    LangChainToolset,
    ManagedIdentityArtifactPublisher,
    PydanticArtifactPublisher,
    PydanticHeroOutput,
    PydanticHeroRequest,
    PydanticInvocationContext,
    create_pydantic_agent,
    create_pydantic_graph,
)


def _tool(calls: list[str]) -> StructuredTool:
    async def retrieve_forecast_drivers(topic: str) -> str:
        """Retrieve a grounded forecast driver by topic."""

        calls.append(topic)
        return f"Grounded {topic}. Source title: Demand Forecast Evidence [ref_id:0]"

    return StructuredTool.from_function(
        coroutine=retrieve_forecast_drivers,
        name="retrieve_forecast_drivers",
        description="Retrieve grounded forecast drivers.",
    )


def _marker_only_tool(calls: list[str]) -> StructuredTool:
    async def retrieve_forecast_drivers(topic: str) -> str:
        """Retrieve a grounded forecast driver by topic."""

        calls.append(topic)
        return f"Grounded {topic}. [ref_id:0]"

    return StructuredTool.from_function(
        coroutine=retrieve_forecast_drivers,
        name="retrieve_forecast_drivers",
        description="Retrieve grounded forecast drivers.",
    )


NOW = datetime(2026, 7, 12, 0, 0, tzinfo=UTC)
TICKET = SecretStr(f"apt-{'a' * 32}")
ARTIFACT_APPLICATION_ID = "11111111-1111-4111-8111-abcdefabcdef"
ARTIFACT_TENANT_ID = "22222222-2222-4222-8222-abcdefabcdef"
ARTIFACT_ENDPOINT = (
    "https://agent-iq-orchestrator-canary.bluefield-12345678."
    "swedencentral.azurecontainerapps.io/v1/artifacts/publish"
)
ARTIFACT_CLAIM_ENDPOINT = (
    "https://agent-iq-orchestrator-canary.bluefield-12345678."
    "swedencentral.azurecontainerapps.io/v1/artifacts/tickets/claim"
)
ARTIFACT_SCOPE = f"{ARTIFACT_APPLICATION_ID}/.default"


class RecordingPublisher(PydanticArtifactPublisher):
    def __init__(self) -> None:
        self.calls: list[tuple[PydanticInvocationContext, BusinessArtifact]] = []
        self._references: dict[str, ArtifactChannelReference] = {}

    async def publish(
        self,
        *,
        context: PydanticInvocationContext,
        artifact: BusinessArtifact,
    ) -> ArtifactChannelReference:
        self.calls.append((context, artifact))
        reference = ArtifactChannelReference(
            artifact_id=artifact.artifact_id,
            artifact_kind=artifact.kind,
            invocation_id="inv-demand-1",
            correlation_id="corr-demand-1",
            handle=f"artifact-{artifact.artifact_id}",
            expires_at=NOW + timedelta(minutes=10),
        )
        return self._references.setdefault(artifact.artifact_id, reference)


def _demand_scenario() -> DemandScenario:
    return DemandScenario(
        artifact_id="demand-scenario-1",
        product_id="brightwash-cold",
        market="Germany",
        horizon_start=date(2026, 10, 1),
        horizon_end=date(2026, 10, 31),
        unit="cases",
        baseline_quantity=1000,
        forecast_quantity=1250,
        confidence=0.84,
        evidence_ids=("evidence-demand-1",),
        created_at=NOW,
    )


def _regulatory_decision() -> RegulatoryDecision:
    return RegulatoryDecision(
        artifact_id="regulatory-decision-1",
        product_id="brightwash-cold",
        market="Germany",
        disposition=RegulatoryDisposition.CONDITIONAL,
        claims=(
            RegulatoryClaimDecision(
                claim_id="cold-water-claim",
                claim="Cleans in cold water.",
                status=RegulatoryClaimStatus.CONDITIONAL,
                rationale="Requires the approved evidence package.",
                evidence_ids=("evidence-regulatory-1",),
            ),
        ),
        rationale="The claim is conditional on the cited evidence.",
        evidence_ids=("evidence-regulatory-1",),
        decided_at=NOW,
    )


def _artifact_context(
    dependencies: pydantic_consumer.PydanticHeroDependencies,
) -> RunContext[pydantic_consumer.PydanticHeroDependencies]:
    return cast(
        RunContext[pydantic_consumer.PydanticHeroDependencies],
        SimpleNamespace(deps=dependencies),
    )


def test_pydantic_hero_contracts_are_strict() -> None:
    assert PydanticHeroRequest(prompt="forecast").prompt == "forecast"
    assert PydanticHeroOutput(answer="grounded").answer == "grounded"

    with pytest.raises(ValidationError):
        PydanticHeroRequest(prompt=" ")
    with pytest.raises(ValidationError):
        PydanticHeroOutput.model_validate({"answer": "ok", "unexpected": True})


def test_pydantic_agent_uses_dependency_injected_exact_tool_schema() -> None:
    calls: list[str] = []
    agent, dependencies = create_pydantic_agent(
        "consumer-demand-forecasting",
        model=TestModel(call_tools=["retrieve_forecast_drivers"]),
        tools=[_tool(calls)],
    )

    result = asyncio.run(agent.run("Use the forecast tool.", deps=dependencies))

    assert result.output.answer
    assert calls
    assert set(dependencies.tools) == {"retrieve_forecast_drivers"}


def test_artifact_tool_uses_exact_declared_schema_and_trusted_context() -> None:
    publisher = RecordingPublisher()
    invocation_context = PydanticInvocationContext(
        invocation_id="inv-demand-1",
        correlation_id="corr-demand-1",
    )
    _, dependencies = create_pydantic_agent(
        "consumer-demand-forecasting",
        model=TestModel(call_tools=[]),
        tools=[_tool([])],
        artifact_publisher=publisher,
        invocation_context=invocation_context,
    )
    toolset = LangChainToolset()
    ctx = _artifact_context(dependencies)

    tools = asyncio.run(toolset.get_tools(ctx))
    schema = tools[PUBLISH_ARTIFACT_TOOL].tool_def.parameters_json_schema
    serialized_schema = json.dumps(schema, sort_keys=True)

    assert set(tools) == {"retrieve_forecast_drivers", PUBLISH_ARTIFACT_TOOL}
    assert set(schema["properties"]) == {"artifact"}
    assert "invocationId" not in serialized_schema
    assert "correlationId" not in serialized_schema
    assert "demand_scenario" in serialized_schema
    assert "regulatory_decision" not in serialized_schema

    args = {"artifact": _demand_scenario().model_dump(mode="json", by_alias=True)}
    first = asyncio.run(
        toolset.call_tool(PUBLISH_ARTIFACT_TOOL, args, ctx, tools[PUBLISH_ARTIFACT_TOOL])
    )
    second = asyncio.run(
        toolset.call_tool(PUBLISH_ARTIFACT_TOOL, args, ctx, tools[PUBLISH_ARTIFACT_TOOL])
    )

    assert first == second
    assert first["artifactId"] == "demand-scenario-1"
    assert all(call[0] == invocation_context for call in publisher.calls)
    assert all(call[1] == _demand_scenario() for call in publisher.calls)


def test_artifact_tool_rejects_malformed_and_undeclared_records() -> None:
    publisher = RecordingPublisher()
    _, dependencies = create_pydantic_agent(
        "consumer-demand-forecasting",
        model=TestModel(call_tools=[]),
        tools=[_tool([])],
        artifact_publisher=publisher,
        invocation_context=PydanticInvocationContext(
            invocation_id="inv-demand-2",
            correlation_id="corr-demand-2",
        ),
    )
    toolset = LangChainToolset()
    ctx = _artifact_context(dependencies)
    tool = asyncio.run(toolset.get_tools(ctx))[PUBLISH_ARTIFACT_TOOL]
    malformed = _demand_scenario().model_dump(mode="json", by_alias=True)
    malformed["confidence"] = 2.0

    with pytest.raises(ValueError, match="strict validation"):
        asyncio.run(
            toolset.call_tool(
                PUBLISH_ARTIFACT_TOOL,
                {"artifact": malformed},
                ctx,
                tool,
            )
        )
    with pytest.raises(ValueError, match="not allowed"):
        asyncio.run(
            toolset.call_tool(
                PUBLISH_ARTIFACT_TOOL,
                {
                    "artifact": _regulatory_decision().model_dump(
                        mode="json",
                        by_alias=True,
                    )
                },
                ctx,
                tool,
            )
        )
    assert publisher.calls == []


def test_managed_identity_publisher_claims_ticket_then_sends_typed_artifact() -> None:
    captured: dict[str, Any] = {}

    class FakeCredential:
        async def get_token(self, scope: str) -> Any:
            captured["scope"] = scope
            return SimpleNamespace(token="managed-identity-token")

    def handler(request: httpx.Request) -> httpx.Response:
        captured.setdefault("urls", []).append(str(request.url))
        captured.setdefault("authorizations", []).append(request.headers["Authorization"])
        if str(request.url) == ARTIFACT_CLAIM_ENDPOINT:
            captured["claim_body"] = json.loads(request.content)
            captured["claim_has_ticket"] = "X-Agent-IQ-Publication-Ticket" in request.headers
            return httpx.Response(
                200,
                json={
                    "publicationTicket": TICKET.get_secret_value(),
                    "expiresAt": (NOW + timedelta(minutes=10)).isoformat(),
                },
            )
        assert str(request.url) == ARTIFACT_ENDPOINT
        captured["ticket"] = request.headers["X-Agent-IQ-Publication-Ticket"]
        captured["publish_body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=ArtifactChannelReference(
                artifact_id="demand-scenario-1",
                artifact_kind="demand_scenario",
                invocation_id="inv-demand-remote",
                correlation_id="corr-demand-remote",
                handle="artifact-remote",
                expires_at=NOW + timedelta(minutes=10),
            ).model_dump(mode="json", by_alias=True),
        )

    publisher = ManagedIdentityArtifactPublisher(
        ARTIFACT_ENDPOINT,
        ARTIFACT_APPLICATION_ID,
        ARTIFACT_TENANT_ID,
        credential=FakeCredential(),
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(
        publisher.publish(
            context=PydanticInvocationContext(
                invocation_id="inv-demand-remote",
                correlation_id="corr-demand-remote",
            ),
            artifact=_demand_scenario(),
        )
    )

    assert result.invocation_id == "inv-demand-remote"
    assert captured["scope"] == ARTIFACT_SCOPE
    assert captured["urls"] == [ARTIFACT_CLAIM_ENDPOINT, ARTIFACT_ENDPOINT]
    assert captured["authorizations"] == [
        "Bearer managed-identity-token",
        "Bearer managed-identity-token",
    ]
    assert captured["claim_body"] == {
        "invocationId": "inv-demand-remote",
        "correlationId": "corr-demand-remote",
    }
    assert captured["claim_has_ticket"] is False
    assert captured["ticket"] == TICKET.get_secret_value()
    assert set(captured["publish_body"]) == {"artifact"}
    assert "invocationId" not in json.dumps(captured["publish_body"])
    assert "correlationId" not in json.dumps(captured["publish_body"])


@pytest.mark.parametrize(
    ("endpoint", "application_id", "scope"),
    [
        (
            "https://attacker.example/v1/artifacts/publish",
            ARTIFACT_APPLICATION_ID,
            None,
        ),
        (ARTIFACT_ENDPOINT, "not-a-uuid", None),
        (
            ARTIFACT_ENDPOINT,
            ARTIFACT_APPLICATION_ID,
            "api://22222222-2222-4222-8222-222222222222/.default",
        ),
    ],
)
def test_publisher_revalidates_channel_before_credential_acquisition(
    endpoint: str,
    application_id: str,
    scope: str | None,
) -> None:
    calls = 0

    class FailingCredential:
        async def get_token(self, token_scope: str) -> Any:
            del token_scope
            nonlocal calls
            calls += 1
            raise AssertionError("credential must not be used")

    publisher = ManagedIdentityArtifactPublisher(
        endpoint,
        application_id,
        ARTIFACT_TENANT_ID,
        token_scope=scope,
        credential=FailingCredential(),
    )

    with pytest.raises(ValueError):
        asyncio.run(
            publisher.publish(
                context=PydanticInvocationContext(
                    invocation_id="inv-demand-validation",
                    correlation_id="corr-demand-validation",
                ),
                artifact=_demand_scenario(),
            )
        )
    assert calls == 0


def test_publisher_does_not_follow_redirects() -> None:
    requests: list[httpx.Request] = []

    class FakeCredential:
        async def get_token(self, scope: str) -> Any:
            assert scope == ARTIFACT_SCOPE
            return SimpleNamespace(token="managed-identity-token")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            307,
            headers={"Location": "https://attacker.example/collect"},
        )

    publisher = ManagedIdentityArtifactPublisher(
        ARTIFACT_ENDPOINT,
        ARTIFACT_APPLICATION_ID,
        ARTIFACT_TENANT_ID,
        credential=FakeCredential(),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError, match="HTTP 307"):
        asyncio.run(
            publisher.publish(
                context=PydanticInvocationContext(
                    invocation_id="inv-demand-redirect",
                    correlation_id="corr-demand-redirect",
                ),
                artifact=_demand_scenario(),
            )
        )
    assert len(requests) == 1
    assert str(requests[0].url) == ARTIFACT_CLAIM_ENDPOINT


def test_publisher_rejects_non_success_response() -> None:
    class FakeCredential:
        async def get_token(self, scope: str) -> Any:
            assert scope == ARTIFACT_SCOPE
            return SimpleNamespace(token="managed-identity-token")

    publisher = ManagedIdentityArtifactPublisher(
        ARTIFACT_ENDPOINT,
        ARTIFACT_APPLICATION_ID,
        ARTIFACT_TENANT_ID,
        credential=FakeCredential(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(500, json={"detail": "failed"})
        ),
    )

    with pytest.raises(RuntimeError, match="HTTP 500"):
        asyncio.run(
            publisher.publish(
                context=PydanticInvocationContext(publication_ticket=TICKET),
                artifact=_demand_scenario(),
            )
        )


def test_publisher_ignores_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeCredential:
        async def get_token(self, scope: str) -> Any:
            assert scope == ARTIFACT_SCOPE
            return SimpleNamespace(token="managed-identity-token")

    def handler(request: httpx.Request) -> httpx.Response:
        captured.setdefault("urls", []).append(str(request.url))
        if str(request.url) == ARTIFACT_CLAIM_ENDPOINT:
            return httpx.Response(
                200,
                json={
                    "publicationTicket": TICKET.get_secret_value(),
                    "expiresAt": (NOW + timedelta(minutes=10)).isoformat(),
                },
            )
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["Authorization"]
        captured["ticket"] = request.headers["X-Agent-IQ-Publication-Ticket"]
        return httpx.Response(
            200,
            json=ArtifactChannelReference(
                artifact_id="demand-scenario-1",
                artifact_kind="demand_scenario",
                invocation_id="inv-demand-proxy-test",
                correlation_id="corr-demand-proxy-test",
                handle="artifact-proxy-test",
                expires_at=NOW + timedelta(minutes=10),
            ).model_dump(mode="json", by_alias=True),
        )

    real_async_client = httpx.AsyncClient

    def guarded_async_client(**kwargs: Any) -> httpx.AsyncClient:
        captured["trust_env"] = kwargs.get("trust_env")
        captured["verify"] = kwargs.get("verify")
        if kwargs.get("trust_env") is not False:
            raise AssertionError("proxy environment must be disabled")
        return real_async_client(**kwargs)

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setattr(httpx, "AsyncClient", guarded_async_client)
    publisher = ManagedIdentityArtifactPublisher(
        ARTIFACT_ENDPOINT,
        ARTIFACT_APPLICATION_ID,
        ARTIFACT_TENANT_ID,
        credential=FakeCredential(),
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(
        publisher.publish(
            context=PydanticInvocationContext(
                invocation_id="inv-demand-proxy-test",
                correlation_id="corr-demand-proxy-test",
            ),
            artifact=_demand_scenario(),
        )
    )

    assert result.invocation_id == "inv-demand-proxy-test"
    assert captured["trust_env"] is False
    assert isinstance(captured["verify"], ssl.SSLContext)
    assert captured["verify"].check_hostname is True
    assert captured["verify"].verify_mode is ssl.CERT_REQUIRED
    assert captured["url"] == ARTIFACT_ENDPOINT
    assert captured["urls"] == [ARTIFACT_CLAIM_ENDPOINT, ARTIFACT_ENDPOINT]
    assert str(captured["authorization"]).startswith("Bearer ")
    assert captured["ticket"] == TICKET.get_secret_value()


def test_artifact_runtime_configuration_fails_closed() -> None:
    with pytest.raises(ValueError, match="configured together"):
        create_pydantic_agent(
            "consumer-demand-forecasting",
            model=TestModel(call_tools=[]),
            tools=[_tool([])],
            artifact_publisher=RecordingPublisher(),
        )
    undeclared_dependencies = pydantic_consumer.PydanticHeroDependencies(
        agent_id="consumer-unconfigured",
        tools={"retrieve_forecast_drivers": _tool([])},
        artifact_publisher=RecordingPublisher(),
        invocation_context=PydanticInvocationContext(
            invocation_id="inv-unconfigured",
            correlation_id="corr-unconfigured",
        ),
    )
    with pytest.raises(ValueError, match="at least one allowed kind"):
        asyncio.run(
            LangChainToolset().get_tools(
                _artifact_context(undeclared_dependencies),
            )
        )


def test_pydantic_graph_preserves_framework_neutral_message_shape() -> None:
    graph = create_pydantic_graph(
        "consumer-demand-forecasting",
        model=TestModel(call_tools=[]),
        tools=[_tool([])],
    )

    result = asyncio.run(
        graph.ainvoke(
            {
                "messages": [
                    HumanMessage(content="What was the baseline?"),
                    AIMessage(content="The grounded baseline was 100."),
                    HumanMessage(content="What changed?"),
                ]
            }
        )
    )

    assert isinstance(result["messages"][-1], AIMessage)
    assert result["messages"][-1].text


def test_pydantic_graph_preserves_exact_tool_source_title() -> None:
    graph = create_pydantic_graph(
        "consumer-demand-forecasting",
        model=TestModel(call_tools=["retrieve_forecast_drivers"]),
        tools=[_tool([])],
    )

    result = asyncio.run(graph.ainvoke({"messages": [HumanMessage(content="What changed?")]}))

    assert result["messages"][-1].text.endswith("Sources:\n- Demand Forecast Evidence [ref_id:0]")


def test_pydantic_graph_preserves_marker_without_exposed_source_title() -> None:
    graph = create_pydantic_graph(
        "consumer-demand-forecasting",
        model=TestModel(call_tools=["retrieve_forecast_drivers"]),
        tools=[_marker_only_tool([])],
    )

    result = asyncio.run(graph.ainvoke({"messages": [HumanMessage(content="What changed?")]}))

    assert result["messages"][-1].text.endswith("Sources:\n- [ref_id:0]")


def test_responses_host_strips_coordinates_before_model_and_preserves_private_context() -> None:
    prefixed = attach_publication_context(
        "Ground this forecast.",
        invocation_id="inv-demand-hosted",
        correlation_id="corr-demand-hosted",
    )
    wrapped = f"Foundry A2A user message:\n{prefixed}"
    request = SimpleNamespace(
        instructions=None,
        metadata={"agentIq": "present"},
        previous_response_id=None,
        conversation=None,
    )

    graph_input, context_present = pydantic_consumer._strip_publication_context(
        {"messages": [HumanMessage(content=wrapped)]}
    )
    message = graph_input["messages"][-1]

    assert context_present is True
    assert isinstance(message, HumanMessage)
    assert message.text == "Foundry A2A user message:\nGround this forecast."
    assert "AGENT_IQ_ARTIFACT_CONTEXT" not in message.text
    assert "apt-" not in message.text
    context = pydantic_consumer._invocation_context_from_messages(graph_input["messages"])
    assert context is not None
    assert context.invocation_id == "inv-demand-hosted"
    assert context.correlation_id == "corr-demand-hosted"
    assert context.publication_ticket is None
    assert pydantic_consumer._request_metadata_keys(request) == ("agentIq",)


def test_responses_host_binds_a2a_context_id_without_changing_model_text() -> None:
    context_id = encode_publication_context_id(
        invocation_id="inv-demand-hosted",
        correlation_id="corr-demand-hosted",
    )
    request = SimpleNamespace(
        metadata={"a2a_context_id": context_id, "protocol": "a2a"},
    )
    graph_input = {"messages": [HumanMessage(content="Ground this forecast.")]}

    context = pydantic_consumer._publication_context_from_request(request)
    assert context is not None
    bound = pydantic_consumer._bind_publication_context(graph_input, context)
    message = bound["messages"][-1]

    assert isinstance(message, HumanMessage)
    assert message.text == "Ground this forecast."
    private_context = pydantic_consumer._invocation_context_from_messages(bound["messages"])
    assert private_context is not None
    assert private_context.invocation_id == "inv-demand-hosted"
    assert private_context.correlation_id == "corr-demand-hosted"
    assert private_context.publication_ticket is None


def test_a2a_publication_context_id_ignores_unrelated_ids_and_rejects_malformed_ones() -> None:
    assert decode_publication_context_id("foundry-conversation-id") is None
    with pytest.raises(ValueError, match="Malformed"):
        decode_publication_context_id("aiq-pub-v1|bad id|corr-1")


def test_publication_context_parser_rejects_malformed_or_repeated_prefixes() -> None:
    with pytest.raises(ValueError, match="Malformed"):
        extract_publication_context(
            "AGENT_IQ_ARTIFACT_CONTEXT_V1=invocationId:bad id;correlationId:corr-1\nQuestion."
        )
    repeated = attach_publication_context(
        attach_publication_context(
            "Question.",
            invocation_id="inv-1",
            correlation_id="corr-1",
        ),
        invocation_id="inv-2",
        correlation_id="corr-2",
    )
    with pytest.raises(ValueError, match="Multiple"):
        extract_publication_context(repeated)


def test_publication_ticket_parser_rejects_malformed_or_repeated_prefixes() -> None:
    with pytest.raises(ValueError, match="Malformed"):
        extract_publication_ticket("AGENT_IQ_ARTIFACT_TICKET_V1=not-a-ticket\nQuestion.")
    repeated = attach_publication_ticket(
        attach_publication_ticket("Question.", TICKET),
        SecretStr(f"apt-{'b' * 32}"),
    )
    with pytest.raises(ValueError, match="Multiple"):
        extract_publication_ticket(repeated)


def test_publication_ticket_parser_accepts_legacy_wrapped_prefix() -> None:
    legacy = (
        "Foundry A2A user message:\n"
        f"[[agent-iq-artifact-ticket:v1:{TICKET.get_secret_value()}]]\n"
        "Question."
    )

    clean, ticket = extract_publication_ticket(legacy)

    assert clean == "Foundry A2A user message:\nQuestion."
    assert ticket is not None
    assert ticket.get_secret_value() == TICKET.get_secret_value()


def test_message_adapter_rejects_unsupported_or_misordered_input() -> None:
    request, history = pydantic_consumer._request_from_messages(
        [
            SystemMessage(content="Request-specific instruction."),
            HumanMessage(content="First turn."),
            AIMessage(content="First answer."),
            HumanMessage(content="Second turn."),
        ]
    )

    assert request.prompt == "Second turn."
    assert len(history) == 3
    with pytest.raises(ValueError, match="end with a user"):
        pydantic_consumer._request_from_messages(
            [HumanMessage(content="question"), AIMessage(content="answer")]
        )


@pytest.mark.parametrize(
    ("settings", "expected_scope", "expected_base_url"),
    [
        (
            Settings(
                foundry_project_endpoint=(
                    "https://example.services.ai.azure.com/api/projects/demo"
                ),
                azure_ai_account="example",
                azure_ai_project="demo",
                azure_model_deployment="gpt-current",
                _env_file=None,
            ),
            pydantic_consumer.AZURE_AI_SCOPE,
            "https://example.services.ai.azure.com/api/projects/demo/openai/v1/",
        ),
        (
            Settings(
                azure_ai_account="example",
                azure_openai_endpoint="https://example.cognitiveservices.azure.com/",
                azure_model_deployment="gpt-current",
                _env_file=None,
            ),
            pydantic_consumer.AZURE_COGNITIVE_SERVICES_SCOPE,
            "https://example.cognitiveservices.azure.com/openai/v1/",
        ),
    ],
)
def test_pydantic_model_uses_managed_identity_and_responses_v1(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    expected_scope: str,
    expected_base_url: str,
) -> None:
    captured: dict[str, Any] = {}

    class FakeCredential:
        def __init__(self, **kwargs: Any) -> None:
            captured["credential_options"] = kwargs

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured["client"] = kwargs

    class FakeHttpClient:
        def __init__(self, **kwargs: Any) -> None:
            captured["http_client"] = kwargs

    class FakeProvider:
        def __init__(self, **kwargs: Any) -> None:
            captured["provider"] = kwargs

    class FakeModel:
        def __init__(self, model: str, **kwargs: Any) -> None:
            captured["model"] = model
            captured["model_kwargs"] = kwargs

    def token_provider(credential: Any, scope: str) -> Any:
        captured["credential"] = credential
        captured["scope"] = scope
        return lambda: "token"

    monkeypatch.setattr(pydantic_consumer, "DefaultAzureCredential", FakeCredential)
    monkeypatch.setattr(pydantic_consumer, "DefaultAsyncHttpxClient", FakeHttpClient)
    monkeypatch.setattr(pydantic_consumer, "get_bearer_token_provider", token_provider)
    monkeypatch.setattr(pydantic_consumer, "AsyncOpenAI", FakeClient)
    monkeypatch.setattr(pydantic_consumer, "OpenAIProvider", FakeProvider)
    monkeypatch.setattr(pydantic_consumer, "OpenAIResponsesModel", FakeModel)

    result = pydantic_consumer.pydantic_model(settings)

    assert isinstance(result, FakeModel)
    assert isinstance(captured["credential"], FakeCredential)
    assert captured["credential_options"]["transport"]._use_env_settings is False
    assert captured["credential_options"]["redirect_policy"].allow is False
    assert captured["scope"] == expected_scope
    assert captured["client"]["base_url"] == expected_base_url
    assert callable(captured["client"]["api_key"])
    assert isinstance(captured["client"]["http_client"], FakeHttpClient)
    assert captured["http_client"]["follow_redirects"] is False
    assert captured["http_client"]["trust_env"] is False
    assert isinstance(captured["http_client"]["verify"], ssl.SSLContext)
    assert captured["http_client"]["verify"].check_hostname is True
    assert captured["http_client"]["verify"].verify_mode is ssl.CERT_REQUIRED
    assert captured["model"] == "gpt-current"


@pytest.mark.parametrize(
    ("account", "endpoint", "message"),
    [
        (None, "https://example.cognitiveservices.azure.com/", "AZURE_AI_ACCOUNT is required"),
        (
            "INVALID.ACCOUNT",
            "https://example.cognitiveservices.azure.com/",
            "canonical lowercase DNS label",
        ),
        (
            "example",
            "https://attacker.cognitiveservices.azure.com/",
            "configured Azure AI account",
        ),
    ],
)
def test_pydantic_model_rejects_unbound_endpoint_before_auth_or_client(
    monkeypatch: pytest.MonkeyPatch,
    account: str | None,
    endpoint: str,
    message: str,
) -> None:
    calls = {"credential": 0, "token_provider": 0, "client": 0}

    def credential_factory() -> object:
        calls["credential"] += 1
        return object()

    def token_provider(*_: object) -> object:
        calls["token_provider"] += 1
        return object()

    def client_factory(**_: object) -> object:
        calls["client"] += 1
        return object()

    monkeypatch.setattr(pydantic_consumer, "DefaultAzureCredential", credential_factory)
    monkeypatch.setattr(pydantic_consumer, "get_bearer_token_provider", token_provider)
    monkeypatch.setattr(pydantic_consumer, "AsyncOpenAI", client_factory)
    settings = Settings.model_construct(
        azure_ai_account=account,
        azure_openai_endpoint=AnyHttpUrl(endpoint),
        azure_model_deployment="gpt-current",
    )

    with pytest.raises(ValueError, match=message):
        pydantic_consumer.pydantic_model(settings)

    assert calls == {"credential": 0, "token_provider": 0, "client": 0}


def test_hosted_builder_dispatches_by_framework_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = object()
    monkeypatch.setattr(
        pydantic_consumer,
        "build_pydantic_hosted_graph",
        lambda agent_id, settings: (agent_id, settings, marker),
    )
    settings = Settings(_env_file=None)

    result = hosted_consumer.build_hosted_graph("consumer-demand-forecasting", settings)

    assert result == ("consumer-demand-forecasting", settings, marker)
    with pytest.raises(ValueError, match="not configured for LangGraph"):
        hosted_consumer.create_consumer_graph(
            "consumer-demand-forecasting",
            model=cast(BaseChatModel, object()),
            tools=[_tool([])],
        )


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        (
            "https://example.cognitiveservices.azure.com/",
            "https://example.cognitiveservices.azure.com/openai/v1/",
        ),
        (
            "https://example.services.ai.azure.com/openai/v1/",
            "https://example.services.ai.azure.com/openai/v1/",
        ),
    ],
)
def test_openai_v1_endpoint_normalization(endpoint: str, expected: str) -> None:
    assert pydantic_consumer._openai_v1_base_url(endpoint) == expected


def test_foundry_project_v1_endpoint_preserves_project_scope() -> None:
    assert (
        pydantic_consumer._foundry_project_v1_base_url(
            "https://example.services.ai.azure.com/api/projects/demo"
        )
        == "https://example.services.ai.azure.com/api/projects/demo/openai/v1/"
    )
