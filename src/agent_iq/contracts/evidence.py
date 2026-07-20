"""Structured evidence, claims, and globally normalized citations."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AnyUrl, Field, field_validator, model_validator

from .base import (
    ContractModel,
    Identifier,
    NonEmptyText,
    SchemaVersion,
    Sha256Digest,
    ShortText,
    Timestamp,
)

EVIDENCE_EXTENSION_URI = "urn:agent-iq:capability:structured-evidence:v1"
EVIDENCE_MEDIA_TYPE = "application/vnd.agent-iq.evidence+json;version=1.0"


class EvidenceDelivery(StrEnum):
    A2A_DATA_PART = "a2a_data_part"
    COMPANION = "companion"


class EvidenceAuthorization(StrEnum):
    CALLER_IDENTITY = "caller_identity"


class EvidenceCapability(ContractModel):
    """Advertised transport contract for machine-verifiable evidence."""

    schema_version: SchemaVersion = "1.0"
    delivery: EvidenceDelivery
    media_type: ShortText = EVIDENCE_MEDIA_TYPE
    correlation_key: ShortText = "invocation_id"
    authorization: EvidenceAuthorization = EvidenceAuthorization.CALLER_IDENTITY


class EvidenceLocator(ContractModel):
    uri: AnyUrl | None = None
    document_key: ShortText | None = None
    section: ShortText | None = None


class EvidenceRecord(ContractModel):
    evidence_id: Identifier
    invocation_id: Identifier
    agent_id: Identifier
    source_id: Identifier
    title: ShortText
    locator: EvidenceLocator
    excerpt: NonEmptyText
    local_reference_id: ShortText | None = None
    knowledge_source: ShortText | None = None
    retrieved_at: Timestamp
    confidence: float | None = Field(default=None, ge=0, le=1)
    content_hash: Sha256Digest | None = None


class Claim(ContractModel):
    claim_id: Identifier
    text: NonEmptyText
    evidence_ids: tuple[Identifier, ...]
    agent_ids: tuple[Identifier, ...]
    conflict: bool = False


class Citation(ContractModel):
    number: int = Field(ge=1)
    evidence_id: Identifier
    label: ShortText
    url: AnyUrl | None = None


class EvidenceBundle(ContractModel):
    schema_version: SchemaVersion = "1.0"
    execution_id: Identifier
    records: tuple[EvidenceRecord, ...]
    claims: tuple[Claim, ...] = ()
    citations: tuple[Citation, ...] = ()

    @model_validator(mode="after")
    def validate_references(self) -> EvidenceBundle:
        record_ids = [record.evidence_id for record in self.records]
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("Evidence IDs must be unique.")
        available = set(record_ids)

        numbers = [citation.number for citation in self.citations]
        if numbers and numbers != list(range(1, len(numbers) + 1)):
            raise ValueError("Citation numbers must be contiguous and one-based.")
        for citation in self.citations:
            if citation.evidence_id not in available:
                raise ValueError("A citation must reference structured evidence.")
        for claim in self.claims:
            if set(claim.evidence_ids) - available:
                raise ValueError("A claim must reference structured evidence.")
        return self


class EvidenceEnvelope(ContractModel):
    """Invocation-scoped evidence returned by the companion channel."""

    schema_version: SchemaVersion = "1.0"
    invocation_id: Identifier
    agent_id: Identifier
    records: tuple[EvidenceRecord, ...] = Field(min_length=1)
    issued_at: Timestamp
    expires_at: Timestamp

    @model_validator(mode="after")
    def validate_scope(self) -> EvidenceEnvelope:
        if self.expires_at <= self.issued_at:
            raise ValueError("Evidence envelope expiry must be after issue time.")
        evidence_ids = [record.evidence_id for record in self.records]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Evidence envelope IDs must be unique.")
        for record in self.records:
            if record.invocation_id != self.invocation_id:
                raise ValueError("Evidence must match its envelope invocation.")
            if record.agent_id != self.agent_id:
                raise ValueError("Evidence must match its envelope agent.")
        return self


class EvidenceChannelReference(ContractModel):
    """Opaque response metadata used to fetch identity-bound evidence."""

    schema_version: SchemaVersion = "1.0"
    delivery: EvidenceDelivery = EvidenceDelivery.COMPANION
    media_type: ShortText = EVIDENCE_MEDIA_TYPE
    invocation_id: Identifier
    correlation_id: Identifier
    handle: ShortText
    expires_at: Timestamp

    @field_validator("delivery")
    @classmethod
    def require_companion_delivery(cls, value: EvidenceDelivery) -> EvidenceDelivery:
        if value is not EvidenceDelivery.COMPANION:
            raise ValueError("An evidence channel reference must use companion delivery.")
        return value


class EvidenceFetchRequest(ContractModel):
    """Request body for the authenticated companion evidence endpoint."""

    schema_version: SchemaVersion = "1.0"
    invocation_id: Identifier
    correlation_id: Identifier
    handle: ShortText

    @classmethod
    def from_reference(cls, reference: EvidenceChannelReference) -> EvidenceFetchRequest:
        return cls(
            invocation_id=reference.invocation_id,
            correlation_id=reference.correlation_id,
            handle=reference.handle,
        )
