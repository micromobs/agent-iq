"""Azure embedding adapter behavior without external network access."""

from __future__ import annotations

from typing import cast

import httpx
import pytest
from pydantic import AnyHttpUrl

from agent_iq.config import Settings
from agent_iq.match.semantic import (
    AzureEmbeddingClient,
    EmbeddingResponseError,
    EmbeddingSemanticIndex,
    SemanticConfigurationError,
    build_semantic_index,
)

AZURE_AI_ACCOUNT = "agent-iq-ai"
AZURE_OPENAI_ENDPOINT = "https://agent-iq-ai.cognitiveservices.azure.com/"


def test_azure_client_uses_environment_key_without_exposing_it() -> None:
    expected_key = "-".join(("test", "key", "placeholder"))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "agent-iq-ai.cognitiveservices.azure.com"
        assert request.url.path == "/openai/deployments/embedding-model/embeddings"
        assert request.url.params["api-version"] == "2024-10-21"
        assert request.headers["api-key"] == expected_key
        inputs = request.read().decode("utf-8")
        count = inputs.count('"') // 2 - 1
        data = [{"index": index, "embedding": [float(index + 1), 1.0]} for index in range(count)]
        return httpx.Response(200, json={"data": data})

    client = AzureEmbeddingClient(
        endpoint=AZURE_OPENAI_ENDPOINT,
        deployment="embedding-model",
        api_version="2024-10-21",
        expected_account=AZURE_AI_ACCOUNT,
        api_key=expected_key,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.embed(["one", "two"]) == [[1.0, 1.0], [2.0, 1.0]]


def test_embedding_index_caches_queries() -> None:
    calls: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = request.read().decode("utf-8")
        texts = ["query"] if "query" in payload else ["one", "two"]
        calls.append(texts)
        data = [
            {"index": index, "embedding": [1.0, float(index + 1)]} for index, _ in enumerate(texts)
        ]
        return httpx.Response(200, json={"data": data})

    client = AzureEmbeddingClient(
        endpoint=AZURE_OPENAI_ENDPOINT,
        deployment="embedding-model",
        api_version="2024-10-21",
        expected_account=AZURE_AI_ACCOUNT,
        token_provider=lambda: "token-placeholder",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    index = EmbeddingSemanticIndex({"one": "one", "two": "two"}, client)

    assert index.scores("query") == index.scores("query")
    assert len(calls) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": ["not-an-object"]},
        {"data": [{"index": 0}]},
        {"data": [{"index": 0, "embedding": ["bad"]}]},
        {"data": [{"index": 1, "embedding": [1.0]}]},
    ],
)
def test_invalid_embedding_responses_fail(payload: object) -> None:
    client = AzureEmbeddingClient(
        endpoint=AZURE_OPENAI_ENDPOINT,
        deployment="embedding-model",
        api_version="2024-10-21",
        expected_account=AZURE_AI_ACCOUNT,
        token_provider=lambda: "token-placeholder",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ),
    )

    with pytest.raises(EmbeddingResponseError):
        client.embed(["one"])


def test_azure_mode_requires_complete_configuration_and_auth() -> None:
    settings = Settings(_env_file=None)
    with pytest.raises(SemanticConfigurationError, match="requires endpoint"):
        build_semantic_index({"agent": "text"}, mode="azure", settings=settings)

    with pytest.raises(SemanticConfigurationError, match="environment key"):
        AzureEmbeddingClient(
            endpoint=AZURE_OPENAI_ENDPOINT,
            deployment="embedding-model",
            api_version="2024-10-21",
            expected_account=AZURE_AI_ACCOUNT,
        )


def test_auto_mode_is_offline_only_when_azure_is_not_configured() -> None:
    index = build_semantic_index(
        {"agent": "text"},
        mode="auto",
        settings=Settings(_env_file=None),
    )

    assert index.backend_name == "tfidf-char-ngram"


def test_attacker_endpoint_never_reaches_token_or_http() -> None:
    token_calls = 0
    http_calls = 0

    def token_provider() -> str:
        nonlocal token_calls
        token_calls += 1
        return "token-placeholder"

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        return httpx.Response(200, json={"data": []})

    with pytest.raises(ValueError, match="Azure OpenAI endpoint"):
        AzureEmbeddingClient(
            endpoint="https://attacker.example/",
            deployment="embedding-model",
            api_version="2024-10-21",
            expected_account=AZURE_AI_ACCOUNT,
            token_provider=token_provider,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

    assert token_calls == 0
    assert http_calls == 0


def test_final_request_boundary_revalidates_before_token_or_http() -> None:
    token_calls = 0
    http_calls = 0

    def token_provider() -> str:
        nonlocal token_calls
        token_calls += 1
        return "token-placeholder"

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        return httpx.Response(200, json={"data": []})

    client = AzureEmbeddingClient(
        endpoint=AZURE_OPENAI_ENDPOINT,
        deployment="embedding-model",
        api_version="2024-10-21",
        expected_account=AZURE_AI_ACCOUNT,
        token_provider=token_provider,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client._endpoint = "https://attacker.example/"

    with pytest.raises(ValueError, match="Azure OpenAI endpoint"):
        client.embed(["never-send"])

    assert token_calls == 0
    assert http_calls == 0


def test_final_request_boundary_rejects_missing_account_before_token_or_http() -> None:
    token_calls = 0
    http_calls = 0

    def token_provider() -> str:
        nonlocal token_calls
        token_calls += 1
        return "token-placeholder"

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        return httpx.Response(200, json={"data": []})

    client = AzureEmbeddingClient(
        endpoint=AZURE_OPENAI_ENDPOINT,
        deployment="embedding-model",
        api_version="2024-10-21",
        expected_account=AZURE_AI_ACCOUNT,
        token_provider=token_provider,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client._expected_account = cast(str, None)

    with pytest.raises(ValueError, match="Azure AI account"):
        client.embed(["never-send"])

    assert token_calls == 0
    assert http_calls == 0


@pytest.mark.parametrize(
    ("account", "endpoint", "message"),
    [
        (None, AZURE_OPENAI_ENDPOINT, "requires an Azure AI account"),
        ("INVALID.ACCOUNT", AZURE_OPENAI_ENDPOINT, "canonical lowercase DNS label"),
        (
            AZURE_AI_ACCOUNT,
            "https://attacker.cognitiveservices.azure.com/",
            "configured Azure AI account",
        ),
    ],
)
def test_build_rejects_bypassed_unbound_endpoint_before_credential(
    monkeypatch: pytest.MonkeyPatch,
    account: str | None,
    endpoint: str,
    message: str,
) -> None:
    credential_calls = 0

    def credential_factory() -> object:
        nonlocal credential_calls
        credential_calls += 1
        return object()

    monkeypatch.setattr("azure.identity.DefaultAzureCredential", credential_factory)
    settings = Settings.model_construct(
        azure_ai_account=account,
        azure_openai_endpoint=AnyHttpUrl(endpoint),
        azure_embedding_deployment="embedding-model",
    )

    with pytest.raises(ValueError, match=message):
        build_semantic_index({"agent": "text"}, mode="azure", settings=settings)

    assert credential_calls == 0
