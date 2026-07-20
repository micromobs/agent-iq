"""Intent matching, eligibility, scoring, and selection contracts."""

from __future__ import annotations

from pydantic import Field, field_validator, model_validator

from .agent import InteractionMode, TransportProtocol
from .base import (
    ContractModel,
    Identifier,
    NonEmptyText,
    SchemaVersion,
    ShortText,
    Timestamp,
    VersionText,
    require_unique_text,
)


class CallerContext(ContractModel):
    subject_id: ShortText
    tenant_id: ShortText
    locale: ShortText | None = None
    time_zone: ShortText | None = None
    permissions: tuple[ShortText, ...] = ()
    data_classifications: tuple[ShortText, ...] = ()
    geographies: tuple[ShortText, ...] = ()

    @field_validator("permissions", "data_classifications", "geographies")
    @classmethod
    def validate_unique_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Caller context values")


class MatchFilters(ContractModel):
    max_candidates: int = Field(default=20, ge=1, le=100)
    max_selected: int = Field(default=5, ge=1, le=20)
    required_protocols: tuple[TransportProtocol, ...] = ()
    required_interaction_modes: tuple[InteractionMode, ...] = ()
    allowed_agent_ids: tuple[Identifier, ...] = ()
    excluded_agent_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_agent_sets(self) -> MatchFilters:
        if self.max_selected > self.max_candidates:
            raise ValueError("Maximum selected agents cannot exceed maximum candidates.")
        allowed = {agent_id.casefold() for agent_id in self.allowed_agent_ids}
        excluded = {agent_id.casefold() for agent_id in self.excluded_agent_ids}
        if allowed & excluded:
            raise ValueError("Allowed and excluded agent IDs must not overlap.")
        return self


class MatchRequest(ContractModel):
    schema_version: SchemaVersion = "1.0"
    request_id: Identifier
    intent: NonEmptyText
    caller: CallerContext
    filters: MatchFilters = Field(default_factory=MatchFilters)
    requested_at: Timestamp


class EligibilityDecision(ContractModel):
    eligible: bool
    policy_facts: tuple[ShortText, ...] = ()
    blocked_reasons: tuple[ShortText, ...] = ()
    policy_version: VersionText | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> EligibilityDecision:
        if self.eligible and self.blocked_reasons:
            raise ValueError("Eligible candidates cannot have blocked reasons.")
        if not self.eligible and not self.blocked_reasons:
            raise ValueError("Ineligible candidates require a public blocked reason.")
        return self


class ScoreComponent(ContractModel):
    name: Identifier
    rank: int = Field(ge=1)
    score: float = Field(ge=0)
    matched_fields: tuple[ShortText, ...] = ()


class CandidateScore(ContractModel):
    agent_id: Identifier
    rank: int = Field(ge=1)
    fused_score: float = Field(ge=0)
    components: tuple[ScoreComponent, ...] = ()
    eligibility: EligibilityDecision
    matched_fields: tuple[ShortText, ...] = ()
    explanation: NonEmptyText
    catalog_version: VersionText


class SelectionReason(ContractModel):
    agent_id: Identifier
    work_item_id: Identifier
    work_item: NonEmptyText
    summary: NonEmptyText
    matched_skills: tuple[Identifier, ...] = ()
    matched_tags: tuple[ShortText, ...] = ()
    fused_score: float = Field(ge=0)
    policy_facts: tuple[ShortText, ...] = ()


class SelectionDecision(ContractModel):
    schema_version: SchemaVersion = "1.0"
    request_id: Identifier
    candidates: tuple[CandidateScore, ...]
    selected: tuple[SelectionReason, ...]
    selector: ShortText
    created_at: Timestamp

    @model_validator(mode="after")
    def validate_selection(self) -> SelectionDecision:
        candidate_ids = {candidate.agent_id: candidate for candidate in self.candidates}
        selected_ids = [reason.agent_id for reason in self.selected]
        if len(selected_ids) != len(set(selected_ids)):
            raise ValueError("Selected agent IDs must be unique.")
        for agent_id in selected_ids:
            candidate = candidate_ids.get(agent_id)
            if candidate is None:
                raise ValueError("Every selected agent must be a scored candidate.")
            if not candidate.eligibility.eligible:
                raise ValueError("An ineligible candidate cannot be selected.")
        return self
