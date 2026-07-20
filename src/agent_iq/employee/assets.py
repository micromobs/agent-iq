"""Authoring-time asset schemas validated against the raw JSON data files.

These wrap the presentation-layer building blocks from ``.contracts`` with
catalog-level metadata (publish time, exact ID sets) so the on-disk JSON in
``agent_iq/data/employee`` is validated strictly at load time, before any
presentation contract is ever built from it.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from agent_iq.contracts.base import (
    ContractModel,
    Identifier,
    NonEmptyText,
    SchemaVersion,
    ShortText,
    Timestamp,
    require_unique_text,
)

from .contracts import (
    EmployeeAgentPresentation,
    ScenarioAction,
    ScenarioCategory,
    ScenarioMetric,
    ScenarioPhase,
    ScenarioPhaseStatus,
    ScenarioSection,
    ScenarioSourceRef,
    validate_phase_dag,
)

REQUIRED_AGENT_IDS: frozenset[str] = frozenset(
    {
        # Line of business
        "employee-hr-concierge",
        "employee-benefits-advisor",
        "employee-payroll",
        "employee-it-helpdesk",
        "employee-identity-security",
        "employee-expense",
        "employee-finance",
        "employee-travel",
        "employee-procurement",
        "employee-legal-compliance",
        "employee-sales-customer360",
        # Corporate communications / services
        "employee-news",
        "employee-task-hub",
        "employee-personal-dashboard",
        "employee-org-directory",
        "employee-onboarding",
        "employee-visual-qa",
        "employee-campus-services",
        "employee-learning",
        "employee-recognition",
        "employee-listening",
        # Management / governance
        "employee-site-provisioning",
        "employee-policy-governance",
        "employee-access-governance",
        "employee-site-health",
        "employee-records-lifecycle",
    }
)

REQUIRED_SCENARIO_IDS: frozenset[str] = frozenset(
    {
        "my-day",
        "new-hire-launchpad",
        "parental-leave-planner",
        "benefits-open-enrollment",
        "payslip-explainer",
        "paycheck-discrepancy",
        "promotion-comp-change",
        "time-off-coverage",
        "expense-smart-submit",
        "client-dinner-policy",
        "travel-disruption",
        "offboarding-command-center",
        "vpn-meeting-rescue",
        "lost-device-response",
        "software-access-governance",
        "laptop-refresh",
        "cafeteria-wellness",
        "room-desk-reservation",
        "praise-colleague",
        "pulse-survey-action",
        "sales-customer-360",
        "month-end-close",
        "purchase-vendor-compare",
        "contractor-onboarding",
        "personalized-news",
        "org-explorer",
        "visual-policy-qa",
        "learning-career-path",
        "site-provisioning",
        "site-health-governance",
    }
)


class ScenarioDefinition(ContractModel):
    """The full authored definition for one of the 30 sample scenarios."""

    scenario_id: Identifier
    category: ScenarioCategory
    title: ShortText
    subtitle: ShortText
    prompt: NonEmptyText
    examples: tuple[NonEmptyText, ...] = Field(min_length=1, max_length=6)
    tags: tuple[ShortText, ...] = Field(min_length=1, max_length=8)
    agent_ids: tuple[Identifier, ...] = Field(min_length=2, max_length=6)
    status: ScenarioPhaseStatus
    status_label: ShortText
    phases: tuple[ScenarioPhase, ...] = Field(min_length=2, max_length=10)
    metrics: tuple[ScenarioMetric, ...] = Field(min_length=1, max_length=6)
    sections: tuple[ScenarioSection, ...] = Field(min_length=3, max_length=6)
    sources: tuple[ScenarioSourceRef, ...] = Field(min_length=3, max_length=6)
    actions: tuple[ScenarioAction, ...] = Field(min_length=2, max_length=4)
    suggested_prompts: tuple[ShortText, ...] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def validate_unique_values(self) -> ScenarioDefinition:
        require_unique_text(self.examples, "Scenario examples")
        require_unique_text(self.tags, "Scenario tags")
        require_unique_text(self.agent_ids, "Scenario agent IDs")
        require_unique_text(self.suggested_prompts, "Scenario suggested prompts")

        section_ids = [section.section_id for section in self.sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("Scenario section IDs must be unique.")
        source_ids = [source.source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("Scenario source IDs must be unique.")
        metric_ids = [metric.metric_id for metric in self.metrics]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("Scenario metric IDs must be unique.")
        action_ids = [action.action_id for action in self.actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("Scenario action IDs must be unique.")

        validate_phase_dag(self.phases, frozenset(self.agent_ids))
        return self


class KnowledgeSourceDefinition(ContractModel):
    """A source-of-record referenced by agents and scenarios."""

    knowledge_source_id: Identifier
    title: ShortText
    category: ShortText
    summary: NonEmptyText
    provider: ShortText = "Contoso Employee Services"


class EmployeeAgentCatalogAsset(ContractModel):
    schema_version: SchemaVersion = "1.0"
    catalog_id: Identifier
    published_at: Timestamp
    agents: tuple[EmployeeAgentPresentation, ...] = Field(min_length=26, max_length=26)

    @model_validator(mode="after")
    def validate_agent_ids(self) -> EmployeeAgentCatalogAsset:
        agent_ids = [agent.agent_id for agent in self.agents]
        if len(agent_ids) != len(set(agent_ids)):
            raise ValueError("Employee agent catalog IDs must be unique.")
        if set(agent_ids) != REQUIRED_AGENT_IDS:
            missing = REQUIRED_AGENT_IDS - set(agent_ids)
            extra = set(agent_ids) - REQUIRED_AGENT_IDS
            raise ValueError(
                f"Employee agent catalog must match the required 26 IDs exactly. "
                f"Missing={sorted(missing)} Extra={sorted(extra)}"
            )
        return self


class EmployeeScenarioCatalogAsset(ContractModel):
    schema_version: SchemaVersion = "1.0"
    catalog_id: Identifier
    published_at: Timestamp
    scenarios: tuple[ScenarioDefinition, ...] = Field(min_length=30, max_length=30)

    @model_validator(mode="after")
    def validate_scenario_ids(self) -> EmployeeScenarioCatalogAsset:
        scenario_ids = [scenario.scenario_id for scenario in self.scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("Employee scenario catalog IDs must be unique.")
        if set(scenario_ids) != REQUIRED_SCENARIO_IDS:
            missing = REQUIRED_SCENARIO_IDS - set(scenario_ids)
            extra = set(scenario_ids) - REQUIRED_SCENARIO_IDS
            raise ValueError(
                f"Employee scenario catalog must match the required 30 IDs exactly. "
                f"Missing={sorted(missing)} Extra={sorted(extra)}"
            )
        return self


class EmployeeKnowledgeSourceAsset(ContractModel):
    schema_version: SchemaVersion = "1.0"
    sources: tuple[KnowledgeSourceDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_source_ids(self) -> EmployeeKnowledgeSourceAsset:
        source_ids = [source.knowledge_source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("Knowledge source IDs must be unique.")
        return self
