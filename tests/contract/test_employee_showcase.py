"""Focused contract tests for the isolated Agent IQ Employee Operations showcase.

These tests exercise only ``agent_iq.employee`` (contracts, assets, loader) and
the on-disk sample data under ``src/agent_iq/data/employee``. They do not
touch, and are not touched by, the production MCP surfaces or any UI code.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_iq.employee import (
    REQUIRED_AGENT_IDS,
    REQUIRED_SCENARIO_IDS,
    EmployeeActionPreparationRequest,
    EmployeeActionState,
    EmployeeAgentGroup,
    EmployeeCatalogRequest,
    EmployeeExperiencePresentation,
    EmployeeScenarioCatalogPresentation,
    EmployeeScenarioRequest,
    ScenarioActionClass,
    ScenarioActionKind,
    ScenarioSectionKind,
    build_experience_presentation,
    list_employee_scenarios,
    load_employee_assets,
    match_employee_intent,
    prepare_employee_action,
    resolve_employee_request,
    resolve_employee_scenario,
)
from agent_iq.employee.contracts import (
    _ACTION_KIND_CLASSES,
    READ_ONLY_ACTION_KINDS,
    validate_phase_dag,
)
from agent_iq.employee.loader import DEFAULT_EMPLOYEE_DATA, EmployeeDataError

CATALOG_SIZE_BUDGET_BYTES = 32 * 1024
EXPERIENCE_SIZE_BUDGET_BYTES = 24 * 1024

_ASSETS = load_employee_assets()


# ---------------------------------------------------------------------------
# Catalog shape: exact counts, groups, uniqueness
# ---------------------------------------------------------------------------


def test_exactly_26_agents() -> None:
    agent_ids = {agent.agent_id for agent in _ASSETS.agents.agents}
    assert len(_ASSETS.agents.agents) == 26
    assert agent_ids == REQUIRED_AGENT_IDS


def test_exactly_30_scenarios() -> None:
    scenario_ids = {scenario.scenario_id for scenario in _ASSETS.scenarios.scenarios}
    assert len(_ASSETS.scenarios.scenarios) == 30
    assert scenario_ids == REQUIRED_SCENARIO_IDS


def test_scenario_ids_are_unique() -> None:
    scenario_ids = [scenario.scenario_id for scenario in _ASSETS.scenarios.scenarios]
    assert len(scenario_ids) == len(set(scenario_ids))


def test_all_three_agent_groups_present() -> None:
    groups = {agent.group for agent in _ASSETS.agents.agents}
    assert groups == set(EmployeeAgentGroup)
    # Each group must have at least one agent so the catalog summary is meaningful.
    for group in EmployeeAgentGroup:
        assert any(agent.group == group for agent in _ASSETS.agents.agents)


def test_agent_glyphs_are_distinct_and_short() -> None:
    glyphs = [agent.glyph for agent in _ASSETS.agents.agents]
    assert len(glyphs) == len(set(glyphs))
    for glyph in glyphs:
        assert 1 <= len(glyph) <= 4


def test_agents_use_clean_provider_language() -> None:
    for agent in _ASSETS.agents.agents:
        assert agent.provider == "Contoso Employee Services"


def test_knowledge_sources_use_clean_provider_language() -> None:
    for source in _ASSETS.knowledge_sources.sources:
        assert source.provider == "Contoso Employee Services"


# ---------------------------------------------------------------------------
# Cross-reference integrity
# ---------------------------------------------------------------------------


def test_agent_knowledge_source_refs_are_valid() -> None:
    source_ids = {source.knowledge_source_id for source in _ASSETS.knowledge_sources.sources}
    for agent in _ASSETS.agents.agents:
        assert set(agent.knowledge_source_ids) <= source_ids


def test_scenario_agent_refs_are_valid() -> None:
    agent_ids = {agent.agent_id for agent in _ASSETS.agents.agents}
    for scenario in _ASSETS.scenarios.scenarios:
        assert set(scenario.agent_ids) <= agent_ids
        assert 2 <= len(scenario.agent_ids) <= 6


def test_scenario_knowledge_source_refs_are_valid() -> None:
    source_ids = {source.knowledge_source_id for source in _ASSETS.knowledge_sources.sources}
    for scenario in _ASSETS.scenarios.scenarios:
        scenario_source_ids = {source.knowledge_source_id for source in scenario.sources}
        assert scenario_source_ids <= source_ids
        assert 3 <= len(scenario.sources) <= 6


def test_loader_rejects_dangling_agent_reference(tmp_path: Path) -> None:
    """A scenario referencing an unknown agent must fail cross-reference validation."""

    for name in ("agents.json", "scenarios.jsonl", "knowledge_sources.json"):
        (tmp_path / name).write_text(
            (DEFAULT_EMPLOYEE_DATA / name).read_text(encoding="utf-8"), encoding="utf-8"
        )

    scenario_lines = (tmp_path / "scenarios.jsonl").read_text(encoding="utf-8").splitlines()
    first = json.loads(scenario_lines[0])
    # Append a bogus agent id rather than replacing an existing one, so no phase
    # ends up referencing an agent that is absent from agentIds (a separate,
    # already-covered validator) -- this isolates the cross-reference check.
    first["agentIds"] = [*first["agentIds"], "employee-does-not-exist"]
    scenario_lines[0] = json.dumps(first)
    (tmp_path / "scenarios.jsonl").write_text("\n".join(scenario_lines) + "\n", encoding="utf-8")

    with pytest.raises(EmployeeDataError, match="unknown agent"):
        load_employee_assets(tmp_path)


# ---------------------------------------------------------------------------
# DAG acyclicity
# ---------------------------------------------------------------------------


def test_every_scenario_phase_dag_is_acyclic() -> None:
    for scenario in _ASSETS.scenarios.scenarios:
        # Re-validating here is a smoke check; construction already enforced it.
        validate_phase_dag(scenario.phases, frozenset(scenario.agent_ids))


def test_phase_dag_rejects_a_cycle() -> None:
    from agent_iq.employee.contracts import ScenarioPhase, ScenarioPhaseStatus

    phases = (
        ScenarioPhase(
            phase_id="a",
            agent_id="employee-hr-concierge",
            title="A",
            status=ScenarioPhaseStatus.COMPLETED,
            status_label="Completed",
            output_label="A done",
            depends_on=("b",),
        ),
        ScenarioPhase(
            phase_id="b",
            agent_id="employee-hr-concierge",
            title="B",
            status=ScenarioPhaseStatus.COMPLETED,
            status_label="Completed",
            output_label="B done",
            depends_on=("a",),
        ),
    )
    with pytest.raises(ValueError, match="acyclic"):
        validate_phase_dag(phases, frozenset({"employee-hr-concierge"}))


# ---------------------------------------------------------------------------
# Section-kind coverage and per-kind validators
# ---------------------------------------------------------------------------


def test_all_core_section_kinds_appear_somewhere_in_the_catalog() -> None:
    used_kinds: set[ScenarioSectionKind] = set()
    for scenario in _ASSETS.scenarios.scenarios:
        used_kinds.update(section.kind for section in scenario.sections)
    assert used_kinds == set(ScenarioSectionKind)


def test_room_desk_reservation_uses_a_map_section() -> None:
    scenario = next(
        s for s in _ASSETS.scenarios.scenarios if s.scenario_id == "room-desk-reservation"
    )
    kinds = {section.kind for section in scenario.sections}
    assert ScenarioSectionKind.MAP in kinds


def test_org_explorer_uses_an_org_chart_section() -> None:
    scenario = next(s for s in _ASSETS.scenarios.scenarios if s.scenario_id == "org-explorer")
    kinds = {section.kind for section in scenario.sections}
    assert ScenarioSectionKind.ORG_CHART in kinds


def test_software_access_governance_covers_policy_and_blocked_access() -> None:
    scenario = next(
        s for s in _ASSETS.scenarios.scenarios if s.scenario_id == "software-access-governance"
    )
    kinds = {section.kind for section in scenario.sections}
    assert ScenarioSectionKind.POLICY_FINDINGS in kinds
    assert scenario.status.value == "blocked"


def test_site_health_governance_covers_dashboard_findings_and_remediation() -> None:
    scenario = next(
        s for s in _ASSETS.scenarios.scenarios if s.scenario_id == "site-health-governance"
    )
    kinds = {section.kind for section in scenario.sections}
    assert ScenarioSectionKind.DASHBOARD in kinds
    assert ScenarioSectionKind.POLICY_FINDINGS in kinds
    assert ScenarioSectionKind.CHECKLIST in kinds
    assert scenario.status.value == "blocked"


def test_comparison_table_section_rejects_row_length_mismatch() -> None:
    from agent_iq.employee.contracts import ScenarioSection, ScenarioSectionItem

    with pytest.raises(ValidationError, match="one value per column"):
        ScenarioSection(
            section_id="bad-table",
            kind=ScenarioSectionKind.COMPARISON_TABLE,
            title="Bad table",
            columns=("A", "B"),
            items=(ScenarioSectionItem(item_id="row-1", label="Row 1", values=("only-one",)),),
        )


def test_dashboard_section_requires_a_value_field() -> None:
    from agent_iq.employee.contracts import ScenarioSection, ScenarioSectionItem

    with pytest.raises(ValidationError, match="requires 'value'"):
        ScenarioSection(
            section_id="bad-dashboard",
            kind=ScenarioSectionKind.DASHBOARD,
            title="Bad dashboard",
            items=(ScenarioSectionItem(item_id="tile-1", label="Tile 1"),),
        )


# ---------------------------------------------------------------------------
# Safe actions: no execution claims, write-like actions are approval-bound
# ---------------------------------------------------------------------------


def test_no_action_claims_execution_occurred() -> None:
    banned_snippets = ("has been sent", "auto-approved", "executed automatically")
    for scenario in _ASSETS.scenarios.scenarios:
        for action in scenario.actions:
            haystack = f"{action.label} {action.description}".casefold()
            for snippet in banned_snippets:
                assert snippet not in haystack

        assert 2 <= len(scenario.actions) <= 4


def test_write_like_actions_require_approval() -> None:
    for scenario in _ASSETS.scenarios.scenarios:
        for action in scenario.actions:
            if action.kind not in READ_ONLY_ACTION_KINDS:
                assert action.requires_approval is True


def test_action_kind_validator_rejects_unapproved_write_action() -> None:
    from agent_iq.employee.contracts import ScenarioAction

    with pytest.raises(ValidationError, match="approval-bound"):
        ScenarioAction(
            action_id="bad-action",
            kind=ScenarioActionKind.REQUEST_APPROVAL,
            label="Do it now",
            description="Send this immediately without review.",
            requires_approval=False,
        )


def test_action_class_is_auto_derived_from_kind_when_omitted() -> None:
    from agent_iq.employee.contracts import ScenarioAction

    action = ScenarioAction(
        action_id="view-it",
        kind=ScenarioActionKind.VIEW_SOURCE,
        label="View source",
        description="Open the cited synthetic source in a side panel.",
        requires_approval=False,
    )
    assert action.action_class is ScenarioActionClass.LOCAL_UI


def test_action_class_validator_rejects_mismatched_explicit_value() -> None:
    from agent_iq.employee.contracts import ScenarioAction

    with pytest.raises(ValidationError, match="must declare action_class"):
        ScenarioAction(
            action_id="mismatched-action",
            kind=ScenarioActionKind.VIEW_SOURCE,
            action_class=ScenarioActionClass.SEND_MESSAGE,
            label="View source",
            description="Open the cited synthetic source in a side panel.",
            requires_approval=False,
        )


def test_action_preparation_round_trip_never_claims_execution() -> None:
    prepared = prepare_employee_action(
        EmployeeActionPreparationRequest(
            scenario_id="expense-smart-submit",
            action_id="ess-request-approval",
            form_values={"business-purpose": "Synthetic client workshop"},
        )
    )
    assert prepared.state is EmployeeActionState.PREPARED
    assert not prepared.executed
    assert prepared.required_approver == "Expense approver"
    assert prepared.submitted_values[0].field_id == "business-purpose"

    review = prepare_employee_action(
        EmployeeActionPreparationRequest(
            scenario_id="expense-smart-submit",
            action_id="ess-request-approval",
            form_values={"business-purpose": "Synthetic client workshop"},
            confirmed=True,
        )
    )
    assert review.state is EmployeeActionState.REVIEW_REQUIRED
    assert not review.executed
    assert "not been submitted" in review.summary


def test_read_only_action_receipt_is_explicitly_not_executed() -> None:
    receipt = prepare_employee_action(
        EmployeeActionPreparationRequest(
            scenario_id="my-day",
            action_id="my-day-view-sources",
        )
    )
    assert receipt.state is EmployeeActionState.NOT_EXECUTED
    assert not receipt.requires_approval
    assert receipt.required_approver is None
    assert not receipt.executed


def test_action_preparation_rejects_unknown_action() -> None:
    with pytest.raises(EmployeeDataError, match="Unknown action"):
        prepare_employee_action(
            EmployeeActionPreparationRequest(
                scenario_id="my-day",
                action_id="not-a-real-action",
            )
        )


def test_action_preparation_rejects_action_from_a_different_scenario() -> None:
    # "ess-request-approval" only exists on expense-smart-submit, not on my-day: an
    # action_id must be validated against the *selected* scenario, not any global registry.
    with pytest.raises(EmployeeDataError, match="Unknown action"):
        prepare_employee_action(
            EmployeeActionPreparationRequest(
                scenario_id="my-day",
                action_id="ess-request-approval",
            )
        )


def test_action_class_is_bounded_and_matches_kind() -> None:
    for scenario in _ASSETS.scenarios.scenarios:
        for action in scenario.actions:
            assert action.action_class in set(ScenarioActionClass)
            assert action.action_class == _ACTION_KIND_CLASSES[action.kind]


# ---------------------------------------------------------------------------
# Presentation building + size budgets
# ---------------------------------------------------------------------------


def test_compact_catalog_presentation_serializes_under_budget() -> None:
    catalog = list_employee_scenarios()
    assert len(catalog.scenarios) == 30
    payload = catalog.model_dump_json(by_alias=True).encode("utf-8")
    assert len(payload) <= CATALOG_SIZE_BUDGET_BYTES


@pytest.mark.parametrize("scenario_id", sorted(REQUIRED_SCENARIO_IDS))
def test_each_full_experience_serializes_under_budget(scenario_id: str) -> None:
    experience = resolve_employee_scenario(scenario_id)
    payload = experience.model_dump_json(by_alias=True).encode("utf-8")
    assert len(payload) <= EXPERIENCE_SIZE_BUDGET_BYTES


def test_experience_presentation_declares_disclosures_and_trust_boundary() -> None:
    experience = resolve_employee_scenario("my-day")
    assert "selected employee-services workflow" in experience.data_scope.casefold()
    payload = json.dumps(experience.model_dump(mode="json", by_alias=True)).casefold()
    assert "synthetic" not in payload
    assert "demo" not in payload
    assert "canary" not in payload
    assert "read-only" in experience.read_only_disclosure.casefold() or (
        "no" in experience.read_only_disclosure.casefold()
        and "write" in experience.read_only_disclosure.casefold()
    )
    assert experience.trust_boundary


def test_build_experience_presentation_matches_resolver() -> None:
    scenario = next(s for s in _ASSETS.scenarios.scenarios if s.scenario_id == "my-day")
    built = build_experience_presentation(_ASSETS, scenario)
    resolved = resolve_employee_scenario("my-day")
    assert built == resolved


def test_resolve_unknown_scenario_raises() -> None:
    with pytest.raises(EmployeeDataError, match="Unknown"):
        resolve_employee_scenario("not-a-real-scenario")


# ---------------------------------------------------------------------------
# Requests: validation and resolution
# ---------------------------------------------------------------------------


def test_scenario_request_requires_id_or_intent() -> None:
    with pytest.raises(ValidationError):
        EmployeeScenarioRequest()


def test_scenario_request_resolves_by_exact_id() -> None:
    request = EmployeeScenarioRequest(scenario_id="my-day")
    result = resolve_employee_request(request)
    assert isinstance(result, EmployeeExperiencePresentation)
    assert result.scenario_id == "my-day"


def test_catalog_request_resolves_to_compact_catalog() -> None:
    result = resolve_employee_request(EmployeeCatalogRequest())
    assert isinstance(result, EmployeeScenarioCatalogPresentation)
    assert len(result.scenarios) == 30


# ---------------------------------------------------------------------------
# Deterministic intent routing across representative prompts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("intent", "expected_scenario_id"),
    [
        ("I need help planning my upcoming parental leave", "parental-leave-planner"),
        ("My VPN won't connect and I have a client meeting soon", "vpn-meeting-rescue"),
        ("Can you explain a line item on my payslip?", "payslip-explainer"),
        ("Where do we stand on month-end close?", "month-end-close"),
        ("Help me submit an expense report for my trip", "expense-smart-submit"),
        ("What's the latest personalized company news for me?", "personalized-news"),
        ("Show me my day dashboard with tasks and news", "my-day"),
        ("Help me request a new team site provisioning", "site-provisioning"),
        ("This software request seems to violate policy", "software-access-governance"),
    ],
)
def test_intent_routing_hits_expected_scenario(intent: str, expected_scenario_id: str) -> None:
    matches = match_employee_intent(intent)
    assert matches
    assert matches[0].scenario_id == expected_scenario_id
    assert matches[0].score > 0.0


def test_blocked_access_intent_routes_to_a_blocked_scenario() -> None:
    matches = match_employee_intent("why is my access blocked right now")
    assert matches
    assert matches[0].scenario_id in {"software-access-governance", "site-health-governance"}


def test_intent_match_is_deterministic_across_repeated_calls() -> None:
    first = match_employee_intent("help me with my benefits enrollment")
    second = match_employee_intent("help me with my benefits enrollment")
    assert first == second


def test_intent_match_ties_are_broken_by_scenario_id_ascending() -> None:
    # An intent composed only of stopwords tokenizes to an empty set, so every
    # scenario scores exactly 0.0 and the full ranking is a pure tie-break.
    matches = match_employee_intent("the a of to for and is are", limit=30)
    scenario_ids = [match.scenario_id for match in matches]
    assert all(match.score == 0.0 for match in matches)
    assert scenario_ids == sorted(scenario_ids)
