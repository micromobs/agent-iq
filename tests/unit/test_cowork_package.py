from __future__ import annotations

import json
import shutil
import sys
import zipfile
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from PIL import Image

import agent_iq.deployments.cowork as cowork_module
from agent_iq.deployments.cowork import (
    CONSUMER_SKILLS,
    DEFAULT_SOURCE_ROOT,
    EMPLOYEE_SKILLS,
    EXPECTED_SKILLS,
    MAX_RUN_REPORTS,
    RUN_REPORT_PREFIX,
    CoworkPackageError,
    build_package,
    validate_source,
    verify_built_package,
    write_report,
)


def _copy_source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    shutil.copytree(DEFAULT_SOURCE_ROOT, source)
    return source


def _read_manifest(source: Path) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((source / "manifest" / "manifest.json").read_text(encoding="utf-8")),
    )


def _write_manifest(source: Path, manifest: dict[str, Any]) -> None:
    (source / "manifest" / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )


def test_source_validates_exact_package_contract() -> None:
    manifest = validate_source()

    assert manifest["version"] == "1.5.1"
    assert manifest["name"] == {"short": "Agent IQ", "full": "Agent IQ"}
    assert [item["folder"] for item in manifest["agentSkills"]] == [
        f"./skills/{skill}" for skill in EXPECTED_SKILLS
    ]
    assert len(manifest["agentConnectors"]) == 1
    assert manifest["icons"] == {
        "color": "color.png",
        "outline": "outline.png",
        "color32x32": "color32.png",
    }
    remote = manifest["agentConnectors"][0]["toolSource"]["remoteMcpServer"]
    assert remote["authorization"] == {"type": "None"}
    assert remote["mcpServerUrl"].endswith("/mcp")
    single_agent_skill = (
        DEFAULT_SOURCE_ROOT / "skills" / "single-agent-analysis" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert 'version: "1.2.0"' in single_agent_skill
    assert "run_single_agent_demo" in single_agent_skill
    assert "real catalog discovery and ranking" in single_agent_skill
    vacation_skill = (DEFAULT_SOURCE_ROOT / "skills" / "vacation-balance" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "check_vacation_balance" in vacation_skill
    assert 'version: "1.2.0"' in vacation_skill
    assert "`presentationStyle`" in vacation_skill
    assert "`compact_ring`" in vacation_skill
    assert "`sources`" in vacation_skill
    assert "Apple-Watch-style ring" in vacation_skill
    assert "Do not show a dashboard" in vacation_skill
    assert "Is this for you or your team?" in vacation_skill
    assert "United States, Europe, APAC, or all" in vacation_skill
    assert "manager" in vacation_skill.casefold()
    assert "synthetic" not in vacation_skill.casefold()
    assert "demo" not in vacation_skill.casefold()
    ask_it_skill = (DEFAULT_SOURCE_ROOT / "skills" / "ask-it" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "orchestrate",
        "exactly once",
        "resolve_employee_intent",
        "employee-it-helpdesk",
        "employee-identity-security",
    ):
        assert marker in ask_it_skill
    assert "synthetic" not in ask_it_skill.casefold()
    assert "demo" not in ask_it_skill.casefold()
    assert "canary" not in ask_it_skill.casefold()
    assert not (DEFAULT_SOURCE_ROOT / "skills" / "workday-rescue").exists()
    visible_manifest = json.dumps(
        {
            "developer": manifest["developer"]["name"],
            "name": manifest["name"],
            "description": manifest["description"],
            "connectorDisplayName": manifest["agentConnectors"][0]["displayName"],
            "connectorDescription": manifest["agentConnectors"][0]["description"],
        }
    ).casefold()
    for forbidden_label in ("synthetic", "demo", "canary", "non-production"):
        assert forbidden_label not in visible_manifest
    for skill in CONSUMER_SKILLS:
        text = (DEFAULT_SOURCE_ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        for field in (
            "`selectedAgents`",
            "`answer`",
            "`citations`",
            "`sourcesMarkdown`",
            "`issues`",
            "`nextAction`",
        ):
            assert field in text
        compact_text = " ".join(text.split())
        assert "Do not call `get_execution`, search for spill files" in compact_text
        assert "user explicitly requests audit details for an execution ID" in compact_text
    for skill in EMPLOYEE_SKILLS:
        text = (DEFAULT_SOURCE_ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        for field in (
            "`selectedAgents`",
            "`phases`",
            "`sections`",
            "`sources`",
            "`actions`",
            "`actionClass`",
            "`executed=false`",
        ):
            assert field in text
    for skill in EXPECTED_SKILLS:
        text = (DEFAULT_SOURCE_ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        visible_text = text.casefold()
        for identifier in ("run_single_agent_demo", "run_vacation_balance_demo"):
            visible_text = visible_text.replace(identifier, "")
        for forbidden_label in ("synthetic", "demo", "canary", "non-production"):
            assert forbidden_label not in visible_text


@pytest.mark.parametrize(
    "connector_url",
    (
        "https://user@example.invalid/mcp",
        "https://:password@example.invalid/mcp",
        "https://user:password@example.invalid/mcp",  # pragma: allowlist secret
        "https://example.invalid/mcp?credential=secret",
        "https://example.invalid/mcp#credential",
    ),
)
def test_connector_url_rejects_userinfo_query_and_fragment(
    tmp_path: Path,
    connector_url: str,
) -> None:
    source = _copy_source(tmp_path)
    manifest = _read_manifest(source)
    manifest["agentConnectors"][0]["toolSource"]["remoteMcpServer"]["mcpServerUrl"] = connector_url
    _write_manifest(source, manifest)

    with pytest.raises(CoworkPackageError, match="absolute HTTPS /mcp"):
        build_package(source, tmp_path / "output")
    assert not (tmp_path / "output" / "agent-iq-canary.zip").exists()


def test_build_is_deterministic_and_does_not_overwrite_release_package(
    tmp_path: Path,
) -> None:
    output_one = tmp_path / "one"
    output_two = tmp_path / "two"
    release_package = output_one / "agent-iq.zip"
    output_one.mkdir()
    release_package.write_bytes(b"existing-release")

    first = build_package(output_root=output_one)
    second = build_package(output_root=output_two)
    repeated = build_package(output_root=output_one)

    first_bytes = Path(first.package_path).read_bytes()
    second_bytes = Path(second.package_path).read_bytes()
    assert first_bytes == second_bytes
    assert first_bytes == Path(repeated.package_path).read_bytes()
    assert first.package_sha256 == second.package_sha256
    assert release_package.read_bytes() == b"existing-release"
    with zipfile.ZipFile(first.package_path) as archive:
        assert archive.namelist() == list(first.files)
        assert archive.namelist() == [
            "color.png",
            "color32.png",
            "manifest.json",
            "outline.png",
            *[f"skills/{skill}/SKILL.md" for skill in sorted(EXPECTED_SKILLS)],
        ]
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())


def test_build_supports_tenant_specific_endpoint_and_identity(tmp_path: Path) -> None:
    connector_url = "https://agent-iq-orchestrator-canary.example.azurecontainerapps.io/mcp"
    manifest_id = "11111111-1111-4111-8111-111111111111"

    first = build_package(
        output_root=tmp_path / "one",
        connector_url=connector_url,
        manifest_id=manifest_id,
        app_version="2.0.0",
        authorization_type="None",
    )
    second = build_package(
        output_root=tmp_path / "two",
        connector_url=connector_url,
        manifest_id=manifest_id,
        app_version="2.0.0",
        authorization_type="None",
    )

    assert Path(first.package_path).read_bytes() == Path(second.package_path).read_bytes()
    assert first.connector_url == connector_url
    assert first.manifest_id == manifest_id
    with zipfile.ZipFile(first.package_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["id"] == manifest_id
    assert manifest["version"] == "2.0.0"
    remote = manifest["agentConnectors"][0]["toolSource"]["remoteMcpServer"]
    assert remote == {
        "mcpServerUrl": connector_url,
        "authorization": {"type": "None"},
    }


def test_cli_builds_and_checks_tenant_specific_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "output"
    reports = tmp_path / "reports"
    manifest_id = "-".join(("11111111", "1111", "4111", "8111", "111111111111"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cowork-build",
            "--out",
            str(output),
            "--report-root",
            str(reports),
            "--connector-url",
            "https://agent-iq-orchestrator-canary.example.azurecontainerapps.io/mcp",
            "--manifest-id",
            manifest_id,
            "--app-version",
            "2.3.4",
            "--json",
        ],
    )

    assert cowork_module.main() == 0
    built = json.loads(capsys.readouterr().out)
    assert built["status"] == "pass"
    assert Path(built["package_path"]).is_file()
    assert Path(built["reportPath"]).is_file()

    canonical_output = tmp_path / "canonical"
    monkeypatch.setattr(
        sys,
        "argv",
        ["cowork-build", "--out", str(canonical_output), "--json"],
    )
    assert cowork_module.main() == 0
    canonical = json.loads(capsys.readouterr().out)

    monkeypatch.setattr(
        sys,
        "argv",
        ["cowork-build", "--out", str(canonical_output), "--check", "--json"],
    )
    assert cowork_module.main() == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["status"] == "pass"
    assert checked["package_sha256"] == canonical["package_sha256"]


def test_build_supports_vault_authorization_reference(tmp_path: Path) -> None:
    result = build_package(
        output_root=tmp_path / "output",
        connector_url="https://agent-iq.example.com/mcp",
        authorization_type="OAuthPluginVault",
        authorization_reference_id="agent-iq-oauth",
    )

    with zipfile.ZipFile(result.package_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    remote = manifest["agentConnectors"][0]["toolSource"]["remoteMcpServer"]
    assert remote["authorization"] == {
        "type": "OAuthPluginVault",
        "referenceId": "agent-iq-oauth",
    }


@pytest.mark.parametrize(
    ("authorization_type", "reference_id", "message"),
    (
        (None, "unexpected", "requires an explicit"),
        ("None", "unexpected", "cannot include"),
        ("OAuthPluginVault", None, "requires a safe"),
        ("OAuthPluginVault", "../unsafe", "requires a safe"),
        ("AzureKeyVault", "vault-reference", "Unsupported"),
    ),
)
def test_build_rejects_invalid_authorization_configuration(
    tmp_path: Path,
    authorization_type: str | None,
    reference_id: str | None,
    message: str,
) -> None:
    with pytest.raises(CoworkPackageError, match=message):
        build_package(
            output_root=tmp_path / "output",
            authorization_type=authorization_type,
            authorization_reference_id=reference_id,
        )


def test_build_ignores_empty_staging_placeholder(tmp_path: Path) -> None:
    output = tmp_path / "output"
    placeholder = output / "agent-iq-canary" / "skills" / "vacation-balance-demo"
    placeholder.mkdir(parents=True)

    result = build_package(output_root=output)

    assert Path(result.package_path).is_file()
    with zipfile.ZipFile(result.package_path) as archive:
        assert not any("vacation-balance-demo" in name for name in archive.namelist())


def test_reports_are_unique_at_the_same_instant_and_latest_is_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FrozenDateTime:
        @classmethod
        def now(cls, tz: object) -> datetime:
            del tz
            return datetime(2026, 7, 12, 11, 48, tzinfo=UTC)

    monkeypatch.setattr(cowork_module, "datetime", FrozenDateTime)
    result = build_package(output_root=tmp_path / "output")

    first = write_report(result, tmp_path / "reports")
    second = write_report(result, tmp_path / "reports")

    assert first != second
    assert first.name.startswith(RUN_REPORT_PREFIX)
    assert second.name.startswith(RUN_REPORT_PREFIX)
    assert first.exists() and second.exists()
    assert (tmp_path / "reports" / "latest.json").read_text(encoding="utf-8") == second.read_text(
        encoding="utf-8"
    )
    assert not any(path.suffix == ".tmp" for path in (tmp_path / "reports").iterdir())


def test_report_retention_is_bounded_and_preserves_unrelated_entries(tmp_path: Path) -> None:
    result = build_package(output_root=tmp_path / "output")
    report_root = tmp_path / "reports"
    report_root.mkdir()
    unrelated_json = report_root / "unrelated.json"
    unrelated_json.write_text("keep me", encoding="utf-8")
    unrelated_file = report_root / "notes.txt"
    unrelated_file.write_text("also keep me", encoding="utf-8")
    unrelated_directory = report_root / "unrelated-directory"
    unrelated_directory.mkdir()
    run_named_directory = report_root / f"{RUN_REPORT_PREFIX}20260712T114800000000Z-{'0' * 32}.json"
    run_named_directory.mkdir()
    near_match = report_root / f"{RUN_REPORT_PREFIX}20260712T114800000000Z-not-a-uuid.json"
    near_match.write_text("keep this too", encoding="utf-8")

    paths = [write_report(result, report_root) for _ in range(MAX_RUN_REPORTS + 3)]

    assert sum(path.exists() for path in paths) == MAX_RUN_REPORTS
    assert unrelated_json.read_text(encoding="utf-8") == "keep me"
    assert unrelated_file.read_text(encoding="utf-8") == "also keep me"
    assert unrelated_directory.is_dir()
    assert run_named_directory.is_dir()
    assert near_match.read_text(encoding="utf-8") == "keep this too"
    assert not any(path.suffix == ".tmp" for path in report_root.iterdir())


def test_report_rejects_unsafe_persisted_connector_endpoint(tmp_path: Path) -> None:
    result = build_package(output_root=tmp_path / "output")
    unsafe = replace(result, connector_url="https://user:secret@example.invalid/mcp")

    with pytest.raises(CoworkPackageError, match="absolute HTTPS /mcp"):
        write_report(unsafe, tmp_path / "reports")
    assert not (tmp_path / "reports").exists()


def test_built_package_check_rejects_missing_or_stale_artifact(tmp_path: Path) -> None:
    output = tmp_path / "output"
    with pytest.raises(CoworkPackageError, match="missing"):
        verify_built_package(output_root=output)

    built = build_package(output_root=output)
    verified = verify_built_package(output_root=output)
    assert verified.package_sha256 == built.package_sha256

    Path(built.package_path).write_bytes(b"stale-package")
    with pytest.raises(CoworkPackageError, match="stale"):
        verify_built_package(output_root=output)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("http-url", "absolute HTTPS /mcp"),
        ("authenticated", "authorization must be explicitly set to None"),
        ("embedded-agent", "cannot declare agents"),
        (
            "missing-skill",
            f"exactly the {len(EXPECTED_SKILLS)} canonical skill folders",
        ),
        ("wrong-frontmatter", "frontmatter name must match folder"),
        ("wrong-icon-size", "outline.png must be 32x32"),
        ("zero-app-version", "stable x.y.z with a nonzero major"),
    ),
)
def test_invalid_package_source_fails_closed(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    source = _copy_source(tmp_path)
    manifest = _read_manifest(source)
    if mutation == "http-url":
        manifest["agentConnectors"][0]["toolSource"]["remoteMcpServer"]["mcpServerUrl"] = (
            "http://localhost/mcp"
        )
        _write_manifest(source, manifest)
    elif mutation == "authenticated":
        manifest["agentConnectors"][0]["toolSource"]["remoteMcpServer"]["authorization"] = {
            "type": "OAuthPluginVault",
            "referenceId": "test",
        }
        _write_manifest(source, manifest)
    elif mutation == "embedded-agent":
        manifest["copilotAgents"] = {"declarativeAgents": []}
        _write_manifest(source, manifest)
    elif mutation == "zero-app-version":
        manifest["version"] = "0.1.0"
        _write_manifest(source, manifest)
    elif mutation == "missing-skill":
        unexpected = source / "skills" / "unexpected-skill"
        unexpected.mkdir()
        (unexpected / "SKILL.md").write_text(
            "---\nname: unexpected-skill\n---\n",
            encoding="utf-8",
        )
    elif mutation == "wrong-icon-size":
        outline = source / "assets" / "outline.png"
        outline.chmod(0o666)
        shutil.copyfile(source / "assets" / "color.png", outline)
    else:
        skill_path = source / "skills" / EXPECTED_SKILLS[0] / "SKILL.md"
        skill_path.write_text(
            skill_path.read_text(encoding="utf-8").replace(
                f"name: {EXPECTED_SKILLS[0]}",
                "name: incorrect",
                1,
            ),
            encoding="utf-8",
        )

    with pytest.raises(CoworkPackageError, match=message):
        validate_source(source)


@pytest.mark.parametrize(
    ("name", "size", "transparent"),
    (
        ("color.png", (192, 192), False),
        ("outline.png", (32, 32), True),
        ("color32.png", (32, 32), True),
    ),
)
def test_icon_assets_follow_microsoft_365_contract(
    name: str,
    size: tuple[int, int],
    transparent: bool,
) -> None:
    with Image.open(DEFAULT_SOURCE_ROOT / "assets" / name) as source:
        image = source.convert("RGBA")
    assert image.size == size
    alpha_min, alpha_max = image.getchannel("A").getextrema()
    assert alpha_max == 255
    if transparent:
        assert alpha_min == 0
    else:
        assert alpha_min == 255
    if name == "outline.png":
        visible_colors = {
            pixel[:3]
            for pixel in cast("Iterable[tuple[int, int, int, int]]", image.getdata())
            if pixel[3] > 0
        }
        assert visible_colors == {(255, 255, 255)}
