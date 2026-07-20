"""Grounded multi-agent synthesis results."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from .base import ContractModel, Identifier, NonEmptyText, SchemaVersion
from .evidence import EvidenceBundle


class SynthesisStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    DECLINED = "declined"


class SynthesisIssueStatus(StrEnum):
    DECLINED = "declined"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    OMITTED_UNGROUNDED = "omitted_ungrounded"


class SynthesisIssue(ContractModel):
    agent_id: Identifier
    status: SynthesisIssueStatus
    message: NonEmptyText


class GroundingCoverage(ContractModel):
    total_claims: int = Field(ge=0)
    grounded_claims: int = Field(ge=0)
    ratio: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_ratio(self) -> GroundingCoverage:
        if self.grounded_claims > self.total_claims:
            raise ValueError("Grounded claims cannot exceed total claims.")
        expected = 1.0 if self.total_claims == 0 else self.grounded_claims / self.total_claims
        if abs(self.ratio - expected) > 1e-9:
            raise ValueError("Grounding coverage ratio does not match its claim counts.")
        return self


class SynthesisResult(ContractModel):
    schema_version: SchemaVersion = "1.0"
    execution_id: Identifier
    status: SynthesisStatus
    answer: NonEmptyText
    evidence: EvidenceBundle
    coverage: GroundingCoverage
    issues: tuple[SynthesisIssue, ...] = ()
    conflict_claim_ids: tuple[Identifier, ...] = ()
    sources_markdown: NonEmptyText | None = None

    @model_validator(mode="after")
    def validate_result(self) -> SynthesisResult:
        if self.evidence.execution_id != self.execution_id:
            raise ValueError("Synthesis evidence must match the execution.")

        available_conflicts = {claim.claim_id for claim in self.evidence.claims if claim.conflict}
        if len(self.conflict_claim_ids) != len(set(self.conflict_claim_ids)):
            raise ValueError("Conflict claim IDs must be unique.")
        if set(self.conflict_claim_ids) - available_conflicts:
            raise ValueError("Conflict IDs must reference conflicting evidence claims.")

        has_citations = bool(self.evidence.citations)
        if has_citations != (self.sources_markdown is not None):
            raise ValueError("Sources markdown must match the presence of citations.")

        if self.status is SynthesisStatus.COMPLETE:
            if self.issues or self.conflict_claim_ids or self.coverage.ratio != 1:
                raise ValueError("Complete synthesis cannot contain gaps or conflicts.")
        elif self.status is SynthesisStatus.PARTIAL:
            if self.coverage.grounded_claims == 0:
                raise ValueError("Partial synthesis requires at least one grounded claim.")
            if not self.issues and not self.conflict_claim_ids and self.coverage.ratio == 1:
                raise ValueError("Partial synthesis requires a visible gap or conflict.")
        elif self.coverage.grounded_claims != 0:
            raise ValueError("Declined synthesis cannot contain grounded claims.")
        return self
