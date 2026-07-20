"""Versioned typed business artifacts for invocation-bound companion delivery."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import AnyUrl, Field, field_validator, model_validator

from .base import (
    ContractModel,
    Identifier,
    NonEmptyText,
    SchemaVersion,
    Sha256Digest,
    ShortText,
    Timestamp,
    VersionText,
    require_unique_text,
)

ARTIFACT_MEDIA_TYPE = "application/vnd.agent-iq.business-artifact+json;version=1.0"


class ArtifactKind(StrEnum):
    DEMAND_SCENARIO = "demand_scenario"
    REGULATORY_DECISION = "regulatory_decision"
    SAP_CHANGE_PROPOSAL = "sap_change_proposal"
    SUPPLY_COMMITMENT = "supply_commitment"


class SupplyCommitmentStatus(StrEnum):
    AVAILABLE = "available"
    CONSTRAINED = "constrained"
    NOT_AVAILABLE = "not_available"
    PROPOSED = "proposed"


class RegulatoryDisposition(StrEnum):
    APPROVED = "approved"
    CONDITIONAL = "conditional"
    REJECTED = "rejected"
    REQUIRES_REVIEW = "requires_review"


class RegulatoryClaimStatus(StrEnum):
    APPROVED = "approved"
    CONDITIONAL = "conditional"
    REJECTED = "rejected"
    REQUIRES_EVIDENCE = "requires_evidence"


class SapChangeOperation(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    RELEASE = "release"
    CANCEL = "cancel"


class SapChangeRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DemandScenario(ContractModel):
    schema_version: SchemaVersion = "1.0"
    kind: Literal[ArtifactKind.DEMAND_SCENARIO] = ArtifactKind.DEMAND_SCENARIO
    artifact_id: Identifier
    product_id: ShortText
    market: ShortText
    horizon_start: date
    horizon_end: date
    unit: ShortText
    baseline_quantity: float = Field(ge=0)
    forecast_quantity: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    assumptions: tuple[ShortText, ...] = ()
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)
    created_at: Timestamp

    @field_validator("assumptions", "evidence_ids")
    @classmethod
    def validate_unique_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Demand scenario values")

    @model_validator(mode="after")
    def validate_horizon(self) -> DemandScenario:
        if self.horizon_end < self.horizon_start:
            raise ValueError("Demand scenario horizon end cannot precede its start.")
        return self


class SupplyCommitment(ContractModel):
    schema_version: SchemaVersion = "1.0"
    kind: Literal[ArtifactKind.SUPPLY_COMMITMENT] = ArtifactKind.SUPPLY_COMMITMENT
    artifact_id: Identifier
    material_id: ShortText
    plant_id: ShortText
    market: ShortText
    quantity: float = Field(ge=0)
    unit: ShortText
    available_from: date
    available_until: date | None = None
    status: SupplyCommitmentStatus
    source_system: ShortText
    source_object_id: ShortText
    source_url: AnyUrl | None = None
    as_of: Timestamp
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)

    @field_validator("evidence_ids")
    @classmethod
    def validate_unique_evidence(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Supply commitment evidence IDs")

    @model_validator(mode="after")
    def validate_availability_window(self) -> SupplyCommitment:
        if self.available_until is not None and self.available_until < self.available_from:
            raise ValueError("Supply commitment end cannot precede its start.")
        return self


class RegulatoryClaimDecision(ContractModel):
    claim_id: Identifier
    claim: NonEmptyText
    status: RegulatoryClaimStatus
    rationale: NonEmptyText
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)

    @field_validator("evidence_ids")
    @classmethod
    def validate_unique_evidence(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Regulatory claim evidence IDs")


class RegulatoryDecision(ContractModel):
    schema_version: SchemaVersion = "1.0"
    kind: Literal[ArtifactKind.REGULATORY_DECISION] = ArtifactKind.REGULATORY_DECISION
    artifact_id: Identifier
    product_id: ShortText
    market: ShortText
    disposition: RegulatoryDisposition
    claims: tuple[RegulatoryClaimDecision, ...] = Field(min_length=1)
    rationale: NonEmptyText
    conditions: tuple[ShortText, ...] = ()
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)
    decided_at: Timestamp

    @field_validator("conditions", "evidence_ids")
    @classmethod
    def validate_unique_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Regulatory decision values")

    @model_validator(mode="after")
    def validate_claim_evidence(self) -> RegulatoryDecision:
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("Regulatory claim IDs must be unique.")
        available = set(self.evidence_ids)
        for claim in self.claims:
            if set(claim.evidence_ids) - available:
                raise ValueError("Regulatory claim evidence must belong to the decision.")
        return self


ArtifactScalar: TypeAlias = str | int | float | bool | None


class SapFieldChange(ContractModel):
    field_path: ShortText
    before: ArtifactScalar = None
    after: ArtifactScalar

    @model_validator(mode="after")
    def require_change(self) -> SapFieldChange:
        if self.before == self.after:
            raise ValueError("SAP field change must modify the value.")
        return self


class SapChangeProposal(ContractModel):
    schema_version: SchemaVersion = "1.0"
    kind: Literal[ArtifactKind.SAP_CHANGE_PROPOSAL] = ArtifactKind.SAP_CHANGE_PROPOSAL
    artifact_id: Identifier
    operation: SapChangeOperation
    object_type: ShortText
    object_id: ShortText
    source_system: ShortText = "SAP"
    changes: tuple[SapFieldChange, ...] = Field(min_length=1)
    reason: NonEmptyText
    risk: SapChangeRisk
    approval_required: Literal[True] = True
    source_url: AnyUrl | None = None
    proposed_at: Timestamp
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)

    @field_validator("evidence_ids")
    @classmethod
    def validate_unique_evidence(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "SAP change proposal evidence IDs")

    @model_validator(mode="after")
    def validate_changes(self) -> SapChangeProposal:
        paths = [change.field_path.casefold() for change in self.changes]
        if len(paths) != len(set(paths)):
            raise ValueError("SAP field paths must be unique.")
        return self


BusinessArtifact: TypeAlias = Annotated[
    DemandScenario | SupplyCommitment | RegulatoryDecision | SapChangeProposal,
    Field(discriminator="kind"),
]


class ArtifactProducerIdentity(ContractModel):
    agent_id: Identifier
    agent_version: VersionText
    identity_id: ShortText
    framework: ShortText | None = None


class ArtifactPublishRequest(ContractModel):
    schema_version: SchemaVersion = "1.0"
    invocation_id: Identifier
    correlation_id: Identifier
    artifact: BusinessArtifact


class ArtifactEnvelope(ContractModel):
    schema_version: SchemaVersion = "1.0"
    artifact_id: Identifier
    invocation_id: Identifier
    correlation_id: Identifier
    tenant_id: ShortText
    caller_subject_id: ShortText
    producer: ArtifactProducerIdentity
    artifact: BusinessArtifact
    content_hash: Sha256Digest
    issued_at: Timestamp
    expires_at: Timestamp

    @model_validator(mode="after")
    def validate_scope(self) -> ArtifactEnvelope:
        if self.artifact_id != self.artifact.artifact_id:
            raise ValueError("Artifact envelope identity must match its payload.")
        if self.expires_at <= self.issued_at:
            raise ValueError("Artifact envelope expiry must be after issue time.")
        return self


class ArtifactChannelReference(ContractModel):
    schema_version: SchemaVersion = "1.0"
    delivery: Literal["companion"] = "companion"
    media_type: ShortText = ARTIFACT_MEDIA_TYPE
    artifact_id: Identifier
    artifact_kind: ArtifactKind
    invocation_id: Identifier
    correlation_id: Identifier
    handle: ShortText
    expires_at: Timestamp


class ArtifactFetchRequest(ContractModel):
    schema_version: SchemaVersion = "1.0"
    artifact_id: Identifier
    invocation_id: Identifier
    correlation_id: Identifier
    handle: ShortText

    @classmethod
    def from_reference(cls, reference: ArtifactChannelReference) -> ArtifactFetchRequest:
        return cls(
            artifact_id=reference.artifact_id,
            invocation_id=reference.invocation_id,
            correlation_id=reference.correlation_id,
            handle=reference.handle,
        )


class ArtifactInvocationFetchRequest(ContractModel):
    """Fetch every artifact published for one authorized invocation."""

    schema_version: SchemaVersion = "1.0"
    invocation_id: Identifier
    correlation_id: Identifier
