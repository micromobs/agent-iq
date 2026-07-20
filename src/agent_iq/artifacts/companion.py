"""Identity-bound companion storage for validated business artifacts."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import SecretStr

from agent_iq.contracts import (
    ArtifactChannelReference,
    ArtifactEnvelope,
    ArtifactFetchRequest,
    ArtifactInvocationFetchRequest,
    ArtifactKind,
    ArtifactProducerIdentity,
    ArtifactPublishRequest,
    BusinessArtifact,
    CallerContext,
    ErrorCode,
)
from agent_iq.errors import operation_error
from agent_iq.publication import validate_publication_ticket


@dataclass(frozen=True, slots=True)
class ArtifactPublicationTicket:
    """Short-lived opaque credential for one authorized hosted invocation."""

    secret: SecretStr
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class _PublicationGrant:
    caller_key: tuple[str, str]
    correlation_id: str
    producer: ArtifactProducerIdentity
    allowed_kinds: frozenset[ArtifactKind]
    ticket: ArtifactPublicationTicket
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class _StoredArtifact:
    caller_key: tuple[str, str]
    envelope: ArtifactEnvelope
    reference: ArtifactChannelReference


class CompanionArtifactStore:
    """Publish only pre-authorized typed artifacts and isolate every fetch."""

    def __init__(
        self,
        *,
        ttl: timedelta = timedelta(minutes=10),
        clock: Callable[[], datetime] | None = None,
        handle_factory: Callable[[], str] | None = None,
        ticket_factory: Callable[[], str] | None = None,
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("Artifact channel TTL must be positive.")
        self._ttl = ttl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._handle_factory = handle_factory or (lambda: f"ath-{secrets.token_hex(24)}")
        self._ticket_factory = ticket_factory or (lambda: f"apt-{secrets.token_urlsafe(32)}")
        self._grants: dict[str, _PublicationGrant] = {}
        self._invocations_by_ticket: dict[str, str] = {}
        self._entries: dict[str, _StoredArtifact] = {}
        self._handles_by_artifact: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()

    def authorize_invocation(
        self,
        *,
        invocation_id: str,
        correlation_id: str,
        caller: CallerContext,
        producer: ArtifactProducerIdentity,
        allowed_kinds: Sequence[ArtifactKind],
    ) -> ArtifactPublicationTicket:
        kinds = frozenset(allowed_kinds)
        if not kinds:
            raise ValueError("At least one artifact kind must be authorized.")
        now = self._clock()
        expires_at = now + self._ttl
        ticket = ArtifactPublicationTicket(
            secret=validate_publication_ticket(self._ticket_factory()),
            expires_at=expires_at,
        )
        grant = _PublicationGrant(
            caller_key=_caller_key(caller),
            correlation_id=correlation_id,
            producer=producer,
            allowed_kinds=kinds,
            ticket=ticket,
            expires_at=expires_at,
        )
        with self._lock:
            self._purge_expired(now)
            existing = self._grants.get(invocation_id)
            if existing is not None:
                if _same_grant(existing, grant):
                    return existing.ticket
                raise operation_error(
                    ErrorCode.ARTIFACT_CONFLICT,
                    "The invocation already has a different artifact publication grant.",
                    correlation_id=correlation_id,
                )
            self._grants[invocation_id] = grant
            ticket_value = ticket.secret.get_secret_value()
            if ticket_value in self._invocations_by_ticket:
                del self._grants[invocation_id]
                raise RuntimeError("Artifact publication ticket collision.")
            self._invocations_by_ticket[ticket_value] = invocation_id
            return ticket

    def claim_publication_ticket(
        self,
        *,
        invocation_id: str,
        correlation_id: str,
        producer_identity_id: str,
    ) -> ArtifactPublicationTicket:
        """Return the existing opaque ticket only to its authenticated producer."""

        now = self._clock()
        with self._lock:
            self._purge_expired(now)
            grant = self._grants.get(invocation_id)
            if (
                grant is None
                or grant.expires_at <= now
                or grant.correlation_id != correlation_id
                or grant.producer.identity_id != producer_identity_id
            ):
                raise _access_denied(correlation_id)
            return grant.ticket

    def publish_with_ticket(
        self,
        ticket: SecretStr | str,
        artifact: BusinessArtifact,
        *,
        producer: ArtifactProducerIdentity,
    ) -> ArtifactChannelReference:
        """Publish using only an opaque ticket plus authenticated producer identity."""

        ticket_value = validate_publication_ticket(ticket).get_secret_value()
        now = self._clock()
        with self._lock:
            self._purge_expired(now)
            invocation_id = self._invocations_by_ticket.get(ticket_value)
            grant = self._grants.get(invocation_id) if invocation_id is not None else None
            if grant is None:
                raise _access_denied("artifact-publication")
            correlation_id = grant.correlation_id
        return self.publish(
            ArtifactPublishRequest(
                invocation_id=invocation_id,
                correlation_id=correlation_id,
                artifact=artifact,
            ),
            producer=producer,
        )

    def producer_for_ticket(
        self,
        ticket: SecretStr | str,
    ) -> ArtifactProducerIdentity:
        """Resolve only the trusted producer identity bound to a valid ticket."""

        ticket_value = validate_publication_ticket(ticket).get_secret_value()
        now = self._clock()
        with self._lock:
            self._purge_expired(now)
            invocation_id = self._invocations_by_ticket.get(ticket_value)
            grant = self._grants.get(invocation_id) if invocation_id is not None else None
            if grant is None:
                raise _access_denied("artifact-publication")
            return grant.producer

    def publish(
        self,
        request: ArtifactPublishRequest,
        *,
        producer: ArtifactProducerIdentity,
    ) -> ArtifactChannelReference:
        now = self._clock()
        with self._lock:
            self._purge_expired(now)
            grant = self._grants.get(request.invocation_id)
            if (
                grant is None
                or grant.expires_at <= now
                or grant.correlation_id != request.correlation_id
                or grant.producer != producer
            ):
                raise _access_denied(request.correlation_id)
            if request.artifact.kind not in grant.allowed_kinds:
                raise operation_error(
                    ErrorCode.POLICY_BLOCKED,
                    "The artifact kind is not authorized for this invocation.",
                    correlation_id=request.correlation_id,
                )

            artifact_key = (request.invocation_id, request.artifact.artifact_id)
            content_hash = _content_hash(request)
            existing_handle = self._handles_by_artifact.get(artifact_key)
            if existing_handle is not None:
                existing = self._entries[existing_handle]
                if existing.envelope.content_hash != content_hash:
                    raise operation_error(
                        ErrorCode.ARTIFACT_CONFLICT,
                        "The artifact identity was republished with different content.",
                        correlation_id=request.correlation_id,
                    )
                return existing.reference

            handle = self._handle_factory()
            if handle in self._entries:
                raise RuntimeError("Artifact handle collision.")
            envelope = ArtifactEnvelope(
                artifact_id=request.artifact.artifact_id,
                invocation_id=request.invocation_id,
                correlation_id=request.correlation_id,
                tenant_id=grant.caller_key[0],
                caller_subject_id=grant.caller_key[1],
                producer=producer,
                artifact=request.artifact,
                content_hash=content_hash,
                issued_at=now,
                expires_at=grant.expires_at,
            )
            reference = ArtifactChannelReference(
                artifact_id=envelope.artifact_id,
                artifact_kind=request.artifact.kind,
                invocation_id=request.invocation_id,
                correlation_id=request.correlation_id,
                handle=handle,
                expires_at=envelope.expires_at,
            )
            self._entries[handle] = _StoredArtifact(
                caller_key=grant.caller_key,
                envelope=envelope,
                reference=reference,
            )
            self._handles_by_artifact[artifact_key] = handle
            return reference

    def fetch(
        self,
        request: ArtifactFetchRequest,
        *,
        caller: CallerContext,
    ) -> ArtifactEnvelope:
        now = self._clock()
        with self._lock:
            entry = self._entries.get(request.handle)
            if entry is None:
                raise _access_denied(request.correlation_id)
            if entry.envelope.expires_at <= now:
                self._delete_entry(request.handle)
                raise operation_error(
                    ErrorCode.ARTIFACT_UNAVAILABLE,
                    "The typed artifact reference expired.",
                    correlation_id=request.correlation_id,
                )
            if (
                entry.caller_key != _caller_key(caller)
                or entry.envelope.artifact_id != request.artifact_id
                or entry.envelope.invocation_id != request.invocation_id
                or entry.envelope.correlation_id != request.correlation_id
            ):
                raise _access_denied(request.correlation_id)
            return entry.envelope

    def fetch_invocation(
        self,
        request: ArtifactInvocationFetchRequest,
        *,
        caller: CallerContext,
    ) -> tuple[ArtifactEnvelope, ...]:
        now = self._clock()
        with self._lock:
            self._purge_expired(now)
            grant = self._grants.get(request.invocation_id)
            if (
                grant is None
                or grant.caller_key != _caller_key(caller)
                or grant.correlation_id != request.correlation_id
            ):
                raise _access_denied(request.correlation_id)
            entries = (
                entry.envelope
                for entry in self._entries.values()
                if entry.envelope.invocation_id == request.invocation_id
                and entry.envelope.correlation_id == request.correlation_id
            )
            return tuple(sorted(entries, key=lambda entry: entry.artifact_id))

    def revoke_invocation(self, invocation_id: str) -> None:
        with self._lock:
            grant = self._grants.pop(invocation_id, None)
            if grant is not None:
                self._invocations_by_ticket.pop(
                    grant.ticket.secret.get_secret_value(),
                    None,
                )
            handles = [
                handle
                for handle, entry in self._entries.items()
                if entry.envelope.invocation_id == invocation_id
            ]
            for handle in handles:
                self._delete_entry(handle)

    def _purge_expired(self, now: datetime) -> None:
        expired_grants = [
            invocation_id
            for invocation_id, grant in self._grants.items()
            if grant.expires_at <= now
        ]
        for invocation_id in expired_grants:
            grant = self._grants.pop(invocation_id)
            self._invocations_by_ticket.pop(
                grant.ticket.secret.get_secret_value(),
                None,
            )
        expired_entries = [
            handle for handle, entry in self._entries.items() if entry.envelope.expires_at <= now
        ]
        for handle in expired_entries:
            self._delete_entry(handle)

    def _delete_entry(self, handle: str) -> None:
        entry = self._entries.pop(handle, None)
        if entry is None:
            return
        key = (entry.envelope.invocation_id, entry.envelope.artifact_id)
        if self._handles_by_artifact.get(key) == handle:
            del self._handles_by_artifact[key]


def _content_hash(request: ArtifactPublishRequest) -> str:
    payload = request.artifact.model_dump(mode="json", by_alias=True)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"


def _caller_key(caller: CallerContext) -> tuple[str, str]:
    return caller.tenant_id, caller.subject_id


def _same_grant(left: _PublicationGrant, right: _PublicationGrant) -> bool:
    return (
        left.caller_key == right.caller_key
        and left.correlation_id == right.correlation_id
        and left.producer == right.producer
        and left.allowed_kinds == right.allowed_kinds
    )


def _access_denied(correlation_id: str) -> Exception:
    return operation_error(
        ErrorCode.AUTHORIZATION_DENIED,
        "The typed artifact is unavailable to this caller or producer.",
        correlation_id=correlation_id,
    )
