"""Certify Agent IQ plugin loading in the real GitHub Copilot CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from .copilot_plugin import (
    DEFAULT_SOURCE_ROOT,
    EXPECTED_FILES,
    EXPECTED_SKILLS,
    PLUGIN_NAME,
    PLUGIN_VERSION,
    validate_source,
)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORT_ROOT = ROOT / "reports" / "evals" / "copilot-plugin"
EXPECTED_AGENT = f"{PLUGIN_NAME}:{PLUGIN_NAME}"
EXPECTED_REPLY = "AGENT_IQ_PLUGIN_REMOTE_OK"
RUN_REPORT_PREFIX = "copilot-plugin-probe-"
MAX_RUN_REPORTS = 20
_RUN_REPORT_NAME = re.compile(
    rf"^{re.escape(RUN_REPORT_PREFIX)}\d{{8}}T\d{{12}}Z-[0-9a-f]{{32}}\.json$"
)
_REPORT_WRITE_LOCK = Lock()


class CopilotPluginProbeError(RuntimeError):
    """Raised when the installed Copilot CLI does not load the plugin contract."""


@dataclass(frozen=True, slots=True)
class CopilotPluginProbe:
    status: str
    cli_version: str
    plugin_name: str
    plugin_version: str
    source_sha256: str
    agent: str
    skills: tuple[str, ...]
    mcp_server: str
    mcp_status: str
    mcp_transport: str
    response_marker: str
    tool_calls: int


def run_probe(
    source_root: Path = DEFAULT_SOURCE_ROOT,
    *,
    timeout_seconds: int = 120,
) -> CopilotPluginProbe:
    """Load the local plugin, connect its remote MCP server, and run a no-tool turn."""

    validate_source(source_root)
    executable = _copilot_executable()
    version_process = subprocess.run(
        [executable, "--version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if version_process.returncode != 0 or not version_process.stdout.strip():
        raise CopilotPluginProbeError("Copilot CLI version probe failed.")
    cli_version = version_process.stdout.splitlines()[0].strip()

    command = [
        executable,
        "--plugin-dir",
        str(source_root.resolve()),
        "--agent",
        EXPECTED_AGENT,
        "--disable-builtin-mcps",
        "--disable-mcp-server",
        "playwright",
        "--no-auto-update",
        "--no-custom-instructions",
        "--no-remote",
        "--output-format",
        "json",
        "--prompt",
        f"Do not call tools. Reply with exactly: {EXPECTED_REPLY}",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        raise CopilotPluginProbeError(
            f"Copilot plugin probe exited with code {completed.returncode}."
        )

    summary = _validate_events(_parse_event_lines(completed.stdout))
    return CopilotPluginProbe(
        status="pass",
        cli_version=cli_version,
        plugin_name=PLUGIN_NAME,
        plugin_version=PLUGIN_VERSION,
        source_sha256=_source_sha256(source_root),
        agent=EXPECTED_AGENT,
        skills=EXPECTED_SKILLS,
        mcp_server=PLUGIN_NAME,
        mcp_status=str(summary["mcp_status"]),
        mcp_transport=str(summary["mcp_transport"]),
        response_marker=EXPECTED_REPLY,
        tool_calls=int(summary["tool_calls"]),
    )


def write_report(
    result: CopilotPluginProbe,
    report_root: Path = DEFAULT_REPORT_ROOT,
) -> Path:
    """Persist a content-safe host certificate and latest pointer."""

    report_root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    payload = {
        "schemaVersion": "1.0",
        "generatedAt": now.isoformat(),
        **asdict(result),
    }
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    with _REPORT_WRITE_LOCK:
        path = _new_run_path(report_root, now)
        _atomic_write_text(path, text)
        _atomic_write_text(report_root / "latest.json", text)
        _prune_run_reports(report_root)
        return path


def _new_run_path(report_root: Path, now: datetime | None = None) -> Path:
    timestamp = datetime.now(UTC) if now is None else now
    for _ in range(100):
        stamp = timestamp.strftime("%Y%m%dT%H%M%S%fZ")
        candidate = report_root / f"{RUN_REPORT_PREFIX}{stamp}-{uuid.uuid4().hex}.json"
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not allocate a unique Copilot plugin probe report name.")


def _atomic_write_text(path: Path, text: str) -> None:
    temporary_name: str | None = None
    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        assert temporary_name is not None
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _prune_run_reports(report_root: Path) -> None:
    run_paths = sorted(
        (
            path
            for path in report_root.iterdir()
            if path.is_file() and _RUN_REPORT_NAME.fullmatch(path.name)
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    for path in run_paths[MAX_RUN_REPORTS:]:
        path.unlink(missing_ok=True)


def _copilot_executable() -> str:
    names = ("copilot.exe", "copilot.bat", "copilot.cmd") if os.name == "nt" else ("copilot",)
    for name in names:
        executable = shutil.which(name)
        if executable is not None:
            return executable
    raise CopilotPluginProbeError("GitHub Copilot CLI is not installed or not on PATH.")


def _parse_event_lines(output: str) -> tuple[dict[str, Any], ...]:
    events: list[dict[str, Any]] = []
    for line in output.splitlines():
        if not line.lstrip().startswith("{"):
            continue
        try:
            value: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("type"), str):
            events.append(value)
    if not events:
        raise CopilotPluginProbeError("Copilot CLI emitted no machine-readable session events.")
    return tuple(events)


def _validate_events(events: tuple[dict[str, Any], ...]) -> dict[str, str | int]:
    skills: set[str] = set()
    mcp_status: str | None = None
    mcp_transport: str | None = None
    response_seen = False
    tool_calls: int | None = None
    terminal_exit_code: int | None = None

    for event in events:
        event_type = event.get("type")
        data = event.get("data")
        if event_type == "session.skills_loaded" and isinstance(data, dict):
            loaded = data.get("skills")
            if isinstance(loaded, list):
                for skill in loaded:
                    if isinstance(skill, dict) and skill.get("source") == "plugin":
                        name = skill.get("name")
                        if isinstance(name, str):
                            skills.add(name)
        elif event_type == "session.mcp_servers_loaded" and isinstance(data, dict):
            servers = data.get("servers")
            if isinstance(servers, list):
                for server in servers:
                    if (
                        isinstance(server, dict)
                        and server.get("name") == PLUGIN_NAME
                        and server.get("source") == "plugin"
                        and server.get("pluginName") == PLUGIN_NAME
                    ):
                        status = server.get("status")
                        transport = server.get("transport")
                        if isinstance(status, str) and isinstance(transport, str):
                            mcp_status = status
                            mcp_transport = transport
        elif event_type == "assistant.message" and isinstance(data, dict):
            if data.get("content") == EXPECTED_REPLY:
                response_seen = True
                requests = data.get("toolRequests")
                if isinstance(requests, list):
                    tool_calls = len(requests)
        elif event_type == "result":
            exit_code = event.get("exitCode")
            if isinstance(exit_code, int):
                terminal_exit_code = exit_code

    missing_skills = set(EXPECTED_SKILLS) - skills
    if missing_skills:
        raise CopilotPluginProbeError(
            f"Copilot did not load required plugin skills: {', '.join(sorted(missing_skills))}."
        )
    if mcp_status != "connected" or mcp_transport != "http":
        raise CopilotPluginProbeError("Agent IQ plugin MCP server did not connect over HTTP.")
    if not response_seen or tool_calls != 0:
        raise CopilotPluginProbeError("Namespaced Agent IQ custom-agent no-tool turn failed.")
    if terminal_exit_code != 0:
        raise CopilotPluginProbeError(
            "Copilot session did not report a successful terminal result."
        )
    return {
        "mcp_status": mcp_status,
        "mcp_transport": mcp_transport,
        "tool_calls": tool_calls,
    }


def _source_sha256(source_root: Path) -> str:
    digest = hashlib.sha256()
    for relative in sorted(EXPECTED_FILES):
        digest.update(relative.encode("ascii"))
        digest.update(b"\0")
        digest.update((source_root / relative).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = run_probe(args.source_root, timeout_seconds=args.timeout_seconds)
    payload = asdict(result)
    if not args.no_report:
        payload["reportPath"] = str(write_report(result, args.report_root))
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"GitHub Copilot plugin: PASS ({len(result.skills)} skills, MCP {result.mcp_status})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
