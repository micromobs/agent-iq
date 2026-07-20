from __future__ import annotations

import asyncio
import copy
import json
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from scripts import certify_launch_command_center as launch_certifier
from scripts.certify_launch_command_center import (
    EXPECTED_AGENTS,
    EXPECTED_CONTEXT,
    LAUNCH_INTENT,
    MAX_RUN_CERTIFICATES,
    RUN_CERTIFICATE_PREFIX,
    UNAPPROVED_CONTEXT_ID,
    _sha256_text,
    build_launch_command,
    certify_session,
    write_report,
)

from agent_iq.api import AgentIqService, create_mcp_server

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def test_launch_command_center_certifies_five_agent_cowork_scenario(
    service_factory: Callable[..., AgentIqService],
) -> None:
    async def run() -> None:
        server = create_mcp_server(service_factory())
        command = build_launch_command(
            request_id="d12-launch-command-center-pass",
            requested_at=NOW,
        )
        async with create_connected_server_and_client_session(server) as session:
            report = await certify_session(session, command)

        assert report["status"] == "pass"
        assert all(report["checks"].values())
        assert report["execution"]["selectedAgentIds"] == list(EXPECTED_AGENTS)
        assert report["execution"]["payloadBytes"] <= 16 * 1024
        assert report["execution"]["citationCount"] >= 5
        assert report["scenario"]["contextMode"] == "synthetic_metadata_only"
        assert report["scenario"]["heroAgentIds"] == list(EXPECTED_AGENTS)
        assert "BrightWash Cold" in report["scenario"]["intent"]
        assert "ClearWave" in report["scenario"]["intent"]
        assert "Fabrikam Consumer Products (synthetic demo)" in report["scenario"]["intent"]
        assert "approval" in report["scenario"]["intent"].casefold()
        assert all(
            plane["mode"] == "fixture" and plane["live"] is False and "NOT LIVE" in plane["label"]
            for plane in report["scenario"]["enterpriseDataPlanes"].values()
        )
        assert all(
            plane["sourceMode"] == "fixture"
            for plane in report["scenario"]["enterpriseDataPlanes"].values()
        )
        assert report["scenario"]["fallbackPolicy"]["allowed"] is False
        assert report["execution"]["approvalGate"] == {
            "analysisOnly": True,
            "approvalResolutionsObserved": [],
            "mutationArtifactsObserved": 0,
            "mutationArtifactsRequireApproval": True,
            "readOnlyTool": True,
            "requiredBeforeMutation": True,
            "status": "read_only_no_mutation",
        }
        assert report["execution"]["handoff"]["status"] == "not_required_for_sync_analysis"
        assert (
            report["execution"]["handoff"]["resumeFacts"]["planId"] == report["execution"]["planId"]
        )
        assert report["execution"]["lifecycle"]["firstEvent"] == "request.accepted"
        assert report["execution"]["lifecycle"]["lastEvent"] == "execution.completed"
        plan = report["execution"]["plan"]
        assert [step["contextIds"][0] for step in plan] == list(EXPECTED_CONTEXT.values())
        assert all(UNAPPROVED_CONTEXT_ID not in step["contextIds"] for step in plan)

    asyncio.run(run())


def test_launch_command_is_caller_bound_and_report_is_persistent(tmp_path: Path) -> None:
    command = build_launch_command(
        request_id="d12-launch-command-shape",
        requested_at=NOW,
    )
    caller = command.request.caller

    assert len(command.context_references) == 6
    assert all(
        reference.subject_id == caller.subject_id and reference.tenant_id == caller.tenant_id
        for reference in command.context_references
    )
    assert sum(reference.approved for reference in command.context_references) == 5
    report = {"schemaVersion": "1.0", "status": "pass"}
    path = write_report(report, tmp_path)
    assert json.loads(path.read_text(encoding="utf-8")) == report
    assert (tmp_path / "latest.json").read_text(encoding="utf-8") == path.read_text(
        encoding="utf-8"
    )


def test_persisted_certificate_redacts_answer_sources_and_context_ids(tmp_path: Path) -> None:
    report = {
        "schemaVersion": "1.0",
        "status": "pass",
        "execution": {
            "answer": "Private source content must not be persisted.",
            "sourcesMarkdown": "Private source URL: https://internal.example.invalid/secret",
            "plan": [{"contextIds": ["private-workiq-reference"]}],
        },
    }

    path = write_report(report, tmp_path)
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert persisted["persistence"]["contentSafe"] is True
    assert "answer" not in persisted["execution"]
    assert "sourcesMarkdown" not in persisted["execution"]
    assert persisted["execution"]["plan"][0]["contextIds"] != ["private-workiq-reference"]
    assert "Private source content" not in path.read_text(encoding="utf-8")
    original_execution = report["execution"]
    assert isinstance(original_execution, dict)
    assert original_execution["answer"].startswith("Private source content")


def test_persisted_run_and_latest_redact_canonical_intent_and_prompt_fields(
    tmp_path: Path,
) -> None:
    prompts: list[str] = [str(prompt) for prompt in launch_certifier.SCENARIO["rehearsalPrompts"]]
    report = {
        "schemaVersion": "1.0",
        "status": "pass",
        "scenario": {
            "name": "EU Cold-Water Launch Command Center",
            "intent": LAUNCH_INTENT,
            "rehearsalPrompts": list(prompts),
            "contextMode": "synthetic_metadata_only",
        },
        "execution": {
            "prompt": prompts[0],
            "question": prompts[1],
        },
    }

    path = write_report(report, tmp_path)
    run_bytes = path.read_bytes()
    latest_bytes = (tmp_path / "latest.json").read_bytes()
    persisted = json.loads(run_bytes)

    assert run_bytes == latest_bytes
    for persisted_bytes in (run_bytes, latest_bytes):
        for raw_value in (LAUNCH_INTENT, *prompts):
            assert raw_value.encode("utf-8") not in persisted_bytes
        for representative in (
            "validate the documented BrightWash Cold",
            "prepare a formulation brief",
            "Forecast German launch demand and verify plant OEE capacity.",
        ):
            assert representative.encode("utf-8") not in persisted_bytes

    scenario = persisted["scenario"]
    assert "intent" not in scenario
    assert scenario["intentSha256"] == _sha256_text(LAUNCH_INTENT)
    assert scenario["intentLength"] == len(LAUNCH_INTENT)
    assert "rehearsalPrompts" not in scenario
    assert "prompt" not in persisted["execution"]
    assert "question" not in persisted["execution"]
    assert persisted["persistence"]["rawIntentPersisted"] is False
    assert persisted["persistence"]["intentHashedOnPersist"] is True
    assert persisted["scenario"]["name"] == "EU Cold-Water Launch Command Center"
    assert persisted["scenario"]["contextMode"] == "synthetic_metadata_only"

    in_memory_scenario = report["scenario"]
    assert isinstance(in_memory_scenario, dict)
    assert in_memory_scenario["intent"] == LAUNCH_INTENT
    assert in_memory_scenario["rehearsalPrompts"] == list(prompts)


def test_launch_cli_json_prints_a_content_safe_deep_copy(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    intent = "SENTINEL-LAUNCH-RAW-INTENT"
    prompt = "SENTINEL-LAUNCH-RAW-PROMPT"
    answer = "SENTINEL-LAUNCH-RAW-ANSWER"
    source_markdown = "SENTINEL-LAUNCH-RAW-SOURCE-MARKDOWN"
    tool_arguments = "SENTINEL-LAUNCH-RAW-TOOL-ARGUMENTS"
    context_id = "SENTINEL-LAUNCH-RAW-CONTEXT-ID"
    credential = "SENTINEL-LAUNCH-RAW-CREDENTIAL"
    mcp_url = "https://example.invalid/mcp"
    report: dict[str, object] = {
        "schemaVersion": "1.0",
        "status": "pass",
        "mcpUrl": mcp_url,
        "scenario": {"intent": intent},
        "execution": {
            "status": "completed",
            "selectedAgentIds": ["safe-agent-id"],
            "completionMs": 12,
            "prompt": prompt,
            "answer": answer,
            "sourcesMarkdown": source_markdown,
            "plan": [{"contextIds": [context_id]}],
            "toolCalls": [{"name": "safe-tool-id", "arguments": {"query": tool_arguments}}],
            "credentials": {"apiKey": credential},
        },
    }
    original = copy.deepcopy(report)

    async def fake_certify_endpoint(
        endpoint: str,
        command: object,
        *,
        timeout_seconds: float,
    ) -> dict[str, object]:
        del endpoint, command, timeout_seconds
        return report

    monkeypatch.setattr(launch_certifier, "certify_endpoint", fake_certify_endpoint)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "certify_launch_command_center.py",
            "--base-url",
            "https://example.invalid",
            "--no-report",
            "--json",
        ],
    )

    assert launch_certifier.main() == 0
    stdout = capsys.readouterr().out
    payload = json.loads(stdout)

    for sensitive in (
        intent,
        prompt,
        answer,
        source_markdown,
        tool_arguments,
        context_id,
        credential,
    ):
        assert sensitive not in stdout
    assert payload["status"] == "pass"
    assert payload["mcpUrl"] == mcp_url
    assert payload["scenario"]["intentSha256"] == _sha256_text(intent)
    assert payload["execution"]["selectedAgentIds"] == ["safe-agent-id"]
    assert payload["execution"]["completionMs"] == 12
    assert payload["execution"]["plan"][0]["contextIds"] == [_sha256_text(context_id)]
    assert "arguments" not in payload["execution"]["toolCalls"][0]
    assert payload["execution"]["toolCalls"][0]["argumentsLength"] > 0
    assert "credentials" not in payload["execution"]
    assert payload["execution"]["credentialsLength"] > 0
    assert report == original


def test_launch_report_names_are_unique_at_the_same_instant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FrozenDateTime:
        @classmethod
        def now(cls, tz: object) -> datetime:
            del tz
            return NOW

    monkeypatch.setattr(launch_certifier, "datetime", FrozenDateTime)

    first = write_report({"schemaVersion": "1.0", "status": "pass"}, tmp_path)
    second = write_report({"schemaVersion": "1.0", "status": "pass"}, tmp_path)

    assert first != second
    assert first.exists() and second.exists()
    assert first.name.startswith(RUN_CERTIFICATE_PREFIX)
    assert second.name.startswith(RUN_CERTIFICATE_PREFIX)
    assert (tmp_path / "latest.json").read_text(encoding="ascii") == second.read_text(
        encoding="ascii"
    )
    assert not any(path.suffix == ".tmp" for path in tmp_path.iterdir())


def test_launch_report_writes_are_concurrent_safe_and_latest_is_complete(tmp_path: Path) -> None:
    def write(index: int) -> Path:
        return write_report({"schemaVersion": "1.0", "run": index}, tmp_path)

    with ThreadPoolExecutor(max_workers=8) as executor:
        paths = list(executor.map(write, range(8)))

    assert len({path.name for path in paths}) == len(paths)
    assert all(
        json.loads(path.read_text(encoding="ascii"))["schemaVersion"] == "1.0" for path in paths
    )
    latest = (tmp_path / "latest.json").read_text(encoding="ascii")
    assert latest in {path.read_text(encoding="ascii") for path in paths}
    assert not any(path.suffix == ".tmp" for path in tmp_path.iterdir())


def test_launch_report_retention_preserves_unrelated_files(tmp_path: Path) -> None:
    unrelated = tmp_path / "unrelated.json"
    unrelated.write_text("keep me", encoding="ascii")
    other_certificate = tmp_path / "other-certifier-20260711T120000000000Z.json"
    other_certificate.write_text("also keep me", encoding="ascii")

    for index in range(MAX_RUN_CERTIFICATES + 3):
        write_report({"schemaVersion": "1.0", "run": index}, tmp_path)

    run_files = [
        path
        for path in tmp_path.iterdir()
        if path.is_file() and path.name.startswith(RUN_CERTIFICATE_PREFIX)
    ]
    assert len(run_files) == MAX_RUN_CERTIFICATES
    assert unrelated.read_text(encoding="ascii") == "keep me"
    assert other_certificate.read_text(encoding="ascii") == "also keep me"


@pytest.mark.parametrize(
    "base_url",
    (
        "https://user:password@example.invalid",  # pragma: allowlist secret
        "https://example.invalid?access_token=secret",
        "https://example.invalid/#secret",
    ),
)
def test_launch_base_url_rejects_credentials_query_and_fragment(base_url: str) -> None:
    with pytest.raises(ValueError):
        launch_certifier._validate_base_url(base_url)


def test_launch_report_rejects_an_unsafe_endpoint_before_persistence(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_report(
            {
                "schemaVersion": "1.0",
                "mcpUrl": "https://user:password@example.invalid/mcp",  # pragma: allowlist secret
            },
            tmp_path,
        )

    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize(
    "location",
    (
        "https://evil.example.invalid/mcp",
        "https://example.invalid/mcp",
    ),
)
def test_launch_certifier_rejects_cross_and_same_origin_redirects(location: str) -> None:
    response = httpx.Response(
        307,
        headers={"location": location},
        request=httpx.Request("POST", "https://example.invalid/mcp"),
    )

    with pytest.raises(RuntimeError, match="redirect"):
        asyncio.run(launch_certifier._reject_redirect(response))


def test_launch_certifier_uses_a_non_following_client() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "scripts" / "certify_launch_command_center.py"
    ).read_text(encoding="utf-8")
    assert "follow_redirects=True" not in source
    assert "follow_redirects=False" in source

    async def check() -> None:
        async with launch_certifier._non_following_http_client(httpx.Timeout(1)) as client:
            assert client.follow_redirects is False

    asyncio.run(check())


def test_updated_demo_keeps_scenario_as_the_sole_prompt_source() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    scenario = json.loads(
        (repository_root / "evals" / "launch_command_center.json").read_text(encoding="utf-8")
    )
    documentation = (repository_root / "docs" / "operations" / "updated-demo.md").read_text(
        encoding="utf-8"
    )

    assert "evals\\launch_command_center.json" in documentation
    assert "sole source of truth" in documentation
    assert scenario["intent"] not in documentation
    assert all(prompt not in documentation for prompt in scenario["rehearsalPrompts"])
