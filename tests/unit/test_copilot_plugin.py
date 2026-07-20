"""Deterministic GitHub Copilot plugin packaging tests."""

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

import agent_iq.deployments.copilot_plugin as copilot_module
from agent_iq.deployments.copilot_plugin import (
    DEFAULT_SOURCE_ROOT,
    EXPECTED_AGENT,
    EXPECTED_FILES,
    EXPECTED_SKILLS,
    MAX_RUN_REPORTS,
    PACKAGE_ARCHIVE_NAME,
    PLUGIN_VERSION,
    RUN_REPORT_PREFIX,
    CopilotPluginError,
    build_plugin,
    main,
    validate_source,
    verify_built_plugin,
    write_report,
)


def _copy_source(tmp_path: Path) -> Path:
    source = tmp_path / "copilot"
    shutil.copytree(DEFAULT_SOURCE_ROOT, source)
    return source


def test_source_has_one_agent_two_versioned_skills_and_bounded_mcp() -> None:
    manifest = validate_source()
    mcp = json.loads((DEFAULT_SOURCE_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    agent = (DEFAULT_SOURCE_ROOT / EXPECTED_AGENT).read_text(encoding="utf-8")
    plugin_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in DEFAULT_SOURCE_ROOT.rglob("*")
        if path.is_file()
    )

    assert manifest["name"] == "agent-iq"
    assert manifest["version"] == PLUGIN_VERSION
    assert tuple(mcp["mcpServers"]) == ("agent-iq",)
    assert mcp["mcpServers"]["agent-iq"]["tools"] == [
        "search_agents",
        "orchestrate",
        "get_execution",
        "cancel_execution",
        "submit_feedback",
        "capabilities",
    ]
    assert "tools:\n  - agent-iq/search_agents" in agent
    assert "  - workiq/ask" in agent
    assert 'tools: ["*"]' not in agent
    assert "TOOL_CALL_ARGS" not in plugin_text
    assert "REASONING_" not in plugin_text
    assert "raw provider errors" in plugin_text


def test_build_is_byte_deterministic_and_installable_as_a_directory(tmp_path: Path) -> None:
    first = build_plugin(output_root=tmp_path / "out")
    first_bytes = Path(first.package_path).read_bytes()
    legacy_archive = tmp_path / "out" / "agent-iq-plugin.zip"
    legacy_archive.write_bytes(b"stale")
    second = build_plugin(output_root=tmp_path / "out")

    assert first.package_sha256 == second.package_sha256
    assert Path(second.package_path).name == PACKAGE_ARCHIVE_NAME
    assert not legacy_archive.exists()
    assert first_bytes == Path(second.package_path).read_bytes()
    assert first.files == tuple(sorted(EXPECTED_FILES))
    assert first.skills == EXPECTED_SKILLS
    package_dir = tmp_path / "out" / "agent-iq-plugin"
    assert json.loads((package_dir / "plugin.json").read_text(encoding="utf-8"))["name"] == (
        "agent-iq"
    )

    with zipfile.ZipFile(first.package_path) as archive:
        assert tuple(archive.namelist()) == tuple(sorted(EXPECTED_FILES))
        assert all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in archive.infolist())
        assert all((item.external_attr >> 16) == 0o100644 for item in archive.infolist())


def test_build_configures_tenant_specific_mcp_endpoint(tmp_path: Path) -> None:
    endpoint = "https://agent-iq-orchestrator.example.azurecontainerapps.io/mcp"
    output = tmp_path / "out"

    first = build_plugin(output_root=output, mcp_url=endpoint)
    second = build_plugin(output_root=tmp_path / "second", mcp_url=endpoint)
    verified = verify_built_plugin(output_root=output, mcp_url=endpoint)

    assert first.package_sha256 == second.package_sha256 == verified.package_sha256
    package_dir = output / "agent-iq-plugin"
    configured = json.loads((package_dir / ".mcp.json").read_text(encoding="utf-8"))
    assert configured["mcpServers"]["agent-iq"]["url"] == endpoint
    with zipfile.ZipFile(first.package_path) as archive:
        archived = json.loads(archive.read(".mcp.json"))
    assert archived["mcpServers"]["agent-iq"]["url"] == endpoint
    source = json.loads((DEFAULT_SOURCE_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    assert source["mcpServers"]["agent-iq"]["url"] != endpoint


def test_reports_are_unique_at_the_same_instant_and_latest_is_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FrozenDateTime:
        @classmethod
        def now(cls, tz: object) -> datetime:
            del tz
            return datetime(2026, 7, 12, 11, 48, tzinfo=UTC)

    monkeypatch.setattr(copilot_module, "datetime", FrozenDateTime)
    result = build_plugin(output_root=tmp_path / "output")

    first = write_report(result, tmp_path / "reports")
    second = write_report(result, tmp_path / "reports")

    assert first != second
    assert first.name.startswith(RUN_REPORT_PREFIX)
    assert second.name.startswith(RUN_REPORT_PREFIX)
    assert first.exists() and second.exists()
    assert (tmp_path / "reports" / "latest.json").read_bytes() == second.read_bytes()
    assert not any(path.suffix == ".tmp" for path in (tmp_path / "reports").iterdir())


def test_report_retention_is_bounded_and_preserves_unrelated_entries(tmp_path: Path) -> None:
    result = build_plugin(output_root=tmp_path / "output")
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


def test_built_plugin_check_rejects_missing_or_stale_artifact(tmp_path: Path) -> None:
    output = tmp_path / "output"
    with pytest.raises(CopilotPluginError, match="missing"):
        verify_built_plugin(output_root=output)

    built = build_plugin(output_root=output)
    verified = verify_built_plugin(output_root=output)
    assert verified == built

    Path(built.package_path).write_bytes(b"stale-plugin")
    with pytest.raises(CopilotPluginError, match="stale"):
        verify_built_plugin(output_root=output)


def test_report_is_content_safe_and_tracks_package_hash(tmp_path: Path) -> None:
    result = build_plugin(output_root=tmp_path / "out")

    report_path = write_report(result, tmp_path / "reports")
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["status"] == "pass"
    assert report["package_sha256"] == result.package_sha256
    assert report["plugin_version"] == PLUGIN_VERSION
    assert "template-00000000" not in report_path.read_text(encoding="utf-8")
    assert (tmp_path / "reports" / "latest.json").read_bytes() == report_path.read_bytes()


@pytest.mark.parametrize(
    "endpoint",
    (
        "http://example.invalid/mcp",
        "https://user@example.invalid/mcp",
        "https://:password@example.invalid/mcp",
        "https://user:password@example.invalid/mcp",  # pragma: allowlist secret
        "https://example.invalid/mcp?credential=secret",
        "https://example.invalid/mcp#credential",
    ),
)
def test_validator_rejects_insecure_endpoint_and_unbounded_agent_tools(
    tmp_path: Path,
    endpoint: str,
) -> None:
    source = _copy_source(tmp_path)
    mcp_path = source / ".mcp.json"
    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    mcp["mcpServers"]["agent-iq"]["url"] = endpoint
    mcp_path.write_text(json.dumps(mcp), encoding="utf-8")
    with pytest.raises(CopilotPluginError, match="absolute HTTPS"):
        validate_source(source)

    source = _copy_source(tmp_path / "second")
    agent_path = source / EXPECTED_AGENT
    agent_path.write_text(
        agent_path.read_text(encoding="utf-8").replace(
            "  - agent-iq/search_agents",
            '  - "*"',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(CopilotPluginError, match="bounded Agent IQ"):
        validate_source(source)


def test_validator_rejects_hard_coded_routes_and_skill_mutation_preapproval(
    tmp_path: Path,
) -> None:
    source = _copy_source(tmp_path)
    agent_path = source / EXPECTED_AGENT
    agent_path.write_text(
        agent_path.read_text(encoding="utf-8") + "\nRoute to " + "pine" + "apple-example-agent.\n",
        encoding="utf-8",
    )
    with pytest.raises(CopilotPluginError, match="forbidden term"):
        validate_source(source)

    source = _copy_source(tmp_path / "second")
    skill_path = source / "skills" / "replay-execution" / "SKILL.md"
    skill_path.write_text(
        skill_path.read_text(encoding="utf-8").replace(
            "  - agent-iq/capabilities",
            "  - agent-iq/capabilities\n  - agent-iq/cancel_execution",
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(CopilotPluginError, match="unsafe or incomplete"):
        validate_source(source)


def test_validator_rejects_unversioned_or_unexpected_components(tmp_path: Path) -> None:
    source = _copy_source(tmp_path)
    skill_path = source / "skills" / "orchestrate-agents" / "SKILL.md"
    skill_path.write_text(
        skill_path.read_text(encoding="utf-8").replace(
            f'version: "{PLUGIN_VERSION}"',
            'version: "2.0.0"',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(CopilotPluginError, match="version must match"):
        validate_source(source)

    source = _copy_source(tmp_path / "second")
    (source / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(CopilotPluginError, match="unexpected files"):
        validate_source(source)


def test_cli_check_and_build_emit_machine_readable_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_root = tmp_path / "check-out"
    built_artifact = build_plugin(output_root=output_root)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "copilot-build",
            "--source-root",
            str(DEFAULT_SOURCE_ROOT),
            "--out",
            str(output_root),
            "--check",
            "--json",
        ],
    )
    assert main() == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked == {
        "agent": built_artifact.agent,
        "files": list(built_artifact.files),
        "package_path": built_artifact.package_path,
        "package_sha256": built_artifact.package_sha256,
        "plugin_name": built_artifact.plugin_name,
        "plugin_version": built_artifact.plugin_version,
        "skills": list(built_artifact.skills),
        "status": "pass",
    }

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "copilot-build",
            "--source-root",
            str(DEFAULT_SOURCE_ROOT),
            "--out",
            str(tmp_path / "out"),
            "--report-root",
            str(tmp_path / "reports"),
            "--mcp-url",
            "https://agent-iq-orchestrator.example.azurecontainerapps.io/mcp",
            "--json",
        ],
    )
    assert main() == 0
    built = json.loads(capsys.readouterr().out)
    assert built["status"] == "pass"
    assert Path(built["package_path"]).is_file()
    assert Path(built["reportPath"]).is_file()
    configured = json.loads(
        (tmp_path / "out" / "agent-iq-plugin" / ".mcp.json").read_text(encoding="utf-8")
    )
    assert configured["mcpServers"]["agent-iq"]["url"].startswith(
        "https://agent-iq-orchestrator.example."
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "copilot-build",
            "--source-root",
            str(DEFAULT_SOURCE_ROOT),
            "--out",
            str(tmp_path / "text-out"),
            "--report-root",
            str(tmp_path / "text-reports"),
        ],
    )
    assert main() == 0
    output = capsys.readouterr().out
    assert "Copilot plugin: PASS" in output
    assert "Package:" in output
    assert "SHA-256:" in output
