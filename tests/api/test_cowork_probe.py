from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import scripts.probe_cowork_connector as probe_module
from mcp.shared.memory import create_connected_server_and_client_session
from scripts.probe_cowork_connector import (
    MAX_RUN_REPORTS,
    RUN_REPORT_PREFIX,
    build_probe_command,
    canonicalize_mcp_url,
    mcp_url_from_manifest,
    probe_endpoint,
    probe_session,
    write_report,
)

from agent_iq.api import AgentIqService, create_mcp_server
from agent_iq.api.mcp import MCP_TOOLS
from agent_iq.api.mcp_apps import (
    EMPLOYEE_OPERATIONS_MIME_TYPE,
    EMPLOYEE_OPERATIONS_RESOURCE_URI,
    FORMATION_MIME_TYPE,
    FORMATION_RESOURCE_URI,
    VACATION_RING_MIME_TYPE,
    VACATION_RING_RESOURCE_URI,
)

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def test_cowork_probe_certifies_the_full_remote_mcp_contract(
    service_factory: Callable[..., AgentIqService],
) -> None:
    async def run() -> None:
        server = create_mcp_server(service_factory())
        command = build_probe_command(
            request_id="d12-cowork-probe-pass",
            requested_at=NOW,
        )
        async with create_connected_server_and_client_session(server) as session:
            report = await probe_session(session, command)

        assert report["status"] == "pass"
        assert all(report["checks"].values())
        assert report["checks"]["safetyAnnotations"]
        assert report["checks"]["mcpAppToolMetadata"]
        assert report["checks"]["mcpAppResource"]
        assert report["checks"]["mcpAppSelfContained"]
        assert report["checks"]["employeeMcpAppResource"]
        assert report["checks"]["employeeMcpAppSelfContained"]
        assert report["checks"]["vacationMcpAppResource"]
        assert report["checks"]["vacationMcpAppSelfContained"]
        assert report["handshake"]["tools"] == sorted(MCP_TOOLS)
        assert report["handshake"]["toolMeta"]["orchestrate"] == {
            "ui": {
                "resourceUri": FORMATION_RESOURCE_URI,
                "visibility": ["model", "app"],
            }
        }
        assert report["handshake"]["toolMeta"]["run_single_agent_demo"] == {
            "ui": {
                "resourceUri": FORMATION_RESOURCE_URI,
                "visibility": ["model", "app"],
            }
        }
        assert report["handshake"]["formationResource"]["mimeType"] == FORMATION_MIME_TYPE
        assert report["handshake"]["formationResource"]["bytes"] <= 64 * 1024
        assert report["handshake"]["toolMeta"]["prepare_employee_action"] == {
            "ui": {
                "resourceUri": EMPLOYEE_OPERATIONS_RESOURCE_URI,
                "visibility": ["model", "app"],
            }
        }
        assert (
            report["handshake"]["employeeOperationsResource"]["mimeType"]
            == EMPLOYEE_OPERATIONS_MIME_TYPE
        )
        assert report["handshake"]["employeeOperationsResource"]["bytes"] <= 96 * 1024
        assert report["handshake"]["toolMeta"]["check_vacation_balance"] == {
            "ui": {
                "resourceUri": VACATION_RING_RESOURCE_URI,
                "visibility": ["model", "app"],
            }
        }
        assert report["handshake"]["vacationRingResource"]["mimeType"] == VACATION_RING_MIME_TYPE
        assert report["handshake"]["vacationRingResource"]["bytes"] <= 24 * 1024
        assert report["handshake"]["toolAnnotations"]["orchestrate"] == {
            "title": "Run a read-only Agent IQ analysis",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
        assert report["search"]["topCandidate"]["agentId"] == "consumer-formulation-rd"
        assert report["search"]["payloadBytes"] <= 12 * 1024
        assert report["orchestration"]["tool"] == "run_single_agent_demo"
        assert report["orchestration"]["selectedAgentIds"] == ["consumer-formulation-rd"]
        assert report["orchestration"]["payloadBytes"] <= 16 * 1024
        assert report["orchestration"]["detailsAvailableVia"] == "get_execution"
        progress = [event["message"] for event in report["orchestration"]["progressEvents"]]
        assert progress[0] == "Matching eligible specialists"
        assert progress[1].startswith(
            "Selected Fabrikam Consumer Products Formulation R&D - matched "
        )
        assert progress[2:] == [
            "Prepared the governed execution plan",
            "Retrieving approved evidence from Fabrikam Consumer Products Formulation R&D",
            "Building the cited answer",
            "Completed",
        ]
        assert report["orchestration"]["citationCount"] == 2
        assert report["coworkUx"]["status"] == "pass"

    asyncio.run(run())


def test_cowork_probe_fails_closed_when_the_expected_route_is_wrong(
    service_factory: Callable[..., AgentIqService],
) -> None:
    async def run() -> None:
        server = create_mcp_server(service_factory())
        command = build_probe_command(
            request_id="d12-cowork-probe-fail",
            requested_at=NOW,
        )
        async with create_connected_server_and_client_session(server) as session:
            report = await probe_session(
                session,
                command,
                expected_agent="consumer-regulatory-affairs",
            )

        assert report["status"] == "fail"
        assert report["failures"] == [
            "searchTop1",
            "orchestrationSelected",
            "coworkUx",
        ]

    asyncio.run(run())


def test_cowork_probe_uses_manifest_url_and_writes_latest_report(tmp_path: Path) -> None:
    mcp_url = mcp_url_from_manifest()
    report = {"schemaVersion": "1.0", "status": "pass", "mcpUrl": mcp_url}

    path = write_report(report, tmp_path)

    assert mcp_url.endswith("/mcp")
    assert json.loads(path.read_text(encoding="utf-8")) == report
    assert (tmp_path / "latest.json").read_text(encoding="utf-8") == path.read_text(
        encoding="utf-8"
    )
    assert path.name.startswith(RUN_REPORT_PREFIX)
    assert len(path.stem.rsplit("-", 1)[-1]) == 32
    assert not any(path.suffix == ".tmp" for path in tmp_path.iterdir())


@pytest.mark.parametrize(
    "url",
    (
        "http://example.invalid/mcp",
        "example.invalid/mcp",
        "https://user@example.invalid/mcp",
        "https://:password@example.invalid/mcp",
        "https://example.invalid/mcp?query=private",
        "https://example.invalid/mcp#private",
        "https://example.invalid/mcp/",
        "https://example.invalid//mcp",
        "https://example.invalid/%6dcp",
        "https://example.invalid\\mcp",
        "https://example.invalid/mcp\n",
        "https://example..invalid/mcp",
        "https://-example.invalid/mcp",
        "https://example-.invalid/mcp",
        "https://example.invalid:444/mcp",
        "https://example.invalid:443/mcp",
        "https://127.0.0.1/mcp",
        "https://localhost/mcp",
    ),
)
def test_cowork_probe_rejects_unsafe_or_noncanonical_mcp_urls(url: str) -> None:
    with pytest.raises(ValueError, match="canonical absolute HTTPS /mcp"):
        canonicalize_mcp_url(url)


def test_cowork_probe_canonicalizes_the_manifest_endpoint() -> None:
    assert canonicalize_mcp_url("https://example.invalid/mcp") == "https://example.invalid/mcp"


def test_cowork_probe_validates_before_network_use(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_client(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise AssertionError("HTTP client must not be constructed for an unsafe URL.")

    monkeypatch.setattr(httpx, "AsyncClient", unexpected_client)

    with pytest.raises(ValueError, match="canonical absolute HTTPS /mcp"):
        asyncio.run(
            probe_endpoint(
                "https://localhost/mcp",
                build_probe_command(request_id="unsafe-url-test"),
            )
        )


def test_cowork_probe_revalidates_mcp_url_before_persistence(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="canonical absolute HTTPS /mcp"):
        write_report(
            {"schemaVersion": "1.0", "mcpUrl": "https://example.invalid/mcp?private"},
            tmp_path,
        )

    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize("location", ("https://other.invalid/mcp", "/other"))
def test_cowork_probe_rejects_same_origin_and_cross_origin_redirects(
    monkeypatch: pytest.MonkeyPatch,
    location: str,
) -> None:
    requests: list[httpx.Request] = []
    seen: dict[str, object] = {}

    def redirect(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(307, headers={"location": location}, request=request)

    class RecordingClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            seen["follow_redirects"] = kwargs["follow_redirects"]
            kwargs["transport"] = httpx.MockTransport(redirect)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", RecordingClient)

    with pytest.raises((RuntimeError, ExceptionGroup)):
        asyncio.run(
            probe_endpoint(
                "https://example.invalid/mcp",
                build_probe_command(request_id="redirect-test"),
                timeout_seconds=1,
            )
        )

    assert seen["follow_redirects"] is False
    assert len(requests) == 1


def test_cowork_probe_report_redaction_is_byte_based_and_deep_copied(tmp_path: Path) -> None:
    intent = "café intent"
    answer = "grounded answer - keep private"
    sources_markdown = "## Source\nConfidential evidence"
    source_text = "Source text with résumé"
    tool_query = "tool query 🔒"
    report = {
        "mcpUrl": "https://example.invalid/mcp",
        "search": {"intent": intent, "candidateCount": 2},
        "orchestration": {
            "answer": answer,
            "sourcesMarkdown": sources_markdown,
            "status": "completed",
        },
        "sources": [{"sourceId": "source-1", "text": source_text}],
        "toolCalls": [
            {
                "name": "search_agents",
                "status": "completed",
                "arguments": {"query": tool_query, "maxCandidates": 20},
            }
        ],
    }
    original = copy.deepcopy(report)

    path = write_report(report, tmp_path)
    persisted_text = path.read_text(encoding="utf-8")
    persisted = json.loads(persisted_text)

    assert (tmp_path / "latest.json").read_text(encoding="utf-8") == persisted_text
    for secret in (intent, answer, sources_markdown, source_text, tool_query):
        assert secret not in persisted_text
    assert report == original
    assert persisted["search"]["intentSha256"] == hashlib.sha256(intent.encode("utf-8")).hexdigest()
    assert persisted["search"]["intentLength"] == len(intent.encode("utf-8"))
    assert "intent" not in persisted["search"]
    assert persisted["search"]["candidateCount"] == 2
    assert persisted["orchestration"]["status"] == "completed"
    assert persisted["sources"][0]["sourceId"] == "source-1"
    assert persisted["sources"][0]["textLength"] == len(source_text.encode("utf-8"))
    assert "text" not in persisted["sources"][0]
    assert persisted["toolCalls"][0]["name"] == "search_agents"
    assert persisted["toolCalls"][0]["argumentsLength"] > 0
    assert "arguments" not in persisted["toolCalls"][0]


def test_cowork_probe_cli_json_prints_a_content_safe_deep_copy(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    intent = "SENTINEL-COWORK-RAW-INTENT"
    prompt = "SENTINEL-COWORK-RAW-PROMPT"
    answer = "SENTINEL-COWORK-RAW-ANSWER"
    source_markdown = "SENTINEL-COWORK-RAW-SOURCE-MARKDOWN"
    tool_arguments = "SENTINEL-COWORK-RAW-TOOL-ARGUMENTS"
    context_id = "SENTINEL-COWORK-RAW-CONTEXT-ID"
    credential = "SENTINEL-COWORK-RAW-CREDENTIAL"
    mcp_url = "https://example.invalid/mcp"
    report: dict[str, object] = {
        "schemaVersion": "1.0",
        "status": "pass",
        "mcpUrl": mcp_url,
        "search": {"intent": intent, "candidateCount": 1},
        "orchestration": {
            "status": "completed",
            "selectedAgentIds": ["safe-agent-id"],
            "citationCount": 1,
            "prompt": prompt,
            "answer": answer,
            "sourcesMarkdown": source_markdown,
            "contextIds": [context_id],
        },
        "toolArguments": {"query": tool_arguments},
        "credentials": {"accessToken": credential},
    }
    original = copy.deepcopy(report)

    async def fake_probe_endpoint(
        endpoint: str,
        command: object,
        *,
        expected_agent: str,
        expected_source_ids: tuple[str, ...],
        timeout_seconds: float,
    ) -> dict[str, object]:
        del endpoint, command, expected_agent, expected_source_ids, timeout_seconds
        return report

    monkeypatch.setattr(probe_module, "probe_endpoint", fake_probe_endpoint)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "probe_cowork_connector.py",
            "--mcp-url",
            mcp_url,
            "--intent",
            intent,
            "--no-report",
            "--json",
        ],
    )

    assert probe_module.main() == 0
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
    assert payload["search"]["intentLength"] == len(intent.encode("utf-8"))
    assert payload["orchestration"]["selectedAgentIds"] == ["safe-agent-id"]
    assert payload["orchestration"]["citationCount"] == 1
    assert "contextIds" not in payload["orchestration"]
    assert payload["orchestration"]["contextIdsLength"] > 0
    assert "toolArguments" not in payload
    assert payload["toolArgumentsLength"] > 0
    assert "credentials" not in payload
    assert payload["credentialsLength"] > 0
    assert report == original


def test_cowork_probe_report_collision_does_not_overwrite_existing_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FrozenDateTime:
        @classmethod
        def now(cls, tz: object) -> datetime:
            del tz
            return datetime(2026, 7, 12, 12, 0, tzinfo=UTC)

    first_uuid = uuid.UUID(int=1)
    second_uuid = uuid.UUID(int=2)
    existing = tmp_path / f"{RUN_REPORT_PREFIX}20260712T120000000000Z-{first_uuid.hex}.json"
    existing.write_text("keep this run", encoding="utf-8")
    uuids = iter((first_uuid, second_uuid))

    monkeypatch.setattr(probe_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(uuid, "uuid4", lambda: next(uuids))

    path = write_report({"mcpUrl": "https://example.invalid/mcp"}, tmp_path)

    assert path.name.endswith(f"-{second_uuid.hex}.json")
    assert existing.read_text(encoding="utf-8") == "keep this run"


def test_cowork_probe_report_retention_is_bounded_and_preserves_unrelated_entries(
    tmp_path: Path,
) -> None:
    unrelated_file = tmp_path / "unrelated.json"
    unrelated_file.write_text("keep me", encoding="utf-8")
    unrelated_directory = tmp_path / "unrelated-directory"
    unrelated_directory.mkdir()
    near_match = tmp_path / f"{RUN_REPORT_PREFIX}20260712T120000000000Z-not-a-uuid.json"
    near_match.write_text("keep this too", encoding="utf-8")
    managed_directory = tmp_path / f"{RUN_REPORT_PREFIX}20260712T120000000000Z-{'0' * 32}.json"
    managed_directory.mkdir()

    paths = [
        write_report({"mcpUrl": "https://example.invalid/mcp", "run": index}, tmp_path)
        for index in range(MAX_RUN_REPORTS + 3)
    ]

    assert sum(path.exists() for path in paths) == MAX_RUN_REPORTS
    assert unrelated_file.read_text(encoding="utf-8") == "keep me"
    assert unrelated_directory.is_dir()
    assert near_match.read_text(encoding="utf-8") == "keep this too"
    assert managed_directory.is_dir()
    assert not any(path.suffix == ".tmp" for path in tmp_path.iterdir())
