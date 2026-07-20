"""Stable error codes and retry semantics."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from .base import ContractModel, Identifier, NonEmptyText, SchemaVersion, ShortText


class ErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    CONTRACT_VERSION_UNSUPPORTED = "contract_version_unsupported"
    CATALOG_UNAVAILABLE = "catalog_unavailable"
    AGENT_NOT_FOUND = "agent_not_found"
    AGENT_INELIGIBLE = "agent_ineligible"
    AUTHENTICATION_REQUIRED = "authentication_required"
    AUTHORIZATION_DENIED = "authorization_denied"
    POLICY_BLOCKED = "policy_blocked"
    CARD_INVALID = "card_invalid"
    PROTOCOL_UNSUPPORTED = "protocol_unsupported"
    DEPENDENCY_FAILED = "dependency_failed"
    INVOCATION_TIMEOUT = "invocation_timeout"
    INVOCATION_FAILED = "invocation_failed"
    EVIDENCE_UNAVAILABLE = "evidence_unavailable"
    ARTIFACT_UNAVAILABLE = "artifact_unavailable"
    ARTIFACT_CONFLICT = "artifact_conflict"
    SYNTHESIS_FAILED = "synthesis_failed"
    CANCELLED = "cancelled"
    INTERNAL = "internal"


class ErrorDetail(ContractModel):
    field: ShortText | None = None
    issue: NonEmptyText


class AgentIqError(ContractModel):
    schema_version: SchemaVersion = "1.0"
    error_id: Identifier
    code: ErrorCode
    message: NonEmptyText
    retryable: bool = False
    retry_after_seconds: int | None = Field(default=None, ge=1, le=86_400)
    details: tuple[ErrorDetail, ...] = ()
    correlation_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_retry(self) -> AgentIqError:
        if self.retry_after_seconds is not None and not self.retryable:
            raise ValueError("Retry delay is valid only for retryable errors.")
        return self
