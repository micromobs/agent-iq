"""Provision the canonical synthetic consumer Foundry IQ knowledge foundation."""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from azure.core.credentials import TokenCredential
from azure.core.pipeline.policies import RedirectPolicy
from azure.core.pipeline.transport import RequestsTransport
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    AzureOpenAIVectorizerParameters,
    KnowledgeBase,
    KnowledgeBaseAzureOpenAIModel,
    KnowledgeSourceReference,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchIndexFieldReference,
    SearchIndexKnowledgeSource,
    SearchIndexKnowledgeSourceParameters,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
)
from azure.search.documents.knowledgebases.models import (
    KnowledgeRetrievalLowReasoningEffort,
    KnowledgeRetrievalOutputMode,
)

from agent_iq.catalog import ConsumerAssets, load_consumer_assets
from agent_iq.connectors.entra import isolated_default_azure_credential

FOUNDRY_IQ_API_VERSION = "2026-05-01-preview"
SEMANTIC_CONFIGURATION = "sem"
SEARCH_FIELDS = ("content", "title")
SOURCE_DATA_FIELDS = ("id", "title", "content", "corpusId", "segment", "category")
_RESOURCE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True, slots=True)
class CorpusProvisioning:
    """One corpus index, its documents, and its Foundry IQ knowledge source."""

    corpus_id: str
    index: SearchIndex
    documents: tuple[dict[str, str], ...]
    knowledge_source: SearchIndexKnowledgeSource


@dataclass(frozen=True, slots=True)
class KnowledgeBaseProvisioning:
    """One canonical knowledge binding rendered as a Foundry IQ knowledge base."""

    binding_id: str
    knowledge_base: KnowledgeBase


@dataclass(frozen=True, slots=True)
class ConsumerKnowledgePlan:
    """Deterministic resources required by selected consumer knowledge bindings."""

    corpora: tuple[CorpusProvisioning, ...]
    knowledge_bases: tuple[KnowledgeBaseProvisioning, ...]


@dataclass(frozen=True, slots=True)
class ConsumerKnowledgeResult:
    """Applied resource and document counts."""

    indexes: int
    knowledge_sources: int
    knowledge_bases: int
    documents: int
    deleted_stale_documents: int


class IndexAdministrationClient(Protocol):
    """Azure Search administration operations used by this provisioner."""

    def create_or_update_index(self, index: SearchIndex) -> Any: ...

    def create_or_update_knowledge_source(self, source: SearchIndexKnowledgeSource) -> Any: ...

    def create_or_update_knowledge_base(self, knowledge_base: KnowledgeBase) -> Any: ...

    def close(self) -> None: ...


class DocumentAdministrationClient(Protocol):
    """Azure Search document operations used by this provisioner."""

    def search(
        self,
        search_text: str,
        *,
        select: Sequence[str],
        top: int,
    ) -> Iterable[Mapping[str, Any]]: ...

    def upload_documents(self, documents: Sequence[Mapping[str, Any]]) -> Sequence[Any]: ...

    def delete_documents(self, documents: Sequence[Mapping[str, Any]]) -> Sequence[Any]: ...

    def close(self) -> None: ...


IndexClientFactory = Callable[[str, TokenCredential], IndexAdministrationClient]
DocumentClientFactory = Callable[[str, str, TokenCredential], DocumentAdministrationClient]


def build_consumer_knowledge_plan(
    model_endpoint: str,
    model_deployment: str,
    *,
    binding_ids: Sequence[str] = (),
    assets: ConsumerAssets | None = None,
) -> ConsumerKnowledgePlan:
    """Build deterministic Search and Foundry IQ resources from canonical assets."""

    canonical_model_endpoint = _canonical_endpoint(
        model_endpoint,
        suffix=".cognitiveservices.azure.com",
        trailing_slash=True,
    )
    if not _RESOURCE_NAME.fullmatch(model_deployment):
        raise ValueError("Model deployment is not a canonical Azure resource name.")
    active_assets = assets or load_consumer_assets()
    bindings = {
        binding.knowledge_binding_id: binding
        for binding in active_assets.knowledge_bindings.bindings
    }
    selected_ids = tuple(sorted(set(binding_ids))) if binding_ids else tuple(sorted(bindings))
    unknown = set(selected_ids) - set(bindings)
    if unknown:
        raise ValueError(f"Unknown consumer knowledge binding {sorted(unknown)[0]!r}.")
    selected_bindings = tuple(bindings[binding_id] for binding_id in selected_ids)
    corpus_ids = tuple(
        sorted({corpus_id for binding in selected_bindings for corpus_id in binding.corpus_ids})
    )
    documents_by_corpus = {
        corpus_id: tuple(
            document for document in active_assets.documents if document.corpus_id == corpus_id
        )
        for corpus_id in corpus_ids
    }
    missing = [corpus_id for corpus_id, documents in documents_by_corpus.items() if not documents]
    if missing:
        raise ValueError(f"Consumer corpus {missing[0]!r} has no canonical documents.")

    corpora = tuple(
        CorpusProvisioning(
            corpus_id=corpus_id,
            index=_search_index(corpus_id),
            documents=tuple(
                {
                    "id": document.document_id,
                    "title": document.title,
                    "content": document.content,
                    "corpusId": document.corpus_id,
                    "segment": document.segment,
                    "category": document.category,
                }
                for document in documents_by_corpus[corpus_id]
            ),
            knowledge_source=_knowledge_source(corpus_id),
        )
        for corpus_id in corpus_ids
    )
    knowledge_bases = tuple(
        KnowledgeBaseProvisioning(
            binding_id=binding.knowledge_binding_id,
            knowledge_base=_knowledge_base(
                binding.knowledge_binding_id,
                binding.corpus_ids,
                canonical_model_endpoint,
                model_deployment,
            ),
        )
        for binding in selected_bindings
    )
    return ConsumerKnowledgePlan(corpora=corpora, knowledge_bases=knowledge_bases)


def provision_consumer_knowledge(
    search_endpoint: str,
    model_endpoint: str,
    model_deployment: str,
    *,
    binding_ids: Sequence[str] = (),
    assets: ConsumerAssets | None = None,
    credential: TokenCredential | None = None,
    index_client_factory: IndexClientFactory | None = None,
    document_client_factory: DocumentClientFactory | None = None,
) -> ConsumerKnowledgeResult:
    """Idempotently reconcile selected consumer indexes, sources, and knowledge bases."""

    canonical_search_endpoint = _canonical_endpoint(
        search_endpoint,
        suffix=".search.windows.net",
        trailing_slash=False,
    )
    plan = build_consumer_knowledge_plan(
        model_endpoint,
        model_deployment,
        binding_ids=binding_ids,
        assets=assets,
    )
    active_credential = credential or isolated_default_azure_credential(
        credential_factory=DefaultAzureCredential,
    )
    owns_credential = credential is None
    create_index_client = index_client_factory or _default_index_client
    create_document_client = document_client_factory or _default_document_client
    index_client = create_index_client(canonical_search_endpoint, active_credential)
    deleted_stale = 0
    try:
        for corpus in plan.corpora:
            index_client.create_or_update_index(corpus.index)
            document_client = create_document_client(
                canonical_search_endpoint,
                corpus.corpus_id,
                active_credential,
            )
            try:
                expected_ids = {document["id"] for document in corpus.documents}
                existing_ids = {
                    str(item["id"])
                    for item in document_client.search(
                        "*",
                        select=["id"],
                        top=1000,
                    )
                }
                stale_ids = sorted(existing_ids - expected_ids)
                if stale_ids:
                    _require_success(
                        document_client.delete_documents(
                            [{"id": document_id} for document_id in stale_ids]
                        ),
                        operation=f"delete stale documents from {corpus.corpus_id}",
                        expected=len(stale_ids),
                    )
                    deleted_stale += len(stale_ids)
                _require_success(
                    document_client.upload_documents(corpus.documents),
                    operation=f"upload documents to {corpus.corpus_id}",
                    expected=len(corpus.documents),
                )
            finally:
                document_client.close()
            index_client.create_or_update_knowledge_source(corpus.knowledge_source)
        for binding in plan.knowledge_bases:
            index_client.create_or_update_knowledge_base(binding.knowledge_base)
    finally:
        index_client.close()
        if owns_credential:
            cast(DefaultAzureCredential, active_credential).close()
    return ConsumerKnowledgeResult(
        indexes=len(plan.corpora),
        knowledge_sources=len(plan.corpora),
        knowledge_bases=len(plan.knowledge_bases),
        documents=sum(len(corpus.documents) for corpus in plan.corpora),
        deleted_stale_documents=deleted_stale,
    )


def _search_index(corpus_id: str) -> SearchIndex:
    fields = [
        SearchField(name="id", type=SearchFieldDataType.String, key=True),
        SearchField(name="title", type=SearchFieldDataType.String, searchable=True),
        SearchField(name="content", type=SearchFieldDataType.String, searchable=True),
        SearchField(
            name="corpusId",
            type=SearchFieldDataType.String,
            filterable=True,
        ),
        SearchField(
            name="segment",
            type=SearchFieldDataType.String,
            filterable=True,
            searchable=True,
        ),
        SearchField(
            name="category",
            type=SearchFieldDataType.String,
            filterable=True,
            searchable=True,
        ),
    ]
    semantic = SemanticSearch(
        configurations=[
            SemanticConfiguration(
                name=SEMANTIC_CONFIGURATION,
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="title"),
                    content_fields=[SemanticField(field_name="content")],
                    keywords_fields=[SemanticField(field_name="category")],
                ),
            )
        ]
    )
    return SearchIndex(name=corpus_id, fields=fields, semantic_search=semantic)


def _knowledge_source(corpus_id: str) -> SearchIndexKnowledgeSource:
    return SearchIndexKnowledgeSource(
        name=_knowledge_source_name(corpus_id),
        description=(
            "Fabrikam Consumer Products synthetic-demo knowledge source over "
            f"search index {corpus_id!r}."
        ),
        search_index_parameters=SearchIndexKnowledgeSourceParameters(
            search_index_name=corpus_id,
            semantic_configuration_name=SEMANTIC_CONFIGURATION,
            search_fields=[SearchIndexFieldReference(name=name) for name in SEARCH_FIELDS],
            source_data_fields=[
                SearchIndexFieldReference(name=name) for name in SOURCE_DATA_FIELDS
            ],
        ),
    )


def _knowledge_base(
    binding_id: str,
    corpus_ids: Sequence[str],
    model_endpoint: str,
    model_deployment: str,
) -> KnowledgeBase:
    model = KnowledgeBaseAzureOpenAIModel(
        azure_open_ai_parameters=AzureOpenAIVectorizerParameters(
            resource_url=model_endpoint,
            deployment_name=model_deployment,
            model_name=model_deployment,
        )
    )
    return KnowledgeBase(
        name=binding_id,
        description=(
            f"Fabrikam Consumer Products synthetic-demo Foundry IQ knowledge base for {binding_id}."
        ),
        retrieval_instructions=(
            "Use the approved synthetic consumer knowledge sources. Decompose only when "
            "needed and retrieve the smallest sufficient evidence set for the exact question."
        ),
        answer_instructions=(
            "Answer strictly from retrieved documents. Every cited claim must use the exact "
            "format 'Source title: <exact source title> [ref_id:n]'. Never return a bare "
            "[ref_id:n] marker without its source title. If the answer is absent, state the "
            "gap without guessing."
        ),
        output_mode=KnowledgeRetrievalOutputMode.ANSWER_SYNTHESIS,
        knowledge_sources=[
            KnowledgeSourceReference(name=_knowledge_source_name(corpus_id))
            for corpus_id in corpus_ids
        ],
        models=[model],
        retrieval_reasoning_effort=KnowledgeRetrievalLowReasoningEffort(),
    )


def _knowledge_source_name(corpus_id: str) -> str:
    return f"{corpus_id}-ks"


def _canonical_endpoint(value: str, *, suffix: str, trailing_slash: bool) -> str:
    if not value or value != value.strip() or any(character.isspace() for character in value):
        raise ValueError("Azure endpoint contains whitespace or is empty.")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.casefold().endswith(suffix)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(f"Azure endpoint must be a canonical HTTPS {suffix} origin.")
    origin = f"https://{parsed.hostname.casefold()}"
    return f"{origin}/" if trailing_slash else origin


def _require_success(results: Sequence[Any], *, operation: str, expected: int) -> None:
    items = list(results)
    failed = [item for item in items if not _indexing_succeeded(item)]
    if len(items) != expected or failed:
        detail = str(_indexing_error(failed[0]) or "") if failed else ""
        suffix = f" {detail}" if detail else ""
        raise RuntimeError(
            f"Foundry IQ could not {operation}: expected {expected} successes, "
            f"received {len(items) - len(failed)}.{suffix}"
        )


def _indexing_succeeded(value: Any) -> bool:
    succeeded = getattr(value, "succeeded", None)
    if succeeded is None:
        succeeded = _field(value, "status")
    if not isinstance(succeeded, bool):
        raise RuntimeError("Azure Search returned a malformed indexing result.")
    return succeeded


def _indexing_error(value: Any) -> Any:
    error = getattr(value, "error_message", None)
    return error if error is not None else _field(value, "errorMessage")


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _default_index_client(
    endpoint: str,
    credential: TokenCredential,
) -> IndexAdministrationClient:
    return cast(
        IndexAdministrationClient,
        SearchIndexClient(
            endpoint,
            credential,
            api_version=FOUNDRY_IQ_API_VERSION,
            transport=RequestsTransport(use_env_settings=False),
            redirect_policy=RedirectPolicy(permit_redirects=False),
        ),
    )


def _default_document_client(
    endpoint: str,
    index_name: str,
    credential: TokenCredential,
) -> DocumentAdministrationClient:
    return cast(
        DocumentAdministrationClient,
        SearchClient(
            endpoint,
            index_name,
            credential,
            api_version=FOUNDRY_IQ_API_VERSION,
            transport=RequestsTransport(use_env_settings=False),
            redirect_policy=RedirectPolicy(permit_redirects=False),
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Provision selected canonical bindings from the command line."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search-endpoint", required=True)
    parser.add_argument("--model-endpoint", required=True)
    parser.add_argument("--model-deployment", required=True)
    parser.add_argument("--binding", action="append", default=[])
    arguments = parser.parse_args(argv)
    result = provision_consumer_knowledge(
        arguments.search_endpoint,
        arguments.model_endpoint,
        arguments.model_deployment,
        binding_ids=arguments.binding,
    )
    print(
        "Consumer Foundry IQ foundation: "
        f"indexes={result.indexes} sources={result.knowledge_sources} "
        f"knowledge_bases={result.knowledge_bases} documents={result.documents} "
        f"stale_deleted={result.deleted_stale_documents}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
