"""Identity-bound resolution of normalized evidence citations."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from agent_iq.contracts import CallerContext, ErrorCode, EvidenceBundle, EvidenceRecord
from agent_iq.errors import operation_error


@dataclass(frozen=True, slots=True)
class _StoredBundle:
    caller_key: tuple[str, str]
    bundle: EvidenceBundle
    expires_at: datetime


class EvidenceResolver:
    """Resolve citation URLs only for the caller that owns the execution."""

    def __init__(
        self,
        base_url: str,
        *,
        ttl: timedelta = timedelta(hours=1),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Evidence resolver base URL must be an absolute HTTPS URL.")
        if ttl <= timedelta(0):
            raise ValueError("Evidence resolver TTL must be positive.")
        self._base_url = base_url.rstrip("/")
        self._ttl = ttl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._entries: dict[str, _StoredBundle] = {}
        self._lock = threading.Lock()

    def citation_url(self, execution_id: str, evidence_id: str) -> str:
        return f"{self._base_url}/v1/executions/{execution_id}/evidence/{evidence_id}"

    def publish(self, bundle: EvidenceBundle, *, caller: CallerContext) -> None:
        now = self._clock()
        entry = _StoredBundle(
            caller_key=_caller_key(caller),
            bundle=bundle,
            expires_at=now + self._ttl,
        )
        with self._lock:
            self._purge_expired(now)
            existing = self._entries.get(bundle.execution_id)
            if existing is not None:
                if existing.caller_key != entry.caller_key or existing.bundle != entry.bundle:
                    raise RuntimeError("Evidence execution collision.")
                return
            self._entries[bundle.execution_id] = entry

    def resolve(
        self,
        execution_id: str,
        evidence_id: str,
        *,
        caller: CallerContext,
    ) -> EvidenceRecord:
        now = self._clock()
        with self._lock:
            entry = self._entries.get(execution_id)
            if entry is None or entry.caller_key != _caller_key(caller):
                raise _access_denied(execution_id)
            if entry.expires_at <= now:
                del self._entries[execution_id]
                raise operation_error(
                    ErrorCode.EVIDENCE_UNAVAILABLE,
                    "The normalized evidence bundle expired.",
                    correlation_id=execution_id,
                )
            for record in entry.bundle.records:
                if record.evidence_id == evidence_id:
                    return record
        raise operation_error(
            ErrorCode.EVIDENCE_UNAVAILABLE,
            "The requested structured evidence record does not exist.",
            correlation_id=execution_id,
        )

    def _purge_expired(self, now: datetime) -> None:
        expired = [
            execution_id for execution_id, entry in self._entries.items() if entry.expires_at <= now
        ]
        for execution_id in expired:
            del self._entries[execution_id]


def _caller_key(caller: CallerContext) -> tuple[str, str]:
    return caller.tenant_id, caller.subject_id


def _access_denied(correlation_id: str) -> Exception:
    return operation_error(
        ErrorCode.AUTHORIZATION_DENIED,
        "Structured evidence is unavailable to this caller.",
        correlation_id=correlation_id,
    )
