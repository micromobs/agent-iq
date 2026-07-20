"""GitHub Copilot plugin host-certificate tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agent_iq.deployments import copilot_probe
from agent_iq.deployments.copilot_probe import (
    EXPECTED_AGENT,
    EXPECTED_REPLY,
    MAX_RUN_REPORTS,
    RUN_REPORT_PREFIX,
    CopilotPluginProbeError,
    _parse_event_lines,
    _validate_events,
    main,
    run_probe,
    write_report,
)


def _events(
    *,
    skills: tuple[str, ...] = ("orchestrate-agents", "replay-execution"),
    mcp_status: str = "connected",
    response: str = EXPECTED_REPLY,
    tool_requests: list[object] | None = None,
    exit_code: int = 0,
) -> tuple[dict[str, Any], ...]:
    return (
        {
            "type": "session.skills_loaded",
            "data": {
                "skills": [{"name": name, "source": "plugin", "enabled": True} for name in skills]
            },
        },
        {
            "type": "session.mcp_servers_loaded",
            "data": {
                "servers": [
                    {
                        "name": "agent-iq",
                        "status": mcp_status,
                        "source": "plugin",
                        "transport": "http",
                        "pluginName": "agent-iq",
                    }
                ]
            },
        },
        {
            "type": "assistant.message",
            "data": {
                "content": response,
                "toolRequests": [] if tool_requests is None else tool_requests,
            },
        },
        {"type": "result", "exitCode": exit_code},
    )


def _jsonl(events: tuple[dict[str, Any], ...]) -> str:
    return "diagnostic line\n" + "\n".join(json.dumps(event) for event in events) + "\n"


def _probe_result() -> copilot_probe.CopilotPluginProbe:
    return copilot_probe.CopilotPluginProbe(
        status="pass",
        cli_version="GitHub Copilot CLI 1.0.71-0",
        plugin_name="agent-iq",
        plugin_version="1.0.0",
        source_sha256="a" * 64,
        agent=EXPECTED_AGENT,
        skills=("orchestrate-agents", "replay-execution"),
        mcp_server="agent-iq",
        mcp_status="connected",
        mcp_transport="http",
        response_marker=EXPECTED_REPLY,
        tool_calls=0,
    )


def test_event_validation_certifies_skills_mcp_agent_and_no_tool_turn() -> None:
    parsed = _parse_event_lines(_jsonl(_events()))

    summary = _validate_events(parsed)

    assert summary == {
        "mcp_status": "connected",
        "mcp_transport": "http",
        "tool_calls": 0,
    }


@pytest.mark.parametrize(
    ("events", "message"),
    [
        (_events(skills=("orchestrate-agents",)), "required plugin skills"),
        (_events(mcp_status="failed"), "did not connect"),
        (_events(response="wrong"), "no-tool turn failed"),
        (_events(tool_requests=[{"name": "agent-iq/orchestrate"}]), "no-tool turn failed"),
        (_events(exit_code=1), "successful terminal result"),
    ],
)
def test_event_validation_fails_closed(
    events: tuple[dict[str, Any], ...],
    message: str,
) -> None:
    with pytest.raises(CopilotPluginProbeError, match=message):
        _validate_events(events)


def test_event_parser_rejects_missing_machine_readable_events() -> None:
    with pytest.raises(CopilotPluginProbeError, match="no machine-readable"):
        _parse_event_lines("not json\n{invalid}\n")


def test_run_probe_uses_namespaced_agent_and_returns_content_safe_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "GitHub Copilot CLI 1.0.71-0\n", "")
        return subprocess.CompletedProcess(command, 0, _jsonl(_events()), "")

    monkeypatch.setattr(copilot_probe, "_copilot_executable", lambda: "copilot.exe")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_probe()

    assert result.status == "pass"
    assert result.agent == EXPECTED_AGENT
    assert result.mcp_status == "connected"
    assert result.tool_calls == 0
    assert "--agent" in calls[1]
    assert EXPECTED_AGENT in calls[1]
    assert "AGENT_IQ_PLUGIN_REMOTE_OK" in calls[1][-1]


@pytest.mark.parametrize(
    ("version_result", "session_result", "message"),
    [
        (
            subprocess.CompletedProcess(["copilot", "--version"], 1, "", ""),
            None,
            "version probe failed",
        ),
        (
            subprocess.CompletedProcess(
                ["copilot", "--version"], 0, "GitHub Copilot CLI 1.0.71-0\n", ""
            ),
            subprocess.CompletedProcess(["copilot"], 9, "", ""),
            "exited with code 9",
        ),
    ],
)
def test_run_probe_surfaces_cli_process_failures(
    version_result: subprocess.CompletedProcess[str],
    session_result: subprocess.CompletedProcess[str] | None,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = iter([version_result] if session_result is None else [version_result, session_result])
    monkeypatch.setattr(copilot_probe, "_copilot_executable", lambda: "copilot.exe")
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: next(results))

    with pytest.raises(CopilotPluginProbeError, match=message):
        run_probe()


def test_probe_report_excludes_session_payloads(tmp_path: Path) -> None:
    result = _probe_result()

    report_path = write_report(result, tmp_path)
    report_text = report_path.read_text(encoding="utf-8")

    assert json.loads(report_text)["source_sha256"] == "a" * 64
    assert "sessionId" not in report_text
    assert "apiCallId" not in report_text
    assert (tmp_path / "latest.json").read_bytes() == report_path.read_bytes()


def test_reports_are_unique_at_the_same_instant_and_latest_is_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FrozenDateTime:
        @classmethod
        def now(cls, tz: object) -> datetime:
            del tz
            return datetime(2026, 7, 12, 11, 48, tzinfo=UTC)

    monkeypatch.setattr(copilot_probe, "datetime", FrozenDateTime)
    result = _probe_result()

    first = write_report(result, tmp_path)
    second = write_report(result, tmp_path)

    assert first != second
    assert first.name.startswith(RUN_REPORT_PREFIX)
    assert second.name.startswith(RUN_REPORT_PREFIX)
    assert first.exists() and second.exists()
    assert (tmp_path / "latest.json").read_bytes() == second.read_bytes()
    assert not any(path.suffix == ".tmp" for path in tmp_path.iterdir())


def test_report_writes_allocate_unique_names_concurrently(tmp_path: Path) -> None:
    result = _probe_result()

    def write(_: int) -> Path:
        return write_report(result, tmp_path)

    with ThreadPoolExecutor(max_workers=8) as executor:
        paths = list(executor.map(write, range(8)))

    assert len({path.name for path in paths}) == len(paths)
    assert all(path.exists() and path.name.startswith(RUN_REPORT_PREFIX) for path in paths)
    assert (tmp_path / "latest.json").read_bytes() in {path.read_bytes() for path in paths}
    assert not any(path.suffix == ".tmp" for path in tmp_path.iterdir())


def test_report_retention_is_bounded_and_preserves_unrelated_entries(tmp_path: Path) -> None:
    result = _probe_result()
    unrelated_json = tmp_path / "unrelated.json"
    unrelated_json.write_text("keep me", encoding="utf-8")
    unrelated_file = tmp_path / "notes.txt"
    unrelated_file.write_text("also keep me", encoding="utf-8")
    unrelated_directory = tmp_path / "unrelated-directory"
    unrelated_directory.mkdir()
    run_named_directory = tmp_path / f"{RUN_REPORT_PREFIX}20260712T114800000000Z-{'0' * 32}.json"
    run_named_directory.mkdir()
    near_match = tmp_path / f"{RUN_REPORT_PREFIX}20260712T114800000000Z-not-a-uuid.json"
    near_match.write_text("keep this too", encoding="utf-8")
    other_writer = tmp_path / (f"copilot-canvas-probe-20260712T114800000000Z-{'1' * 32}.json")
    other_writer.write_text("keep the other writer", encoding="utf-8")

    paths = [write_report(result, tmp_path) for _ in range(MAX_RUN_REPORTS + 3)]

    assert sum(path.exists() for path in paths) == MAX_RUN_REPORTS
    assert unrelated_json.read_text(encoding="utf-8") == "keep me"
    assert unrelated_file.read_text(encoding="utf-8") == "also keep me"
    assert unrelated_directory.is_dir()
    assert run_named_directory.is_dir()
    assert near_match.read_text(encoding="utf-8") == "keep this too"
    assert other_writer.read_text(encoding="utf-8") == "keep the other writer"
    assert not any(path.suffix == ".tmp" for path in tmp_path.iterdir())


def test_report_cleans_temporary_file_when_atomic_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_replace(*_args: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        write_report(_probe_result(), tmp_path)

    assert not any(path.suffix == ".tmp" for path in tmp_path.iterdir())
    assert not (tmp_path / "latest.json").exists()


def test_missing_cli_fails_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: None)

    with pytest.raises(CopilotPluginProbeError, match="not installed"):
        copilot_probe._copilot_executable()


def test_cli_resolution_returns_first_available_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        shutil, "which", lambda name: "C:\\bin\\copilot.exe" if name.endswith(".exe") else None
    )

    assert copilot_probe._copilot_executable() == "C:\\bin\\copilot.exe"


def test_probe_cli_writes_json_report_and_supports_no_report_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(copilot_probe, "run_probe", lambda *_args, **_kwargs: _probe_result())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "copilot-probe",
            "--report-root",
            str(tmp_path),
            "--timeout-seconds",
            "30",
            "--json",
        ],
    )

    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "pass"
    assert Path(payload["reportPath"]).is_file()

    monkeypatch.setattr(sys, "argv", ["copilot-probe", "--no-report"])
    assert main() == 0
    assert capsys.readouterr().out.startswith("GitHub Copilot plugin: PASS")
