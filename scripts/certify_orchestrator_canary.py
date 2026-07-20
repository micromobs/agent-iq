"""Remotely certify Agent IQ HTTP/SSE, MCP, and native A2A ingress parity.

Transport certification accepts only an explicit deployment-writer report.  The
report is rejected when it is older than :data:`MAX_DEPLOYMENT_REPORT_AGE`
(currently 30 minutes), and the current Azure Container App must agree with
that report before a transport result can pass.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

import httpx
from a2a.client import A2ACardResolver, Client, ClientConfig, ClientFactory
from a2a.types import AgentCard, Message, Part, Role, SendMessageRequest
from google.protobuf.json_format import MessageToDict
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from agent_iq.api.mcp import MCP_TOOLS
from agent_iq.contracts import (
    CallerContext,
    ExecutionSnapshot,
    MatchFilters,
    MatchRequest,
    OrchestrationCommand,
    OrchestrationPresentation,
    OrchestrationResult,
    TransportProtocol,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_ROOT = ROOT / "reports" / "evals" / "orchestrator-live"
RUN_CERTIFICATE_PREFIX = "orchestrator-canary-"
MAX_RUN_CERTIFICATES = 20
_RUN_CERTIFICATE_NAME = re.compile(
    rf"^{re.escape(RUN_CERTIFICATE_PREFIX)}\d{{8}}T\d{{12}}Z-[0-9a-f]{{32}}\.json$"
)
DEPLOYMENT_REPORT_PREFIX = "orchestrator-canary-run-"
_DEPLOYMENT_REPORT_NAME = re.compile(
    rf"^{re.escape(DEPLOYMENT_REPORT_PREFIX)}\d{{8}}T\d{{13}}Z-[0-9a-f]{{32}}\.json$"
)
# The deployment writer emits a timestamped report immediately after readiness.
# A bounded age prevents an old successful deployment from certifying a newer
# endpoint, while still allowing a normal operator handoff from deploy to probe.
MAX_DEPLOYMENT_REPORT_AGE = timedelta(minutes=30)
MIN_REPLICAS = 1
MAX_REPLICAS = 1
EXPECTED_DEPLOYMENT_OWNERSHIP_TAGS = {
    "agent-iq-owner": "agent-iq",
    "agent-iq-component": "orchestrator",
    "agent-iq-canary": "true",
    "agent-iq-environment": "canary",
    "agent-iq-purpose": "canary",
    "agent-iq-managed-by": "deploy_orchestrator_canary",
}
EXPECTED_DEPLOYMENT_AUTHENTICATION = "anonymous_canary"
EXPECTED_CONTAINER_APP_AUTH_CONFIG = "none"
EXPECTED_DEPLOYMENT_INGRESS = {"mcp", "http_sse", "a2a"}
EXPECTED_CONTAINER_APP_INGRESS = {
    "external": True,
    "targetPort": 8080,
    "transport": "auto",
}
ANONYMOUS_CANARY_ENVIRONMENT_VARIABLE = "AGENT_IQ_ALLOW_ANONYMOUS_CONNECTOR"
PUBLIC_BASE_URL_ENVIRONMENT_VARIABLE = "AGENT_IQ_PUBLIC_BASE_URL"
EXPECTED_A2A_PROTOCOL_BINDING = "JSONRPC"
EXPECTED_A2A_PROTOCOL_VERSION = "1.0"
EXPECTED_A2A_PATH = "/a2a"
_CURRENT_CONTAINER_APP_QUERY = (
    "{name:name,tags:tags,properties:{"
    "latestReadyRevisionName:properties.latestReadyRevisionName,"
    "configuration:{activeRevisionsMode:properties.configuration.activeRevisionsMode,"
    "ingress:{external:properties.configuration.ingress.external,"
    "fqdn:properties.configuration.ingress.fqdn,"
    "targetPort:properties.configuration.ingress.targetPort,"
    "transport:properties.configuration.ingress.transport,"
    "traffic:properties.configuration.ingress.traffic}},"
    "template:{containers:properties.template.containers[].{image:image,env:env},"
    "scale:properties.template.scale}}}"
)
_CURRENT_REVISION_QUERY = (
    "{name:name,properties:{active:properties.active,"
    "healthState:properties.healthState,"
    "provisioningState:properties.provisioningState,"
    "runningState:properties.runningState,"
    "template:{containers:properties.template.containers[].{image:image,env:env}}}}"
)
_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$", re.IGNORECASE)
_IMMUTABLE_IMAGE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:/-]*@(?P<digest>sha256:[0-9a-f]{64})$",
    re.IGNORECASE,
)
_READY_REVISION_RUNNING_STATES = frozenset({"Running", "RunningAtMaxScale"})
_LATEST_WRITE_LOCK = Lock()
INTENT = (
    "What four enzymes power BrightWash Cold, and which fifth enzyme differentiates "
    "ClearWave Cold Clean? Cite both product sources."
)
EXPECTED_AGENT = "consumer-formulation-rd"
ORCHESTRATION_PAYLOAD_BUDGET = 16 * 1024
EXPECTED_TOOLS = set(MCP_TOOLS)


def _validate_base_url(value: str) -> str:
    """Return the canonical HTTPS origin accepted by the canary."""

    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value)
        or "\\" in value
    ):
        raise ValueError("Base URL must be an HTTPS origin without whitespace or backslashes.")
    parsed = urlsplit(value)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "?" in value
        or "#" in value
        or parsed.path not in ("", "/")
    ):
        raise ValueError(
            "Base URL must be an HTTPS origin without credentials, a query, a fragment, or a path."
        )
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Base URL must contain a valid host and port.") from exc
    if not hostname or (parsed.netloc.endswith(":") and port is None):
        raise ValueError("Base URL must contain a valid host and port.")
    canonical_host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        canonical_host = f"{canonical_host}:{port}"
    return f"https://{canonical_host}"


def _base_url_argument(value: str) -> str:
    try:
        return _validate_base_url(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _structured_content(result: CallToolResult, tool_name: str) -> dict[str, Any]:
    if result.isError or not isinstance(result.structuredContent, dict):
        raise RuntimeError(f"The remote MCP {tool_name} tool failed.")
    return result.structuredContent


def _payload_bytes(result: CallToolResult) -> int:
    structured = json.dumps(
        result.structuredContent,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    text = "".join(
        content.text for content in result.content if isinstance(content, TextContent)
    ).encode("utf-8")
    return len(structured) + len(text)


async def _request_without_redirects(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    response = await client.request(
        method,
        url,
        follow_redirects=False,
        **kwargs,
    )
    if 300 <= response.status_code < 400:
        raise httpx.HTTPStatusError(
            "Redirects are forbidden during canary transport certification.",
            request=response.request,
            response=response,
        )
    response.raise_for_status()
    return response


def _trusted_a2a_card(card: AgentCard, *, base_url: str) -> AgentCard:
    """Validate the advertised interface and replace it with a trusted binding."""

    expected_url = f"{_validate_base_url(base_url)}{EXPECTED_A2A_PATH}"
    interfaces = list(card.supported_interfaces)
    if len(interfaces) != 1:
        raise RuntimeError("The A2A Agent Card must advertise exactly one supported interface.")
    interface = interfaces[0]
    if interface.protocol_binding != EXPECTED_A2A_PROTOCOL_BINDING:
        raise RuntimeError("The A2A Agent Card must advertise the JSONRPC protocol binding.")
    if interface.protocol_version != EXPECTED_A2A_PROTOCOL_VERSION:
        raise RuntimeError("The A2A Agent Card must advertise protocol version 1.0.")
    if interface.url != expected_url:
        raise RuntimeError(
            "The A2A Agent Card interface URL must exactly match the certified canonical endpoint."
        )

    try:
        trusted_card = AgentCard()
        trusted_card.CopyFrom(card)
        trusted_card.ClearField("supported_interfaces")
        trusted_interface = trusted_card.supported_interfaces.add()
        trusted_interface.url = expected_url
        trusted_interface.protocol_binding = EXPECTED_A2A_PROTOCOL_BINDING
        trusted_interface.protocol_version = EXPECTED_A2A_PROTOCOL_VERSION
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError("The A2A Agent Card could not be safely reconstructed.") from exc
    return trusted_card


def _create_trusted_a2a_client(
    card: AgentCard,
    *,
    base_url: str,
    httpx_client: httpx.AsyncClient,
) -> Client:
    trusted_card = _trusted_a2a_card(card, base_url=base_url)
    return ClientFactory(
        ClientConfig(
            streaming=False,
            httpx_client=httpx_client,
            supported_protocol_bindings=[EXPECTED_A2A_PROTOCOL_BINDING],
        )
    ).create(trusted_card)


async def certify(arguments: argparse.Namespace) -> dict[str, Any]:
    base_url = _validate_base_url(arguments.base_url)
    resource_group = getattr(arguments, "resource_group", None)
    app_name = getattr(arguments, "app_name", None)
    app_insights = getattr(arguments, "app_insights", None)
    deployment = _deployment_evidence(
        getattr(arguments, "deployment_report", None),
        base_url=base_url,
        resource_group=resource_group,
        app_name=app_name,
        app_insights=app_insights,
    )
    if not deployment["valid"]:
        return _failed_certificate(base_url, deployment)

    command = OrchestrationCommand(
        request=MatchRequest(
            request_id=f"d11-canary-{int(time.time())}",
            intent=INTENT,
            caller=CallerContext(
                subject_id="d11-canary-caller",
                tenant_id="d11-canary-tenant",
            ),
            filters=MatchFilters(
                max_candidates=20,
                max_selected=1,
                required_protocols=(TransportProtocol.A2A,),
            ),
            requested_at=datetime.now(UTC),
        )
    )
    payload = command.model_dump(mode="json", by_alias=True)

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(360.0),
        follow_redirects=False,
    ) as http:
        health = await _request_without_redirects(http, "GET", f"{base_url}/healthz")
        ready = await _request_without_redirects(http, "GET", f"{base_url}/readyz")
        capabilities = await _request_without_redirects(
            http,
            "GET",
            f"{base_url}/v1/capabilities",
        )

        http_response = await _request_without_redirects(
            http,
            "POST",
            f"{base_url}/v1/orchestrate",
            json=payload,
        )
        http_result = OrchestrationResult.model_validate(http_response.json())

        sse_response = await _request_without_redirects(
            http,
            "GET",
            f"{base_url}/v1/executions/{http_result.summary.execution_id}/events",
        )
        sse_types = tuple(
            line.removeprefix("event: ")
            for line in sse_response.text.splitlines()
            if line.startswith("event: ")
        )
        evidence_id = http_result.summary.synthesis.evidence.records[0].evidence_id
        evidence_response = await _request_without_redirects(
            http,
            "GET",
            (f"{base_url}/v1/executions/{http_result.summary.execution_id}/evidence/{evidence_id}"),
            headers={
                "X-Agent-IQ-Subject": command.request.caller.subject_id,
                "X-Agent-IQ-Tenant": command.request.caller.tenant_id,
            },
        )

    mcp_progress: list[dict[str, Any]] = []
    async with (
        streamable_http_client(f"{base_url}/mcp") as (
            read_stream,
            write_stream,
            _,
        ),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        listed = await session.list_tools()
        mcp_tools = {tool.name for tool in listed.tools}
        mcp_started = time.perf_counter()

        async def progress(
            progress: float,
            total: float | None,
            message: str | None,
        ) -> None:
            if message is not None:
                mcp_progress.append(
                    {
                        "progress": progress,
                        "total": total,
                        "message": message,
                        "elapsedMs": int((time.perf_counter() - mcp_started) * 1_000),
                    }
                )

        mcp_call = await session.call_tool(
            "orchestrate",
            {"command": payload},
            progress_callback=progress,
        )
        mcp_elapsed_ms = int((time.perf_counter() - mcp_started) * 1_000)
        mcp_presentation = OrchestrationPresentation.model_validate(
            _structured_content(mcp_call, "orchestration")
        )
        mcp_payload_bytes = _payload_bytes(mcp_call)
        execution_call = await session.call_tool(
            "get_execution",
            {"execution_id": mcp_presentation.execution_id},
        )
        mcp_snapshot = ExecutionSnapshot.model_validate(
            _structured_content(execution_call, "get_execution")
        )
        if mcp_snapshot.summary is None:
            raise RuntimeError("The completed MCP execution is missing its audit summary.")
        mcp_result = OrchestrationResult(
            summary=mcp_snapshot.summary,
            events=mcp_snapshot.events,
        )

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(360.0),
        follow_redirects=False,
    ) as a2a_http:
        card = await A2ACardResolver(a2a_http, base_url).get_agent_card()
        a2a_client = _create_trusted_a2a_client(
            card,
            base_url=base_url,
            httpx_client=a2a_http,
        )
        responses = [
            response
            async for response in a2a_client.send_message(
                SendMessageRequest(
                    message=Message(
                        message_id=f"a2a-{command.request.request_id}",
                        role=Role.ROLE_USER,
                        parts=(Part(text=command.request.intent),),
                        metadata={"agentIqCommand": payload},
                    )
                )
            )
        ]
        await a2a_client.close()
    if len(responses) != 1 or not responses[0].HasField("message"):
        raise RuntimeError("The remote A2A coordinator returned an invalid response.")
    a2a_message = MessageToDict(responses[0].message)
    a2a_result = OrchestrationResult.model_validate(a2a_message["parts"][1]["data"])

    results = {
        "http_sse": http_result,
        "mcp": mcp_result,
        "a2a": a2a_result,
    }
    normalized = {name: _normalized(result) for name, result in results.items()}
    execution_ids = tuple(result.summary.execution_id for result in results.values())
    telemetry = (
        _wait_for_telemetry(
            str(resource_group),
            str(app_insights or deployment["report"]["applicationInsights"]),
            execution_ids,
            attempts=getattr(arguments, "telemetry_attempts", 20),
            delay_seconds=getattr(arguments, "telemetry_delay", 15),
        )
        if resource_group and (app_insights or deployment["report"]["applicationInsights"])
        else []
    )
    telemetry_ids = {str(row.get("executionId")) for row in telemetry if row.get("executionId")}
    mcp_messages = tuple(str(event["message"]) for event in mcp_progress)
    mcp_progress_values = tuple(float(event["progress"]) for event in mcp_progress)
    expected_progress = (
        "Matching eligible specialists",
        "Selected ",
        "Retrieving approved evidence",
        "Building the cited answer",
        "Completed",
    )
    progress_index = 0
    for message in mcp_messages:
        if (
            progress_index < len(expected_progress)
            and expected_progress[progress_index].casefold() in message.casefold()
        ):
            progress_index += 1
    progress_text = " ".join(mcp_messages).casefold()
    checks = {
        "health": health.json().get("status") == "healthy",
        "readiness": ready.json().get("status") == "ready",
        "capabilities": set(capabilities.json()["ingressTransports"]) == {"mcp", "http_sse", "a2a"},
        "mcpToolContract": mcp_tools == EXPECTED_TOOLS,
        "mcpProgress": (
            progress_index == len(expected_progress)
            and bool(mcp_progress_values)
            and list(mcp_progress_values) == sorted(mcp_progress_values)
            and all(event["total"] == 100 for event in mcp_progress)
            and not any(
                jargon in progress_text
                for jargon in (
                    "request.accepted",
                    "catalog.searched",
                    "candidates.ranked",
                    "glob",
                    ".json",
                    "spilled",
                )
            )
        ),
        "mcpFirstProgressLatency": (
            bool(mcp_progress) and int(mcp_progress[0]["elapsedMs"]) <= 1_000
        ),
        "mcpPayloadBudget": mcp_payload_bytes <= ORCHESTRATION_PAYLOAD_BUDGET,
        "mcpProjectionParity": (
            mcp_presentation.execution_id == mcp_result.summary.execution_id
            and mcp_presentation.status is mcp_result.summary.status
            and tuple(agent.agent_id for agent in mcp_presentation.selected_agents)
            == tuple(reason.agent_id for reason in mcp_result.summary.selection.selected)
            and mcp_presentation.answer == mcp_result.summary.synthesis.answer
            and tuple(citation.source_id for citation in mcp_presentation.citations)
            == tuple(record.source_id for record in mcp_result.summary.synthesis.evidence.records)
        ),
        "sseLifecycle": (
            bool(sse_types)
            and sse_types[0] == "request.accepted"
            and sse_types[-1] == "execution.completed"
        ),
        "identityBoundEvidence": evidence_response.json()["evidenceId"] == evidence_id,
        "transportParity": _transport_parity(
            normalized,
            (
                *(
                    tuple(reason.agent_id for reason in result.summary.selection.selected)
                    for result in results.values()
                ),
                tuple(agent.agent_id for agent in mcp_presentation.selected_agents),
            ),
        ),
        "nativeA2ASouthbound": all(
            step.protocol_version == "1.0" and step.transport is TransportProtocol.A2A
            for result in results.values()
            for step in result.summary.steps
        ),
        "groundingComplete": all(
            result.summary.synthesis.coverage.ratio == 1 for result in results.values()
        ),
        "telemetryCorrelated": (
            bool(resource_group)
            and bool(app_insights or deployment["report"]["applicationInsights"])
            and set(execution_ids).issubset(telemetry_ids)
        ),
    }
    checks["deploymentEvidence"] = bool(deployment["valid"])
    failures = [name for name, passed in checks.items() if not passed]
    return {
        "schemaVersion": "1.0",
        "generatedAt": datetime.now(UTC).isoformat(),
        "status": "pass" if not failures else "fail",
        "baseUrl": base_url,
        "checks": checks,
        "failures": failures,
        "transports": {
            name: {
                "executionId": result.summary.execution_id,
                "status": result.summary.status.value,
                "selectedAgentIds": [
                    reason.agent_id for reason in result.summary.selection.selected
                ],
                "stepCount": len(result.summary.steps),
                "evidenceCount": sum(len(step.evidence_ids) for step in result.summary.steps),
                "eventCount": len(result.events),
                "answerLength": len(result.summary.synthesis.answer),
                "answerSha256": (
                    "sha256:"
                    + hashlib.sha256(result.summary.synthesis.answer.encode("utf-8")).hexdigest()
                ),
            }
            for name, result in results.items()
        },
        "mcpPresentation": {
            "executionId": mcp_presentation.execution_id,
            "selectedAgentIds": [agent.agent_id for agent in mcp_presentation.selected_agents],
            "progressEvents": mcp_progress,
            "firstProgressMs": (int(mcp_progress[0]["elapsedMs"]) if mcp_progress else None),
            "completionMs": mcp_elapsed_ms,
            "payloadBytes": mcp_payload_bytes,
            "payloadBudgetBytes": ORCHESTRATION_PAYLOAD_BUDGET,
            "fullAuditPayloadBytes": _payload_bytes(execution_call),
            "detailsAvailableVia": mcp_presentation.details_available_via,
        },
        "telemetry": {
            "rowCount": len(telemetry),
            "executionIds": sorted(telemetry_ids),
            "operationIds": sorted(
                {str(row.get("operation_Id")) for row in telemetry if row.get("operation_Id")}
            ),
        },
        "deployment": deployment,
    }


def _normalized(result: OrchestrationResult) -> dict[str, Any]:
    return {
        "selected": [reason.agent_id for reason in result.summary.selection.selected],
        "dag": [
            {"agentId": step.agent_id, "dependsOn": list(step.depends_on)}
            for step in result.summary.plan.steps
        ],
        "steps": [
            {
                "agentId": step.agent_id,
                "status": step.status.value,
                "transport": step.transport.value,
                "protocolVersion": step.protocol_version,
                "grounded": bool(step.evidence_ids),
            }
            for step in result.summary.steps
        ],
        "coverage": result.summary.synthesis.coverage.model_dump(mode="json"),
        "events": [event.payload.type for event in result.events],
    }


def _transport_parity(
    normalized: dict[str, dict[str, Any]],
    selected_agent_ids: tuple[tuple[str, ...], ...],
) -> bool:
    return (
        all(agent_ids == (EXPECTED_AGENT,) for agent_ids in selected_agent_ids)
        and len({json.dumps(value, sort_keys=True) for value in normalized.values()}) == 1
    )


def _wait_for_telemetry(
    resource_group: str,
    app_insights: str,
    execution_ids: tuple[str, ...],
    *,
    attempts: int,
    delay_seconds: int,
) -> list[dict[str, Any]]:
    az = shutil.which("az")
    if az is None:
        raise RuntimeError("Azure CLI is required for telemetry certification.")
    quoted_ids = ", ".join(f'"{execution_id}"' for execution_id in execution_ids)
    query = (
        "union withsource=tableName requests, dependencies, traces "
        "| where timestamp > ago(2h) "
        '| extend executionId=tostring(customDimensions["agent_iq.execution.id"]) '
        f"| where executionId in ({quoted_ids}) "
        "| project tableName, timestamp, name, operation_Id, executionId"
    )
    for attempt in range(attempts):
        completed = subprocess.run(
            [
                az,
                "monitor",
                "app-insights",
                "query",
                "--app",
                app_insights,
                "--resource-group",
                resource_group,
                "--analytics-query",
                query,
                "--output",
                "json",
                "--only-show-errors",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
        )
        if completed.returncode != 0:
            if attempt + 1 == attempts:
                raise RuntimeError("Application Insights telemetry query failed.")
        else:
            rows = _query_rows(json.loads(completed.stdout))
            observed = {str(row.get("executionId")) for row in rows if row.get("executionId")}
            if set(execution_ids).issubset(observed):
                return rows
        time.sleep(delay_seconds)
    return []


def _query_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    tables = payload.get("tables")
    if not isinstance(tables, list) or not tables:
        return []
    table = tables[0]
    columns = [column["name"] for column in table.get("columns", [])]
    return [
        dict(zip(columns, row, strict=True))
        for row in table.get("rows", [])
        if len(row) == len(columns)
    ]


_DEPLOYMENT_REPORT_CHECKS = (
    "schemaVersion",
    "status",
    "freshness",
    "appName",
    "resourceGroup",
    "fqdn",
    "revision",
    "immutableImage",
    "imageDigest",
    "ownershipTags",
    "authentication",
    "containerAppAuthConfig",
    "ingress",
    "containerAppIngress",
    "replicaBounds",
    "applicationInsights",
)
_CURRENT_STATE_CHECKS = (
    "currentState",
    "currentAppName",
    "currentRevision",
    "currentIngressFqdn",
    "currentActiveRevisionsMode",
    "currentTraffic",
    "currentTemplateImageDigest",
    "currentRevisionState",
    "currentRevisionImageDigest",
    "currentImageDigest",
    "currentOwnershipTags",
    "currentIngressExternal",
    "currentIngressTargetPort",
    "currentIngressTransport",
    "currentAuthentication",
    "currentPublicBaseUrl",
    "currentRevisionPublicBaseUrl",
    "currentReplicaBounds",
)


def _deployment_report_path_argument(value: str) -> Path:
    """Validate the deployment writer's run-specific filename before I/O."""

    path = Path(value)
    if (
        not value
        or path.name == "latest.json"
        or _DEPLOYMENT_REPORT_NAME.fullmatch(path.name) is None
    ):
        raise argparse.ArgumentTypeError(
            "Deployment report must be an explicit orchestrator-canary-run-*.json file."
        )
    return path


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


def _parse_utc_timestamp(value: Any) -> datetime | None:
    if not _is_nonempty_string(value):
        return None
    timestamp = str(value)
    if timestamp.endswith("Z"):
        timestamp = f"{timestamp[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _json_object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key.")
        result[key] = value
    return result


def _canonical_current_fqdn(value: Any) -> str | None:
    if not _is_nonempty_string(value):
        return None
    candidate = str(value)
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    try:
        return _validate_base_url(candidate)
    except ValueError:
        return None


def _deployment_report_projection(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist only known deployment metadata, never arbitrary report content."""

    keys = (
        "schemaVersion",
        "generatedAt",
        "status",
        "appName",
        "resourceGroup",
        "fqdn",
        "revision",
        "image",
        "imageTag",
        "imageTagReference",
        "imageDigest",
        "ownershipTags",
        "managedIdentityObjectId",
        "ingress",
        "containerAppIngress",
        "authentication",
        "containerAppAuthConfig",
        "applicationInsights",
        "minReplicas",
        "maxReplicas",
    )
    return {key: payload[key] for key in keys if key in payload}


def _validate_deployment_report(
    payload: dict[str, Any],
    *,
    base_url: str | None,
    resource_group: str | None,
    app_name: str | None,
    app_insights: str | None,
    now: datetime,
) -> dict[str, bool]:
    checks = {name: False for name in _DEPLOYMENT_REPORT_CHECKS}
    checks["schemaVersion"] = payload.get("schemaVersion") == "1.0"
    checks["status"] = payload.get("status") == "pass"

    generated_at = _parse_utc_timestamp(payload.get("generatedAt"))
    if generated_at is not None:
        reference_now = now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)
        age = reference_now - generated_at
        checks["freshness"] = timedelta(0) <= age <= MAX_DEPLOYMENT_REPORT_AGE

    report_app_name = payload.get("appName")
    checks["appName"] = _is_nonempty_string(app_name) and report_app_name == app_name
    report_resource_group = payload.get("resourceGroup")
    checks["resourceGroup"] = (
        _is_nonempty_string(resource_group) and report_resource_group == resource_group
    )

    report_fqdn = payload.get("fqdn")
    canonical_report_fqdn = _canonical_current_fqdn(report_fqdn)
    checks["fqdn"] = canonical_report_fqdn is not None and canonical_report_fqdn == base_url

    revision = payload.get("revision")
    checks["revision"] = _is_nonempty_string(revision)

    image = payload.get("image")
    image_match = _IMMUTABLE_IMAGE.fullmatch(image) if isinstance(image, str) else None
    checks["immutableImage"] = image_match is not None
    image_digest = payload.get("imageDigest")
    checks["imageDigest"] = (
        isinstance(image_digest, str)
        and _SHA256_DIGEST.fullmatch(image_digest) is not None
        and image_match is not None
        and image_digest.casefold() == image_match.group("digest").casefold()
    )

    ownership_tags = payload.get("ownershipTags")
    checks["ownershipTags"] = (
        isinstance(ownership_tags, dict) and ownership_tags == EXPECTED_DEPLOYMENT_OWNERSHIP_TAGS
    )
    ingress = payload.get("ingress")
    checks["ingress"] = (
        isinstance(ingress, list)
        and all(isinstance(item, str) for item in ingress)
        and len(ingress) == len(EXPECTED_DEPLOYMENT_INGRESS)
        and set(ingress) == EXPECTED_DEPLOYMENT_INGRESS
    )
    container_app_ingress = payload.get("containerAppIngress")
    checks["containerAppIngress"] = (
        isinstance(container_app_ingress, dict)
        and container_app_ingress == EXPECTED_CONTAINER_APP_INGRESS
    )
    checks["authentication"] = payload.get("authentication") == EXPECTED_DEPLOYMENT_AUTHENTICATION
    checks["containerAppAuthConfig"] = (
        payload.get("containerAppAuthConfig") == EXPECTED_CONTAINER_APP_AUTH_CONFIG
    )

    min_replicas = payload.get("minReplicas")
    max_replicas = payload.get("maxReplicas")
    checks["replicaBounds"] = (
        isinstance(min_replicas, int)
        and not isinstance(min_replicas, bool)
        and isinstance(max_replicas, int)
        and not isinstance(max_replicas, bool)
        and min_replicas == MIN_REPLICAS
        and max_replicas == MAX_REPLICAS
    )

    application_insights = payload.get("applicationInsights")
    checks["applicationInsights"] = _is_nonempty_string(application_insights) and (
        app_insights is None or application_insights == app_insights
    )
    return checks


def _query_current_container_app(resource_group: str, app_name: str) -> dict[str, Any]:
    """Read the current Container App through Azure CLI without mutating it."""

    az = shutil.which("az")
    if az is None:
        raise RuntimeError("Azure CLI is required for deployment evidence certification.")
    completed = subprocess.run(
        [
            az,
            "containerapp",
            "show",
            "--name",
            app_name,
            "--resource-group",
            resource_group,
            "--query",
            _CURRENT_CONTAINER_APP_QUERY,
            "--output",
            "json",
            "--only-show-errors",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if completed.returncode != 0:
        raise RuntimeError("Azure CLI could not read the current Container App.")
    try:
        payload = json.loads(
            completed.stdout.lstrip("\ufeff"),
            object_pairs_hook=_json_object_no_duplicates,
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Azure CLI returned malformed Container App state.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Azure CLI returned no Container App state.")
    return payload


def _query_current_container_app_auth(resource_group: str, app_name: str) -> dict[str, Any]:
    """Read the current Container Apps authentication child resource."""

    az = shutil.which("az")
    if az is None:
        raise RuntimeError("Azure CLI is required for deployment evidence certification.")
    completed = subprocess.run(
        [
            az,
            "containerapp",
            "auth",
            "show",
            "--name",
            app_name,
            "--resource-group",
            resource_group,
            "--output",
            "json",
            "--only-show-errors",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if completed.returncode != 0:
        raise RuntimeError("Azure CLI could not read the current Container App authentication.")
    try:
        payload = json.loads(
            completed.stdout.lstrip("\ufeff"),
            object_pairs_hook=_json_object_no_duplicates,
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "Azure CLI returned malformed Container App authentication state."
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Azure CLI returned no Container App authentication state.")
    return payload


def _query_current_container_app_revision(
    resource_group: str,
    app_name: str,
    revision: str,
) -> dict[str, Any]:
    """Read one named Container App revision without following a mutable alias."""

    az = shutil.which("az")
    if az is None:
        raise RuntimeError("Azure CLI is required for deployment evidence certification.")
    completed = subprocess.run(
        [
            az,
            "containerapp",
            "revision",
            "show",
            "--name",
            app_name,
            "--resource-group",
            resource_group,
            "--revision",
            revision,
            "--query",
            _CURRENT_REVISION_QUERY,
            "--output",
            "json",
            "--only-show-errors",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if completed.returncode != 0:
        raise RuntimeError("Azure CLI could not read the reported Container App revision.")
    try:
        payload = json.loads(
            completed.stdout.lstrip("\ufeff"),
            object_pairs_hook=_json_object_no_duplicates,
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Azure CLI returned malformed Container App revision state.") from exc
    if not isinstance(payload, dict) or not payload:
        raise RuntimeError("Azure CLI returned no Container App revision state.")
    return payload


def _nested_value(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _environment_variable_value(environment: Any, name: str) -> str | None:
    values = (
        [
            entry.get("value")
            for entry in environment
            if isinstance(entry, dict) and entry.get("name") == name
        ]
        if isinstance(environment, list)
        else []
    )
    if len(values) != 1 or not _is_nonempty_string(values[0]):
        return None
    return str(values[0])


def _current_container_app_projection(
    payload: dict[str, Any],
    auth_config: dict[str, Any] | None,
) -> dict[str, Any]:
    containers = _nested_value(payload, "properties", "template", "containers")
    container = containers[0] if isinstance(containers, list) and len(containers) == 1 else None
    image = container.get("image") if isinstance(container, dict) else None
    environment = container.get("env") if isinstance(container, dict) else None
    anonymous_values = (
        [
            entry.get("value")
            for entry in environment
            if isinstance(entry, dict)
            and entry.get("name") == ANONYMOUS_CANARY_ENVIRONMENT_VARIABLE
        ]
        if isinstance(environment, list)
        else []
    )
    anonymous_canary = anonymous_values == ["true"]
    no_auth_config = auth_config == {} if isinstance(auth_config, dict) else False
    ingress = _nested_value(payload, "properties", "configuration", "ingress")
    transport = ingress.get("transport") if isinstance(ingress, dict) else None
    canonical_transport = (
        transport.casefold()
        if isinstance(transport, str) and _is_nonempty_string(transport)
        else None
    )
    scale = _nested_value(payload, "properties", "template", "scale")
    active_revisions_mode = _nested_value(
        payload,
        "properties",
        "configuration",
        "activeRevisionsMode",
    )
    canonical_active_revisions_mode = (
        active_revisions_mode.casefold()
        if isinstance(active_revisions_mode, str) and _is_nonempty_string(active_revisions_mode)
        else None
    )
    return {
        "appName": payload.get("name"),
        "ownershipTags": payload.get("tags"),
        "revision": _nested_value(payload, "properties", "latestReadyRevisionName"),
        "fqdn": ingress.get("fqdn") if isinstance(ingress, dict) else None,
        "image": image,
        "imageDigest": _deployed_image_digest(image),
        "activeRevisionsMode": canonical_active_revisions_mode,
        "traffic": ingress.get("traffic") if isinstance(ingress, dict) else None,
        "containerAppIngress": {
            "external": ingress.get("external") if isinstance(ingress, dict) else None,
            "targetPort": ingress.get("targetPort") if isinstance(ingress, dict) else None,
            "transport": canonical_transport,
        },
        "authentication": (
            EXPECTED_DEPLOYMENT_AUTHENTICATION if anonymous_canary and no_auth_config else None
        ),
        "containerAppAuthConfig": (EXPECTED_CONTAINER_APP_AUTH_CONFIG if no_auth_config else None),
        "anonymousCanary": anonymous_canary,
        "authConfigAbsent": no_auth_config,
        "publicBaseUrl": _environment_variable_value(
            environment,
            PUBLIC_BASE_URL_ENVIRONMENT_VARIABLE,
        ),
        "minReplicas": scale.get("minReplicas") if isinstance(scale, dict) else None,
        "maxReplicas": scale.get("maxReplicas") if isinstance(scale, dict) else None,
    }


def _current_revision_projection(payload: dict[str, Any]) -> dict[str, Any]:
    properties = payload.get("properties")
    containers = _nested_value(payload, "properties", "template", "containers")
    container = containers[0] if isinstance(containers, list) and len(containers) == 1 else None
    image = container.get("image") if isinstance(container, dict) else None
    environment = container.get("env") if isinstance(container, dict) else None
    return {
        "revision": payload.get("name"),
        "active": properties.get("active") if isinstance(properties, dict) else None,
        "healthState": properties.get("healthState") if isinstance(properties, dict) else None,
        "provisioningState": (
            properties.get("provisioningState") if isinstance(properties, dict) else None
        ),
        "runningState": properties.get("runningState") if isinstance(properties, dict) else None,
        "image": image,
        "imageDigest": _deployed_image_digest(image),
        "publicBaseUrl": _environment_variable_value(
            environment,
            PUBLIC_BASE_URL_ENVIRONMENT_VARIABLE,
        ),
    }


def _traffic_routes_exclusively_to_revision(
    traffic: Any,
    *,
    reported_revision: Any,
    latest_ready_revision: Any,
) -> bool:
    """Resolve one Azure traffic snapshot without aggregating aliases or duplicates."""

    if (
        not isinstance(traffic, list)
        or not traffic
        or not _is_nonempty_string(reported_revision)
        or not _is_nonempty_string(latest_ready_revision)
    ):
        return False
    resolved: dict[str, tuple[int, str | None]] = {}
    labels: set[str] = set()
    total_weight = 0
    for allocation in traffic:
        if not isinstance(allocation, dict):
            return False
        weight = allocation.get("weight")
        if not isinstance(weight, int) or isinstance(weight, bool) or weight < 0 or weight > 100:
            return False
        latest_revision = allocation.get("latestRevision")
        if latest_revision is not None and not isinstance(latest_revision, bool):
            return False
        revision_name = allocation.get("revisionName")
        if revision_name is not None and not _is_nonempty_string(revision_name):
            return False
        label = allocation.get("label")
        if label is not None:
            if not _is_nonempty_string(label) or str(label) in labels:
                return False
            labels.add(str(label))
        if latest_revision is True:
            if revision_name is not None and revision_name != latest_ready_revision:
                return False
            resolved_revision = str(latest_ready_revision)
        else:
            if not _is_nonempty_string(revision_name):
                return False
            resolved_revision = str(revision_name)
        if resolved_revision in resolved:
            return False
        resolved[resolved_revision] = (weight, str(label) if label is not None else None)
        total_weight += weight

    reported_allocation = resolved.get(str(reported_revision))
    if total_weight != 100 or reported_allocation is None or reported_allocation[0] != 100:
        return False
    return all(
        revision == reported_revision or (weight == 0 and label is None)
        for revision, (weight, label) in resolved.items()
    )


def _deployed_image_digest(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _IMMUTABLE_IMAGE.fullmatch(value)
    return match.group("digest").casefold() if match is not None else None


def _deployment_evidence(
    report_path: Path | str | None = None,
    *,
    base_url: str | None = None,
    resource_group: str | None = None,
    app_name: str | None = None,
    app_insights: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate one explicit deployment report and the current Azure state.

    This intentionally has no default report path.  In particular, the
    deployment writer's ``latest.json`` is never an acceptable certificate
    input because it does not identify the deployment run being certified.
    """

    checks = {
        "reportPath": False,
        "reportParse": False,
        **{name: False for name in _DEPLOYMENT_REPORT_CHECKS},
        **{name: False for name in _CURRENT_STATE_CHECKS},
    }
    report: dict[str, Any] = {}
    report_sha256: str | None = None
    current: dict[str, Any] = {}
    current_revision: dict[str, Any] = {}
    try:
        canonical_base_url = _validate_base_url(base_url) if isinstance(base_url, str) else None
    except ValueError:
        canonical_base_url = None
    try:
        path = Path(report_path) if report_path is not None else None
    except (TypeError, ValueError):
        path = None
    if (
        path is not None
        and path.name != "latest.json"
        and _DEPLOYMENT_REPORT_NAME.fullmatch(path.name) is not None
    ):
        try:
            is_safe_file = path.is_file() and not path.is_symlink()
        except OSError:
            is_safe_file = False
        if is_safe_file:
            checks["reportPath"] = True
            try:
                report_bytes = path.read_bytes()
                parsed_report = json.loads(
                    report_bytes.decode("utf-8-sig"),
                    object_pairs_hook=_json_object_no_duplicates,
                )
            except (OSError, UnicodeError, ValueError):
                parsed_report = None
            if isinstance(parsed_report, dict):
                checks["reportParse"] = True
                report_sha256 = f"sha256:{hashlib.sha256(report_bytes).hexdigest()}"
                report = _deployment_report_projection(parsed_report)
                checks.update(
                    _validate_deployment_report(
                        parsed_report,
                        base_url=canonical_base_url,
                        resource_group=resource_group,
                        app_name=app_name,
                        app_insights=app_insights,
                        now=now or datetime.now(UTC),
                    )
                )

    if (
        all(checks[name] for name in _DEPLOYMENT_REPORT_CHECKS)
        and checks["reportParse"]
        and _is_nonempty_string(resource_group)
        and _is_nonempty_string(app_name)
    ):
        try:
            current_payload = _query_current_container_app(
                str(resource_group),
                str(app_name),
            )
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
            current_payload = None
        if isinstance(current_payload, dict):
            try:
                current_auth_config = _query_current_container_app_auth(
                    str(resource_group),
                    str(app_name),
                )
            except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
                current_auth_config = None
            current = _current_container_app_projection(
                current_payload,
                current_auth_config,
            )
            report_revision = report.get("revision")
            try:
                current_revision_payload = _query_current_container_app_revision(
                    str(resource_group),
                    str(app_name),
                    str(report_revision),
                )
            except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
                current_revision_payload = None
            if isinstance(current_revision_payload, dict):
                current_revision = _current_revision_projection(current_revision_payload)
            report_fqdn = _canonical_current_fqdn(report.get("fqdn"))
            report_digest = (
                str(report.get("imageDigest")).casefold()
                if isinstance(report.get("imageDigest"), str)
                else None
            )
            current_fqdn = _canonical_current_fqdn(current.get("fqdn"))
            checks["currentAppName"] = current.get("appName") == app_name
            checks["currentRevision"] = (
                _is_nonempty_string(current.get("revision"))
                and current.get("revision") == report_revision
            )
            checks["currentIngressFqdn"] = (
                current_fqdn is not None and report_fqdn is not None and current_fqdn == report_fqdn
            )
            checks["currentActiveRevisionsMode"] = current.get("activeRevisionsMode") in {
                "single",
                "multiple",
            }
            checks["currentTraffic"] = _traffic_routes_exclusively_to_revision(
                current.get("traffic"),
                reported_revision=report_revision,
                latest_ready_revision=current.get("revision"),
            )
            checks["currentTemplateImageDigest"] = (
                report_digest is not None and current.get("imageDigest") == report_digest
            )
            checks["currentRevisionState"] = (
                current_revision.get("revision") == report_revision
                and current_revision.get("active") is True
                and current_revision.get("healthState") == "Healthy"
                and current_revision.get("provisioningState") == "Provisioned"
                and current_revision.get("runningState") in _READY_REVISION_RUNNING_STATES
            )
            checks["currentRevisionImageDigest"] = (
                report_digest is not None
                and current_revision.get("imageDigest") == report_digest
                and current_revision.get("image") == report.get("image")
            )
            checks["currentImageDigest"] = (
                checks["currentTemplateImageDigest"] and checks["currentRevisionImageDigest"]
            )
            report_ownership_tags = report.get("ownershipTags")
            checks["currentOwnershipTags"] = (
                isinstance(current.get("ownershipTags"), dict)
                and current.get("ownershipTags") == report_ownership_tags
                and current.get("ownershipTags") == EXPECTED_DEPLOYMENT_OWNERSHIP_TAGS
            )
            report_ingress = report.get("containerAppIngress")
            current_ingress = current.get("containerAppIngress")
            checks["currentIngressExternal"] = (
                isinstance(current_ingress, dict)
                and isinstance(report_ingress, dict)
                and current_ingress.get("external") is True
                and current_ingress.get("external") == report_ingress.get("external")
                and current_ingress.get("external") == EXPECTED_CONTAINER_APP_INGRESS["external"]
            )
            checks["currentIngressTargetPort"] = (
                isinstance(current_ingress, dict)
                and isinstance(report_ingress, dict)
                and isinstance(current_ingress.get("targetPort"), int)
                and not isinstance(current_ingress.get("targetPort"), bool)
                and current_ingress.get("targetPort") == report_ingress.get("targetPort")
                and current_ingress.get("targetPort")
                == EXPECTED_CONTAINER_APP_INGRESS["targetPort"]
            )
            checks["currentIngressTransport"] = (
                isinstance(current_ingress, dict)
                and isinstance(report_ingress, dict)
                and current_ingress.get("transport") == report_ingress.get("transport")
                and current_ingress.get("transport") == EXPECTED_CONTAINER_APP_INGRESS["transport"]
            )
            checks["currentAuthentication"] = (
                current.get("authentication") == report.get("authentication")
                and current.get("authentication") == EXPECTED_DEPLOYMENT_AUTHENTICATION
                and current.get("containerAppAuthConfig") == report.get("containerAppAuthConfig")
                and current.get("containerAppAuthConfig") == EXPECTED_CONTAINER_APP_AUTH_CONFIG
                and current.get("anonymousCanary") is True
                and current.get("authConfigAbsent") is True
            )
            checks["currentPublicBaseUrl"] = (
                canonical_base_url is not None
                and current.get("publicBaseUrl") == canonical_base_url
            )
            checks["currentRevisionPublicBaseUrl"] = (
                canonical_base_url is not None
                and current_revision.get("publicBaseUrl") == canonical_base_url
                and current_revision.get("publicBaseUrl") == current.get("publicBaseUrl")
            )
            current_min_replicas = current.get("minReplicas")
            current_max_replicas = current.get("maxReplicas")
            checks["currentReplicaBounds"] = (
                isinstance(current_min_replicas, int)
                and not isinstance(current_min_replicas, bool)
                and isinstance(current_max_replicas, int)
                and not isinstance(current_max_replicas, bool)
                and current_min_replicas == report.get("minReplicas")
                and current_max_replicas == report.get("maxReplicas")
                and current_min_replicas == MIN_REPLICAS
                and current_max_replicas == MAX_REPLICAS
            )
            checks["currentState"] = all(
                checks[name] for name in _CURRENT_STATE_CHECKS if name != "currentState"
            )

    valid = all(checks.values())
    return {
        "valid": valid,
        "reportPath": str(path) if path is not None else None,
        "reportSha256": report_sha256,
        "checks": checks,
        "failures": [name for name, passed in checks.items() if not passed],
        "report": report,
        "current": current,
        "currentRevision": current_revision,
    }


def certify_deployment_evidence(
    report_path: Path | str | None,
    *,
    base_url: str,
    resource_group: str,
    app_name: str,
    app_insights: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Public, reusable entry point for fail-closed Container App certification."""

    return _deployment_evidence(
        report_path,
        base_url=base_url,
        resource_group=resource_group,
        app_name=app_name,
        app_insights=app_insights,
        now=now,
    )


def _failed_certificate(base_url: str, deployment: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": "1.0",
        "generatedAt": datetime.now(UTC).isoformat(),
        "status": "fail",
        "baseUrl": base_url,
        "checks": {"deploymentEvidence": False},
        "failures": ["deploymentEvidence"],
        "transports": {},
        "mcpPresentation": {},
        "telemetry": {"rowCount": 0, "executionIds": [], "operationIds": []},
        "deployment": deployment,
    }


def _write_report(report: dict[str, Any], output_root: Path) -> Path:
    safe_report = dict(report)
    if "baseUrl" in safe_report:
        base_url = safe_report["baseUrl"]
        if not isinstance(base_url, str):
            raise ValueError("Persisted base URL must be a string.")
        safe_report["baseUrl"] = _validate_base_url(base_url)
    text = json.dumps(safe_report, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    output_root.mkdir(parents=True, exist_ok=True)
    path = _new_run_path(output_root)
    _atomic_write_text(path, text)
    with _LATEST_WRITE_LOCK:
        _atomic_write_text(output_root / "latest.json", text)
    _prune_run_reports(output_root)
    return path


def _new_run_path(output_root: Path) -> Path:
    for _ in range(100):
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        candidate = output_root / (f"{RUN_CERTIFICATE_PREFIX}{stamp}-{uuid.uuid4().hex}.json")
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not allocate a unique orchestrator canary report name.")


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
        temporary_path = temporary_name
        assert temporary_path is not None
        for attempt in range(10):
            try:
                os.replace(temporary_path, path)
                temporary_name = None
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.01)
    finally:
        if temporary_name is not None:
            with suppress(FileNotFoundError):
                os.unlink(temporary_name)


def _prune_run_reports(output_root: Path) -> None:
    run_paths = sorted(
        (
            path
            for path in output_root.iterdir()
            if path.is_file() and _RUN_CERTIFICATE_NAME.fullmatch(path.name)
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    for path in run_paths[MAX_RUN_CERTIFICATES:]:
        with suppress(FileNotFoundError):
            path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True, type=_base_url_argument)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--app-name", required=True)
    parser.add_argument(
        "--deployment-report",
        required=True,
        type=_deployment_report_path_argument,
        help=(
            "Explicit deployment-writer report path; only a fresh "
            "orchestrator-canary-run-*.json is accepted."
        ),
    )
    parser.add_argument("--app-insights")
    parser.add_argument("--telemetry-attempts", type=int, default=20)
    parser.add_argument("--telemetry-delay", type=int, default=15)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_REPORT_ROOT)
    arguments = parser.parse_args()
    arguments.base_url = _validate_base_url(arguments.base_url)

    report = asyncio.run(certify(arguments))
    path = _write_report(report, arguments.output_root)
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"Certificate: {path}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
