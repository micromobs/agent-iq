"""Identity-bound companion channel for structured invocation evidence."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from agent_iq.catalog import ConsumerAssets, load_consumer_assets
from agent_iq.contracts import (
    CallerContext,
    ErrorCode,
    EvidenceChannelReference,
    EvidenceEnvelope,
    EvidenceFetchRequest,
    EvidenceLocator,
    EvidenceRecord,
)
from agent_iq.errors import operation_error
from agent_iq.runtime.models import NativeA2ARequest, NativeA2AResult

from .foundry_iq import RetrievedEvidenceSource


class EvidenceRetriever(Protocol):
    async def retrieve(
        self,
        agent_id: str,
        prompt: str,
    ) -> tuple[RetrievedEvidenceSource, ...]: ...


class NativeDispatcher(Protocol):
    async def dispatch(
        self,
        request: NativeA2ARequest,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> NativeA2AResult: ...


@dataclass(frozen=True, slots=True)
class _StoredEvidence:
    caller_key: tuple[str, str]
    correlation_id: str
    envelope: EvidenceEnvelope


class EvidenceRecordMapper:
    """Allowlist live references against canonical source documents."""

    def __init__(self, assets: ConsumerAssets | None = None) -> None:
        self._assets = assets or load_consumer_assets()
        self._documents = {document.document_id: document for document in self._assets.documents}
        self._knowledge_by_agent = {
            runtime.agent_id: runtime.knowledge_binding_id
            for runtime in self._assets.runtime.agents
        }
        self._evidence_agents = {
            descriptor.agent_id
            for descriptor in self._assets.catalog.agents
            if any(capability.evidence is not None for capability in descriptor.capabilities)
        }

    def supports(self, agent_id: str) -> bool:
        return agent_id in self._evidence_agents

    def map(
        self,
        *,
        invocation_id: str,
        agent_id: str,
        references: Sequence[RetrievedEvidenceSource],
        retrieved_at: datetime | None = None,
        max_records: int = 8,
    ) -> tuple[EvidenceRecord, ...]:
        if agent_id not in self._knowledge_by_agent:
            raise operation_error(
                ErrorCode.EVIDENCE_UNAVAILABLE,
                "The agent has no canonical evidence binding.",
                correlation_id=invocation_id,
            )
        knowledge_source = self._knowledge_by_agent[agent_id]
        records: list[EvidenceRecord] = []
        seen: set[str] = set()
        timestamp = retrieved_at or datetime.now(UTC)
        for reference in references:
            if reference.source_id in seen:
                continue
            document = self._documents.get(reference.source_id)
            if document is None:
                raise operation_error(
                    ErrorCode.EVIDENCE_UNAVAILABLE,
                    "Foundry IQ returned an unknown source ID; no locator was created.",
                    correlation_id=invocation_id,
                )
            if reference.knowledge_source != knowledge_source:
                raise operation_error(
                    ErrorCode.EVIDENCE_UNAVAILABLE,
                    "Foundry IQ evidence crossed the agent's knowledge binding.",
                    correlation_id=invocation_id,
                )
            digest = hashlib.sha256(document.content.encode("utf-8")).hexdigest()
            evidence_digest = hashlib.sha256(
                f"{invocation_id}\0{agent_id}\0{document.document_id}".encode()
            ).hexdigest()
            records.append(
                EvidenceRecord(
                    evidence_id=f"evidence-{evidence_digest[:24]}",
                    invocation_id=invocation_id,
                    agent_id=agent_id,
                    source_id=document.document_id,
                    title=document.title,
                    locator=EvidenceLocator(document_key=document.document_id),
                    excerpt=document.content,
                    local_reference_id=reference.local_reference_id,
                    knowledge_source=knowledge_source,
                    retrieved_at=timestamp,
                    content_hash=f"sha256:{digest}",
                )
            )
            seen.add(reference.source_id)
            if len(records) >= max_records:
                break
        if not records:
            raise operation_error(
                ErrorCode.EVIDENCE_UNAVAILABLE,
                "No canonical evidence records could be created.",
                correlation_id=invocation_id,
            )
        return tuple(records)


class CompanionEvidenceStore:
    """Store evidence behind an opaque handle and exact caller/tenant scope."""

    def __init__(
        self,
        *,
        ttl: timedelta = timedelta(minutes=10),
        clock: Callable[[], datetime] | None = None,
        handle_factory: Callable[[], str] | None = None,
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("Evidence channel TTL must be positive.")
        self._ttl = ttl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._handle_factory = handle_factory or (lambda: f"evh-{secrets.token_hex(24)}")
        self._entries: dict[str, _StoredEvidence] = {}
        self._lock = threading.Lock()

    def publish(
        self,
        records: Sequence[EvidenceRecord],
        *,
        caller: CallerContext,
        correlation_id: str,
    ) -> EvidenceChannelReference:
        if not records:
            raise ValueError("At least one evidence record is required.")
        issued_at = self._clock()
        expires_at = issued_at + self._ttl
        envelope = EvidenceEnvelope(
            invocation_id=records[0].invocation_id,
            agent_id=records[0].agent_id,
            records=tuple(records),
            issued_at=issued_at,
            expires_at=expires_at,
        )
        handle = self._handle_factory()
        entry = _StoredEvidence(
            caller_key=_caller_key(caller),
            correlation_id=correlation_id,
            envelope=envelope,
        )
        with self._lock:
            self._purge_expired(issued_at)
            if handle in self._entries:
                raise RuntimeError("Evidence handle collision.")
            self._entries[handle] = entry
        return EvidenceChannelReference(
            invocation_id=envelope.invocation_id,
            correlation_id=correlation_id,
            handle=handle,
            expires_at=expires_at,
        )

    def fetch(
        self,
        request: EvidenceFetchRequest,
        *,
        caller: CallerContext,
    ) -> EvidenceEnvelope:
        now = self._clock()
        with self._lock:
            entry = self._entries.get(request.handle)
            if entry is None:
                raise _access_denied(request.correlation_id)
            if entry.envelope.expires_at <= now:
                del self._entries[request.handle]
                raise operation_error(
                    ErrorCode.EVIDENCE_UNAVAILABLE,
                    "The structured evidence reference expired.",
                    correlation_id=request.correlation_id,
                )
            if (
                entry.caller_key != _caller_key(caller)
                or entry.correlation_id != request.correlation_id
                or entry.envelope.invocation_id != request.invocation_id
            ):
                raise _access_denied(request.correlation_id)
            return entry.envelope

    def _purge_expired(self, now: datetime) -> None:
        expired = [
            handle for handle, entry in self._entries.items() if entry.envelope.expires_at <= now
        ]
        for handle in expired:
            del self._entries[handle]


class StructuredEvidenceDispatcher:
    """Attach companion evidence metadata to a successful native A2A result."""

    def __init__(
        self,
        native: NativeDispatcher,
        retriever: EvidenceRetriever,
        *,
        mapper: EvidenceRecordMapper | None = None,
        store: CompanionEvidenceStore | None = None,
        minimum_records: int = 1,
    ) -> None:
        if minimum_records < 1:
            raise ValueError("Minimum evidence records must be at least one.")
        self._native = native
        self._retriever = retriever
        self._mapper = mapper or EvidenceRecordMapper()
        self._store = store or CompanionEvidenceStore()
        self._minimum_records = minimum_records

    async def dispatch(
        self,
        request: NativeA2ARequest,
        *,
        caller: CallerContext,
        cancel_event: asyncio.Event | None = None,
    ) -> NativeA2AResult:
        if not self._mapper.supports(request.agent_id):
            raise operation_error(
                ErrorCode.EVIDENCE_UNAVAILABLE,
                "The agent does not advertise a structured evidence channel.",
                correlation_id=request.correlation_id,
            )
        result = await self._native.dispatch(request, cancel_event=cancel_event)
        references = await self._retriever.retrieve(request.agent_id, request.prompt)
        records = self._mapper.map(
            invocation_id=result.invocation_id,
            agent_id=result.agent_id,
            references=references,
        )
        if len(records) < self._minimum_records:
            raise operation_error(
                ErrorCode.EVIDENCE_UNAVAILABLE,
                "The invocation did not meet the structured evidence requirement.",
                correlation_id=request.correlation_id or result.invocation_id,
            )
        correlation_id = request.correlation_id or result.invocation_id
        reference = self._store.publish(
            records,
            caller=caller,
            correlation_id=correlation_id,
        )
        return result.model_copy(update={"evidence": reference})

    def fetch_evidence(
        self,
        request: EvidenceFetchRequest,
        *,
        caller: CallerContext,
    ) -> EvidenceEnvelope:
        return self._store.fetch(request, caller=caller)


def _caller_key(caller: CallerContext) -> tuple[str, str]:
    return caller.tenant_id, caller.subject_id


def _access_denied(correlation_id: str) -> Exception:
    return operation_error(
        ErrorCode.AUTHORIZATION_DENIED,
        "Structured evidence is unavailable to this caller.",
        correlation_id=correlation_id,
    )
