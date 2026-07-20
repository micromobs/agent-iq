"""GitHub Copilot Canvas host-registration certificate tests."""

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

from agent_iq.deployments import copilot_canvas_probe
from agent_iq.deployments.copilot_canvas_probe import (
    CANVAS_ID,
    CANVAS_NAME,
    DEFAULT_SOURCE_ROOT,
    EXPECTED_ACTIONS,
    EXPECTED_REPLY,
    MAX_RUN_REPORTS,
    RUN_REPORT_PREFIX,
    CopilotCanvasProbeError,
    _validate_events,
    main,
    run_probe,
    validate_source,
    write_report,
)


def _events(
    *,
    canvas_id: str = CANVAS_ID,
    actions: tuple[str, ...] = EXPECTED_ACTIONS,
    response: str = EXPECTED_REPLY,
    tool_requests: list[object] | None = None,
    exit_code: int = 0,
) -> tuple[dict[str, Any], ...]:
    return (
        {
            "type": "session.canvas.registry_changed",
            "data": {
                "canvases": [
                    {
                        "canvasId": canvas_id,
                        "displayName": CANVAS_NAME,
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                        "actions": [
                            {
                                "name": name,
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {},
                                    "additionalProperties": False,
                                },
                            }
                            for name in actions
                        ],
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
    return "\n".join(json.dumps(event) for event in events) + "\n"


def _probe_result() -> copilot_canvas_probe.CopilotCanvasProbe:
    return copilot_canvas_probe.CopilotCanvasProbe(
        status="pass",
        cli_version="GitHub Copilot CLI 1.0.71-0",
        extension_version="0.1.0",
        source_sha256="a" * 64,
        load_scope="temporary-user",
        canvas_id=CANVAS_ID,
        canvas_name=CANVAS_NAME,
        actions=EXPECTED_ACTIONS,
        host_registration="registered",
        render_acceptance="not-exercised-by-prompt-mode-probe",
        response_marker=EXPECTED_REPLY,
        tool_calls=0,
    )


def test_source_validation_certifies_replay_and_deterministic_file_set() -> None:
    files, version = validate_source(DEFAULT_SOURCE_ROOT)

    assert version == "0.1.0"
    assert len(files) == 8
    assert "web/demo/launch-command-center.agui.json" in files


def test_event_validation_certifies_canvas_actions_and_no_tool_turn() -> None:
    summary = _validate_events(_events())

    assert summary.actions == EXPECTED_ACTIONS
    assert summary.tool_calls == 0


@pytest.mark.parametrize(
    ("events", "message"),
    [
        (_events(canvas_id="other"), "did not register"),
        (_events(actions=("load_demo",)), "unexpected Canvas action"),
        (_events(response="wrong"), "no-tool turn failed"),
        (_events(tool_requests=[{"name": "open"}]), "no-tool turn failed"),
        (_events(exit_code=1), "successful result"),
    ],
)
def test_event_validation_fails_closed(
    events: tuple[dict[str, Any], ...],
    message: str,
) -> None:
    with pytest.raises(CopilotCanvasProbeError, match=message):
        _validate_events(events)


def test_run_probe_uses_temporary_user_scope_and_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    extensions_root = tmp_path / "extensions"

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "GitHub Copilot CLI 1.0.71-0\n", "")
        assert (extensions_root / "agent-iq-canvas" / "extension.mjs").is_file()
        return subprocess.CompletedProcess(command, 0, _jsonl(_events()), "")

    monkeypatch.setattr(copilot_canvas_probe, "_copilot_executable", lambda: "copilot.exe")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_probe(user_extensions_root=extensions_root)

    assert result.status == "pass"
    assert result.load_scope == "temporary-user"
    assert result.actions == EXPECTED_ACTIONS
    assert result.tool_calls == 0
    assert "--experimental" in calls[1]
    assert "playwright" in calls[1]
    assert not (extensions_root / "agent-iq-canvas").exists()


def test_run_probe_preserves_identical_existing_user_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "extensions" / "agent-iq-canvas"
    shutil.copytree(DEFAULT_SOURCE_ROOT, target)
    monkeypatch.setattr(copilot_canvas_probe, "_copilot_executable", lambda: "copilot.exe")
    results = iter(
        (
            subprocess.CompletedProcess(
                ["copilot", "--version"], 0, "GitHub Copilot CLI 1.0.71-0\n", ""
            ),
            subprocess.CompletedProcess(["copilot"], 0, _jsonl(_events()), ""),
        )
    )
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: next(results))

    result = run_probe(user_extensions_root=target.parent)

    assert result.load_scope == "existing-user-identical"
    assert target.is_dir()


def test_run_probe_rejects_different_existing_user_install(tmp_path: Path) -> None:
    target = tmp_path / "extensions" / "agent-iq-canvas"
    shutil.copytree(DEFAULT_SOURCE_ROOT, target)
    (target / "extension.mjs").write_text("different", encoding="utf-8")

    with pytest.raises(CopilotCanvasProbeError, match="different user-scoped"):
        run_probe(user_extensions_root=target.parent)


def test_run_probe_cleans_up_after_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extensions_root = tmp_path / "extensions"
    results: list[subprocess.CompletedProcess[str] | subprocess.TimeoutExpired] = [
        subprocess.CompletedProcess(
            ["copilot", "--version"], 0, "GitHub Copilot CLI 1.0.71-0\n", ""
        ),
        subprocess.TimeoutExpired(["copilot"], 1),
    ]

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        result = results.pop(0)
        if isinstance(result, subprocess.TimeoutExpired):
            raise result
        return result

    monkeypatch.setattr(copilot_canvas_probe, "_copilot_executable", lambda: "copilot.exe")
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(CopilotCanvasProbeError, match="timed out"):
        run_probe(user_extensions_root=extensions_root)

    assert not (extensions_root / "agent-iq-canvas").exists()


def test_run_probe_cleans_up_after_failed_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extensions_root = tmp_path / "extensions"
    results = iter(
        (
            subprocess.CompletedProcess(
                ["copilot", "--version"], 0, "GitHub Copilot CLI 1.0.71-0\n", ""
            ),
            subprocess.CompletedProcess(["copilot"], 2, "", "failed"),
        )
    )
    monkeypatch.setattr(copilot_canvas_probe, "_copilot_executable", lambda: "copilot.exe")
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: next(results))

    with pytest.raises(CopilotCanvasProbeError, match="exited with code 2"):
        run_probe(user_extensions_root=extensions_root)

    assert not (extensions_root / "agent-iq-canvas").exists()


def test_run_probe_rejects_failed_version_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(copilot_canvas_probe, "_copilot_executable", lambda: "copilot.exe")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["copilot", "--version"], 1, "", "failed"
        ),
    )

    with pytest.raises(CopilotCanvasProbeError, match="version probe failed"):
        run_probe(user_extensions_root=tmp_path / "extensions")


def test_probe_rejects_executable_resolution_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_resolution() -> str:
        raise RuntimeError("missing Copilot CLI")

    monkeypatch.setattr(copilot_canvas_probe, "_copilot_executable", fail_resolution)

    with pytest.raises(CopilotCanvasProbeError, match="missing Copilot CLI"):
        copilot_canvas_probe._resolve_executable()


def test_probe_cleanup_fails_loudly_when_residue_remains(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "agent-iq-canvas"
    target.mkdir()
    monkeypatch.setattr(shutil, "rmtree", lambda _path: None)

    with pytest.raises(CopilotCanvasProbeError, match="cleanup did not complete"):
        copilot_canvas_probe._remove_tree(target)


def test_probe_report_excludes_host_session_payloads(tmp_path: Path) -> None:
    report_path = write_report(_probe_result(), tmp_path)
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

    monkeypatch.setattr(copilot_canvas_probe, "datetime", FrozenDateTime)
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
    other_writer = tmp_path / f"copilot-plugin-probe-20260712T114800000000Z-{'1' * 32}.json"
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


def test_probe_cli_writes_json_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        copilot_canvas_probe,
        "run_probe",
        lambda *_args, **_kwargs: _probe_result(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "copilot-canvas-probe",
            "--report-root",
            str(tmp_path),
            "--json",
        ],
    )

    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["host_registration"] == "registered"
    assert payload["render_acceptance"] == "not-exercised-by-prompt-mode-probe"
    assert Path(payload["reportPath"]).is_file()
