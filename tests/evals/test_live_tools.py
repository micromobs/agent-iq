"""Live hero tool trace evaluation tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

import agent_iq.evals.live_tools as live_tools
from agent_iq.contracts import SapSourceMode, ToolBackend
from agent_iq.evals.live_tools import (
    MAX_RUN_REPORTS,
    LiveToolCase,
    build_live_tool_payload,
    evaluate_live_tool_case,
    load_live_tool_suite,
    message_operation_id,
    parse_tool_telemetry_messages,
    write_live_tool_report,
)
from agent_iq.runtime.telemetry import ToolTelemetryEvent

OPERATION_ID = "a" * 32


def _case() -> LiveToolCase:
    return LiveToolCase(
        case_id="demand-live",
        agent_id="consumer-demand-forecasting",
        prompt="Use the demand tool for the labelled fixture.",
        expected_tool_ids=("retrieve_demand_history",),
        expected_backend=ToolBackend.SAP,
        expected_source_mode=SapSourceMode.FIXTURE,
        required_answer_terms=("NOT LIVE SAP",),
        require_citation=False,
    )


def _probe() -> dict[str, object]:
    checks = {
        name: True
        for name in (
            "agentActive",
            "hostedKind",
            "responsesProtocolV2",
            "pythonRuntimeIs313",
            "modelDeploymentMatches",
            "canonicalToolboxBound",
            "codeContentHashPresent",
            "cardNameMatches",
            "cardVersionIsV1",
            "cardMatchesCanonical",
            "nativeCardTracePresent",
            "nativeMessageTracePresent",
            "answerNonempty",
            "requiredTermsPresent",
            "sourceTitlePolicySatisfied",
            "traceContainsNoPrompt",
            "traceContainsNoToken",
            "traceContainsNoAuthorization",
        )
    }
    checks["sourceReferencePresent"] = False
    return {
        "checks": checks,
        "invocation": {
            "answer": "DETERMINISTIC FIXTURE - NOT LIVE SAP",
            "latencyMs": 12.0,
        },
        "transport": {
            "protocol": "a2a",
            "protocolVersion": "1.0",
            "binding": "JSONRPC",
            "fallbackUsed": False,
        },
        "agent": {"version": "3"},
        "trace": {
            "events": [
                {
                    "operation": "foundry.a2a.message.send",
                    "responseHeaders": {"x-request-id": OPERATION_ID},
                }
            ]
        },
    }


def _event(
    *,
    tool_id: str = "retrieve_demand_history",
    source_mode: str | None = "fixture",
    arguments_captured: bool = False,
) -> ToolTelemetryEvent:
    return ToolTelemetryEvent(
        occurred_at=datetime(2026, 7, 11, tzinfo=UTC),
        agent_id="consumer-demand-forecasting",
        tool_id=tool_id,
        backend="sap",
        status="completed",
        duration_ms=5,
        cost_units=None,
        cost_capture_status="unavailable",
        source_mode=source_mode,
        arguments_captured=arguments_captured,
    )


def test_live_tool_case_passes_with_correlated_safe_telemetry() -> None:
    result = evaluate_live_tool_case(_case(), _probe(), [_event()])

    assert result["status"] == "pass"
    assert result["operationId"] == OPERATION_ID
    assert result["observedToolIds"] == ["retrieve_demand_history"]
    assert "prompt" not in result
    assert "answer" not in result
    assert result["checks"]["telemetry.opaqueCatchAllAbsent"] is True


@pytest.mark.parametrize(
    ("event", "failed_check"),
    [
        (_event(tool_id="knowledge_base_retrieve"), "telemetry.exactToolSet"),
        (_event(source_mode="live"), "telemetry.sourceModeMatches"),
        (_event(arguments_captured=True), "telemetry.argumentsNotCaptured"),
    ],
)
def test_live_tool_case_fails_closed_on_trace_drift(
    event: ToolTelemetryEvent,
    failed_check: str,
) -> None:
    result = evaluate_live_tool_case(_case(), _probe(), [event])

    assert result["status"] == "fail"
    assert failed_check in result["failures"]


def test_telemetry_wire_parser_and_operation_validation() -> None:
    message = json.dumps(
        {
            "event": "agent_iq.tool",
            "occurredAt": "2026-07-11T00:00:00Z",
            "agentId": "consumer-demand-forecasting",
            "toolId": "retrieve_demand_history",
            "backend": "sap",
            "status": "completed",
            "durationMs": 5,
            "costUnits": None,
            "costCaptureStatus": "unavailable",
            "sourceMode": "fixture",
            "argumentsCaptured": False,
        }
    )

    parsed = parse_tool_telemetry_messages([message])

    assert parsed == (_event(),)
    assert message_operation_id(_probe()) == OPERATION_ID
    with pytest.raises(ValueError, match="operation ID"):
        message_operation_id({"trace": {"events": []}})


def test_live_tool_suite_validation_and_report_publication(tmp_path: Path) -> None:
    suite_path = tmp_path / "cases.json"
    case = _case().model_dump(by_alias=True, mode="json")
    suite_path.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "suiteId": "consumer-hero-tools-live",
                "cases": [case],
            }
        ),
        encoding="utf-8",
    )
    assert load_live_tool_suite(suite_path).cases == (_case(),)

    result = evaluate_live_tool_case(_case(), _probe(), [_event()])
    payload = build_live_tool_payload(
        [result],
        expected_case_count=1,
        project_endpoint="https://example.invalid/project",
        generated_at=datetime(2026, 7, 11, tzinfo=UTC),
    )
    report = write_live_tool_report(payload, tmp_path / "reports")

    assert payload["status"] == "pass"
    assert payload["exactToolSelection"] == 1.0
    assert report.exists()
    assert (tmp_path / "reports" / "latest.json").read_text() == report.read_text()

    duplicate = {
        "schemaVersion": "1.0",
        "suiteId": "consumer-hero-tools-live",
        "cases": [case, case],
    }
    with pytest.raises(ValidationError, match="case IDs"):
        load_live_tool_suite(_write_json(tmp_path / "duplicate.json", duplicate))


def test_live_case_rejects_source_mode_on_foundry_backend() -> None:
    with pytest.raises(ValidationError, match="Only SAP"):
        LiveToolCase(
            case_id="invalid",
            agent_id="consumer-formulation-rd",
            prompt="Retrieve grounded formula evidence.",
            expected_tool_ids=("retrieve_formula_components",),
            expected_backend=ToolBackend.FOUNDRY_IQ,
            expected_source_mode=SapSourceMode.FIXTURE,
            required_answer_terms=("protease",),
        )


def test_live_tool_payload_fails_when_suite_is_incomplete() -> None:
    payload = build_live_tool_payload(
        [],
        expected_case_count=5,
        project_endpoint="https://example.invalid/project",
        generated_at=datetime(2026, 7, 11, tzinfo=UTC),
    )

    assert payload["status"] == "fail"
    assert payload["completeSuite"] is False
    assert payload["exactToolSelection"] == 0.0


def test_live_tool_report_is_collision_safe_bounded_and_preserves_unrelated_entries(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "reports"
    runs_dir = output_dir / "runs"
    unrelated = runs_dir / "unrelated.json"
    unrelated_directory = runs_dir / f"{live_tools.RUN_REPORT_PREFIX}not-a-run"
    near_match = runs_dir / f"{live_tools.RUN_REPORT_PREFIX}not-a-run.json"
    runs_dir.mkdir(parents=True)
    unrelated.write_text("keep", encoding="utf-8")
    unrelated_directory.mkdir()
    (unrelated_directory / "marker.txt").write_text("keep", encoding="utf-8")
    near_match.write_text("keep", encoding="utf-8")

    paths = []
    for index in range(MAX_RUN_REPORTS + 3):
        payload = build_live_tool_payload(
            [{"caseId": f"case-{index}", "status": "pass", "checks": {}}],
            expected_case_count=1,
            project_endpoint="https://example.invalid/project",
            generated_at=datetime(2026, 7, 11, tzinfo=UTC),
        )
        paths.append(write_live_tool_report(payload, output_dir))

    managed = [
        path
        for path in runs_dir.iterdir()
        if path.is_file() and live_tools._RUN_REPORT_NAME.fullmatch(path.name)
    ]
    assert len(paths) == len({path.name for path in paths})
    assert len(managed) == MAX_RUN_REPORTS
    assert (output_dir / "latest.json").read_text(encoding="utf-8") in {
        path.read_text(encoding="utf-8") for path in managed
    }
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert near_match.read_text(encoding="utf-8") == "keep"
    assert (unrelated_directory / "marker.txt").read_text(encoding="utf-8") == "keep"
    assert not list(tmp_path.rglob("*.tmp"))


def test_live_tool_reports_reject_raw_prompt_or_tool_arguments(tmp_path: Path) -> None:
    payload = build_live_tool_payload(
        [{"caseId": "case-1", "status": "pass", "checks": {}}],
        expected_case_count=1,
        project_endpoint="https://example.invalid/project",
        generated_at=datetime(2026, 7, 11, tzinfo=UTC),
    )

    unsafe_prompt = {**payload, "prompt": "do not persist this"}
    with pytest.raises(ValueError, match="Raw prompt"):
        write_live_tool_report(unsafe_prompt, tmp_path / "prompt")

    unsafe_arguments = {**payload, "toolArguments": {"query": "do not persist this"}}
    with pytest.raises(ValueError, match="Raw sensitive"):
        write_live_tool_report(unsafe_arguments, tmp_path / "arguments")


def test_live_tool_report_cleans_temp_on_atomic_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = build_live_tool_payload(
        [{"caseId": "case-1", "status": "pass", "checks": {}}],
        expected_case_count=1,
        project_endpoint="https://example.invalid/project",
        generated_at=datetime(2026, 7, 11, tzinfo=UTC),
    )

    def fail_replace(source: object, destination: object) -> None:
        del source, destination
        raise OSError("simulated replace failure")

    monkeypatch.setattr("agent_iq.evals.live_tools.os.replace", fail_replace)

    with pytest.raises(OSError, match="simulated"):
        write_live_tool_report(payload, tmp_path / "reports")

    assert not list(tmp_path.rglob("*.tmp"))


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
