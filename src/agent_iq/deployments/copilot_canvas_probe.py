"""Certify Agent IQ Canvas registration in the real GitHub Copilot CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from agent_iq.contracts.agui import AgUiProjection
from agent_iq.presentation import replay_agui_state

from .copilot_probe import _copilot_executable, _parse_event_lines

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE_ROOT = ROOT / ".github" / "extensions" / "agent-iq-canvas"
DEFAULT_USER_EXTENSIONS_ROOT = Path.home() / ".copilot" / "extensions"
DEFAULT_REPORT_ROOT = ROOT / "reports" / "evals" / "copilot-canvas"
CANVAS_ID = "agent-iq-command-center"
CANVAS_NAME = "Agent IQ Command Center"
EXTENSION_NAME = "agent-iq-canvas"
EXPECTED_ACTIONS = ("load_demo", "load_execution", "set_replay_cursor")
EXPECTED_REPLY = "AGENT_IQ_CANVAS_EXTENSION_OK"
RUN_REPORT_PREFIX = "copilot-canvas-probe-"
MAX_RUN_REPORTS = 20
_RUN_REPORT_NAME = re.compile(
    rf"^{re.escape(RUN_REPORT_PREFIX)}\d{{8}}T\d{{12}}Z-[0-9a-f]{{32}}\.json$"
)
_REPORT_WRITE_LOCK = Lock()
REQUIRED_FILES = frozenset(
    {
        "extension.mjs",
        "package.json",
        "runtime.mjs",
        "runtime.test.mjs",
        "web/demo/launch-command-center.agui.json",
        "web/index.html",
    }
)


class CopilotCanvasProbeError(RuntimeError):
    """Raised when Copilot does not register the governed Canvas contract."""


@dataclass(frozen=True, slots=True)
class CopilotCanvasProbe:
    status: str
    cli_version: str
    extension_version: str
    source_sha256: str
    load_scope: str
    canvas_id: str
    canvas_name: str
    actions: tuple[str, ...]
    host_registration: str
    render_acceptance: str
    response_marker: str
    tool_calls: int


@dataclass(frozen=True, slots=True)
class _EventSummary:
    actions: tuple[str, ...]
    tool_calls: int


def validate_source(source_root: Path) -> tuple[tuple[str, ...], str]:
    """Validate the deterministic extension source and replay artifact."""

    if not source_root.is_dir():
        raise CopilotCanvasProbeError(f"Canvas extension source is missing: {source_root}")
    files = _source_files(source_root)
    missing = REQUIRED_FILES - set(files)
    if missing:
        raise CopilotCanvasProbeError(
            f"Canvas extension is missing required files: {', '.join(sorted(missing))}."
        )
    css_assets = [
        path for path in files if path.startswith("web/assets/") and path.endswith(".css")
    ]
    js_assets = [path for path in files if path.startswith("web/assets/") and path.endswith(".js")]
    if len(files) != len(REQUIRED_FILES) + 2 or len(css_assets) != 1 or len(js_assets) != 1:
        raise CopilotCanvasProbeError("Canvas extension file set is not the deterministic build.")

    package_value: object = json.loads((source_root / "package.json").read_text(encoding="utf-8"))
    if not isinstance(package_value, dict):
        raise CopilotCanvasProbeError("Canvas extension package metadata must be an object.")
    if (
        package_value.get("name") != "agent-iq-canvas-extension"
        or package_value.get("private") is not True
        or package_value.get("type") != "module"
    ):
        raise CopilotCanvasProbeError("Canvas extension package metadata is invalid.")
    version = package_value.get("version")
    if not isinstance(version, str) or not version:
        raise CopilotCanvasProbeError("Canvas extension version is missing.")

    projection = AgUiProjection.model_validate_json(
        (source_root / "web" / "demo" / "launch-command-center.agui.json").read_text(
            encoding="utf-8"
        )
    )
    state = replay_agui_state(projection)
    if state.status != "completed" or len(state.selections) != 5:
        raise CopilotCanvasProbeError("Canvas replay did not reach the certified terminal state.")
    return files, version


def run_probe(
    source_root: Path = DEFAULT_SOURCE_ROOT,
    *,
    user_extensions_root: Path = DEFAULT_USER_EXTENSIONS_ROOT,
    timeout_seconds: int = 120,
) -> CopilotCanvasProbe:
    """Temporarily install the extension and certify its registered Canvas contract."""

    source_root = source_root.resolve()
    files, extension_version = validate_source(source_root)
    target = user_extensions_root.resolve() / EXTENSION_NAME
    created_target = False
    if target.exists():
        target_files, _ = validate_source(target)
        if target_files != files or _source_sha256(target, target_files) != _source_sha256(
            source_root, files
        ):
            raise CopilotCanvasProbeError(
                "A different user-scoped Agent IQ Canvas extension is already installed."
            )
        load_scope = "existing-user-identical"
    else:
        load_scope = "temporary-user"

    executable = _resolve_executable()
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
        raise CopilotCanvasProbeError("Copilot CLI version probe failed.")
    cli_version = version_process.stdout.splitlines()[0].strip()

    command = [
        executable,
        "--experimental",
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
    try:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.mkdir()
            created_target = True
            _copy_source(source_root, target, files)
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
    except subprocess.TimeoutExpired as error:
        raise CopilotCanvasProbeError("Copilot Canvas probe timed out.") from error
    finally:
        if created_target:
            _remove_tree(target)

    if completed.returncode != 0:
        raise CopilotCanvasProbeError(
            f"Copilot Canvas probe exited with code {completed.returncode}."
        )
    try:
        events = _parse_event_lines(completed.stdout)
    except RuntimeError as error:
        raise CopilotCanvasProbeError(str(error)) from error
    summary = _validate_events(events)
    return CopilotCanvasProbe(
        status="pass",
        cli_version=cli_version,
        extension_version=extension_version,
        source_sha256=_source_sha256(source_root, files),
        load_scope=load_scope,
        canvas_id=CANVAS_ID,
        canvas_name=CANVAS_NAME,
        actions=summary.actions,
        host_registration="registered",
        render_acceptance="not-exercised-by-prompt-mode-probe",
        response_marker=EXPECTED_REPLY,
        tool_calls=summary.tool_calls,
    )


def write_report(
    result: CopilotCanvasProbe,
    report_root: Path = DEFAULT_REPORT_ROOT,
) -> Path:
    """Persist a content-safe host-registration certificate and latest pointer."""

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
    raise RuntimeError("Could not allocate a unique Copilot Canvas probe report name.")


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


def _resolve_executable() -> str:
    try:
        return _copilot_executable()
    except RuntimeError as error:
        raise CopilotCanvasProbeError(str(error)) from error


def _validate_events(events: tuple[dict[str, Any], ...]) -> _EventSummary:
    registered: dict[str, Any] | None = None
    response_seen = False
    terminal_exit_code: int | None = None
    tool_calls = 0

    for event in events:
        event_type = event.get("type")
        data = event.get("data")
        if event_type == "session.canvas.registry_changed" and isinstance(data, dict):
            canvases = data.get("canvases")
            if isinstance(canvases, list):
                for canvas in canvases:
                    if isinstance(canvas, dict) and canvas.get("canvasId") == CANVAS_ID:
                        registered = canvas
        elif event_type == "assistant.message" and isinstance(data, dict):
            requests = data.get("toolRequests")
            if isinstance(requests, list):
                tool_calls += len(requests)
            if data.get("content") == EXPECTED_REPLY:
                response_seen = True
        elif event_type == "result":
            exit_code = event.get("exitCode")
            if isinstance(exit_code, int):
                terminal_exit_code = exit_code

    if registered is None:
        raise CopilotCanvasProbeError("Copilot did not register the Agent IQ Canvas.")
    if registered.get("displayName") != CANVAS_NAME:
        raise CopilotCanvasProbeError("Copilot registered unexpected Canvas metadata.")
    input_schema = registered.get("inputSchema")
    if not isinstance(input_schema, dict) or input_schema.get("additionalProperties") is not False:
        raise CopilotCanvasProbeError("Canvas input schema is not closed.")
    actions_value = registered.get("actions")
    if not isinstance(actions_value, list):
        raise CopilotCanvasProbeError("Copilot registered no Canvas actions.")
    actions = tuple(
        action.get("name")
        for action in actions_value
        if isinstance(action, dict) and isinstance(action.get("name"), str)
    )
    if actions != EXPECTED_ACTIONS:
        raise CopilotCanvasProbeError("Copilot registered an unexpected Canvas action set.")
    if any(
        not isinstance(action, dict)
        or not isinstance(action.get("inputSchema"), dict)
        or action["inputSchema"].get("additionalProperties") is not False
        for action in actions_value
    ):
        raise CopilotCanvasProbeError("Canvas action schemas must be closed.")
    if not response_seen or tool_calls != 0:
        raise CopilotCanvasProbeError("Copilot Canvas no-tool turn failed.")
    if terminal_exit_code != 0:
        raise CopilotCanvasProbeError("Copilot session did not report a successful result.")
    return _EventSummary(actions=actions, tool_calls=tool_calls)


def _source_files(source_root: Path) -> tuple[str, ...]:
    directory_entries = {
        source_root: {"extension.mjs", "package.json", "runtime.mjs", "runtime.test.mjs", "web"},
        source_root / "web": {"assets", "demo", "index.html"},
        source_root / "web" / "demo": {"launch-command-center.agui.json"},
    }
    for directory, expected in directory_entries.items():
        try:
            actual = {path.name for path in directory.iterdir()}
        except OSError as error:
            raise CopilotCanvasProbeError(
                f"Canvas extension directory is unavailable: {directory}"
            ) from error
        if actual != expected:
            raise CopilotCanvasProbeError("Canvas extension file set is not deterministic.")

    assets = source_root / "web" / "assets"
    try:
        asset_names = {path.name for path in assets.iterdir()}
    except OSError as error:
        raise CopilotCanvasProbeError("Canvas extension asset directory is unavailable.") from error
    css_assets = {name for name in asset_names if name.endswith(".css")}
    js_assets = {name for name in asset_names if name.endswith(".js")}
    if len(asset_names) != 2 or len(css_assets) != 1 or len(js_assets) != 1:
        raise CopilotCanvasProbeError("Canvas extension asset set is not deterministic.")

    files = tuple(
        sorted((*REQUIRED_FILES, f"web/assets/{css_assets.pop()}", f"web/assets/{js_assets.pop()}"))
    )
    for relative in files:
        path = source_root / relative
        if path.is_symlink():
            raise CopilotCanvasProbeError("Canvas extension cannot contain symbolic links.")
        try:
            with path.open("rb") as handle:
                handle.read(1)
        except OSError as error:
            raise CopilotCanvasProbeError(
                f"Canvas extension file is unavailable: {relative}"
            ) from error
    return files


def _source_sha256(source_root: Path, files: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for relative in files:
        digest.update(relative.encode("ascii"))
        digest.update(b"\0")
        digest.update((source_root / relative).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _copy_source(source_root: Path, target: Path, files: tuple[str, ...]) -> None:
    for relative in files:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / relative, destination)


def _make_tree_writable(root: Path) -> None:
    for path in root.rglob("*"):
        path.chmod(path.stat().st_mode | stat.S_IWUSR)
    root.chmod(root.stat().st_mode | stat.S_IWUSR)


def _remove_tree(root: Path) -> None:
    _make_tree_writable(root)
    shutil.rmtree(root)
    if root.exists():
        raise CopilotCanvasProbeError("Temporary Canvas extension cleanup did not complete.")


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
        print(f"GitHub Copilot Canvas: PASS ({result.canvas_name}, {len(result.actions)} actions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
