from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_iq.catalog import load_consumer_assets
from agent_iq.config import Settings
from agent_iq.evals import (
    DependencyExpectation,
    OrchestrationCase,
    build_orchestration_payload,
    evaluate_orchestration,
    load_orchestration_cases,
    write_orchestration_report,
)
from agent_iq.match import MatchEngine
from agent_iq.orchestrate import ExecutionPlanner

NOW = datetime(2026, 7, 11, 3, 0, tzinfo=UTC)


def test_labelled_orchestration_selection_and_dag_gate_is_perfect(tmp_path: Path) -> None:
    assets = load_consumer_assets()
    matcher = MatchEngine(
        assets.catalog,
        semantic_mode="offline",
        settings=Settings(_env_file=None),
    )
    planner = ExecutionPlanner(
        assets.catalog,
        clock=lambda: NOW,
        plan_id_factory=lambda: "plan-eval",
    )
    cases = load_orchestration_cases()

    result = evaluate_orchestration(matcher, planner, cases)
    payload = build_orchestration_payload(
        result,
        agent_count=len(assets.catalog.agents),
        generated_at=NOW,
    )
    run_path = write_orchestration_report(payload, tmp_path)

    assert len(cases) == 17
    assert result.exact_selection_accuracy == 1.0
    assert result.exact_dag_accuracy == 1.0
    assert result.false_positives == 0
    assert result.passes
    assert payload["passes"] is True
    assert payload["failures"] == []
    assert payload["gates"]["exactSelectionAccuracy"]["passes"] is True
    assert run_path.exists()
    assert json.loads((tmp_path / "latest.json").read_text(encoding="utf-8")) == payload

    five_agent = next(case for case in result.cases if case.case_id == "five-agent-sequential")
    assert len(five_agent.selected_agents) == 5
    assert len(five_agent.actual_dependencies) == 4


def test_orchestration_case_contract_rejects_invalid_dependency_graph() -> None:
    with pytest.raises(ValidationError, match="target"):
        OrchestrationCase(
            case_id="invalid-target",
            intent="Do work.",
            expected_agent_ids=("agent-a",),
            expected_dependencies=(
                DependencyExpectation(
                    agent_id="agent-b",
                    depends_on_agent_ids=("agent-a",),
                ),
            ),
        )
    with pytest.raises(ValidationError, match="source"):
        OrchestrationCase(
            case_id="invalid-source",
            intent="Do work.",
            expected_agent_ids=("agent-a",),
            expected_dependencies=(
                DependencyExpectation(
                    agent_id="agent-a",
                    depends_on_agent_ids=("agent-b",),
                ),
            ),
        )
    with pytest.raises(ValidationError, match="itself"):
        OrchestrationCase(
            case_id="invalid-self",
            intent="Do work.",
            expected_agent_ids=("agent-a",),
            expected_dependencies=(
                DependencyExpectation(
                    agent_id="agent-a",
                    depends_on_agent_ids=("agent-a",),
                ),
            ),
        )


def test_orchestration_case_loader_and_report_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Cannot read"):
        load_orchestration_cases(tmp_path / "missing.jsonl")

    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="At least one"):
        load_orchestration_cases(empty)

    duplicate = tmp_path / "duplicate.jsonl"
    line = '{"caseId":"same","intent":"Do work.","expectedAgentIds":[]}\n'
    duplicate.write_text(line + line, encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        load_orchestration_cases(duplicate)

    with pytest.raises(ValueError, match="Unsupported"):
        write_orchestration_report(
            {
                "schemaVersion": "2.0",
                "suiteId": "consumer-orchestration",
            },
            tmp_path,
        )
