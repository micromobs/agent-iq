"""Validated request and result models for native A2A invocation."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from agent_iq.contracts import EvidenceChannelReference
from agent_iq.contracts.base import (
    ContractModel,
    Identifier,
    NonEmptyText,
    SchemaVersion,
    ShortText,
    VersionText,
    require_unique_text,
)


class IdempotencyPolicy(StrEnum):
    NO_RETRY = "no_retry"
    RETRY_WITH_KEY = "retry_with_key"


class NativeA2ARequest(ContractModel):
    schema_version: SchemaVersion = "1.0"
    invocation_id: Identifier | None = None
    agent_id: Identifier
    prompt: NonEmptyText
    deadline_seconds: float = Field(default=120.0, gt=0, le=600)
    preferred_card_versions: tuple[VersionText, ...] = ("1.0", "0.3")
    idempotency_policy: IdempotencyPolicy = IdempotencyPolicy.NO_RETRY
    idempotency_key: ShortText | None = None
    max_attempts: int = Field(default=1, ge=1, le=3)
    correlation_id: Identifier | None = None
    preserve_evidence_markers: bool = False
    enable_artifact_publication: bool = False

    @field_validator("preferred_card_versions")
    @classmethod
    def validate_card_versions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values:
            raise ValueError("At least one Agent Card version is required.")
        if any(value not in {"1.0", "0.3"} for value in values):
            raise ValueError("Supported Agent Card versions are 1.0 and 0.3.")
        return require_unique_text(values, "Agent Card versions")

    @model_validator(mode="after")
    def validate_idempotency(self) -> NativeA2ARequest:
        if (
            self.idempotency_policy is IdempotencyPolicy.RETRY_WITH_KEY
            and self.idempotency_key is None
        ):
            raise ValueError("Retry-with-key policy requires an idempotency key.")
        if (
            self.max_attempts > 1
            and self.idempotency_policy is not IdempotencyPolicy.RETRY_WITH_KEY
        ):
            raise ValueError("Multiple attempts require retry-with-key policy.")
        return self


class NativeA2AResult(ContractModel):
    schema_version: SchemaVersion = "1.0"
    invocation_id: Identifier
    agent_id: Identifier
    agent_version: VersionText
    protocol_version: VersionText
    message_id: Identifier
    answer: NonEmptyText
    duration_ms: int = Field(ge=0)
    attempt_count: int = Field(ge=1, le=3)
    correlation_id: Identifier | None = None
    evidence: EvidenceChannelReference | None = None
