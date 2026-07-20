"""Certify five hosted hero agents from native A2A through their named tools."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
from azure.identity import DefaultAzureCredential

from agent_iq.config import canonicalize_foundry_project_endpoint
from agent_iq.connectors.entra import isolated_default_azure_credential
from agent_iq.deployments.foundry_canary import AZURE_AI_SCOPE, probe_canary
from agent_iq.evals.live_tools import (
    LiveToolCase,
    build_live_tool_payload,
    evaluate_live_tool_case,
    load_live_tool_suite,
    message_operation_id,
    parse_tool_telemetry_messages,
    write_live_tool_report,
)
from agent_iq.runtime.telemetry import ToolTelemetryEvent

DEFAULT_PROJECT = os.environ.get("AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT", "")
DEFAULT_ACCOUNT = os.environ.get("AGENT_IQ_AZURE_AI_ACCOUNT", "")
DEFAULT_PROJECT_NAME = os.environ.get("AGENT_IQ_AZURE_AI_PROJECT", "")
DEFAULT_CASES = Path("evals/consumer_hero_tools_live.json")
DEFAULT_ARTIFACT_ROOT = Path.home() / "AppData" / "Local" / "AgentIQ" / "foundry" / "consumer"
APP_INSIGHTS_SCOPE = "https://api.applicationinsights.io/.default"
_OPERATION_ID = re.compile(r"^[0-9a-f]{32}$")
_AGENT_VERSION = re.compile(r"^[^@/\\?#%\x00-\x20\x7f]+$")


async def certify(arguments: argparse.Namespace) -> dict[str, Any]:
    arguments.project_endpoint = canonicalize_foundry_project_endpoint(
        arguments.project_endpoint,
        expected_account=arguments.account_name,
        expected_project=arguments.project_name,
    )
    suite = load_live_tool_suite(arguments.cases)
    suite_agent_ids = {case.agent_id for case in suite.cases}
    if set(arguments.agent_versions) != suite_agent_ids:
        raise ValueError("Exact immutable versions must cover the configured hero tool suite.")
    credential = isolated_default_azure_credential(
        credential_factory=DefaultAzureCredential,
    )
    ai_token = credential.get_token(AZURE_AI_SCOPE).token
    insights_token = credential.get_token(APP_INSIGHTS_SCOPE).token
    results: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0),
            follow_redirects=False,
            trust_env=False,
        ) as http:
            for case in suite.cases:
                probe = await _invoke_case(case, arguments, ai_token)
                operation_id = message_operation_id(probe)
                telemetry = await _wait_for_tool_telemetry(
                    http,
                    application_id=arguments.app_insights_application_id,
                    access_token=insights_token,
                    operation_id=operation_id,
                    expected_tool_ids=case.expected_tool_ids,
                    timeout_seconds=arguments.telemetry_wait,
                    poll_seconds=arguments.telemetry_poll,
                )
                result = evaluate_live_tool_case(case, probe, telemetry)
                results.append(result)
                print(
                    f"{case.case_id}: {result['status'].upper()} "
                    f"tools={','.join(result['observedToolIds']) or 'none'}"
                )
                if result["status"] != "pass":
                    break
    finally:
        credential.close()
    return build_live_tool_payload(
        results,
        expected_case_count=len(suite.cases),
        project_endpoint=arguments.project_endpoint,
    )


async def _invoke_case(
    case: LiveToolCase,
    arguments: argparse.Namespace,
    token: str,
) -> dict[str, Any]:
    project = arguments.artifact_root / case.agent_id
    return await probe_canary(
        project_endpoint=arguments.project_endpoint,
        account_name=arguments.account_name,
        project_name=arguments.project_name,
        agent_id=case.agent_id,
        agent_version=arguments.agent_versions[case.agent_id],
        token=token,
        canonical_card_path=project / "agent-card.json",
        prompt=case.prompt,
        required_terms=case.required_answer_terms,
        required_sources=case.required_sources,
        require_source_titles=case.require_source_titles,
        required_model=arguments.model,
    )


async def _wait_for_tool_telemetry(
    http: httpx.AsyncClient,
    *,
    application_id: str,
    access_token: str,
    operation_id: str,
    expected_tool_ids: Sequence[str],
    timeout_seconds: float,
    poll_seconds: float,
) -> tuple[ToolTelemetryEvent, ...]:
    if not _OPERATION_ID.fullmatch(operation_id):
        raise ValueError("Application Insights operation ID is invalid.")
    deadline = time.monotonic() + timeout_seconds
    latest: tuple[ToolTelemetryEvent, ...] = ()
    while True:
        messages = await _query_tool_messages(
            http,
            application_id=application_id,
            access_token=access_token,
            operation_id=operation_id,
        )
        latest = parse_tool_telemetry_messages(messages)
        observed = {event.tool_id for event in latest}
        if set(expected_tool_ids).issubset(observed):
            return latest
        if time.monotonic() >= deadline:
            return latest
        await asyncio.sleep(poll_seconds)


async def _query_tool_messages(
    http: httpx.AsyncClient,
    *,
    application_id: str,
    access_token: str,
    operation_id: str,
) -> tuple[str, ...]:
    query = (
        "traces "
        "| where timestamp > ago(1h) "
        f"| where operation_Id == '{operation_id}' "
        '| where message contains \'"event": "agent_iq.tool"\' '
        "| project timestamp, message "
        "| order by timestamp asc"
    )
    response = await http.get(
        f"https://api.applicationinsights.io/v1/apps/{application_id}/query",
        params={"query": query},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise ValueError("Application Insights query returned an invalid response.")
    tables = payload.get("tables")
    if not isinstance(tables, list) or not tables:
        return ()
    table = tables[0]
    if not isinstance(table, Mapping):
        raise ValueError("Application Insights query table is invalid.")
    columns = table.get("columns")
    rows = table.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise ValueError("Application Insights query rows are invalid.")
    names = [column.get("name") if isinstance(column, Mapping) else None for column in columns]
    try:
        message_index = names.index("message")
    except ValueError as exc:
        raise ValueError("Application Insights query omitted the message column.") from exc
    messages: list[str] = []
    for row in rows:
        if not isinstance(row, list) or message_index >= len(row):
            raise ValueError("Application Insights query returned a malformed row.")
        message = row[message_index]
        if not isinstance(message, str):
            raise ValueError("Application Insights telemetry message must be text.")
        messages.append(message)
    return tuple(messages)


def _parse_agent_versions(values: Sequence[str]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for value in values:
        agent_id, separator, version = value.partition("=")
        if (
            separator != "="
            or not agent_id
            or not _AGENT_VERSION.fullmatch(version)
            or agent_id in versions
        ):
            raise ValueError(
                "Each --agent-version must be a unique AGENT_ID=IMMUTABLE_VERSION value."
            )
        versions[agent_id] = version
    return versions


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-endpoint",
        default=DEFAULT_PROJECT,
        required=not bool(DEFAULT_PROJECT),
    )
    parser.add_argument(
        "--account-name",
        default=DEFAULT_ACCOUNT,
        required=not bool(DEFAULT_ACCOUNT),
    )
    parser.add_argument(
        "--project-name",
        default=DEFAULT_PROJECT_NAME,
        required=not bool(DEFAULT_PROJECT_NAME),
    )
    parser.add_argument("--app-insights-application-id", required=True)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument(
        "--agent-version",
        action="append",
        required=True,
        help="Exact immutable target as AGENT_ID=VERSION; repeat for every suite agent.",
    )
    parser.add_argument("--telemetry-wait", type=float, default=180.0)
    parser.add_argument("--telemetry-poll", type=float, default=10.0)
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("reports/evals/hero-tools-live"),
    )
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()
    if arguments.telemetry_wait < 0 or arguments.telemetry_poll <= 0:
        parser.error("Telemetry wait must be nonnegative and poll must be positive.")
    try:
        arguments.agent_versions = _parse_agent_versions(arguments.agent_version)
        expected_agent_ids = {case.agent_id for case in load_live_tool_suite(arguments.cases).cases}
        if set(arguments.agent_versions) != expected_agent_ids:
            raise ValueError(
                "--agent-version values must exactly cover the configured hero tool suite."
            )
    except ValueError as exc:
        parser.error(str(exc))

    payload = asyncio.run(certify(arguments))
    report = write_live_tool_report(payload, arguments.report_dir)
    if arguments.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            "Agent IQ live hero tools: "
            f"{payload['status'].upper()} "
            f"cases={payload['passedCases']}/{payload['cases']} "
            f"report={report.resolve()}"
        )
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
