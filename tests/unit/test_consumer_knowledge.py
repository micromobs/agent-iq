"""Canonical synthetic-consumer Foundry IQ provisioning tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from typing import Any

import pytest

from agent_iq.deployments.consumer_knowledge import (
    build_consumer_knowledge_plan,
    provision_consumer_knowledge,
)

MODEL_ENDPOINT = "https://agent-iq-example-ai.cognitiveservices.azure.com/"
SEARCH_ENDPOINT = "https://agent-iq-example-search.search.windows.net"


def test_demand_plan_uses_only_its_canonical_corpus_and_source_titles() -> None:
    plan = build_consumer_knowledge_plan(
        MODEL_ENDPOINT,
        "gpt-5.4-mini",
        binding_ids=["consumer-supply-manufacturing-kb"],
    )

    assert [corpus.corpus_id for corpus in plan.corpora] == ["consumer-supply-manufacturing-kb"]
    assert [binding.binding_id for binding in plan.knowledge_bases] == [
        "consumer-supply-manufacturing-kb"
    ]
    documents = plan.corpora[0].documents
    demand = next(document for document in documents if document["id"] == "consumer-demand-sensing")
    assert demand["title"] == "Adaptive Supply Network demand sensing"
    assert "forecast error" in demand["content"]
    assert "stockouts" in demand["content"]
    source = plan.corpora[0].knowledge_source
    assert source.name == "consumer-supply-manufacturing-kb-ks"
    assert source.search_index_parameters.search_index_name == plan.corpora[0].corpus_id


def test_shared_consumer_care_binding_reuses_two_canonical_sources() -> None:
    plan = build_consumer_knowledge_plan(
        MODEL_ENDPOINT,
        "gpt-5.4-mini",
        binding_ids=["consumer-consumer-care-kb"],
    )

    assert [corpus.corpus_id for corpus in plan.corpora] == [
        "consumer-market-brand-consumer-kb",
        "consumer-safety-regulatory-quality-kb",
    ]
    knowledge_base = plan.knowledge_bases[0].knowledge_base
    assert [source.name for source in knowledge_base.knowledge_sources] == [
        "consumer-market-brand-consumer-kb-ks",
        "consumer-safety-regulatory-quality-kb-ks",
    ]
    assert knowledge_base.answer_instructions is not None
    assert (
        "source title: <exact source title> [ref_id:n]"
        in knowledge_base.answer_instructions.casefold()
    )


def test_plan_rejects_unknown_binding_and_noncanonical_endpoints() -> None:
    with pytest.raises(ValueError, match="Unknown consumer knowledge binding"):
        build_consumer_knowledge_plan(
            MODEL_ENDPOINT,
            "gpt-5.4-mini",
            binding_ids=["consumer-unknown-kb"],
        )
    with pytest.raises(ValueError, match="canonical HTTPS"):
        provision_consumer_knowledge(
            "https://attacker.example",
            MODEL_ENDPOINT,
            "gpt-5.4-mini",
            credential=object(),  # type: ignore[arg-type]
        )


class _FakeIndexClient:
    def __init__(self) -> None:
        self.indexes: list[str] = []
        self.sources: list[str] = []
        self.knowledge_bases: list[str] = []
        self.closed = False

    def create_or_update_index(self, index: Any) -> None:
        self.indexes.append(index.name)

    def create_or_update_knowledge_source(self, source: Any) -> None:
        self.sources.append(source.name)

    def create_or_update_knowledge_base(self, knowledge_base: Any) -> None:
        self.knowledge_bases.append(knowledge_base.name)

    def close(self) -> None:
        self.closed = True


class _FakeDocumentClient:
    def __init__(self, *, fail_upload: bool = False) -> None:
        self.fail_upload = fail_upload
        self.uploaded: list[str] = []
        self.deleted: list[str] = []
        self.closed = False

    def search(
        self,
        search_text: str,
        *,
        select: Sequence[str],
        top: int,
    ) -> list[Mapping[str, Any]]:
        assert search_text == "*"
        assert select == ["id"]
        assert top == 1000
        return [{"id": "stale-document"}]

    def upload_documents(
        self,
        documents: Sequence[Mapping[str, Any]],
    ) -> Sequence[Mapping[str, Any] | SimpleNamespace]:
        self.uploaded.extend(str(document["id"]) for document in documents)
        return [
            SimpleNamespace(
                succeeded=not self.fail_upload,
                error_message="denied" if self.fail_upload else None,
            )
            for _ in documents
        ]

    def delete_documents(
        self,
        documents: Sequence[Mapping[str, Any]],
    ) -> list[SimpleNamespace]:
        self.deleted.extend(str(document["id"]) for document in documents)
        return [SimpleNamespace(succeeded=True) for _ in documents]

    def close(self) -> None:
        self.closed = True


def test_provisioner_reconciles_stale_documents_and_closes_clients() -> None:
    index_client = _FakeIndexClient()
    document_client = _FakeDocumentClient()

    result = provision_consumer_knowledge(
        SEARCH_ENDPOINT,
        MODEL_ENDPOINT,
        "gpt-5.4-mini",
        binding_ids=["consumer-supply-manufacturing-kb"],
        credential=object(),  # type: ignore[arg-type]
        index_client_factory=lambda *_: index_client,
        document_client_factory=lambda *_: document_client,
    )

    assert result.indexes == 1
    assert result.knowledge_sources == 1
    assert result.knowledge_bases == 1
    assert result.documents == len(document_client.uploaded)
    assert result.deleted_stale_documents == 1
    assert document_client.deleted == ["stale-document"]
    assert index_client.knowledge_bases == ["consumer-supply-manufacturing-kb"]
    assert index_client.closed is True
    assert document_client.closed is True


def test_provisioner_fails_closed_on_partial_upload() -> None:
    index_client = _FakeIndexClient()
    document_client = _FakeDocumentClient(fail_upload=True)

    with pytest.raises(RuntimeError, match="upload documents"):
        provision_consumer_knowledge(
            SEARCH_ENDPOINT,
            MODEL_ENDPOINT,
            "gpt-5.4-mini",
            binding_ids=["consumer-supply-manufacturing-kb"],
            credential=object(),  # type: ignore[arg-type]
            index_client_factory=lambda *_: index_client,
            document_client_factory=lambda *_: document_client,
        )

    assert index_client.knowledge_bases == []
    assert index_client.closed is True
    assert document_client.closed is True


def test_provisioner_accepts_sdk_mapping_aliases_for_indexing_results() -> None:
    class MappingDocumentClient(_FakeDocumentClient):
        def upload_documents(
            self,
            documents: Sequence[Mapping[str, Any]],
        ) -> list[Mapping[str, Any]]:
            self.uploaded.extend(str(document["id"]) for document in documents)
            return [{"status": True, "errorMessage": None} for _ in documents]

    index_client = _FakeIndexClient()
    document_client = MappingDocumentClient()

    result = provision_consumer_knowledge(
        SEARCH_ENDPOINT,
        MODEL_ENDPOINT,
        "gpt-5.4-mini",
        binding_ids=["consumer-supply-manufacturing-kb"],
        credential=object(),  # type: ignore[arg-type]
        index_client_factory=lambda *_: index_client,
        document_client_factory=lambda *_: document_client,
    )

    assert result.knowledge_bases == 1
