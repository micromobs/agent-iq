"""Compact host-facing projections over canonical Agent IQ audit contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import AnyUrl, Field, model_validator

from .base import ContractModel, Identifier, NonEmptyText, SchemaVersion, ShortText
from .execution import ExecutionStatus
from .synthesis import SynthesisIssueStatus
from .vpn import VpnRecoveryOutcomeKind


class AgentCandidatePresentation(ContractModel):
    agent_id: Identifier
    display_name: ShortText
    rank: int = Field(ge=1)
    eligible: bool
    reason: NonEmptyText
    matched_fields: tuple[ShortText, ...] = ()


class AgentSearchPresentation(ContractModel):
    """Bounded recommendation preview safe for routine MCP model context."""

    schema_version: SchemaVersion = "1.0"
    request_id: Identifier
    total_candidates: int = Field(ge=0)
    candidates: tuple[AgentCandidatePresentation, ...] = Field(max_length=5)


class SelectedAgentPresentation(ContractModel):
    agent_id: Identifier
    display_name: ShortText
    reason: NonEmptyText
    matched_skills: tuple[Identifier, ...] = ()
    matched_tags: tuple[ShortText, ...] = ()


class CitationPresentation(ContractModel):
    number: int = Field(ge=1)
    title: ShortText
    source_id: Identifier
    url: AnyUrl | None = None
    excerpt: NonEmptyText | None = None


class ExecutionIssuePresentation(ContractModel):
    agent_id: Identifier
    status: SynthesisIssueStatus
    message: NonEmptyText


class VpnRecoveryOutcomePresentation(ContractModel):
    outcome_id: VpnRecoveryOutcomeKind
    label: ShortText
    count: int = Field(ge=0)
    share: float = Field(ge=0, le=1)
    recommended: bool = False


class VpnRecoveryPresentation(ContractModel):
    """Compact public projection for aggregate VPN recovery guidance."""

    status: Literal["available", "unavailable"]
    title: ShortText
    summary_lines: tuple[ShortText, ...] = Field(min_length=1, max_length=2)
    population: int | None = Field(default=None, ge=1)
    period_label: ShortText | None = None
    scope_label: ShortText | None = None
    recommended_first_action: ShortText | None = None
    escalation_boundary: NonEmptyText | None = None
    freshness_label: ShortText | None = None
    guidance_note: ShortText | None = None
    read_only_label: ShortText = "Read-only guidance only; no action was taken."
    selected_agent: SelectedAgentPresentation | None = None
    source: CitationPresentation | None = None
    outcomes: tuple[VpnRecoveryOutcomePresentation, ...] = Field(default=(), max_length=4)

    @model_validator(mode="after")
    def validate_vpn_recovery(self) -> VpnRecoveryPresentation:
        if self.status == "available":
            if (
                self.population is None
                or self.period_label is None
                or self.scope_label is None
                or self.recommended_first_action is None
                or self.escalation_boundary is None
                or self.freshness_label is None
                or self.guidance_note is None
                or self.selected_agent is None
                or self.source is None
                or len(self.outcomes) != 4
            ):
                raise ValueError("An available VPN recovery flow requires full grounded data.")
        elif (
            any(
                value is not None
                for value in (
                    self.population,
                    self.period_label,
                    self.scope_label,
                    self.recommended_first_action,
                    self.escalation_boundary,
                    self.freshness_label,
                    self.guidance_note,
                    self.selected_agent,
                    self.source,
                )
            )
            or self.outcomes
        ):
            raise ValueError(
                "An unavailable VPN recovery flow cannot expose partial grounded data."
            )
        return self


class OrchestrationPresentation(ContractModel):
    """Concise completed result; full execution remains available through the ledger."""

    schema_version: SchemaVersion = "1.0"
    execution_id: Identifier
    request_id: Identifier
    status: ExecutionStatus
    duration_ms: int = Field(ge=0)
    selected_agents: tuple[SelectedAgentPresentation, ...] = ()
    answer: NonEmptyText
    citations: tuple[CitationPresentation, ...] = ()
    sources_markdown: NonEmptyText | None = None
    issues: tuple[ExecutionIssuePresentation, ...] = ()
    next_action: NonEmptyText | None = None
    vpn_recovery: VpnRecoveryPresentation | None = None
    details_available_via: Literal["get_execution"] = "get_execution"

    @model_validator(mode="after")
    def validate_sources(self) -> OrchestrationPresentation:
        if bool(self.citations) != (self.sources_markdown is not None):
            raise ValueError("Sources markdown must match the compact citation list.")
        return self
