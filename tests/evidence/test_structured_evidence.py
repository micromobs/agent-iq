"""Structured evidence mapping, isolation, and A2A shape tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from a2a.types import Artifact, Part, SendMessageResponse, Task, TaskState, TaskStatus
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Value
from pydantic import ValidationError

from agent_iq.contracts import (
    CallerContext,
    ErrorCode,
    EvidenceEnvelope,
    EvidenceFetchRequest,
)
from agent_iq.errors import AgentIqOperationError
from agent_iq.evidence import (
    A2AEventShapeRecorder,
    CompanionEvidenceStore,
    EvidenceRecordMapper,
    FoundryIQEvidenceRetriever,
    RetrievedEvidenceSource,
    StructuredEvidenceDispatcher,
)
from agent_iq.runtime import NativeA2ARequest, NativeA2AResult

AGENT_ID = "consumer-formulation-rd"
KNOWLEDGE_SOURCE = "consumer-product-innovation-kb"
NOW = datetime(2026, 7, 10, 18, 0, tzinfo=UTC)


def _source(
    reference_id: str,
    source_id: str,
    title: str,
    *,
    knowledge_source: str = KNOWLEDGE_SOURCE,
) -> RetrievedEvidenceSource:
    return RetrievedEvidenceSource(
        local_reference_id=reference_id,
        source_id=source_id,
        title=title,
        knowledge_source=knowledge_source,
    )


def _references() -> tuple[RetrievedEvidenceSource, ...]:
    return (
        _source(
            "0",
            "consumer-brightwash-cold",
            "BrightWash Cold active system",
        ),
        _source(
            "1",
            "consumer-comparison-method",
            "Governed product comparison method",
        ),
    )


def _caller(subject: str = "caller-1", tenant: str = "tenant-1") -> CallerContext:
    return CallerContext(subject_id=subject, tenant_id=tenant)


def test_mapper_uses_stable_allowlisted_sources_and_rejects_unknown_ids() -> None:
    mapper = EvidenceRecordMapper()
    records = mapper.map(
        invocation_id="inv-1",
        agent_id=AGENT_ID,
        references=_references(),
        retrieved_at=NOW,
    )

    assert [record.source_id for record in records] == [
        "consumer-brightwash-cold",
        "consumer-comparison-method",
    ]
    assert [record.local_reference_id for record in records] == ["0", "1"]
    assert all(record.locator.uri is None for record in records)
    assert all(record.locator.document_key == record.source_id for record in records)
    assert all(
        record.content_hash and record.content_hash.startswith("sha256:") for record in records
    )

    with pytest.raises(AgentIqOperationError) as caught:
        mapper.map(
            invocation_id="inv-unknown",
            agent_id=AGENT_ID,
            references=(_source("0", "unknown-source", "Unknown"),),
            retrieved_at=NOW,
        )
    assert caught.value.error.code is ErrorCode.EVIDENCE_UNAVAILABLE
    assert "no locator" in caught.value.error.message

    with pytest.raises(AgentIqOperationError, match="crossed"):
        mapper.map(
            invocation_id="inv-wrong-kb",
            agent_id=AGENT_ID,
            references=(
                _source(
                    "0",
                    "consumer-brightwash-cold",
                    "BrightWash Cold active system",
                    knowledge_source="other-kb",
                ),
            ),
            retrieved_at=NOW,
        )


def test_companion_store_enforces_caller_tenant_invocation_and_expiry() -> None:
    current = [NOW]
    mapper = EvidenceRecordMapper()
    records = mapper.map(
        invocation_id="inv-1",
        agent_id=AGENT_ID,
        references=_references(),
        retrieved_at=NOW,
    )
    store = CompanionEvidenceStore(
        ttl=timedelta(minutes=5),
        clock=lambda: current[0],
        handle_factory=lambda: "evh-fixed",
    )
    reference = store.publish(records, caller=_caller(), correlation_id="corr-1")
    request = EvidenceFetchRequest.from_reference(reference)

    envelope = store.fetch(request, caller=_caller())
    assert envelope.records == records

    for caller in (_caller("caller-2"), _caller(tenant="tenant-2")):
        with pytest.raises(AgentIqOperationError) as denied:
            store.fetch(request, caller=caller)
        assert denied.value.error.code is ErrorCode.AUTHORIZATION_DENIED

    for tampered in (
        request.model_copy(update={"invocation_id": "inv-2"}),
        request.model_copy(update={"correlation_id": "corr-2"}),
        request.model_copy(update={"handle": "evh-missing"}),
    ):
        with pytest.raises(AgentIqOperationError) as denied:
            store.fetch(tampered, caller=_caller())
        assert denied.value.error.code is ErrorCode.AUTHORIZATION_DENIED

    current[0] += timedelta(minutes=6)
    with pytest.raises(AgentIqOperationError) as expired:
        store.fetch(request, caller=_caller())
    assert expired.value.error.code is ErrorCode.EVIDENCE_UNAVAILABLE


def test_envelope_rejects_cross_invocation_or_agent_records() -> None:
    record = EvidenceRecordMapper().map(
        invocation_id="inv-1",
        agent_id=AGENT_ID,
        references=_references(),
        retrieved_at=NOW,
    )[0]

    with pytest.raises(ValidationError, match="invocation"):
        EvidenceEnvelope(
            invocation_id="inv-2",
            agent_id=AGENT_ID,
            records=(record,),
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=1),
        )
    with pytest.raises(ValidationError, match="agent"):
        EvidenceEnvelope(
            invocation_id="inv-1",
            agent_id="consumer-brand-marketing",
            records=(record,),
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=1),
        )
    with pytest.raises(ValidationError, match="expiry"):
        EvidenceEnvelope(
            invocation_id="inv-1",
            agent_id=AGENT_ID,
            records=(record,),
            issued_at=NOW,
            expires_at=NOW,
        )


def test_structured_dispatcher_attaches_fetchable_response_metadata() -> None:
    class FakeNative:
        def __init__(self) -> None:
            self.cancel_event: asyncio.Event | None = None

        async def dispatch(
            self,
            request: NativeA2ARequest,
            *,
            cancel_event: asyncio.Event | None = None,
        ) -> NativeA2AResult:
            self.cancel_event = cancel_event
            return NativeA2AResult(
                invocation_id="inv-live",
                agent_id=request.agent_id,
                agent_version="2",
                protocol_version="1.0",
                message_id="msg-live",
                answer="Grounded answer. [ref_id:0] [ref_id:1]",
                duration_ms=10,
                attempt_count=1,
                correlation_id=request.correlation_id,
            )

    class FakeRetriever:
        async def retrieve(
            self,
            agent_id: str,
            prompt: str,
        ) -> tuple[RetrievedEvidenceSource, ...]:
            assert agent_id == AGENT_ID
            assert prompt == "Prompt."
            return _references()

    native = FakeNative()
    store = CompanionEvidenceStore(
        clock=lambda: NOW,
        handle_factory=lambda: "evh-dispatch",
    )
    dispatcher = StructuredEvidenceDispatcher(
        native,
        FakeRetriever(),
        store=store,
        minimum_records=2,
    )
    cancel_event = asyncio.Event()
    request = NativeA2ARequest(
        agent_id=AGENT_ID,
        prompt="Prompt.",
        correlation_id="corr-live",
    )
    result = asyncio.run(
        dispatcher.dispatch(
            request,
            caller=_caller(),
            cancel_event=cancel_event,
        )
    )

    assert native.cancel_event is cancel_event
    assert result.evidence is not None
    assert result.evidence.invocation_id == result.invocation_id
    envelope = dispatcher.fetch_evidence(
        EvidenceFetchRequest.from_reference(result.evidence),
        caller=_caller(),
    )
    assert len(envelope.records) == 2


def test_structured_dispatcher_fails_closed_for_unsupported_or_thin_evidence() -> None:
    class Native:
        calls = 0

        async def dispatch(
            self,
            request: NativeA2ARequest,
            *,
            cancel_event: asyncio.Event | None = None,
        ) -> NativeA2AResult:
            del cancel_event
            self.calls += 1
            return NativeA2AResult(
                invocation_id="inv-thin",
                agent_id=request.agent_id,
                agent_version="2",
                protocol_version="1.0",
                message_id="msg-thin",
                answer="Answer.",
                duration_ms=1,
                attempt_count=1,
            )

    class ThinRetriever:
        async def retrieve(
            self,
            agent_id: str,
            prompt: str,
        ) -> tuple[RetrievedEvidenceSource, ...]:
            del agent_id, prompt
            return _references()[:1]

    native = Native()
    dispatcher = StructuredEvidenceDispatcher(
        native,
        ThinRetriever(),
        minimum_records=2,
    )
    with pytest.raises(AgentIqOperationError) as unsupported:
        asyncio.run(
            dispatcher.dispatch(
                NativeA2ARequest(agent_id="external-agent-without-evidence", prompt="Prompt."),
                caller=_caller(),
            )
        )
    assert unsupported.value.error.code is ErrorCode.EVIDENCE_UNAVAILABLE
    assert native.calls == 0

    with pytest.raises(AgentIqOperationError) as thin:
        asyncio.run(
            dispatcher.dispatch(
                NativeA2ARequest(agent_id=AGENT_ID, prompt="Prompt."),
                caller=_caller(),
            )
        )
    assert thin.value.error.code is ErrorCode.EVIDENCE_UNAVAILABLE
    assert native.calls == 1


def test_foundry_iq_retriever_maps_sdk_response_without_answer_prose() -> None:
    captured: dict[str, Any] = {}

    class FakeClient:
        closed = False

        def retrieve(self, request: Any) -> dict[str, Any]:
            captured["request"] = request
            return {
                "response": [{"content": [{"type": "text", "text": "ignored answer"}]}],
                "references": [
                    {
                        "id": 0,
                        "docKey": "consumer-brightwash-cold",
                        "title": "BrightWash Cold active system",
                        "type": "searchIndex",
                        "rerankerScore": 0.0,
                    }
                ],
            }

        def close(self) -> None:
            self.closed = True
            captured["closed"] = True

    def factory(endpoint: str, credential: Any, knowledge_source: str) -> FakeClient:
        del credential
        captured["endpoint"] = endpoint
        captured["knowledge_source"] = knowledge_source
        return FakeClient()

    retriever = FoundryIQEvidenceRetriever(
        "https://search.example.invalid",
        credential=object(),  # type: ignore[arg-type]
        client_factory=factory,
    )
    references = asyncio.run(retriever.retrieve(AGENT_ID, "Prompt."))
    retriever.close()

    assert captured["endpoint"] == "https://search.example.invalid"
    assert captured["knowledge_source"] == KNOWLEDGE_SOURCE
    assert captured["closed"] is True
    assert len(captured["request"].messages) == 1
    assert references == (
        RetrievedEvidenceSource(
            local_reference_id="0",
            source_id="consumer-brightwash-cold",
            title="BrightWash Cold active system",
            knowledge_source=KNOWLEDGE_SOURCE,
            source_type="searchIndex",
            reranker_score=0.0,
        ),
    )


def test_foundry_iq_retriever_rejects_unknown_agent_and_empty_references() -> None:
    class EmptyClient:
        def retrieve(self, request: Any) -> dict[str, Any]:
            del request
            return {"references": []}

        def close(self) -> None:
            return None

    retriever = FoundryIQEvidenceRetriever(
        "https://search.example.invalid",
        credential=object(),  # type: ignore[arg-type]
        client_factory=lambda *_: EmptyClient(),
    )
    with pytest.raises(AgentIqOperationError) as unknown:
        asyncio.run(retriever.retrieve("consumer-unknown", "Prompt."))
    assert unknown.value.error.code is ErrorCode.EVIDENCE_UNAVAILABLE

    with pytest.raises(AgentIqOperationError) as empty:
        asyncio.run(retriever.retrieve(AGENT_ID, "Prompt."))
    assert empty.value.error.code is ErrorCode.EVIDENCE_UNAVAILABLE
    retriever.close()


def test_foundry_iq_retriever_retries_with_focused_sentence() -> None:
    queries: list[str] = []

    class FocusedClient:
        def retrieve(self, request: Any) -> dict[str, Any]:
            query = request.messages[0].content[0].text
            queries.append(query)
            if len(queries) == 1:
                return {"references": []}
            return {
                "references": [
                    {
                        "id": 0,
                        "docKey": "consumer-demand-sensing",
                        "title": "Adaptive Supply Network demand sensing",
                    }
                ]
            }

        def close(self) -> None:
            return None

    retriever = FoundryIQEvidenceRetriever(
        "https://search.example.invalid",
        credential=object(),  # type: ignore[arg-type]
        client_factory=lambda *_: FocusedClient(),
    )
    references = asyncio.run(
        retriever.retrieve(
            "consumer-demand-forecasting",
            (
                "Improve forecast accuracy using demand sensing across POS signals. "
                "Create a reusable structured scenario for BrightWash in Germany."
            ),
        )
    )
    retriever.close()

    assert queries == [
        (
            "Improve forecast accuracy using demand sensing across POS signals. "
            "Create a reusable structured scenario for BrightWash in Germany."
        ),
        "Improve forecast accuracy using demand sensing across POS signals",
    ]
    assert references[0].local_reference_id == "0"
    assert references[0].source_id == "consumer-demand-sensing"


def test_foundry_iq_default_client_disables_proxies_and_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["args"] = args
            captured["kwargs"] = kwargs

    monkeypatch.setattr("agent_iq.evidence.foundry_iq.KnowledgeBaseRetrievalClient", FakeClient)
    client = FoundryIQEvidenceRetriever(
        "https://search.example.invalid",
        credential=object(),  # type: ignore[arg-type]
    )._client_factory(
        "https://search.example.invalid",
        object(),  # type: ignore[arg-type]
        KNOWLEDGE_SOURCE,
    )

    assert isinstance(client, FakeClient)
    assert captured["kwargs"]["transport"]._use_env_settings is False
    assert captured["kwargs"]["redirect_policy"].allow is False


def test_a2a_shape_recorder_counts_text_data_metadata_and_artifacts() -> None:
    data_value = ParseDict({"sourceId": "consumer-brightwash-cold"}, Value())
    event = SendMessageResponse(
        task=Task(
            id="task-1",
            context_id="context-1",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            artifacts=[
                Artifact(
                    artifact_id="artifact-1",
                    parts=[
                        Part(text="Grounded answer."),
                        Part(data=data_value),
                    ],
                    metadata={"kind": "evidence"},
                )
            ],
            metadata={"invocationId": "inv-1"},
        )
    )
    recorder = A2AEventShapeRecorder()

    assert recorder(event) == "Grounded answer."
    summary = recorder.summary()
    assert summary.payload_kinds == ("task",)
    assert summary.text_part_count == 1
    assert summary.data_part_count == 1
    assert summary.artifact_count == 1
    assert summary.metadata_keys == ("invocationId", "kind")
    assert summary.data_keys == ("sourceId",)
    assert summary.preserves_structured_parts is True
