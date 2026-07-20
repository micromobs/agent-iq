from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_iq.evals import (
    CoworkUxCase,
    CoworkUxObservation,
    build_cowork_ux_payload,
    evaluate_cowork_ux,
    evaluate_cowork_ux_suite,
    load_cowork_ux_cases,
    load_cowork_ux_observations,
    write_cowork_ux_report,
)

NOW = datetime(2026, 7, 11, 18, 30, tzinfo=UTC)


def ideal_observation() -> CoworkUxObservation:
    return CoworkUxObservation(
        transcript_id="cowork-brightwash-ideal",
        case_id="brightwash-coldwater-citations",
        tool_calls=("orchestrate",),
        approval_count=0,
        spill_recovery_count=0,
        progress_events=(
            "Matching eligible specialists",
            "Selected Fabrikam Consumer Products Formulation R&D - matched enzymes and BrightWash",
            "Retrieving approved evidence from Fabrikam Consumer Products Formulation R&D",
            "Building the cited answer",
            "Completed",
        ),
        first_progress_ms=25,
        completion_ms=28_000,
        payload_bytes=8_192,
        selected_agent_ids=("consumer-formulation-rd",),
        selection_reasons=(
            "Selected Fabrikam Consumer Products Formulation R&D "
            "for formulation science and the BrightWash-ClearWave enzyme comparison.",
        ),
        answer=(
            "BrightWash's cold-water package uses protease, amylase, mannanase, and lipase [1]. "
            "ClearWave adds pectate lyase to the same enzyme core [2]."
        ),
        source_ids=("consumer-brightwash-cold", "consumer-clearwave-cold"),
        citation_count=2,
    )


def test_historical_transcripts_expose_the_expected_p0_failures() -> None:
    results = evaluate_cowork_ux_suite(
        load_cowork_ux_cases(),
        load_cowork_ux_observations(),
    )

    assert len(results) == 2
    assert not any(result.passes for result in results)
    assert {
        "oneOrchestrationCall",
        "zeroGenericApprovals",
        "payloadBudget",
        "orderedReadableProgress",
        "firstProgressLatency",
        "transportSilent",
    }.issubset(results[0].failures)
    assert {
        "oneOrchestrationCall",
        "zeroSpillRecovery",
        "payloadBudget",
        "noProgressJargon",
        "intentScopedAnswer",
        "minimalSources",
    }.issubset(results[1].failures)


def test_ideal_transcript_passes_and_publishes_versioned_report(tmp_path: Path) -> None:
    case = load_cowork_ux_cases()[0]
    result = evaluate_cowork_ux(case, ideal_observation())
    payload = build_cowork_ux_payload(
        (result,),
        generated_at=NOW,
        run_label="canary-1.0.2",
    )
    paths = write_cowork_ux_report(payload, tmp_path)

    assert result.passes
    assert payload["passes"] is True
    assert json.loads(paths.latest_json.read_text(encoding="utf-8")) == payload
    html = paths.latest_html.read_text(encoding="utf-8")
    assert "Agent IQ Cowork UX: PASS" in html
    assert "<script" not in html
    history = json.loads(paths.history_json.read_text(encoding="utf-8"))
    assert history == [
        {
            "cases": 1,
            "failedCases": 0,
            "generatedAt": "2026-07-11T18:30:00Z",
            "passedCases": 1,
            "passes": True,
            "runId": "2026-07-11T18-30-00Z-canary-1.0.2",
            "runLabel": "canary-1.0.2",
        }
    ]


def test_dedicated_single_agent_demo_counts_as_one_orchestration_call() -> None:
    case = load_cowork_ux_cases()[0]
    observation = ideal_observation().model_copy(update={"tool_calls": ("run_single_agent_demo",)})

    assert evaluate_cowork_ux(case, observation).passes


def test_minimal_sources_are_exact_but_order_independent() -> None:
    case = load_cowork_ux_cases()[0]
    observation = ideal_observation()

    reversed_sources = observation.model_copy(
        update={"source_ids": tuple(reversed(observation.source_ids))}
    )
    assert evaluate_cowork_ux(case, reversed_sources).passes

    duplicate_sources = observation.model_copy(
        update={"source_ids": ("consumer-brightwash-cold", "consumer-brightwash-cold")}
    )
    assert "minimalSources" in evaluate_cowork_ux(case, duplicate_sources).failures


def test_eval_contracts_fail_closed(tmp_path: Path) -> None:
    case = load_cowork_ux_cases()[0]
    with pytest.raises(ValueError, match="does not match"):
        evaluate_cowork_ux(
            case,
            ideal_observation().model_copy(update={"case_id": "different-case"}),
        )
    with pytest.raises(ValidationError, match="selection reason"):
        CoworkUxObservation.model_validate(
            ideal_observation().model_dump(by_alias=False) | {"selection_reasons": ()}
        )
    with pytest.raises(ValueError, match="Unsupported"):
        write_cowork_ux_report(
            {"schemaVersion": "2.0", "suiteId": "cowork-ux"},
            tmp_path,
        )


def test_suite_loader_and_history_fail_closed(tmp_path: Path) -> None:
    case = load_cowork_ux_cases()[0]
    observation = ideal_observation()
    result = evaluate_cowork_ux(case, observation)
    payload = build_cowork_ux_payload((result,), generated_at=NOW)

    with pytest.raises(ValueError, match="unique"):
        evaluate_cowork_ux_suite((case, case), (observation,))
    with pytest.raises(ValueError, match="Unknown"):
        evaluate_cowork_ux_suite(
            (case,),
            (observation.model_copy(update={"case_id": "unknown-case"}),),
        )
    with pytest.raises(ValueError, match="At least one Cowork UX observation"):
        evaluate_cowork_ux_suite((case,), ())
    with pytest.raises(ValueError, match="At least one Cowork UX result"):
        build_cowork_ux_payload(())

    missing = tmp_path / "missing.jsonl"
    with pytest.raises(ValueError, match="Cannot read"):
        load_cowork_ux_cases(missing)
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="At least one cowork ux case"):
        load_cowork_ux_cases(empty)

    invalid_history_root = tmp_path / "invalid-history"
    invalid_history_root.mkdir()
    (invalid_history_root / "history.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON list"):
        write_cowork_ux_report(payload, invalid_history_root)
    with pytest.raises(TypeError, match="caseResults"):
        write_cowork_ux_report(
            payload | {"caseResults": "invalid"},
            tmp_path / "invalid-payload",
        )
    with pytest.raises(ValueError, match="identifiers"):
        write_cowork_ux_report(
            payload | {"runLabel": ""},
            tmp_path / "invalid-label",
        )

    history_root = tmp_path / "history"
    write_cowork_ux_report(payload, history_root)
    write_cowork_ux_report(payload, history_root)
    assert len(json.loads((history_root / "history.json").read_text(encoding="utf-8"))) == 1

    with pytest.raises(ValidationError, match="cannot overlap"):
        CoworkUxCase.model_validate(
            case.model_dump(by_alias=False) | {"forbidden_answer_terms": case.required_answer_terms}
        )
