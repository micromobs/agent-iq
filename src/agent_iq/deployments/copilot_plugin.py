"""Validate and deterministically build the Agent IQ GitHub Copilot plugin."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE_ROOT = ROOT / "packages" / "copilot"
DEFAULT_OUTPUT_ROOT = ROOT / "dist" / "copilot"
DEFAULT_REPORT_ROOT = ROOT / "reports" / "deployments" / "copilot" / "plugin"
PLUGIN_NAME = "agent-iq"
PACKAGE_NAME = "agent-iq-plugin"
PLUGIN_VERSION = "1.0.0"
PACKAGE_ARCHIVE_NAME = f"agent-iq-copilot-plugin-{PLUGIN_VERSION}.zip"
EXPECTED_AGENT = "agents/agent-iq.agent.md"
EXPECTED_SKILLS = ("orchestrate-agents", "replay-execution")
PRODUCT_TOOLS = (
    "search_agents",
    "orchestrate",
    "get_execution",
    "cancel_execution",
    "submit_feedback",
    "capabilities",
)
AGENT_TOOLS = (*(f"agent-iq/{name}" for name in PRODUCT_TOOLS), "workiq/ask")
SKILL_ALLOWED_TOOLS = {
    "orchestrate-agents": (
        "agent-iq/search_agents",
        "agent-iq/orchestrate",
        "agent-iq/capabilities",
        "workiq/ask",
    ),
    "replay-execution": (
        "agent-iq/get_execution",
        "agent-iq/capabilities",
    ),
}
EXPECTED_FILES = (
    ".mcp.json",
    "README.md",
    EXPECTED_AGENT,
    "plugin.json",
    *(f"skills/{name}/SKILL.md" for name in EXPECTED_SKILLS),
)
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
FORBIDDEN_PUBLIC_TERMS = (
    "chain-of-thought",
    "pine" + "apple",
    "raw event fallback",
)
RUN_REPORT_PREFIX = f"{PACKAGE_NAME}-"
MAX_RUN_REPORTS = 20
_RUN_REPORT_NAME = re.compile(
    rf"^{re.escape(RUN_REPORT_PREFIX)}\d{{8}}T\d{{12}}Z-[0-9a-f]{{32}}\.json$"
)
_REPORT_WRITE_LOCK = Lock()


class CopilotPluginError(ValueError):
    """Raised when the Copilot plugin source violates its product contract."""


@dataclass(frozen=True, slots=True)
class CopilotPluginBuild:
    package_path: str
    package_sha256: str
    plugin_name: str
    plugin_version: str
    agent: str
    skills: tuple[str, ...]
    files: tuple[str, ...]


def validate_source(source_root: Path = DEFAULT_SOURCE_ROOT) -> dict[str, Any]:
    """Validate the plugin manifest, MCP allowlist, custom agent, and skills."""

    manifest = _read_json(source_root / "plugin.json", "plugin manifest")
    _require(manifest.get("name") == PLUGIN_NAME, "Plugin name must remain agent-iq.")
    _require(
        manifest.get("version") == PLUGIN_VERSION
        and SEMVER_PATTERN.fullmatch(str(manifest.get("version"))) is not None,
        "Plugin version must match the stable package version.",
    )
    description = str(manifest.get("description", "")).casefold()
    _require("canary" in description, "Plugin description must identify the canary.")
    _require(
        manifest.get("author") == {"name": "Agent IQ"},
        "Plugin author metadata must use Agent IQ branding.",
    )
    _require(manifest.get("agents") == "agents/", "Plugin must advertise agents/.")
    _require(manifest.get("skills") == "skills/", "Plugin must advertise skills/.")
    _require(
        manifest.get("mcpServers") == ".mcp.json",
        "Plugin must advertise the root .mcp.json.",
    )
    _require("license" not in manifest, "Plugin cannot claim a license before release clearance.")

    mcp = _read_json(source_root / ".mcp.json", "MCP configuration")
    servers = mcp.get("mcpServers")
    if not isinstance(servers, dict) or tuple(servers) != (PLUGIN_NAME,):
        raise CopilotPluginError("Plugin must configure exactly one agent-iq MCP server.")
    server = servers[PLUGIN_NAME]
    if not isinstance(server, dict):
        raise CopilotPluginError("Agent IQ MCP configuration must be an object.")
    _require(server.get("type") == "http", "Agent IQ MCP transport must be HTTP.")
    _validate_mcp_endpoint(str(server.get("url", "")))
    _require(
        tuple(server.get("tools", ())) == PRODUCT_TOOLS,
        "Agent IQ MCP tools must match the six bounded product operations.",
    )
    _require(
        set(server) == {"type", "url", "tools"},
        "Anonymous canary MCP configuration cannot add headers or credentials.",
    )

    agent_path = source_root / EXPECTED_AGENT
    _require(agent_path.is_file(), f"Plugin is missing {EXPECTED_AGENT}.")
    agent_text = agent_path.read_text(encoding="utf-8")
    _validate_ascii(agent_text, EXPECTED_AGENT)
    _require(
        _frontmatter_scalar(agent_text, "name") == PLUGIN_NAME,
        "Custom agent name must remain agent-iq.",
    )
    _require(
        _frontmatter_scalar(agent_text, "disable-model-invocation") == "false",
        "Custom agent must remain available for contextual invocation.",
    )
    _require(
        _frontmatter_scalar(agent_text, "user-invocable") == "true",
        "Custom agent must remain user-invocable.",
    )
    _require(
        _frontmatter_list(agent_text, "tools") == AGENT_TOOLS,
        "Custom agent tools must use the bounded Agent IQ and read-only Work IQ allowlist.",
    )
    _validate_versioned_frontmatter(agent_text, "Custom agent")
    _require_markers(
        agent_text,
        (
            "one host-neutral agent iq command",
            "contextreference",
            "call `agent-iq/orchestrate` once",
            "public selection summaries",
            "never expose hidden reasoning",
            "anonymous non-production canary",
            "never claim that a preference",
        ),
        "Custom agent",
    )

    skills_root = source_root / "skills"
    actual_skills = tuple(sorted(path.name for path in skills_root.iterdir() if path.is_dir()))
    _require(
        actual_skills == tuple(sorted(EXPECTED_SKILLS)),
        "Plugin must contain exactly the orchestration and replay skills.",
    )
    for skill_name in EXPECTED_SKILLS:
        relative = f"skills/{skill_name}/SKILL.md"
        path = source_root / relative
        _require(path.is_file(), f"Plugin is missing {relative}.")
        text = path.read_text(encoding="utf-8")
        _validate_ascii(text, relative)
        _require(
            _frontmatter_scalar(text, "name") == skill_name,
            f"Skill frontmatter name must match {skill_name}.",
        )
        _require(
            _frontmatter_list(text, "allowed-tools") == SKILL_ALLOWED_TOOLS[skill_name],
            f"Skill {skill_name} has an unsafe or incomplete tool allowlist.",
        )
        _validate_versioned_frontmatter(text, f"Skill {skill_name}")
        _require("never expose hidden reasoning" in text.casefold(), f"{skill_name} must redact.")
        _require(
            "cancel_execution" not in _frontmatter(text), f"{skill_name} cannot preapprove cancel."
        )

    _require_markers(
        (source_root / "skills" / "orchestrate-agents" / "SKILL.md").read_text(encoding="utf-8"),
        (
            "minimum eligible squad",
            "contextreference",
            "workiq/ask",
            "call `agent-iq/orchestrate` once",
            "do not call `search_agents` first",
            "preserve global citation numbers",
            "non-production canary",
        ),
        "Orchestration skill",
    )
    _require_markers(
        (source_root / "skills" / "replay-execution" / "SKILL.md").read_text(encoding="utf-8"),
        (
            "agent-iq/get_execution",
            "immutable event sequence",
            "do not display intent or catalog query text",
            "do not display tool arguments",
            "safe ag-ui presentation",
            "do not fabricate a timeline",
        ),
        "Replay skill",
    )

    readme = source_root / "README.md"
    _require(readme.is_file(), "Plugin requires an operator README.")
    _validate_ascii(readme.read_text(encoding="utf-8"), "README.md")

    actual_files = tuple(
        sorted(
            path.relative_to(source_root).as_posix()
            for path in source_root.rglob("*")
            if path.is_file()
        )
    )
    _require(actual_files == tuple(sorted(EXPECTED_FILES)), "Plugin contains unexpected files.")
    for relative in EXPECTED_FILES:
        text = (source_root / relative).read_text(encoding="utf-8")
        normalized = text.casefold()
        for forbidden in FORBIDDEN_PUBLIC_TERMS:
            _require(
                forbidden not in normalized,
                f"Plugin file {relative} contains forbidden term {forbidden!r}.",
            )
    return manifest


def build_plugin(
    source_root: Path = DEFAULT_SOURCE_ROOT,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    *,
    mcp_url: str | None = None,
) -> CopilotPluginBuild:
    """Build matching directory and ZIP artifacts accepted by Copilot plugin loading."""

    manifest = validate_source(source_root)
    if mcp_url is not None:
        _validate_mcp_endpoint(mcp_url)
    package_dir = output_root / PACKAGE_NAME
    package_path = output_root / PACKAGE_ARCHIVE_NAME
    legacy_package_path = output_root / f"{PACKAGE_NAME}.zip"
    output_root.mkdir(parents=True, exist_ok=True)
    if package_dir.exists():
        shutil.rmtree(package_dir, onerror=_clear_readonly_and_retry)
    if legacy_package_path.exists():
        legacy_package_path.unlink()
    package_dir.mkdir(parents=True)

    files: list[tuple[Path, str]] = []
    for relative in EXPECTED_FILES:
        source = source_root / relative
        destination = package_dir / Path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if relative == ".mcp.json" and mcp_url is not None:
            mcp = _read_json(source, "MCP configuration")
            mcp["mcpServers"][PLUGIN_NAME]["url"] = mcp_url
            destination.write_text(
                json.dumps(mcp, indent=2) + "\n",
                encoding="utf-8",
                newline="",
            )
        else:
            shutil.copyfile(source, destination)
        files.append((destination, relative))

    validate_source(package_dir)

    with zipfile.ZipFile(
        package_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for source, archive_name in sorted(files, key=lambda item: item[1]):
            info = zipfile.ZipInfo(archive_name, ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())

    return CopilotPluginBuild(
        package_path=str(package_path),
        package_sha256=hashlib.sha256(package_path.read_bytes()).hexdigest(),
        plugin_name=PLUGIN_NAME,
        plugin_version=str(manifest["version"]),
        agent=EXPECTED_AGENT,
        skills=EXPECTED_SKILLS,
        files=tuple(archive_name for _, archive_name in sorted(files, key=lambda item: item[1])),
    )


def verify_built_plugin(
    source_root: Path = DEFAULT_SOURCE_ROOT,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    *,
    mcp_url: str | None = None,
) -> CopilotPluginBuild:
    """Require the generated plugin archive to match the deterministic source build."""

    package_path = output_root / PACKAGE_ARCHIVE_NAME
    if not package_path.is_file():
        raise CopilotPluginError(
            f"Copilot plugin artifact is missing at {package_path}; rebuild it before acceptance."
        )
    with tempfile.TemporaryDirectory(prefix="agent-iq-copilot-check-") as temporary:
        expected = build_plugin(source_root, Path(temporary), mcp_url=mcp_url)
        expected_hash = expected.package_sha256
    actual_hash = hashlib.sha256(package_path.read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        raise CopilotPluginError(
            "Copilot plugin artifact is stale; rebuild it from the current plugin source."
        )
    return CopilotPluginBuild(
        package_path=str(package_path),
        package_sha256=actual_hash,
        plugin_name=expected.plugin_name,
        plugin_version=expected.plugin_version,
        agent=expected.agent,
        skills=expected.skills,
        files=expected.files,
    )


def _clear_readonly_and_retry(
    operation: Callable[[str], object],
    path: str,
    _: object,
) -> None:
    os.chmod(path, stat.S_IWRITE)
    operation(path)


def write_report(
    result: CopilotPluginBuild,
    report_root: Path = DEFAULT_REPORT_ROOT,
) -> Path:
    """Persist a content-safe build record and latest pointer."""

    report_root.mkdir(parents=True, exist_ok=True)
    path = _new_run_path(report_root)
    now = datetime.now(UTC)
    payload = {
        "schemaVersion": "1.0",
        "generatedAt": now.isoformat(),
        "status": "pass",
        **asdict(result),
    }
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    _atomic_write_text(path, text)
    with _REPORT_WRITE_LOCK:
        _atomic_write_text(report_root / "latest.json", text)
        _prune_run_reports(report_root)
    return path


def _new_run_path(report_root: Path) -> Path:
    for _ in range(100):
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        candidate = report_root / f"{RUN_REPORT_PREFIX}{stamp}-{uuid.uuid4().hex}.json"
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not allocate a unique Copilot plugin report name.")


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


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise CopilotPluginError(f"Plugin is missing {path.name}.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CopilotPluginError(f"{label.capitalize()} is not valid JSON.") from exc
    if not isinstance(value, dict):
        raise CopilotPluginError(f"{label.capitalize()} must be a JSON object.")
    return value


def _validate_mcp_endpoint(endpoint: str) -> None:
    parsed = urlsplit(endpoint)
    _require(
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.path == "/mcp"
        and not parsed.query
        and not parsed.fragment
        and parsed.username is None
        and parsed.password is None,
        "Agent IQ MCP URL must be an absolute HTTPS /mcp endpoint.",
    )


def _frontmatter(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise CopilotPluginError("Markdown component requires YAML frontmatter.")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise CopilotPluginError("Markdown component has unterminated frontmatter.") from exc
    return "\n".join(lines[1:end])


def _frontmatter_scalar(text: str, key: str) -> str | None:
    prefix = f"{key}:"
    for line in _frontmatter(text).splitlines():
        if line.startswith(prefix):
            return line.partition(":")[2].strip().strip("\"'")
    return None


def _frontmatter_list(text: str, key: str) -> tuple[str, ...]:
    lines = _frontmatter(text).splitlines()
    prefix = f"{key}:"
    for index, line in enumerate(lines):
        if line != prefix:
            continue
        values: list[str] = []
        for item in lines[index + 1 :]:
            if not item.startswith("  - "):
                break
            values.append(item[4:].strip().strip("\"'"))
        return tuple(values)
    return ()


def _validate_versioned_frontmatter(text: str, label: str) -> None:
    frontmatter = _frontmatter(text)
    _require("metadata:" in frontmatter, f"{label} requires metadata.")
    _require(
        f'version: "{PLUGIN_VERSION}"' in frontmatter,
        f"{label} version must match the plugin.",
    )
    _require("status: canary" in frontmatter, f"{label} must identify canary status.")


def _validate_ascii(text: str, relative: str) -> None:
    _require(text.isascii(), f"Plugin file {relative} must remain ASCII.")


def _require_markers(text: str, markers: tuple[str, ...], label: str) -> None:
    normalized = " ".join(text.casefold().split())
    for marker in markers:
        _require(marker in normalized, f"{label} is missing required language: {marker}.")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CopilotPluginError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--mcp-url")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()

    if arguments.check:
        built = verify_built_plugin(
            arguments.source_root,
            arguments.out,
            mcp_url=arguments.mcp_url,
        )
        result: dict[str, Any] = {
            "status": "pass",
            **asdict(built),
        }
    else:
        built = build_plugin(
            arguments.source_root,
            arguments.out,
            mcp_url=arguments.mcp_url,
        )
        report_path = write_report(built, arguments.report_root)
        result = {**asdict(built), "status": "pass", "reportPath": str(report_path)}
    if arguments.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Copilot plugin: PASS ({len(EXPECTED_SKILLS)} skills, {len(EXPECTED_FILES)} files)")
        if not arguments.check:
            print(f"Package: {result['package_path']}")
            print(f"SHA-256: {result['package_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
