"""Offline semantic matching and real Azure embedding support."""

from __future__ import annotations

import math
import time
from collections import Counter
from collections.abc import Callable
from typing import ClassVar, Literal, Protocol
from urllib.parse import quote

import httpx

from agent_iq.config import Settings, canonicalize_azure_openai_endpoint
from agent_iq.connectors.entra import isolated_default_azure_credential

from .lexical import tokenize

SemanticMode = Literal["auto", "offline", "azure"]


class SemanticConfigurationError(ValueError):
    """The requested semantic backend lacks required configuration."""


class EmbeddingResponseError(RuntimeError):
    """Azure returned a response that cannot be used as embedding vectors."""


class SemanticIndex(Protocol):
    backend_name: str

    def scores(self, query: str) -> dict[str, float]: ...


class OfflineSemanticIndex:
    """TF-IDF over tokens and character trigrams for deterministic offline use."""

    backend_name = "tfidf-char-ngram"
    _TRIGRAM_WEIGHT = 0.5

    def __init__(self, documents: dict[str, str]):
        self.ids = tuple(documents)
        self._document_features = {
            agent_id: self._features(documents[agent_id]) for agent_id in self.ids
        }

        document_frequency: dict[str, int] = {}
        for agent_id in self.ids:
            for feature in set(self._document_features[agent_id]):
                document_frequency[feature] = document_frequency.get(feature, 0) + 1
        count = len(self.ids) or 1
        self._inverse_document_frequency = {
            feature: math.log((count + 1) / (frequency + 1)) + 1.0
            for feature, frequency in document_frequency.items()
        }
        self._document_vectors = {
            agent_id: self._vectorize(self._document_features[agent_id]) for agent_id in self.ids
        }
        self._document_norms = {
            agent_id: _mapping_norm(vector) or 1.0
            for agent_id, vector in self._document_vectors.items()
        }

    @staticmethod
    def _features(text: str) -> list[str]:
        tokens = tokenize(text)
        features = list(tokens)
        for token in tokens:
            padded = f"#{token}#"
            for offset in range(len(padded) - 2):
                features.append("$" + padded[offset : offset + 3])
        return features

    def _vectorize(self, features: list[str]) -> dict[str, float]:
        vector: dict[str, float] = {}
        for feature, count in Counter(features).items():
            inverse_frequency = self._inverse_document_frequency.get(feature)
            if inverse_frequency is None:
                continue
            weight = self._TRIGRAM_WEIGHT if feature.startswith("$") else 1.0
            vector[feature] = count * inverse_frequency * weight
        return vector

    def scores(self, query: str) -> dict[str, float]:
        query_vector = self._vectorize(self._features(query))
        query_norm = _mapping_norm(query_vector) or 1.0
        scores: dict[str, float] = {}
        for agent_id in self.ids:
            document_vector = self._document_vectors[agent_id]
            small, large = (
                (query_vector, document_vector)
                if len(query_vector) <= len(document_vector)
                else (document_vector, query_vector)
            )
            dot = sum(weight * large.get(feature, 0.0) for feature, weight in small.items())
            scores[agent_id] = dot / (query_norm * self._document_norms[agent_id])
        return scores


class AzureEmbeddingClient:
    """Minimal Azure OpenAI embeddings client with key or Entra authentication."""

    _RETRYABLE_STATUS: ClassVar[set[int]] = {
        404,
        408,
        429,
        500,
        502,
        503,
        504,
    }

    def __init__(
        self,
        *,
        endpoint: str,
        deployment: str,
        api_version: str,
        expected_account: str,
        api_key: str | None = None,
        token_provider: Callable[[], str] | None = None,
        client: httpx.Client | None = None,
        attempts: int = 3,
    ):
        self._endpoint = canonicalize_azure_openai_endpoint(
            endpoint,
            expected_account=expected_account,
        )
        self._expected_account = expected_account
        if not api_key and token_provider is None:
            raise SemanticConfigurationError(
                "Azure embeddings require an environment key or Entra credential."
            )
        self._deployment = deployment
        self._api_version = api_version
        self._api_key = api_key
        self._token_provider = token_provider
        self._client = client
        self._attempts = max(1, attempts)

    def embed(self, texts: list[str]) -> list[list[float]]:
        endpoint = canonicalize_azure_openai_endpoint(
            self._endpoint,
            expected_account=self._expected_account,
        )
        url = (
            f"{endpoint}openai/deployments/{quote(self._deployment, safe='')}"
            f"/embeddings?api-version={quote(self._api_version, safe='')}"
        )
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["api-key"] = self._api_key
        else:
            assert self._token_provider is not None
            headers["Authorization"] = f"Bearer {self._token_provider()}"

        response: httpx.Response | None = None
        for attempt in range(self._attempts):
            if self._client is None:
                with httpx.Client(
                    timeout=60.0,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    response = client.post(url, headers=headers, json={"input": texts})
            else:
                response = self._client.post(url, headers=headers, json={"input": texts})
            if response.status_code not in self._RETRYABLE_STATUS:
                break
            if attempt < self._attempts - 1:
                time.sleep(0.25 * (2**attempt))

        assert response is not None
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or len(data) != len(texts):
            raise EmbeddingResponseError("Azure embedding response has an invalid data array.")

        vectors_by_index: dict[int, list[float]] = {}
        for item in data:
            if not isinstance(item, dict):
                raise EmbeddingResponseError("Azure embedding item is not an object.")
            index = item.get("index")
            vector = item.get("embedding")
            if not isinstance(index, int) or not isinstance(vector, list):
                raise EmbeddingResponseError("Azure embedding item is missing index or vector.")
            if not all(isinstance(value, int | float) for value in vector):
                raise EmbeddingResponseError("Azure embedding vector contains a non-number.")
            vectors_by_index[index] = [float(value) for value in vector]

        try:
            return [vectors_by_index[index] for index in range(len(texts))]
        except KeyError as exc:
            raise EmbeddingResponseError("Azure embedding response has missing indexes.") from exc


class EmbeddingSemanticIndex:
    """Cosine similarity over vectors supplied by an embedding client."""

    backend_name = "azure-embeddings"

    def __init__(self, documents: dict[str, str], client: AzureEmbeddingClient):
        self.ids = tuple(documents)
        self._client = client
        vectors = client.embed([documents[agent_id] for agent_id in self.ids])
        self._document_vectors = dict(zip(self.ids, vectors, strict=True))
        self._document_norms = {
            agent_id: _sequence_norm(vector) or 1.0
            for agent_id, vector in self._document_vectors.items()
        }
        self._query_cache: dict[str, list[float]] = {}

    def scores(self, query: str) -> dict[str, float]:
        vector = self._query_cache.get(query)
        if vector is None:
            vector = self._client.embed([query])[0]
            if len(self._query_cache) < 4_096:
                self._query_cache[query] = vector
        query_norm = _sequence_norm(vector) or 1.0
        return {
            agent_id: _dot(vector, self._document_vectors[agent_id])
            / (query_norm * self._document_norms[agent_id])
            for agent_id in self.ids
        }


def build_semantic_index(
    documents: dict[str, str],
    *,
    mode: SemanticMode,
    settings: Settings,
    client: AzureEmbeddingClient | None = None,
) -> SemanticIndex:
    """Build the explicitly configured backend without failure-shaped fallback."""

    configured = bool(settings.azure_openai_endpoint and settings.azure_embedding_deployment)
    if mode == "offline" or (mode == "auto" and not configured):
        return OfflineSemanticIndex(documents)
    if not configured:
        raise SemanticConfigurationError(
            "Azure semantic mode requires endpoint and embedding deployment."
        )
    account = settings.azure_ai_account
    if account is None:
        raise SemanticConfigurationError(
            "Azure semantic mode requires an Azure AI account bound to its endpoint."
        )
    endpoint = canonicalize_azure_openai_endpoint(
        str(settings.azure_openai_endpoint),
        expected_account=account,
    )

    if client is None:
        api_key = (
            settings.azure_api_key.get_secret_value()
            if settings.azure_api_key is not None
            else None
        )
        token_provider: Callable[[], str] | None = None
        if api_key is None:
            from azure.identity import DefaultAzureCredential

            credential = isolated_default_azure_credential(
                credential_factory=DefaultAzureCredential,
            )

            def get_token() -> str:
                return credential.get_token("https://cognitiveservices.azure.com/.default").token

            token_provider = get_token
        client = AzureEmbeddingClient(
            endpoint=endpoint,
            deployment=settings.azure_embedding_deployment or "",
            api_version=settings.azure_embedding_api_version,
            expected_account=account,
            api_key=api_key,
            token_provider=token_provider,
        )
    return EmbeddingSemanticIndex(documents, client)


def _mapping_norm(vector: dict[str, float]) -> float:
    return math.sqrt(sum(value * value for value in vector.values()))


def _sequence_norm(vector: list[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))


def _dot(left: list[float], right: list[float]) -> float:
    return sum(x * y for x, y in zip(left, right, strict=True))
