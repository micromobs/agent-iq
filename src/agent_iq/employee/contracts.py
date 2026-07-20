"""Wire contracts for Agent IQ Employee Operations.

Every model here describes a read-only employee-services surface. Nothing in
this module represents (or is capable of triggering) a real write against any
enterprise system: any action that resembles a write is always an
approval-bound draft or message, never an executed side effect. These
contracts follow the same strict/frozen/camelCase conventions as
``agent_iq.contracts`` (see ``agent_iq.contracts.base.ContractModel``) but are
intentionally kept in their own package so the experience can evolve without
touching the production contract surface.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import AnyUrl, Field, field_validator, model_validator

from agent_iq.contracts.base import (
    ContractModel,
    Identifier,
    NonEmptyText,
    SchemaVersion,
    ShortText,
    require_unique_text,
)

DATA_SCOPE_NOTE = (
    "Results are scoped to the selected employee-services workflow and the "
    "sources returned for this request."
)
READ_ONLY_DISCLOSURE = (
    "This experience is read-only. No enterprise system is written to. Any "
    "action that looks like a write only prepares an approval-bound draft or "
    "message for a human to review and send."
)

GlyphToken = Identifier  # short UI token; length is bounded by the field definition below


class EmployeeAgentGroup(StrEnum):
    """The three employee-services navigation groups."""

    LINE_OF_BUSINESS = "line_of_business"
    CORPORATE_SERVICES = "corporate_services"
    MANAGEMENT_GOVERNANCE = "management_governance"


class ScenarioCategory(StrEnum):
    """Bounded scenario themes; several scenarios may share a category."""

    COMMAND_CENTER = "command_center"
    ONBOARDING = "onboarding"
    BENEFITS = "benefits"
    PAYROLL = "payroll"
    COMPENSATION = "compensation"
    TIME_OFF = "time_off"
    EXPENSE = "expense"
    TRAVEL = "travel"
    OFFBOARDING = "offboarding"
    IT_SUPPORT = "it_support"
    SECURITY_ACCESS = "security_access"
    DEVICE_LIFECYCLE = "device_lifecycle"
    WORKPLACE_WELLNESS = "workplace_wellness"
    WORKPLACE_RESERVATIONS = "workplace_reservations"
    RECOGNITION = "recognition"
    LISTENING = "listening"
    SALES_CUSTOMER = "sales_customer"
    FINANCE_CLOSE = "finance_close"
    PROCUREMENT = "procurement"
    CONTRACTOR = "contractor"
    NEWS = "news"
    ORG_DIRECTORY = "org_directory"
    VISUAL_QA = "visual_qa"
    LEARNING = "learning"
    SITE_PROVISIONING = "site_provisioning"
    SITE_GOVERNANCE = "site_governance"


class ScenarioPhaseStatus(StrEnum):
    """Public status labels for a DAG phase; never hidden chain-of-thought."""

    COMPLETED = "completed"
    IN_PROGRESS = "in_progress"
    NEEDS_APPROVAL = "needs_approval"
    BLOCKED = "blocked"
    PENDING = "pending"


class ScenarioSectionKind(StrEnum):
    """Bounded display-section kinds a scenario experience can render."""

    DASHBOARD = "dashboard"
    TIMELINE = "timeline"
    FORM = "form"
    COMPARISON_TABLE = "comparison_table"
    CHECKLIST = "checklist"
    CHART = "chart"
    RADIAL_GOAL = "radial_goal"
    PEOPLE_CARDS = "people_cards"
    ORG_CHART = "org_chart"
    MAP = "map"
    GALLERY = "gallery"
    DOCUMENT_CALLOUTS = "document_callouts"
    DECISION_TREE = "decision_tree"
    APPROVALS = "approvals"
    POLICY_FINDINGS = "policy_findings"
    SOURCE_DRAWER = "source_drawer"
    FOLLOW_UPS = "follow_ups"


class ScenarioActionKind(StrEnum):
    """Bounded safe UI actions. None of these execute a real enterprise write."""

    OPEN_DRAFT = "open_draft"
    REQUEST_APPROVAL = "request_approval"
    SEND_MESSAGE_DRAFT = "send_message_draft"
    SCHEDULE_DRAFT = "schedule_draft"
    VIEW_SOURCE = "view_source"
    ASK_FOLLOW_UP = "ask_follow_up"
    EXPORT_SUMMARY = "export_summary"
    ESCALATE_DRAFT = "escalate_draft"


class EmployeeActionState(StrEnum):
    """The only states returned by a showcase action preparation."""

    PREPARED = "prepared"
    REVIEW_REQUIRED = "review_required"
    NOT_EXECUTED = "not_executed"


READ_ONLY_ACTION_KINDS = frozenset(
    {
        ScenarioActionKind.VIEW_SOURCE,
        ScenarioActionKind.ASK_FOLLOW_UP,
        ScenarioActionKind.EXPORT_SUMMARY,
    }
)


class ScenarioActionClass(StrEnum):
    """The three safe execution classes every scenario action must declare.

    Every action is one of: a purely local UI operation, a drafted message to
    a human, or a read-only/draft tool action. None of these ever mutate an
    enterprise system.
    """

    LOCAL_UI = "local_ui"
    SEND_MESSAGE = "send_message"
    READ_ONLY_TOOL = "read_only_tool"


_ACTION_KIND_CLASSES: dict[ScenarioActionKind, ScenarioActionClass] = {
    ScenarioActionKind.VIEW_SOURCE: ScenarioActionClass.LOCAL_UI,
    ScenarioActionKind.ASK_FOLLOW_UP: ScenarioActionClass.LOCAL_UI,
    ScenarioActionKind.EXPORT_SUMMARY: ScenarioActionClass.LOCAL_UI,
    ScenarioActionKind.OPEN_DRAFT: ScenarioActionClass.READ_ONLY_TOOL,
    ScenarioActionKind.SCHEDULE_DRAFT: ScenarioActionClass.READ_ONLY_TOOL,
    ScenarioActionKind.REQUEST_APPROVAL: ScenarioActionClass.SEND_MESSAGE,
    ScenarioActionKind.SEND_MESSAGE_DRAFT: ScenarioActionClass.SEND_MESSAGE,
    ScenarioActionKind.ESCALATE_DRAFT: ScenarioActionClass.SEND_MESSAGE,
}

_BANNED_EXECUTION_PHRASES: tuple[str, ...] = (
    "has been sent",
    "has been submitted",
    "was submitted",
    "auto-approved",
    "automatically approved",
    "executed automatically",
    "goes live immediately",
    "already processed",
    "payment sent",
    "confirmed and paid",
)

_ALLOWED_ITEM_STATES: dict[ScenarioSectionKind, frozenset[str]] = {
    ScenarioSectionKind.TIMELINE: frozenset(status.value for status in ScenarioPhaseStatus),
    ScenarioSectionKind.CHECKLIST: frozenset({"done", "pending", "blocked", "not_applicable"}),
    ScenarioSectionKind.APPROVALS: frozenset({"pending_approval", "approved", "rejected", "draft"}),
    ScenarioSectionKind.POLICY_FINDINGS: frozenset(
        {"info", "advisory", "warning", "critical", "resolved"}
    ),
    ScenarioSectionKind.DECISION_TREE: frozenset({"yes", "no", "branch", "outcome"}),
}

_REQUIRED_ITEM_FIELDS: dict[ScenarioSectionKind, tuple[str, ...]] = {
    ScenarioSectionKind.DASHBOARD: ("value",),
    ScenarioSectionKind.TIMELINE: ("state", "description"),
    ScenarioSectionKind.FORM: ("value",),
    ScenarioSectionKind.COMPARISON_TABLE: ("values",),
    ScenarioSectionKind.CHECKLIST: ("state",),
    ScenarioSectionKind.CHART: ("metric_value",),
    ScenarioSectionKind.RADIAL_GOAL: ("metric_value",),
    ScenarioSectionKind.PEOPLE_CARDS: ("description",),
    ScenarioSectionKind.ORG_CHART: ("value",),
    ScenarioSectionKind.MAP: ("description",),
    ScenarioSectionKind.GALLERY: ("href",),
    ScenarioSectionKind.DOCUMENT_CALLOUTS: ("href", "description"),
    ScenarioSectionKind.DECISION_TREE: ("description", "state"),
    ScenarioSectionKind.APPROVALS: ("state", "description"),
    ScenarioSectionKind.POLICY_FINDINGS: ("state", "description"),
    ScenarioSectionKind.SOURCE_DRAWER: ("href", "description"),
    ScenarioSectionKind.FOLLOW_UPS: ("label",),
}


class ScenarioSectionItem(ContractModel):
    """One bounded, generic row/tile/card usable by any section kind."""

    item_id: Identifier
    label: ShortText
    value: ShortText | None = None
    description: NonEmptyText | None = None
    state: ShortText | None = None
    href: AnyUrl | None = None
    metric_value: float | None = None
    values: tuple[ShortText, ...] = Field(default=(), max_length=8)
    tags: tuple[ShortText, ...] = Field(default=(), max_length=6)

    @field_validator("tags")
    @classmethod
    def validate_unique_tags(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Section item tags")


class ScenarioSection(ContractModel):
    """One bounded display section within a scenario experience."""

    section_id: Identifier
    kind: ScenarioSectionKind
    title: ShortText
    subtitle: ShortText | None = None
    columns: tuple[ShortText, ...] = Field(default=(), max_length=8)
    items: tuple[ScenarioSectionItem, ...] = Field(min_length=1, max_length=12)
    footnote: NonEmptyText | None = None

    @model_validator(mode="after")
    def validate_items_for_kind(self) -> ScenarioSection:
        item_ids = [item.item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("Section item IDs must be unique.")

        required_fields = _REQUIRED_ITEM_FIELDS.get(self.kind, ())
        allowed_states = _ALLOWED_ITEM_STATES.get(self.kind)

        if self.kind is ScenarioSectionKind.COMPARISON_TABLE:
            if len(self.columns) < 2:
                raise ValueError("A comparison table section requires at least two columns.")
        elif self.columns:
            raise ValueError("Only a comparison table section may declare columns.")

        for item in self.items:
            for field_name in required_fields:
                if getattr(item, field_name) in (None, ()):
                    raise ValueError(f"A '{self.kind.value}' section item requires '{field_name}'.")
            if self.kind is ScenarioSectionKind.COMPARISON_TABLE and len(item.values) != len(
                self.columns
            ):
                raise ValueError("Each comparison table row must have one value per column.")
            if allowed_states is not None and item.state not in allowed_states:
                raise ValueError(
                    f"A '{self.kind.value}' section item state must be one of "
                    f"{sorted(allowed_states)}."
                )
        return self


class ScenarioAction(ContractModel):
    """A safe, bounded UI action. Write-like actions are approval-bound drafts.

    Every action declares an ``action_class`` (local UI, send-message, or
    read-only/draft tool) alongside its more specific ``kind``. ``action_class``
    is derived from ``kind`` automatically when omitted from the input, and is
    always cross-checked against ``kind`` so the two can never disagree.
    """

    action_id: Identifier
    kind: ScenarioActionKind
    action_class: ScenarioActionClass
    label: ShortText
    description: NonEmptyText
    requires_approval: bool = True

    @model_validator(mode="before")
    @classmethod
    def default_action_class(cls, data: object) -> object:
        if isinstance(data, dict) and "action_class" not in data and "actionClass" not in data:
            kind_value = data.get("kind")
            if not isinstance(kind_value, str):
                return data
            try:
                kind = ScenarioActionKind(kind_value)
            except ValueError:
                return data
            data = dict(data)
            data["action_class"] = _ACTION_KIND_CLASSES[kind]
        return data

    @model_validator(mode="after")
    def validate_no_execution_claim(self) -> ScenarioAction:
        expected_class = _ACTION_KIND_CLASSES[self.kind]
        if self.action_class != expected_class:
            raise ValueError(
                f"Action kind '{self.kind.value}' must declare action_class "
                f"'{expected_class.value}', not '{self.action_class.value}'."
            )
        if self.kind not in READ_ONLY_ACTION_KINDS and not self.requires_approval:
            raise ValueError(
                "A write-like scenario action must be approval-bound (requires_approval=True)."
            )
        haystack = f"{self.label} {self.description}".casefold()
        for phrase in _BANNED_EXECUTION_PHRASES:
            if phrase in haystack:
                raise ValueError(
                    f"Scenario action text must not claim execution occurred: '{phrase}'."
                )
        return self


class ScenarioMetric(ContractModel):
    metric_id: Identifier
    label: ShortText
    value: ShortText
    trend: ShortText | None = None
    helper_text: NonEmptyText | None = None

    @field_validator("trend")
    @classmethod
    def validate_trend(cls, value: str | None) -> str | None:
        if value is not None and value not in {"up", "down", "flat"}:
            raise ValueError("Metric trend must be 'up', 'down', or 'flat'.")
        return value


class ScenarioSourceRef(ContractModel):
    """A source-of-record summary backing scenario grounding."""

    source_id: Identifier
    title: ShortText
    knowledge_source_id: Identifier
    summary: NonEmptyText
    url: AnyUrl | None = None


class ScenarioPhase(ContractModel):
    """One node of the public, acyclic multi-phase scenario DAG."""

    phase_id: Identifier
    agent_id: Identifier
    title: ShortText
    status: ScenarioPhaseStatus
    status_label: ShortText
    output_label: ShortText
    depends_on: tuple[Identifier, ...] = Field(default=(), max_length=6)

    @field_validator("depends_on")
    @classmethod
    def validate_dependencies(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Phase dependencies")


def validate_phase_dag(phases: tuple[ScenarioPhase, ...], agent_ids: frozenset[str]) -> None:
    """Shared acyclic-DAG and agent-reference validation for scenario phases.

    Used by both ``EmployeeExperiencePresentation`` (presentation layer) and
    ``ScenarioDefinition`` (authoring/asset layer) so the two never drift.
    """

    phases_by_id = {phase.phase_id: phase for phase in phases}
    if len(phases_by_id) != len(phases):
        raise ValueError("Scenario phase IDs must be unique.")

    for phase in phases:
        if phase.phase_id in phase.depends_on:
            raise ValueError("A scenario phase cannot depend on itself.")
        missing = set(phase.depends_on) - phases_by_id.keys()
        if missing:
            raise ValueError(f"Phase dependency does not exist: {sorted(missing)[0]}.")
        if phase.agent_id not in agent_ids:
            raise ValueError(f"Phase agent '{phase.agent_id}' must be one of the selected agents.")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(phase_id: str) -> None:
        if phase_id in visiting:
            raise ValueError("Scenario phase dependencies must be acyclic.")
        if phase_id in visited:
            return
        visiting.add(phase_id)
        for dependency in phases_by_id[phase_id].depends_on:
            visit(dependency)
        visiting.remove(phase_id)
        visited.add(phase_id)

    for phase_id in phases_by_id:
        visit(phase_id)


class EmployeeAgentPresentation(ContractModel):
    """A compact, UI-renderable profile for one showcase sample agent."""

    schema_version: SchemaVersion = "1.0"
    agent_id: Identifier
    display_name: ShortText
    glyph: GlyphToken
    group: EmployeeAgentGroup
    role_summary: NonEmptyText
    skills: tuple[ShortText, ...] = Field(min_length=1, max_length=8)
    tags: tuple[ShortText, ...] = Field(default=(), max_length=8)
    examples: tuple[NonEmptyText, ...] = Field(min_length=1, max_length=6)
    knowledge_source_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=6)
    provider: ShortText = "Contoso Employee Services"

    @field_validator("skills", "tags", "examples", "knowledge_source_ids")
    @classmethod
    def validate_unique_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Employee agent values")

    @field_validator("glyph")
    @classmethod
    def validate_glyph_length(cls, value: str) -> str:
        if not (1 <= len(value) <= 4):
            raise ValueError("An agent glyph token must be 1-4 characters.")
        return value


class EmployeeSelectedAgentPresentation(ContractModel):
    """An agent as it participates in one specific scenario experience."""

    agent_id: Identifier
    display_name: ShortText
    glyph: GlyphToken
    group: EmployeeAgentGroup
    reason: NonEmptyText


class EmployeeGroupSummary(ContractModel):
    group: EmployeeAgentGroup
    agent_count: int = Field(ge=1)
    agent_ids: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_count(self) -> EmployeeGroupSummary:
        if self.agent_count != len(self.agent_ids):
            raise ValueError("Group agent count must match the listed agent IDs.")
        return self


class EmployeeScenarioCard(ContractModel):
    """One compact catalog card; the catalog holds exactly 30 of these."""

    scenario_id: Identifier
    category: ScenarioCategory
    title: ShortText
    subtitle: ShortText
    agent_ids: tuple[Identifier, ...] = Field(min_length=2, max_length=6)
    tags: tuple[ShortText, ...] = Field(default=(), max_length=8)

    @field_validator("agent_ids", "tags")
    @classmethod
    def validate_unique_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Scenario card values")


class EmployeeScenarioCatalogPresentation(ContractModel):
    """The compact, whole-catalog employee presentation."""

    schema_version: SchemaVersion = "1.0"
    data_scope: NonEmptyText = DATA_SCOPE_NOTE
    read_only_disclosure: NonEmptyText = READ_ONLY_DISCLOSURE
    group_summaries: tuple[EmployeeGroupSummary, ...] = Field(min_length=3, max_length=3)
    scenarios: tuple[EmployeeScenarioCard, ...] = Field(min_length=30, max_length=30)
    total_agents: int = Field(ge=1)
    total_knowledge_sources: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_catalog(self) -> EmployeeScenarioCatalogPresentation:
        scenario_ids = [card.scenario_id for card in self.scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("Scenario card IDs must be unique.")
        groups = {summary.group for summary in self.group_summaries}
        if groups != set(EmployeeAgentGroup):
            raise ValueError("The catalog must summarize all three showcase groups.")
        total = sum(summary.agent_count for summary in self.group_summaries)
        if total != self.total_agents:
            raise ValueError("Total agents must equal the sum of every group summary.")
        return self


class EmployeeExperiencePresentation(ContractModel):
    """A single rich, renderable scenario walkthrough."""

    schema_version: SchemaVersion = "1.0"
    scenario_id: Identifier
    category: ScenarioCategory
    data_scope: NonEmptyText = DATA_SCOPE_NOTE
    read_only_disclosure: NonEmptyText = READ_ONLY_DISCLOSURE
    title: ShortText
    subtitle: ShortText
    status: ScenarioPhaseStatus
    status_label: ShortText
    selected_agents: tuple[EmployeeSelectedAgentPresentation, ...] = Field(
        min_length=1, max_length=6
    )
    phases: tuple[ScenarioPhase, ...] = Field(min_length=2, max_length=10)
    metrics: tuple[ScenarioMetric, ...] = Field(min_length=1, max_length=6)
    sections: tuple[ScenarioSection, ...] = Field(min_length=3, max_length=6)
    sources: tuple[ScenarioSourceRef, ...] = Field(min_length=1, max_length=6)
    actions: tuple[ScenarioAction, ...] = Field(min_length=2, max_length=4)
    trust_boundary: NonEmptyText = READ_ONLY_DISCLOSURE
    suggested_prompts: tuple[ShortText, ...] = Field(min_length=1, max_length=6)

    @field_validator("suggested_prompts")
    @classmethod
    def validate_unique_prompts(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Suggested prompts")

    @model_validator(mode="after")
    def validate_phase_dag(self) -> EmployeeExperiencePresentation:
        agent_ids = frozenset(agent.agent_id for agent in self.selected_agents)
        validate_phase_dag(self.phases, agent_ids)
        return self


class VacationBalancePresentation(ContractModel):
    """Compact, citation-backed vacation ring returned to Cowork."""

    schema_version: SchemaVersion = "1.0"
    presentation_style: Literal["compact_ring"] = "compact_ring"
    execution_id: Identifier
    balance_year: int = Field(ge=2000, le=2100)
    title: ShortText
    supporting_line: ShortText
    status: ScenarioPhaseStatus
    entitlement_days: Decimal = Field(ge=0)
    taken_days: Decimal = Field(ge=0)
    booked_days: Decimal = Field(ge=0)
    remaining_days: Decimal = Field(ge=0)
    selected_agents: tuple[EmployeeSelectedAgentPresentation, ...] = Field(
        min_length=1, max_length=1
    )
    sources: tuple[ScenarioSourceRef, ...] = Field(min_length=1, max_length=1)

    @model_validator(mode="after")
    def validate_compact_balance(self) -> VacationBalancePresentation:
        if self.status is not ScenarioPhaseStatus.COMPLETED:
            raise ValueError("A vacation balance presentation must be completed.")
        if self.entitlement_days != (self.taken_days + self.booked_days + self.remaining_days):
            raise ValueError("Vacation ring values must reconcile to entitlement.")
        return self


class EmployeeScenarioRequest(ContractModel):
    """A request naming an exact scenario, a free-text intent, or both."""

    scenario_id: Identifier | None = None
    intent: NonEmptyText | None = None

    @model_validator(mode="after")
    def validate_usable(self) -> EmployeeScenarioRequest:
        if self.scenario_id is None and self.intent is None:
            raise ValueError(
                "Provide a scenario_id, an intent, or use EmployeeCatalogRequest for the "
                "default catalog."
            )
        return self


class EmployeeCatalogRequest(ContractModel):
    """An explicit request for the default compact scenario catalog."""

    schema_version: SchemaVersion = "1.0"


class EmployeeIntentMatch(ContractModel):
    """One transparent, ranked candidate from deterministic intent matching."""

    scenario_id: Identifier
    rank: int = Field(ge=1)
    score: float = Field(ge=0, le=1)
    matched_terms: tuple[ShortText, ...] = Field(default=(), max_length=16)


class EmployeeActionPreparationRequest(ContractModel):
    """Prepare, but never execute, one action from a scenario experience."""

    scenario_id: Identifier
    action_id: Identifier
    form_values: dict[Identifier, ShortText] = Field(default_factory=dict)
    confirmed: bool = False

    @model_validator(mode="after")
    def validate_form_values(self) -> EmployeeActionPreparationRequest:
        if len(self.form_values) > 16:
            raise ValueError("An employee action may include at most 16 form values.")
        return self


class EmployeeActionInput(ContractModel):
    field_id: Identifier
    value: ShortText


class EmployeeActionReceipt(ContractModel):
    """A deterministic receipt that explicitly denies execution."""

    schema_version: SchemaVersion = "1.0"
    receipt_id: Identifier
    scenario_id: Identifier
    action_id: Identifier
    action_kind: ScenarioActionKind
    action_class: ScenarioActionClass
    label: ShortText
    state: EmployeeActionState
    summary: NonEmptyText
    requires_approval: bool
    required_approver: ShortText | None = None
    submitted_values: tuple[EmployeeActionInput, ...] = Field(default=(), max_length=16)
    next_steps: tuple[ShortText, ...] = Field(min_length=1, max_length=4)
    executed: Literal[False] = False
    disclosure: NonEmptyText = READ_ONLY_DISCLOSURE

    @model_validator(mode="after")
    def validate_action_boundary(self) -> EmployeeActionReceipt:
        if self.requires_approval and self.required_approver is None:
            raise ValueError("Approval-bound action receipts must name the required approver.")
        if self.state is EmployeeActionState.REVIEW_REQUIRED and not self.requires_approval:
            raise ValueError("Only approval-bound actions may enter review_required.")
        return self
