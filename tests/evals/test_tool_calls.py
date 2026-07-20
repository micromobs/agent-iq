"""Hero tool-call selection, argument, and report gates."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_iq.catalog import load_consumer_runtime_assets
from agent_iq.evals import (
    ToolCallCase,
    build_tool_call_payload,
    evaluate_tool_calls,
    load_tool_call_cases,
    write_tool_call_report,
)

NOW = datetime(2026, 7, 11, 4, 0, tzinfo=UTC)


def test_labelled_hero_tool_selection_and_argument_gate_is_perfect(
    tmp_path: Path,
) -> None:
    assets = load_consumer_runtime_assets()
    cases = load_tool_call_cases()
    expected_tools = {
        tool.descriptor.tool_id for definition in assets.runtime.agents for tool in definition.tools
    }

    result = evaluate_tool_calls(assets.runtime, cases)
    payload = build_tool_call_payload(
        result,
        tool_count=len(expected_tools),
        generated_at=NOW,
    )
    run_path = write_tool_call_report(payload, tmp_path)

    assert len(cases) == 20
    assert {case.expected_tool_id for case in cases} == expected_tools
    assert result.exact_selection_accuracy == 1.0
    assert result.argument_validity_accuracy == 1.0
    assert result.opaque_catchall_selections == 0
    assert result.passes
    assert payload["argumentSource"] == "labelled-evaluation-fixture"
    assert payload["failures"] == []
    assert run_path.exists()
    assert json.loads((tmp_path / "latest.json").read_text(encoding="utf-8")) == payload


def test_tool_call_argument_gate_rejects_extra_model_arguments() -> None:
    assets = load_consumer_runtime_assets()
    invalid = ToolCallCase(
        case_id="invalid-extra",
        agent_id="consumer-formulation-rd",
        intent="Assess enzyme stability for BrightWash.",
        expected_tool_id="assess_enzyme_stability",
        arguments={
            "product": "BrightWash",
            "conditions": "20 C wash",
            "unapproved": "must fail",
        },
    )

    result = evaluate_tool_calls(assets.runtime, (invalid,))

    assert result.exact_selection_accuracy == 1.0
    assert result.argument_validity_accuracy == 0.0
    assert not result.passes


def test_tool_call_loader_and_report_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Cannot read"):
        load_tool_call_cases(tmp_path / "missing.jsonl")

    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="At least one"):
        load_tool_call_cases(empty)

    duplicate = tmp_path / "duplicate.jsonl"
    line = (
        '{"agentId":"consumer-formulation-rd","arguments":{"product":"BrightWash"},'
        '"caseId":"same","expectedToolId":"retrieve_formula_components",'
        '"intent":"Retrieve formula components."}\n'
    )
    duplicate.write_text(line + line, encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        load_tool_call_cases(duplicate)

    with pytest.raises(ValueError, match="Unsupported"):
        write_tool_call_report(
            {"schemaVersion": "2.0", "suiteId": "consumer-hero-tool-calls"},
            tmp_path,
        )
