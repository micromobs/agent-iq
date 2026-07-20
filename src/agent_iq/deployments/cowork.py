"""Validate and deterministically build the Agent IQ Cowork canary package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import tempfile
import uuid
import zipfile
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE_ROOT = ROOT / "packages" / "cowork"
DEFAULT_OUTPUT_ROOT = ROOT / "dist" / "cowork"
DEFAULT_REPORT_ROOT = ROOT / "reports" / "deployments" / "cowork" / "agent-iq-canary"
PACKAGE_NAME = "agent-iq-canary"
SUPPORTED_AUTHORIZATION_TYPES = frozenset(
    {
        "None",
        "OAuthPluginVault",
        "ApiKeyPluginVault",
        "DynamicClientRegistration",
    }
)
REFERENCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MANIFEST_SCHEMA = (
    "https://developer.microsoft.com/json-schemas/teams/v1.28/MicrosoftTeams.schema.json"
)
CONSUMER_SKILLS = (
    "single-agent-analysis",
    "formulation-brief",
    "brand-comparison",
    "supply-readiness",
    "regulatory-claim-review",
    "launch-readiness",
)
VACATION_SKILLS = ("vacation-balance",)
EMPLOYEE_SKILLS = (
    "employee-command-center",
    "ask-hr",
    "ask-it",
    "ask-payroll",
    "ask-finance",
    "ask-expense",
    "corporate-services",
    "management-governance",
)
EXPECTED_SKILLS = CONSUMER_SKILLS + VACATION_SKILLS + EMPLOYEE_SKILLS
EXPECTED_AGENT_IDS = {
    "single-agent-analysis": ("consumer-formulation-rd",),
    "formulation-brief": ("consumer-formulation-rd",),
    "brand-comparison": ("consumer-brand-to-brand-compare",),
    "supply-readiness": (
        "consumer-demand-forecasting",
        "consumer-manufacturing-operations",
    ),
    "regulatory-claim-review": (
        "consumer-formulation-rd",
        "consumer-regulatory-affairs",
    ),
    "launch-readiness": (
        "consumer-formulation-rd",
        "consumer-brand-to-brand-compare",
        "consumer-demand-forecasting",
        "consumer-manufacturing-operations",
        "consumer-regulatory-affairs",
    ),
    "vacation-balance": ("employee-vacation-leave",),
    "employee-command-center": (
        "employee-personal-dashboard",
        "employee-task-hub",
        "employee-news",
        "employee-org-directory",
    ),
    "ask-hr": (
        "employee-hr-concierge",
        "employee-benefits-advisor",
        "employee-onboarding",
        "employee-learning",
        "employee-recognition",
        "employee-listening",
    ),
    "ask-it": (
        "employee-it-helpdesk",
        "employee-identity-security",
        "employee-campus-services",
        "employee-site-provisioning",
    ),
    "ask-payroll": (
        "employee-payroll",
        "employee-hr-concierge",
        "employee-benefits-advisor",
    ),
    "ask-finance": (
        "employee-finance",
        "employee-procurement",
        "employee-legal-compliance",
        "employee-sales-customer360",
    ),
    "ask-expense": (
        "employee-expense",
        "employee-travel",
        "employee-legal-compliance",
    ),
    "corporate-services": (
        "employee-news",
        "employee-task-hub",
        "employee-personal-dashboard",
        "employee-org-directory",
        "employee-onboarding",
        "employee-visual-qa",
        "employee-campus-services",
        "employee-learning",
        "employee-recognition",
        "employee-listening",
    ),
    "management-governance": (
        "employee-site-provisioning",
        "employee-policy-governance",
        "employee-access-governance",
        "employee-site-health",
        "employee-records-lifecycle",
        "employee-legal-compliance",
        "employee-identity-security",
    ),
}
COMMON_SKILL_MARKERS = (
    ("northbound mcp",),
    ("southbound native a2a",),
    ("work iq",),
    ("contextreference",),
    ("search_agents",),
    ("orchestrate",),
    ("sources",),
    (
        "honest",
        "do not invent",
        "do not fabricate",
        "never invent",
        "do not claim",
        "insufficient grounded evidence",
        "evidence unavailable",
    ),
)
EMPLOYEE_SKILL_MARKERS = (
    ("list_employee_scenarios",),
    ("resolve_employee_intent",),
    ("open_employee_experience",),
    ("prepare_employee_action",),
    ("selectedagents",),
    ("phases",),
    ("sections",),
    ("sources",),
    ("actions",),
    ("actionclass", "action class"),
    ("executed=false", "`executed` is always `false`"),
    ("contoso employee services",),
)
VACATION_SKILL_MARKERS = (
    ("check_vacation_balance",),
    ("native a2a",),
    ("work iq",),
    ("selectedagents",),
    ("sources",),
    ("presentationstyle",),
    ("compact_ring",),
    ("apple-watch-style",),
    ("one supporting line",),
    ("do not show a dashboard",),
    ("is this for you or your team",),
    ("united states",),
    ("europe",),
    ("apac",),
    ("all regions",),
    ("manager",),
    ("never replace", "do not present a success"),
)
ASK_IT_MARKERS = (
    *EMPLOYEE_SKILL_MARKERS,
    ("orchestrate",),
    ("exactly once",),
    ("vpn",),
)
UNSUPPORTED_MANIFEST_KEYS = {
    "agents",
    "bots",
    "copilotAgents",
    "declarativeAgents",
    "subAgents",
}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ICON_SPECS = {
    "color.png": (192, 192),
    "outline.png": (32, 32),
    "color32.png": (32, 32),
}
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
APP_VERSION_PATTERN = re.compile(r"^[1-9]\d*\.\d+\.\d+$")
RUN_REPORT_PREFIX = f"{PACKAGE_NAME}-"
MAX_RUN_REPORTS = 20
FORBIDDEN_VISIBLE_LABELS = ("synthetic", "demo", "canary", "non-production")
LEGACY_TOOL_IDENTIFIERS = ("run_single_agent_demo", "run_vacation_balance_demo")
_RUN_REPORT_NAME = re.compile(
    rf"^{re.escape(RUN_REPORT_PREFIX)}\d{{8}}T\d{{12}}Z-[0-9a-f]{{32}}\.json$"
)
_REPORT_WRITE_LOCK = Lock()


class CoworkPackageError(ValueError):
    """Raised when Cowork package source violates the canary contract."""


@dataclass(frozen=True, slots=True)
class BuildResult:
    package_path: str
    package_sha256: str
    manifest_id: str
    connector_url: str
    skills: tuple[str, ...]
    files: tuple[str, ...]


def validate_source(source_root: Path = DEFAULT_SOURCE_ROOT) -> dict[str, Any]:
    """Validate manifest, workflow skills, and image assets before packaging."""

    manifest_path = source_root / "manifest" / "manifest.json"
    if not manifest_path.is_file():
        raise CoworkPackageError("Cowork source is missing manifest/manifest.json.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CoworkPackageError("Cowork manifest is not valid JSON.") from exc
    if not isinstance(manifest, dict):
        raise CoworkPackageError("Cowork manifest must be a JSON object.")

    _require(manifest.get("$schema") == MANIFEST_SCHEMA, "Manifest must use Teams schema v1.28.")
    _require(
        manifest.get("manifestVersion") == "devPreview",
        "Manifest must use the shipping Cowork devPreview shape.",
    )
    _require(
        isinstance(manifest.get("version"), str)
        and APP_VERSION_PATTERN.fullmatch(manifest["version"]) is not None,
        "Manifest app version must be stable x.y.z with a nonzero major version.",
    )
    _require(_is_nonempty(manifest.get("id")), "Manifest requires a stable application ID.")
    _require(
        manifest.get("name") == {"short": "Agent IQ", "full": "Agent IQ"},
        "Manifest must use the Agent IQ product name.",
    )
    visible_manifest_text = " ".join(
        (
            _text(manifest.get("name")),
            _text(manifest.get("description")),
            _text(manifest.get("developer")),
        )
    ).casefold()
    _require(
        not any(label in visible_manifest_text for label in FORBIDDEN_VISIBLE_LABELS),
        "Visible manifest copy must use product language without environment labels.",
    )
    unsupported = UNSUPPORTED_MANIFEST_KEYS & manifest.keys()
    if unsupported:
        raise CoworkPackageError(
            f"Custom Cowork package cannot declare agents: {sorted(unsupported)[0]}."
        )

    expected_folders = [f"./skills/{name}" for name in EXPECTED_SKILLS]
    advertised_folders = [
        item.get("folder") for item in _object_list(manifest.get("agentSkills"), "agentSkills")
    ]
    _require(
        advertised_folders == expected_folders,
        f"Manifest must advertise the {len(EXPECTED_SKILLS)} workflow skills in canonical order.",
    )
    _require(
        manifest.get("icons")
        == {
            "color": "color.png",
            "outline": "outline.png",
            "color32x32": "color32.png",
        },
        "Manifest must reference the complete Microsoft 365 icon set.",
    )

    connectors = _object_list(manifest.get("agentConnectors"), "agentConnectors")
    _require(len(connectors) == 1, "Manifest must contain exactly one remote MCP connector.")
    connector = connectors[0]
    _require(
        connector.get("id") == "agent-iq-orchestrator-mcp",
        "Connector ID must remain agent-iq-orchestrator-mcp.",
    )
    try:
        remote = connector["toolSource"]["remoteMcpServer"]
    except (KeyError, TypeError) as exc:
        raise CoworkPackageError("Connector must use toolSource.remoteMcpServer.") from exc
    if not isinstance(remote, dict):
        raise CoworkPackageError("remoteMcpServer must be an object.")
    _validate_connector_url(remote.get("mcpServerUrl"))
    _require(
        remote.get("authorization") == {"type": "None"},
        "Connector authorization must be explicitly set to None.",
    )
    visible_connector_text = " ".join(
        (
            _text(connector.get("displayName")),
            _text(connector.get("description")),
        )
    ).casefold()
    _require(
        not any(
            label in visible_connector_text
            for label in ("synthetic", "demo", "canary", "non-production")
        ),
        "Visible connector copy must use product language without environment labels.",
    )

    skills_root = source_root / "skills"
    actual_skills = tuple(
        sorted(
            path.name
            for path in skills_root.iterdir()
            if path.is_dir() and (path / "SKILL.md").is_file()
        )
    )
    _require(
        actual_skills == tuple(sorted(EXPECTED_SKILLS)),
        f"Cowork source must contain exactly the {len(EXPECTED_SKILLS)} canonical skill folders.",
    )
    for skill_name in EXPECTED_SKILLS:
        path = skills_root / skill_name / "SKILL.md"
        _require(path.is_file(), f"Skill {skill_name} is missing SKILL.md.")
        text = path.read_text(encoding="utf-8")
        _require(
            _frontmatter_name(text) == skill_name,
            f"Skill frontmatter name must match folder {skill_name}.",
        )
        normalized = " ".join(text.casefold().split())
        markers: tuple[tuple[str, ...], ...]
        if skill_name in CONSUMER_SKILLS:
            markers = COMMON_SKILL_MARKERS
        elif skill_name in VACATION_SKILLS:
            markers = VACATION_SKILL_MARKERS
        elif skill_name == "ask-it":
            markers = ASK_IT_MARKERS
        else:
            markers = EMPLOYEE_SKILL_MARKERS
        for alternatives in markers:
            _require(
                any(marker in normalized for marker in alternatives),
                f"Skill {skill_name} is missing required workflow language.",
            )
        for agent_id in EXPECTED_AGENT_IDS[skill_name]:
            _require(
                agent_id in text,
                f"Skill {skill_name} must identify expected hero {agent_id}.",
            )
        visible_skill_text = normalized
        for identifier in LEGACY_TOOL_IDENTIFIERS:
            visible_skill_text = visible_skill_text.replace(identifier, "")
        _require(
            not any(label in visible_skill_text for label in FORBIDDEN_VISIBLE_LABELS),
            f"Skill {skill_name} contains a forbidden user-visible environment label.",
        )
        _require(
            len(text) >= 2_000,
            f"Skill {skill_name} is too shallow for a workflow-grade Cowork skill.",
        )

    for icon_name, expected_size in ICON_SPECS.items():
        icon_path = source_root / "assets" / icon_name
        _require(icon_path.is_file(), f"Cowork source is missing assets/{icon_name}.")
        width, height, bit_depth, color_type = _png_header(icon_path)
        _require(
            (width, height) == expected_size,
            f"Cowork asset {icon_name} must be {expected_size[0]}x{expected_size[1]}.",
        )
        _require(bit_depth == 8, f"Cowork asset {icon_name} must use 8-bit channels.")
        if icon_name != "color.png":
            _require(
                color_type in {4, 6},
                f"Cowork asset {icon_name} must contain an alpha channel.",
            )
    return manifest


def build_package(
    source_root: Path = DEFAULT_SOURCE_ROOT,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    *,
    connector_url: str | None = None,
    manifest_id: str | None = None,
    app_version: str | None = None,
    authorization_type: str | None = None,
    authorization_reference_id: str | None = None,
) -> BuildResult:
    """Build one stable, content-identical zip from validated package source."""

    manifest = _configured_manifest(
        validate_source(source_root),
        connector_url=connector_url,
        manifest_id=manifest_id,
        app_version=app_version,
        authorization_type=authorization_type,
        authorization_reference_id=authorization_reference_id,
    )
    package_dir = output_root / PACKAGE_NAME
    package_path = output_root / f"{PACKAGE_NAME}.zip"
    output_root.mkdir(parents=True, exist_ok=True)
    _prepare_package_dir(package_dir)

    manifest_overridden = any(
        value is not None
        for value in (
            connector_url,
            manifest_id,
            app_version,
            authorization_type,
            authorization_reference_id,
        )
    )
    manifest_bytes = (
        (json.dumps(manifest, indent=2, ensure_ascii=True) + "\n").encode("utf-8")
        if manifest_overridden
        else (source_root / "manifest" / "manifest.json").read_bytes()
    )
    files: list[tuple[bytes, str]] = [
        (manifest_bytes, "manifest.json"),
        ((source_root / "assets" / "color.png").read_bytes(), "color.png"),
        ((source_root / "assets" / "color32.png").read_bytes(), "color32.png"),
        ((source_root / "assets" / "outline.png").read_bytes(), "outline.png"),
    ]
    files.extend(
        (
            (source_root / "skills" / skill_name / "SKILL.md").read_bytes(),
            f"skills/{skill_name}/SKILL.md",
        )
        for skill_name in EXPECTED_SKILLS
    )
    for content, archive_name in files:
        destination = package_dir / Path(archive_name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)

    with zipfile.ZipFile(
        package_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for content, archive_name in sorted(files, key=lambda item: item[1]):
            info = zipfile.ZipInfo(archive_name, ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)

    connector_url = manifest["agentConnectors"][0]["toolSource"]["remoteMcpServer"]["mcpServerUrl"]
    return BuildResult(
        package_path=str(package_path),
        package_sha256=hashlib.sha256(package_path.read_bytes()).hexdigest(),
        manifest_id=str(manifest["id"]),
        connector_url=str(connector_url),
        skills=EXPECTED_SKILLS,
        files=tuple(archive_name for _, archive_name in sorted(files, key=lambda item: item[1])),
    )


def _configured_manifest(
    manifest: dict[str, Any],
    *,
    connector_url: str | None,
    manifest_id: str | None,
    app_version: str | None,
    authorization_type: str | None,
    authorization_reference_id: str | None,
) -> dict[str, Any]:
    configured = json.loads(json.dumps(manifest))
    if not isinstance(configured, dict):
        raise CoworkPackageError("Cowork manifest must be a JSON object.")

    if manifest_id is not None:
        try:
            parsed_id = uuid.UUID(manifest_id)
        except ValueError as exc:
            raise CoworkPackageError("Manifest application ID must be a canonical UUID.") from exc
        _require(
            parsed_id.int != 0 and manifest_id == str(parsed_id),
            "Manifest application ID must be a canonical nonzero lowercase UUID.",
        )
        configured["id"] = manifest_id

    if app_version is not None:
        _require(
            APP_VERSION_PATTERN.fullmatch(app_version) is not None,
            "Manifest app version must be stable x.y.z with a nonzero major version.",
        )
        configured["version"] = app_version

    connector = _object_list(configured.get("agentConnectors"), "agentConnectors")[0]
    try:
        remote = connector["toolSource"]["remoteMcpServer"]
    except (KeyError, TypeError) as exc:
        raise CoworkPackageError("Connector must use toolSource.remoteMcpServer.") from exc
    if not isinstance(remote, dict):
        raise CoworkPackageError("remoteMcpServer must be an object.")
    if connector_url is not None:
        remote["mcpServerUrl"] = _validate_connector_url(connector_url)

    if authorization_type is None:
        _require(
            authorization_reference_id is None,
            "Authorization reference ID requires an explicit authorization type.",
        )
    else:
        _require(
            authorization_type in SUPPORTED_AUTHORIZATION_TYPES,
            "Unsupported Cowork connector authorization type.",
        )
        if authorization_type == "None":
            _require(
                authorization_reference_id is None,
                "Authorization type None cannot include a reference ID.",
            )
            remote["authorization"] = {"type": "None"}
        else:
            _require(
                authorization_reference_id is not None
                and REFERENCE_ID_PATTERN.fullmatch(authorization_reference_id) is not None,
                "Vault and registration authorization requires a safe reference ID.",
            )
            remote["authorization"] = {
                "type": authorization_type,
                "referenceId": authorization_reference_id,
            }
    return configured


def verify_built_package(
    source_root: Path = DEFAULT_SOURCE_ROOT,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> BuildResult:
    """Require the local canary artifact to match the deterministic source build."""

    package_path = output_root / f"{PACKAGE_NAME}.zip"
    return _verify_package(package_path, source_root, label="Cowork canary package artifact")


def _verify_package(
    package_path: Path,
    source_root: Path,
    *,
    label: str,
) -> BuildResult:
    if not package_path.is_file():
        raise CoworkPackageError(
            f"{label} is missing at {package_path}; rebuild it before acceptance."
        )
    with tempfile.TemporaryDirectory(prefix="agent-iq-cowork-check-") as temporary:
        expected = build_package(source_root, Path(temporary))
        expected_hash = expected.package_sha256
    actual_hash = hashlib.sha256(package_path.read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        raise CoworkPackageError(f"{label} is stale; rebuild it from the current package source.")
    return replace(
        expected,
        package_path=str(package_path),
        package_sha256=actual_hash,
    )


def write_report(
    result: BuildResult,
    report_root: Path = DEFAULT_REPORT_ROOT,
) -> Path:
    """Persist a content-safe build record and a latest pointer."""

    _validate_connector_url(result.connector_url)
    report_root.mkdir(parents=True, exist_ok=True)
    path = _new_run_path(report_root)
    payload = {
        "schemaVersion": "1.0",
        "generatedAt": datetime.now(UTC).isoformat(),
        "status": "pass",
        **asdict(result),
    }
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    _atomic_write_text(path, text)
    with _REPORT_WRITE_LOCK:
        _atomic_write_text(report_root / "latest.json", text)
        _prune_run_reports(report_root)
    return path


def _frontmatter_name(text: str) -> str | None:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None
    for line in lines[1:]:
        if line == "---":
            break
        if line.startswith("name:"):
            return line.partition(":")[2].strip()
    return None


def _png_header(path: Path) -> tuple[int, int, int, int]:
    data = path.read_bytes()
    if not data.startswith(PNG_SIGNATURE) or data[12:16] != b"IHDR":
        raise CoworkPackageError(f"Cowork asset {path.name} is not a PNG.")
    if len(data) < 29:
        raise CoworkPackageError(f"Cowork asset {path.name} has a truncated PNG header.")
    width, height, bit_depth, color_type = struct.unpack(">IIBB", data[16:26])
    return width, height, bit_depth, color_type


def _prepare_package_dir(package_dir: Path) -> None:
    package_dir.mkdir(parents=True, exist_ok=True)
    allowed_directories = {
        Path("skills"),
        *(Path("skills") / skill_name for skill_name in EXPECTED_SKILLS),
    }
    for path in sorted(package_dir.rglob("*"), reverse=True):
        relative = path.relative_to(package_dir)
        if path.is_file():
            path.chmod(0o666)
            path.unlink()
        elif path.is_dir() and relative not in allowed_directories:
            if any(path.iterdir()):
                raise CoworkPackageError(
                    f"Unpacked Cowork output contains unexpected directory {relative}."
                )


def _object_list(value: Any, name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise CoworkPackageError(f"Manifest {name} must be an object array.")
    return value


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(str(item) for item in value.values())
    return ""


def _is_nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_connector_url(value: Any) -> str:
    _require(_is_nonempty(value), "Remote MCP connector requires mcpServerUrl.")
    connector_url = str(value)
    try:
        parsed = urlsplit(connector_url)
    except ValueError as exc:
        raise CoworkPackageError(
            "Canary connector URL must be an absolute HTTPS /mcp endpoint."
        ) from exc
    _require(
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.path == "/mcp"
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment,
        "Canary connector URL must be an absolute HTTPS /mcp endpoint.",
    )
    return connector_url


def _new_run_path(report_root: Path) -> Path:
    for _ in range(100):
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        candidate = report_root / f"{RUN_REPORT_PREFIX}{stamp}-{uuid.uuid4().hex}.json"
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not allocate a unique Cowork package report name.")


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


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CoworkPackageError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--connector-url")
    parser.add_argument("--manifest-id")
    parser.add_argument("--app-version")
    parser.add_argument(
        "--authorization-type",
        choices=sorted(SUPPORTED_AUTHORIZATION_TYPES),
    )
    parser.add_argument("--authorization-reference-id")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()
    custom_configuration = any(
        value is not None
        for value in (
            arguments.connector_url,
            arguments.manifest_id,
            arguments.app_version,
            arguments.authorization_type,
            arguments.authorization_reference_id,
        )
    )
    if custom_configuration and arguments.check:
        parser.error(
            "Custom manifest options are only valid when building a tenant-specific package."
        )

    if arguments.check:
        built = verify_built_package(arguments.source_root, arguments.out)
        result: dict[str, Any] = {
            "status": "pass",
            **asdict(built),
        }
    else:
        built = build_package(
            arguments.source_root,
            arguments.out,
            connector_url=arguments.connector_url,
            manifest_id=arguments.manifest_id,
            app_version=arguments.app_version,
            authorization_type=arguments.authorization_type,
            authorization_reference_id=arguments.authorization_reference_id,
        )
        report_path = write_report(built, arguments.report_root)
        result = {**asdict(built), "status": "pass", "reportPath": str(report_path)}
    if arguments.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Cowork package: PASS ({len(EXPECTED_SKILLS)} workflow skills)")
        if not arguments.check:
            print(f"Package: {result['package_path']}")
            print(f"SHA-256: {result['package_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
