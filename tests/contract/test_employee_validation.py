"""Failure-path coverage for the isolated employee showcase contracts and loaders."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from agent_iq.employee import (
    EmployeeActionPreparationRequest,
    EmployeeActionState,
    EmployeeAgentPresentation,
    EmployeeCatalogRequest,
    EmployeeExperiencePresentation,
    EmployeeGroupSummary,
    EmployeeScenarioCard,
    EmployeeScenarioCatalogPresentation,
    EmployeeScenarioRequest,
    ScenarioAction,
    ScenarioActionClass,
    ScenarioActionKind,
    ScenarioMetric,
    ScenarioPhase,
    ScenarioPhaseStatus,
    ScenarioSection,
    ScenarioSectionItem,
    ScenarioSectionKind,
    list_employee_scenarios,
    prepare_employee_action,
    resolve_employee_request,
)
from agent_iq.employee.assets import (
    EmployeeAgentCatalogAsset,
    EmployeeKnowledgeSourceAsset,
    EmployeeScenarioCatalogAsset,
    ScenarioDefinition,
)
from agent_iq.employee.contracts import EmployeeActionReceipt, validate_phase_dag
from agent_iq.employee.loader import (
    DEFAULT_EMPLOYEE_DATA,
    EmployeeDataError,
    load_employee_agent_catalog,
    load_employee_assets,
    load_employee_catalog,
    load_employee_knowledge_sources,
    load_employee_scenario_catalog,
    match_employee_intent,
)

_ASSETS = load_employee_assets()


def _payload(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True)


def _section(kind: ScenarioSectionKind) -> ScenarioSection:
    for scenario in _ASSETS.scenarios.scenarios:
        for section in scenario.sections:
            if section.kind is kind:
                return section
    raise AssertionError(f"No sample section uses {kind.value}.")


def _phase(
    phase_id: str,
    *,
    agent_id: str = "employee-hr-concierge",
    depends_on: tuple[str, ...] = (),
) -> ScenarioPhase:
    return ScenarioPhase(
        phase_id=phase_id,
        agent_id=agent_id,
        title=f"Phase {phase_id}",
        status=ScenarioPhaseStatus.COMPLETED,
        status_label="Completed",
        output_label=f"{phase_id} complete",
        depends_on=depends_on,
    )


def _copy_employee_data(destination: Path) -> None:
    destination.mkdir(parents=True)
    for name in ("agents.json", "scenarios.jsonl", "knowledge_sources.json"):
        shutil.copy2(DEFAULT_EMPLOYEE_DATA / name, destination / name)


def test_section_item_and_section_kind_error_paths() -> None:
    dashboard = _section(ScenarioSectionKind.DASHBOARD)
    item_payload = _payload(dashboard.items[0])
    item_payload["tags"] = ["duplicate", "duplicate"]
    with pytest.raises(ValidationError, match="tags"):
        ScenarioSectionItem.model_validate(item_payload)

    payload = _payload(dashboard)
    payload["items"] = [payload["items"][0], payload["items"][0]]
    with pytest.raises(ValidationError, match="item IDs must be unique"):
        ScenarioSection.model_validate(payload)

    comparison = _section(ScenarioSectionKind.COMPARISON_TABLE)
    payload = _payload(comparison)
    payload["columns"] = ["Only one"]
    with pytest.raises(ValidationError, match="at least two columns"):
        ScenarioSection.model_validate(payload)

    payload = _payload(dashboard)
    payload["columns"] = ["A", "B"]
    with pytest.raises(ValidationError, match="Only a comparison table"):
        ScenarioSection.model_validate(payload)

    payload = _payload(dashboard)
    payload["items"][0]["value"] = None
    with pytest.raises(ValidationError, match="requires 'value'"):
        ScenarioSection.model_validate(payload)

    checklist = _section(ScenarioSectionKind.CHECKLIST)
    payload = _payload(checklist)
    payload["items"][0]["state"] = "unexpected"
    with pytest.raises(ValidationError, match="item state must be one of"):
        ScenarioSection.model_validate(payload)


def test_action_metric_agent_and_phase_field_validators() -> None:
    explicit = ScenarioAction(
        action_id="explicit-view",
        kind=ScenarioActionKind.VIEW_SOURCE,
        action_class=ScenarioActionClass.LOCAL_UI,
        label="View source",
        description="Open the synthetic source.",
        requires_approval=False,
    )
    assert explicit.action_class is ScenarioActionClass.LOCAL_UI

    for invalid_kind in (42, "unknown_kind"):
        with pytest.raises(ValidationError):
            ScenarioAction.model_validate(
                {
                    "actionId": "invalid-kind",
                    "kind": invalid_kind,
                    "label": "Invalid",
                    "description": "Invalid action kind input.",
                }
            )

    with pytest.raises(ValidationError, match="must not claim execution"):
        ScenarioAction(
            action_id="unsafe-text",
            kind=ScenarioActionKind.VIEW_SOURCE,
            label="Payment sent",
            description="Open the result.",
            requires_approval=False,
        )

    with pytest.raises(ValidationError, match="Metric trend"):
        ScenarioMetric(metric_id="metric", label="Metric", value="10", trend="sideways")

    with pytest.raises(ValidationError, match="Phase dependencies"):
        _phase("duplicate-dependency", depends_on=("first", "first"))

    agent = _ASSETS.agents.agents[0]
    payload = _payload(agent)
    payload["glyph"] = "ABCDE"
    with pytest.raises(ValidationError, match="glyph token"):
        EmployeeAgentPresentation.model_validate(payload)

    payload = _payload(agent)
    payload["skills"] = [agent.skills[0], agent.skills[0]]
    with pytest.raises(ValidationError, match="Employee agent values"):
        EmployeeAgentPresentation.model_validate(payload)


def test_phase_dag_rejects_every_invalid_reference_shape() -> None:
    allowed = frozenset({"employee-hr-concierge"})

    with pytest.raises(ValueError, match="IDs must be unique"):
        validate_phase_dag((_phase("same"), _phase("same")), allowed)

    with pytest.raises(ValueError, match="depend on itself"):
        validate_phase_dag((_phase("self", depends_on=("self",)),), allowed)

    with pytest.raises(ValueError, match="does not exist"):
        validate_phase_dag((_phase("missing", depends_on=("absent",)),), allowed)

    with pytest.raises(ValueError, match="must be one of the selected agents"):
        validate_phase_dag((_phase("foreign", agent_id="employee-it-helpdesk"),), allowed)

    shared = (
        _phase("root"),
        _phase("left", depends_on=("root",)),
        _phase("right", depends_on=("root",)),
    )
    validate_phase_dag(shared, allowed)


def test_presentation_level_catalog_and_experience_validators() -> None:
    catalog = list_employee_scenarios()

    summary = catalog.group_summaries[0]
    with pytest.raises(ValidationError, match="count must match"):
        EmployeeGroupSummary(
            group=summary.group,
            agent_count=summary.agent_count + 1,
            agent_ids=summary.agent_ids,
        )

    card = catalog.scenarios[0]
    payload = _payload(card)
    payload["agentIds"] = [card.agent_ids[0], card.agent_ids[0]]
    with pytest.raises(ValidationError, match="Scenario card values"):
        EmployeeScenarioCard.model_validate(payload)

    payload = _payload(catalog)
    payload["scenarios"][1]["scenarioId"] = payload["scenarios"][0]["scenarioId"]
    with pytest.raises(ValidationError, match="card IDs must be unique"):
        EmployeeScenarioCatalogPresentation.model_validate(payload)

    payload = _payload(catalog)
    payload["groupSummaries"][1]["group"] = payload["groupSummaries"][0]["group"]
    with pytest.raises(ValidationError, match="all three showcase groups"):
        EmployeeScenarioCatalogPresentation.model_validate(payload)

    payload = _payload(catalog)
    payload["totalAgents"] += 1
    with pytest.raises(ValidationError, match="sum of every group"):
        EmployeeScenarioCatalogPresentation.model_validate(payload)

    experience = resolve_employee_request(EmployeeScenarioRequest(scenario_id="my-day"))
    assert isinstance(experience, EmployeeExperiencePresentation)

    payload = _payload(experience)
    payload["suggestedPrompts"] = [
        experience.suggested_prompts[0],
        experience.suggested_prompts[0],
    ]
    with pytest.raises(ValidationError, match="Suggested prompts"):
        EmployeeExperiencePresentation.model_validate(payload)

    payload = _payload(experience)
    payload["phases"][0]["agentId"] = "employee-not-selected"
    with pytest.raises(ValidationError, match="selected agents"):
        EmployeeExperiencePresentation.model_validate(payload)


def test_request_and_receipt_validation_boundaries() -> None:
    values = {f"field-{index}": f"value-{index}" for index in range(17)}
    with pytest.raises(ValidationError, match="at most 16"):
        EmployeeActionPreparationRequest(
            scenario_id="expense-smart-submit",
            action_id="ess-request-approval",
            form_values=values,
        )

    receipt = prepare_employee_action(
        EmployeeActionPreparationRequest(
            scenario_id="expense-smart-submit",
            action_id="ess-request-approval",
        )
    )
    payload = _payload(receipt)
    payload["requiredApprover"] = None
    with pytest.raises(ValidationError, match="must name the required approver"):
        EmployeeActionReceipt.model_validate(payload)

    payload = _payload(receipt)
    payload["state"] = EmployeeActionState.REVIEW_REQUIRED
    payload["requiresApproval"] = False
    payload["requiredApprover"] = None
    with pytest.raises(ValidationError, match="Only approval-bound actions"):
        EmployeeActionReceipt.model_validate(payload)


@pytest.mark.parametrize(
    "field_name",
    ["examples", "tags", "agentIds", "suggestedPrompts"],
)
def test_scenario_definition_rejects_duplicate_public_values(field_name: str) -> None:
    scenario = _ASSETS.scenarios.scenarios[0]
    payload = _payload(scenario)
    values = payload[field_name]
    payload[field_name] = [values[0], values[0]]
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        ScenarioDefinition.model_validate(payload)


@pytest.mark.parametrize(
    ("collection_name", "id_name", "error"),
    [
        ("sections", "sectionId", "section IDs"),
        ("sources", "sourceId", "source IDs"),
        ("metrics", "metricId", "metric IDs"),
        ("actions", "actionId", "action IDs"),
    ],
)
def test_scenario_definition_rejects_duplicate_nested_ids(
    collection_name: str,
    id_name: str,
    error: str,
) -> None:
    scenario = _ASSETS.scenarios.scenarios[0]
    payload = _payload(scenario)
    payload[collection_name][1][id_name] = payload[collection_name][0][id_name]
    with pytest.raises(ValidationError, match=error):
        ScenarioDefinition.model_validate(payload)


def test_asset_catalog_validators_reject_duplicates_and_set_drift() -> None:
    agent_payload = _payload(_ASSETS.agents)
    agent_payload["agents"][1]["agentId"] = agent_payload["agents"][0]["agentId"]
    with pytest.raises(ValidationError, match="catalog IDs must be unique"):
        EmployeeAgentCatalogAsset.model_validate(agent_payload)

    agent_payload = _payload(_ASSETS.agents)
    agent_payload["agents"][1]["agentId"] = "employee-extra-agent"
    with pytest.raises(ValidationError, match="required 26 IDs exactly"):
        EmployeeAgentCatalogAsset.model_validate(agent_payload)

    scenario_payload = _payload(_ASSETS.scenarios)
    scenario_payload["scenarios"][1]["scenarioId"] = scenario_payload["scenarios"][0]["scenarioId"]
    with pytest.raises(ValidationError, match="catalog IDs must be unique"):
        EmployeeScenarioCatalogAsset.model_validate(scenario_payload)

    scenario_payload = _payload(_ASSETS.scenarios)
    scenario_payload["scenarios"][1]["scenarioId"] = "employee-extra-scenario"
    with pytest.raises(ValidationError, match="required 30 IDs exactly"):
        EmployeeScenarioCatalogAsset.model_validate(scenario_payload)

    source_payload = _payload(_ASSETS.knowledge_sources)
    source_payload["sources"][1]["knowledgeSourceId"] = source_payload["sources"][0][
        "knowledgeSourceId"
    ]
    with pytest.raises(ValidationError, match="Knowledge source IDs must be unique"):
        EmployeeKnowledgeSourceAsset.model_validate(source_payload)


def test_loader_reports_file_json_jsonl_and_schema_errors(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(EmployeeDataError, match="Cannot read"):
        load_employee_agent_catalog(missing)

    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("{", encoding="utf-8")
    with pytest.raises(EmployeeDataError, match="Invalid JSON"):
        load_employee_agent_catalog(invalid_json)

    invalid_jsonl = tmp_path / "invalid.jsonl"
    invalid_jsonl.write_text("\n{}\n{\n", encoding="utf-8")
    with pytest.raises(EmployeeDataError, match="at line 3"):
        load_employee_scenario_catalog(invalid_jsonl)

    blank_jsonl = tmp_path / "blank.jsonl"
    blank_jsonl.write_text("\n \n", encoding="utf-8")
    with pytest.raises(EmployeeDataError, match="No scenario records"):
        load_employee_scenario_catalog(blank_jsonl)

    with pytest.raises(EmployeeDataError, match="Cannot read"):
        load_employee_scenario_catalog(tmp_path / "missing.jsonl")

    invalid_agent_schema = tmp_path / "invalid-agents.json"
    invalid_agent_schema.write_text("{}", encoding="utf-8")
    with pytest.raises(EmployeeDataError, match="Invalid employee agent catalog"):
        load_employee_agent_catalog(invalid_agent_schema)

    invalid_scenario_schema = tmp_path / "invalid-scenarios.jsonl"
    invalid_scenario_schema.write_text("{}\n", encoding="utf-8")
    with pytest.raises(EmployeeDataError, match="Invalid employee scenario catalog"):
        load_employee_scenario_catalog(invalid_scenario_schema)

    invalid_source_schema = tmp_path / "invalid-sources.json"
    invalid_source_schema.write_text("{}", encoding="utf-8")
    with pytest.raises(EmployeeDataError, match="Invalid employee knowledge sources"):
        load_employee_knowledge_sources(invalid_source_schema)


def test_loader_rejects_unknown_agent_and_scenario_knowledge_sources(tmp_path: Path) -> None:
    agent_root = tmp_path / "agent-source"
    _copy_employee_data(agent_root)
    agents = json.loads((agent_root / "agents.json").read_text(encoding="utf-8"))
    agents["agents"][0]["knowledgeSourceIds"].append("kb-unknown-source")
    (agent_root / "agents.json").write_text(json.dumps(agents), encoding="utf-8")
    with pytest.raises(EmployeeDataError, match=r"Agent .* unknown knowledge source"):
        load_employee_assets(agent_root)

    scenario_root = tmp_path / "scenario-source"
    _copy_employee_data(scenario_root)
    lines = (scenario_root / "scenarios.jsonl").read_text(encoding="utf-8").splitlines()
    scenario = json.loads(lines[0])
    scenario["sources"][0]["knowledgeSourceId"] = "kb-unknown-source"
    lines[0] = json.dumps(scenario)
    (scenario_root / "scenarios.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(EmployeeDataError, match=r"Scenario .* unknown knowledge source"):
        load_employee_assets(scenario_root)


def test_cached_catalog_and_intent_request_paths(tmp_path: Path) -> None:
    copied_root = tmp_path / "employee-data"
    _copy_employee_data(copied_root)
    load_employee_catalog.cache_clear()
    first = load_employee_catalog(copied_root)
    second = load_employee_catalog(copied_root)
    assert first is second

    result = resolve_employee_request(
        EmployeeScenarioRequest(intent="I need help planning parental leave")
    )
    assert isinstance(result, EmployeeExperiencePresentation)
    assert result.scenario_id == "parental-leave-planner"

    with pytest.raises(EmployeeDataError, match="No employee showcase scenario matches"):
        resolve_employee_request(EmployeeScenarioRequest(intent="qzxwv unmatched vocabulary"))

    catalog = resolve_employee_request(EmployeeCatalogRequest())
    assert isinstance(catalog, EmployeeScenarioCatalogPresentation)
    assert catalog.total_agents == 26
    assert match_employee_intent("anything", limit=0) == ()
