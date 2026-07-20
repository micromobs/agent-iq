"""Keyless structured-source retrieval from a Foundry IQ knowledge base."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from azure.core.credentials import TokenCredential
from azure.core.pipeline.policies import RedirectPolicy
from azure.core.pipeline.transport import RequestsTransport
from azure.identity import DefaultAzureCredential
from azure.search.documents.knowledgebases import KnowledgeBaseRetrievalClient
from azure.search.documents.knowledgebases.models import (
    KnowledgeBaseMessage,
    KnowledgeBaseMessageTextContent,
    KnowledgeBaseRetrievalRequest,
)

from agent_iq.catalog import ConsumerAssets, load_consumer_assets
from agent_iq.connectors.entra import isolated_default_azure_credential
from agent_iq.contracts import ErrorCode
from agent_iq.errors import operation_error


@dataclass(frozen=True, slots=True)
class RetrievedEvidenceSource:
    """One stable source reference returned by Foundry IQ."""

    local_reference_id: str
    source_id: str
    title: str
    knowledge_source: str
    source_type: str | None = None
    reranker_score: float | None = None


class KnowledgeBaseClient(Protocol):
    def retrieve(self, request: Any) -> Any: ...

    def close(self) -> None: ...


ClientFactory = Callable[[str, TokenCredential, str], KnowledgeBaseClient]
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\r?\n+")


class FoundryIQEvidenceRetriever:
    """Retrieve source records separately from model-formatted answer prose."""

    def __init__(
        self,
        search_endpoint: str,
        *,
        assets: ConsumerAssets | None = None,
        credential: TokenCredential | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._search_endpoint = search_endpoint.rstrip("/")
        if not self._search_endpoint.startswith("https://"):
            raise ValueError("Foundry IQ evidence retrieval requires an HTTPS Search endpoint.")
        self._assets = assets or load_consumer_assets()
        self._credential = credential or isolated_default_azure_credential(
            credential_factory=DefaultAzureCredential,
        )
        self._owns_credential = credential is None
        self._client_factory = client_factory or _default_client_factory
        self._clients: dict[str, KnowledgeBaseClient] = {}
        self._knowledge_by_agent = {
            runtime.agent_id: runtime.knowledge_binding_id
            for runtime in self._assets.runtime.agents
        }

    async def retrieve(
        self,
        agent_id: str,
        prompt: str,
    ) -> tuple[RetrievedEvidenceSource, ...]:
        """Return only structured references for the agent's canonical knowledge base."""

        try:
            knowledge_source = self._knowledge_by_agent[agent_id]
        except KeyError as exc:
            raise operation_error(
                ErrorCode.EVIDENCE_UNAVAILABLE,
                "The agent has no canonical Foundry IQ evidence binding.",
                correlation_id=agent_id,
            ) from exc
        return await asyncio.to_thread(
            self._retrieve_sync,
            knowledge_source,
            prompt,
        )

    def close(self) -> None:
        try:
            for client in self._clients.values():
                client.close()
        finally:
            self._clients.clear()
            if self._owns_credential:
                cast(DefaultAzureCredential, self._credential).close()

    def _retrieve_sync(
        self,
        knowledge_source: str,
        prompt: str,
    ) -> tuple[RetrievedEvidenceSource, ...]:
        client = self._clients.get(knowledge_source)
        if client is None:
            client = self._client_factory(
                self._search_endpoint,
                self._credential,
                knowledge_source,
            )
            self._clients[knowledge_source] = client
        for query in _retrieval_queries(prompt):
            request = KnowledgeBaseRetrievalRequest(
                messages=[
                    KnowledgeBaseMessage(
                        role="user",
                        content=[KnowledgeBaseMessageTextContent(text=query)],
                    )
                ]
            )
            response = cast(Mapping[str, Any], dict(client.retrieve(request)))
            references = tuple(
                source
                for source in (
                    _reference(item, knowledge_source)
                    for item in cast(list[Any], response.get("references") or [])
                )
                if source is not None
            )
            if references:
                return references
        raise operation_error(
            ErrorCode.EVIDENCE_UNAVAILABLE,
            "Foundry IQ returned no structured source references.",
            correlation_id=knowledge_source,
        )


def _default_client_factory(
    endpoint: str,
    credential: TokenCredential,
    knowledge_source: str,
) -> KnowledgeBaseClient:
    return cast(
        KnowledgeBaseClient,
        KnowledgeBaseRetrievalClient(
            endpoint,
            credential,
            knowledge_base_name=knowledge_source,
            transport=RequestsTransport(use_env_settings=False),
            redirect_policy=RedirectPolicy(permit_redirects=False),
        ),
    )


def _retrieval_queries(prompt: str) -> tuple[str, ...]:
    full_prompt = prompt.strip()
    queries = [full_prompt]
    for sentence in _SENTENCE_SPLIT.split(full_prompt):
        focused = sentence.strip().rstrip(".!?").strip()
        if focused and focused != full_prompt and len(focused.split()) >= 3:
            queries.append(focused)
    return tuple(dict.fromkeys(queries))


def _reference(
    value: Any,
    knowledge_source: str,
) -> RetrievedEvidenceSource | None:
    source_id = str(_field(value, "docKey") or _field(value, "doc_key") or "").strip()
    raw_reference_id = _field(value, "id")
    local_reference_id = "" if raw_reference_id is None else str(raw_reference_id).strip()
    title = str(_field(value, "title") or "").strip()
    if not source_id or not local_reference_id or not title:
        return None
    score_value = _field(value, "rerankerScore")
    if score_value is None:
        score_value = _field(value, "reranker_score")
    score = float(score_value) if score_value is not None else None
    source_type = str(_field(value, "type") or "").strip() or None
    return RetrievedEvidenceSource(
        local_reference_id=local_reference_id,
        source_id=source_id,
        title=title,
        knowledge_source=knowledge_source,
        source_type=source_type,
        reranker_score=score,
    )


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)
