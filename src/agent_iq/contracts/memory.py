"""Agent IQ Memory preparation, decision, and write-back contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .base import (
    ContractModel,
    Identifier,
    SchemaVersion,
    Sha256Digest,
    ShortText,
    Timestamp,
    VersionText,
    require_unique_text,
)
from .match import SelectionDecision

MEMORY_DELTA_CAP = 0.15
MEMORY_NEAR_TIE_MAX_GAP = 0.10
MEMORY_BASE_CANDIDATE_LIMIT = 10

Percent = Annotated[float, Field(ge=0, le=1)]


class MemoryScope(StrEnum):
    USER = "user"
    PROJECT = "project"
    DEPARTMENT = "department"
    ORGANIZATION = "organization"
    PARTNER = "partner"
    MARKETPLACE = "marketplace"


MEMORY_SCOPE_PRECEDENCE = (
    MemoryScope.USER,
    MemoryScope.PROJECT,
    MemoryScope.DEPARTMENT,
    MemoryScope.ORGANIZATION,
    MemoryScope.PARTNER,
    MemoryScope.MARKETPLACE,
)
MEMORY_SCOPE_WEIGHTS = {
    MemoryScope.USER: 1.00,
    MemoryScope.PROJECT: 0.90,
    MemoryScope.DEPARTMENT: 0.65,
    MemoryScope.ORGANIZATION: 0.45,
    MemoryScope.PARTNER: 0.30,
    MemoryScope.MARKETPLACE: 0.20,
}


class SourceMode(StrEnum):
    LIVE = "live"
    SEEDED = "seeded"
    REPLAY = "replay"
    DEGRADED = "degraded"


class MemorySnapshotStatus(StrEnum):
    DISABLED = "disabled"
    READY = "ready"
    DEGRADED = "degraded"


class MemoryOutcomeStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    DECLINED = "declined"
    FAILURE = "failure"
    CANCELLED = "cancelled"


class FeedbackSignal(StrEnum):
    HELPFUL = "helpful"
    NOT_HELPFUL = "not_helpful"


class MemoryRecordType(StrEnum):
    EXECUTION = "execution"
    FEEDBACK = "feedback"


class EpisodeWriteStatus(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"


class MemoryScopeReference(ContractModel):
    scope: MemoryScope
    graph_id: Identifier


class MemoryClauseReference(ContractModel):
    clause_id: Identifier
    text_hash: Sha256Digest


class MemoryCandidateReference(ContractModel):
    agent_id: Identifier
    base_rank: int = Field(ge=1, le=MEMORY_BASE_CANDIDATE_LIMIT)
    eligible: Literal[True] = True


class MemoryPreparationRequest(ContractModel):
    schema_version: SchemaVersion = "1.0"
    request_id: Identifier
    trace_id: Identifier
    correlation_id: Identifier
    tenant_id: ShortText
    subject_id: ShortText
    project_id: Identifier
    intent_hash: Sha256Digest
    clauses: tuple[MemoryClauseReference, ...] = Field(min_length=1)
    authorized_scopes: tuple[MemoryScopeReference, ...] = Field(min_length=1)
    base_candidates: tuple[MemoryCandidateReference, ...] = Field(min_length=1)
    requested_at: Timestamp

    @model_validator(mode="after")
    def validate_request(self) -> MemoryPreparationRequest:
        clause_ids = [clause.clause_id for clause in self.clauses]
        graph_ids = [scope.graph_id for scope in self.authorized_scopes]
        candidate_ids = [candidate.agent_id for candidate in self.base_candidates]
        candidate_ranks = [candidate.base_rank for candidate in self.base_candidates]
        if len(clause_ids) != len(set(clause_ids)):
            raise ValueError("Memory clause IDs must be unique.")
        if len(graph_ids) != len(set(graph_ids)):
            raise ValueError("Authorized Memory graph IDs must be unique.")
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("Memory candidate IDs must be unique.")
        if len(candidate_ranks) != len(set(candidate_ranks)):
            raise ValueError("Memory candidate base ranks must be unique.")
        return self


class IntentNeighborhoodMatch(ContractModel):
    neighborhood_id: Identifier
    graph_id: Identifier
    scope: MemoryScope
    similarity: Percent
    relevant_execution_count: int = Field(ge=0)
    successful_execution_count: int = Field(ge=0)
    successful_override_count: int = Field(ge=0)
    last_observed_at: Timestamp | None = None
    evidence_ids: tuple[Identifier, ...] = ()

    @field_validator("evidence_ids")
    @classmethod
    def validate_evidence_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Neighborhood evidence IDs")

    @model_validator(mode="after")
    def validate_counts(self) -> IntentNeighborhoodMatch:
        if self.successful_execution_count > self.relevant_execution_count:
            raise ValueError("Successful executions cannot exceed relevant executions.")
        if self.successful_override_count > self.successful_execution_count:
            raise ValueError("Successful overrides cannot exceed successful executions.")
        return self


class ScopedMemoryFact(ContractModel):
    fact_id: Identifier
    graph_id: Identifier
    scope: MemoryScope
    summary: ShortText
    confidence: Percent
    valid_at: Timestamp
    invalid_at: Timestamp | None = None
    provenance_ids: tuple[Identifier, ...] = Field(min_length=1)

    @field_validator("provenance_ids")
    @classmethod
    def validate_provenance_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Memory fact provenance IDs")

    @model_validator(mode="after")
    def validate_validity_window(self) -> ScopedMemoryFact:
        if self.invalid_at is not None and self.invalid_at < self.valid_at:
            raise ValueError("Memory fact invalidation cannot precede validity.")
        return self


class CandidateMemoryPrior(ContractModel):
    agent_id: Identifier
    base_rank: int = Field(ge=1, le=MEMORY_BASE_CANDIDATE_LIMIT)
    scope: MemoryScope
    intent_affinity: Percent
    reputation: Percent
    confidence: Percent
    recency: Percent
    decision_delta: float = Field(ge=-MEMORY_DELTA_CAP, le=MEMORY_DELTA_CAP)
    evidence_fact_ids: tuple[Identifier, ...] = ()
    provisional_override_applied: bool = False
    inclusion_boundary_gap: float | None = Field(default=None, ge=0, le=1)
    same_subject: bool = False
    same_project: bool = False
    same_neighborhood: bool = False

    @field_validator("evidence_fact_ids")
    @classmethod
    def validate_evidence_fact_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Candidate memory evidence IDs")

    @model_validator(mode="after")
    def validate_provisional_override(self) -> CandidateMemoryPrior:
        if not self.provisional_override_applied:
            return self
        if self.decision_delta <= 0:
            raise ValueError("A successful provisional override must add a positive delta.")
        if not (self.same_subject and self.same_project and self.same_neighborhood):
            raise ValueError(
                "A provisional override requires the same user, project, and intent neighborhood."
            )
        if (
            self.inclusion_boundary_gap is None
            or self.inclusion_boundary_gap > MEMORY_NEAR_TIE_MAX_GAP
        ):
            raise ValueError("A provisional override may affect only a near-tie candidate.")
        return self


class MemoryOutcomeSummary(ContractModel):
    execution_id: Identifier
    agent_ids: tuple[Identifier, ...] = Field(min_length=1)
    status: MemoryOutcomeStatus
    observed_at: Timestamp
    business_signal: Identifier | None = None
    evidence_fact_ids: tuple[Identifier, ...] = ()

    @field_validator("agent_ids", "evidence_fact_ids")
    @classmethod
    def validate_unique_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Memory outcome IDs")


class MemorySnapshot(ContractModel):
    schema_version: SchemaVersion = "1.0"
    request_id: Identifier
    trace_id: Identifier
    tenant_id: ShortText
    subject_id: ShortText
    project_id: Identifier
    status: MemorySnapshotStatus
    matched_neighborhoods: tuple[IntentNeighborhoodMatch, ...] = ()
    facts: tuple[ScopedMemoryFact, ...] = ()
    candidate_priors: tuple[CandidateMemoryPrior, ...] = ()
    outcomes: tuple[MemoryOutcomeSummary, ...] = ()
    provenance_ids: tuple[Identifier, ...] = ()
    prepared_at: Timestamp

    @field_validator("provenance_ids")
    @classmethod
    def validate_provenance_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Memory snapshot provenance IDs")

    @model_validator(mode="after")
    def validate_snapshot(self) -> MemorySnapshot:
        neighborhood_ids = [
            neighborhood.neighborhood_id for neighborhood in self.matched_neighborhoods
        ]
        fact_ids = [fact.fact_id for fact in self.facts]
        candidate_ids = [prior.agent_id for prior in self.candidate_priors]
        outcome_ids = [outcome.execution_id for outcome in self.outcomes]
        for values, label in (
            (neighborhood_ids, "Memory neighborhood IDs"),
            (fact_ids, "Memory fact IDs"),
            (candidate_ids, "Memory prior agent IDs"),
            (outcome_ids, "Memory outcome execution IDs"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must be unique.")
        if self.status is MemorySnapshotStatus.DISABLED and any(
            (
                self.matched_neighborhoods,
                self.facts,
                self.candidate_priors,
                self.outcomes,
                self.provenance_ids,
            )
        ):
            raise ValueError("A disabled Memory snapshot must be neutral and empty.")
        return self


class SquadOverride(ContractModel):
    schema_version: SchemaVersion = "1.0"
    override_id: Identifier
    request_id: Identifier
    execution_id: Identifier
    tenant_id: ShortText
    actor_subject_id: ShortText
    project_id: Identifier
    proposed_agent_ids: tuple[Identifier, ...] = Field(min_length=1)
    authorized_agent_ids: tuple[Identifier, ...] = Field(min_length=1)
    reason_code: Identifier
    reason_hash: Sha256Digest
    created_at: Timestamp

    @field_validator("proposed_agent_ids", "authorized_agent_ids")
    @classmethod
    def validate_unique_agents(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Squad override agent IDs")

    @model_validator(mode="after")
    def validate_change(self) -> SquadOverride:
        if self.proposed_agent_ids == self.authorized_agent_ids:
            raise ValueError("A squad override must change the proposed squad.")
        return self


class SquadDecisionEnvelope(ContractModel):
    schema_version: SchemaVersion = "1.0"
    decision_id: Identifier
    request_id: Identifier
    execution_id: Identifier
    proposal: SelectionDecision
    override: SquadOverride | None = None
    execution_decision: SelectionDecision
    created_at: Timestamp

    @model_validator(mode="after")
    def validate_decisions(self) -> SquadDecisionEnvelope:
        if (
            self.proposal.request_id != self.request_id
            or self.execution_decision.request_id != self.request_id
        ):
            raise ValueError("Squad decisions must match the envelope request ID.")
        proposed = tuple(reason.agent_id for reason in self.proposal.selected)
        authorized = tuple(reason.agent_id for reason in self.execution_decision.selected)
        if self.override is None:
            if proposed != authorized:
                raise ValueError("A changed execution squad requires an explicit override.")
        else:
            if (
                self.override.request_id != self.request_id
                or self.override.execution_id != self.execution_id
            ):
                raise ValueError("Squad override identities must match the decision envelope.")
            if self.override.proposed_agent_ids != proposed:
                raise ValueError("Squad override proposal must match the proposed decision.")
            if self.override.authorized_agent_ids != authorized:
                raise ValueError("Squad override result must match the execution decision.")
        if self.created_at < self.proposal.created_at:
            raise ValueError("The squad envelope cannot precede its proposal.")
        return self


class ExecutionMemoryCandidate(ContractModel):
    agent_id: Identifier
    base_rank: int = Field(ge=1, le=MEMORY_BASE_CANDIDATE_LIMIT)


class ExecutionMemoryIntent(ContractModel):
    text_hash: Sha256Digest
    intent_class: Identifier
    neighborhood_id: Identifier | None = None


class ExecutionMemorySummary(ContractModel):
    status: MemoryOutcomeStatus
    latency_ms: int = Field(ge=0)
    evidence_count: int = Field(ge=0)


class OutcomeMemorySummary(ContractModel):
    status: MemoryOutcomeStatus
    business_signal: Identifier | None = None


class FeedbackMemorySummary(ContractModel):
    feedback_id: Identifier
    rating: FeedbackSignal


class IqSourceModes(ContractModel):
    foundry_iq: SourceMode
    work_iq: SourceMode
    fabric_iq: SourceMode
    web_iq: SourceMode


class ExecutionMemoryRecord(ContractModel):
    schema_version: SchemaVersion = "1.0"
    record_type: Literal[MemoryRecordType.EXECUTION] = MemoryRecordType.EXECUTION
    idempotency_key: Identifier
    execution_id: Identifier
    trace_id: Identifier
    correlation_id: Identifier
    tenant_id: ShortText
    subject_id: ShortText
    project_id: Identifier
    intent: ExecutionMemoryIntent
    eligible_candidates: tuple[ExecutionMemoryCandidate, ...] = Field(min_length=1)
    proposed_squad: tuple[Identifier, ...] = Field(min_length=1)
    executed_squad: tuple[Identifier, ...] = Field(min_length=1)
    override: SquadOverride | None = None
    execution: ExecutionMemorySummary
    outcome: OutcomeMemorySummary
    feedback: FeedbackMemorySummary | None = None
    source_modes: IqSourceModes
    policy_version: VersionText
    recorded_at: Timestamp

    @field_validator("proposed_squad", "executed_squad")
    @classmethod
    def validate_unique_squad(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Execution memory squad IDs")

    @model_validator(mode="after")
    def validate_record(self) -> ExecutionMemoryRecord:
        if self.idempotency_key != f"{self.execution_id}:execution":
            raise ValueError("Execution memory idempotency key must be '<executionId>:execution'.")
        candidate_ids = [candidate.agent_id for candidate in self.eligible_candidates]
        candidate_ranks = [candidate.base_rank for candidate in self.eligible_candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("Execution memory candidate IDs must be unique.")
        if len(candidate_ranks) != len(set(candidate_ranks)):
            raise ValueError("Execution memory candidate ranks must be unique.")
        if set(self.proposed_squad) - set(candidate_ids) or set(self.executed_squad) - set(
            candidate_ids
        ):
            raise ValueError("Recorded squads must contain only eligible base candidates.")
        if self.override is None:
            if self.proposed_squad != self.executed_squad:
                raise ValueError("A changed executed squad requires an override record.")
        else:
            if (
                self.override.execution_id != self.execution_id
                or self.override.proposed_agent_ids != self.proposed_squad
                or self.override.authorized_agent_ids != self.executed_squad
            ):
                raise ValueError("Execution memory override must match the recorded squads.")
        return self


class FeedbackMemoryRecord(ContractModel):
    schema_version: SchemaVersion = "1.0"
    record_type: Literal[MemoryRecordType.FEEDBACK] = MemoryRecordType.FEEDBACK
    idempotency_key: Identifier
    feedback_id: Identifier
    execution_id: Identifier
    trace_id: Identifier
    correlation_id: Identifier
    tenant_id: ShortText
    subject_id: ShortText
    project_id: Identifier
    intent: ExecutionMemoryIntent
    rating: FeedbackSignal
    comment_hash: Sha256Digest | None = None
    submitted_at: Timestamp

    @model_validator(mode="after")
    def validate_idempotency_key(self) -> FeedbackMemoryRecord:
        if self.idempotency_key != f"{self.execution_id}:feedback":
            raise ValueError("Feedback idempotency key must be '<executionId>:feedback'.")
        return self


class EpisodeReceipt(ContractModel):
    schema_version: SchemaVersion = "1.0"
    record_type: MemoryRecordType
    execution_id: Identifier
    idempotency_key: Identifier
    graph_id: Identifier
    episode_id: Identifier
    status: EpisodeWriteStatus
    duplicate: bool = False
    received_at: Timestamp

    @model_validator(mode="after")
    def validate_receipt(self) -> EpisodeReceipt:
        expected = f"{self.execution_id}:{self.record_type.value}"
        if self.idempotency_key != expected:
            raise ValueError("Episode receipt idempotency key does not match its record type.")
        if self.duplicate is not (self.status is EpisodeWriteStatus.DUPLICATE):
            raise ValueError("Episode receipt duplicate flag must match its write status.")
        return self
