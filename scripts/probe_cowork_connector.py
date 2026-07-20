"""Probe the exact remote MCP contract advertised by the Agent IQ Cowork package."""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import ipaddress
import json
import os
import re
import tempfile
import time
import unicodedata
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, cast
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from agent_iq.api.mcp import EMPLOYEE_MCP_TOOLS, MCP_TOOLS
from agent_iq.api.mcp_apps import (
    EMPLOYEE_OPERATIONS_MIME_TYPE,
    EMPLOYEE_OPERATIONS_RESOURCE_URI,
    FORMATION_MIME_TYPE,
    FORMATION_RESOURCE_URI,
    VACATION_RING_MIME_TYPE,
    VACATION_RING_RESOURCE_URI,
)
from agent_iq.api.presentation import public_text
from agent_iq.api.service import PRODUCT_TOOLS
from agent_iq.contracts import (
    AgentSearchPresentation,
    CallerContext,
    ExecutionSnapshot,
    MatchFilters,
    MatchRequest,
    OrchestrationCommand,
    OrchestrationPresentation,
    OrchestrationResult,
    ProductCapabilities,
    TransportProtocol,
)
from agent_iq.evals import (
    CoworkUxObservation,
    evaluate_cowork_ux,
    load_cowork_ux_cases,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = ROOT / "packages" / "cowork" / "manifest" / "manifest.json"
DEFAULT_REPORT_ROOT = ROOT / "reports" / "deployments" / "cowork" / "connector-probe"
DEFAULT_INTENT = (
    "What four enzymes power BrightWash Cold, and which fifth enzyme differentiates "
    "ClearWave Cold Clean? Cite both product sources."
)
DEFAULT_EXPECTED_AGENT = "consumer-formulation-rd"
DEFAULT_EXPECTED_SOURCE_IDS = (
    "consumer-brightwash-cold",
    "consumer-clearwave-cold",
)
SEARCH_PAYLOAD_BUDGET = 12 * 1024
ORCHESTRATION_PAYLOAD_BUDGET = 16 * 1024
RUN_REPORT_PREFIX = "cowork-connector-probe-"
MAX_RUN_REPORTS = 20
_RUN_REPORT_NAME = re.compile(
    rf"^{re.escape(RUN_REPORT_PREFIX)}\d{{8}}T\d{{12}}Z-[0-9a-f]{{32}}\.json$"
)
_REPORT_WRITE_LOCK = Lock()
_MCP_URL_ERROR = "MCP URL must be a canonical absolute HTTPS /mcp endpoint."
_SENSITIVE_TEXT_FIELDS = frozenset(
    {
        "answer",
        "intent",
        "prompt",
        "query",
        "question",
        "sourcesmarkdown",
        "sourcetext",
        "sourcecontent",
    }
)
_SENSITIVE_CONTAINER_FIELDS = frozenset(
    {
        "answers",
        "intents",
        "prompts",
        "questions",
        "queries",
        "rehearsalprompts",
        "sourcecontents",
        "sourcetexts",
    }
)
_TOOL_ARGUMENT_FIELDS = frozenset(
    {
        "arguments",
        "args",
        "callarguments",
        "rawarguments",
        "toolarguments",
        "toolargs",
        "toolinput",
        "toolparameters",
    }
)
_SOURCE_CONTAINER_FIELDS = frozenset(
    {
        "citation",
        "citations",
        "evidence",
        "evidencerecords",
        "records",
        "source",
        "sources",
    }
)
_SOURCE_TEXT_FIELDS = frozenset(
    {"body", "content", "excerpt", "markdown", "rawcontent", "snippet", "text", "texts"}
)
_TOOL_CONTEXT_FIELDS = frozenset(
    {"call", "calls", "invocation", "invocations", "tool", "toolcall", "toolcalls"}
)
_CREDENTIAL_FIELDS = frozenset(
    {
        "accesstoken",
        "apikey",
        "authorization",
        "bearertoken",
        "clientsecret",
        "cookie",
        "credential",
        "credentials",
        "idtoken",
        "password",
        "proxyauthorization",
        "refreshtoken",
        "secret",
        "setcookie",
        "subscriptionkey",
        "token",
    }
)
_CONTEXT_ID_FIELDS = frozenset({"contextid", "contextids"})


def _invalid_mcp_url() -> ValueError:
    return ValueError(_MCP_URL_ERROR)


def _canonical_host(hostname: str) -> str:
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            host = hostname.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise _invalid_mcp_url() from exc
        if host == "localhost" or host.endswith(".localhost"):
            raise _invalid_mcp_url() from None
        if host.endswith(".") or len(host) > 253:
            raise _invalid_mcp_url() from None
        labels = host.split(".")
        if (
            len(labels) < 2
            or (len(labels) == 4 and all(label.isdigit() for label in labels))
            or any(
                not label
                or len(label) > 63
                or label[0] == "-"
                or label[-1] == "-"
                or any(
                    char not in "abcdefghijklmnopqrstuvwxyz0123456789-"  # pragma: allowlist secret
                    for char in label
                )
                for label in labels
            )
        ):
            raise _invalid_mcp_url() from None
        return host

    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_multicast
    ):
        raise _invalid_mcp_url()
    if address.version == 6:
        return f"[{address.compressed.lower()}]"
    return address.compressed


def canonicalize_mcp_url(value: str) -> str:
    """Return a canonical, credential-free HTTPS MCP endpoint."""

    if not isinstance(value, str) or not value:
        raise _invalid_mcp_url()
    if any(
        unicodedata.category(char) in {"Cc", "Cf"} or char.isspace() or char in "\\?#"
        for char in value
    ):
        raise _invalid_mcp_url()

    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise _invalid_mcp_url() from exc

    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.path != "/mcp"
        or hostname is None
        or port not in (None, 443)
    ):
        raise _invalid_mcp_url()

    canonical = f"https://{_canonical_host(hostname)}/mcp"
    if value != canonical:
        raise _invalid_mcp_url()
    return canonical


def _validate_mcp_url(value: str) -> str:
    return canonicalize_mcp_url(value)


def mcp_url_from_manifest(manifest_path: Path = DEFAULT_MANIFEST_PATH) -> str:
    """Read and validate the MCP URL that Cowork will use."""

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Cowork manifest is not valid JSON.") from exc
    try:
        url = manifest["agentConnectors"][0]["toolSource"]["remoteMcpServer"]["mcpServerUrl"]
    except (IndexError, KeyError, TypeError) as exc:
        raise ValueError("Cowork manifest does not contain one remote MCP URL.") from exc
    if not isinstance(url, str):
        raise ValueError("Cowork manifest MCP URL must be text.")
    return _validate_mcp_url(url)


def build_probe_command(
    *,
    intent: str = DEFAULT_INTENT,
    request_id: str | None = None,
    requested_at: datetime | None = None,
) -> OrchestrationCommand:
    """Create the synthetic caller-bound command used by the canary probe."""

    return OrchestrationCommand(
        request=MatchRequest(
            request_id=request_id or f"d12-cowork-probe-{int(time.time())}",
            intent=intent,
            caller=CallerContext(
                subject_id="d12-cowork-probe-caller",
                tenant_id="d12-cowork-probe-tenant",
            ),
            filters=MatchFilters(
                max_candidates=20,
                max_selected=1,
                required_protocols=(TransportProtocol.A2A,),
            ),
            requested_at=requested_at or datetime.now(UTC),
        )
    )


def _structured_content(result: CallToolResult, tool_name: str) -> dict[str, Any]:
    if result.isError or result.structuredContent is None:
        raise RuntimeError(f"Remote MCP tool {tool_name} failed.")
    if not isinstance(result.structuredContent, dict):
        raise RuntimeError(f"Remote MCP tool {tool_name} returned invalid structured content.")
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


def _normalized_key(key: object) -> str:
    return "".join(char for char in str(key).casefold() if char.isalnum())


def _redaction_bytes(value: Any) -> bytes:
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=lambda item: f"<{type(item).__module__}.{type(item).__qualname__}>",
    ).encode("utf-8")


def _redacted_value(value: Any) -> dict[str, Any]:
    encoded = _redaction_bytes(value)
    return {
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "length": len(encoded),
    }


def _is_sensitive_text_field(key: str) -> bool:
    return (
        key in _SENSITIVE_TEXT_FIELDS
        or key.endswith("answer")
        or key.endswith("intent")
        or key.endswith("query")
        or key.endswith("prompt")
        or key.endswith("question")
        or key.endswith("querytext")
        or key.endswith("prompttext")
        or key.endswith("questiontext")
    )


def _is_credential_field(key: str) -> bool:
    return key in _CREDENTIAL_FIELDS or key.endswith(
        ("apikey", "authorization", "credential", "password", "secret", "token")
    )


def _redaction_fields(key: object, value: Any) -> dict[str, Any]:
    redacted = _redacted_value(value)
    field = str(key)
    return {
        f"{field}Sha256": redacted["sha256"],
        f"{field}Length": redacted["length"],
    }


def _safe_copy(
    value: Any,
    *,
    source_context: bool = False,
    tool_context: bool = False,
) -> Any:
    if isinstance(value, dict):
        safe: dict[Any, Any] = {}
        current_source_context = source_context or any(
            _normalized_key(key) in _SOURCE_CONTAINER_FIELDS for key in value
        )
        current_tool_context = tool_context or any(
            _normalized_key(key) in _TOOL_CONTEXT_FIELDS for key in value
        )
        for key, item in value.items():
            normalized = _normalized_key(key)
            if (
                normalized in _TOOL_ARGUMENT_FIELDS
                or (current_tool_context and normalized in {"input", "parameters"})
                or _is_sensitive_text_field(normalized)
                or _is_credential_field(normalized)
                or normalized in _CONTEXT_ID_FIELDS
                or normalized in _SENSITIVE_CONTAINER_FIELDS
                or (current_source_context and normalized in _SOURCE_TEXT_FIELDS)
                or (normalized in _SOURCE_CONTAINER_FIELDS and isinstance(item, str))
            ):
                safe.update(_redaction_fields(key, item))
            else:
                safe[key] = _safe_copy(
                    item,
                    source_context=current_source_context or normalized in _SOURCE_CONTAINER_FIELDS,
                    tool_context=current_tool_context or normalized in _TOOL_CONTEXT_FIELDS,
                )
        return safe
    if isinstance(value, list):
        return [
            (
                _redacted_value(item)
                if (source_context or tool_context) and isinstance(item, str)
                else _safe_copy(item, source_context=source_context, tool_context=tool_context)
            )
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            (
                _redacted_value(item)
                if (source_context or tool_context) and isinstance(item, str)
                else _safe_copy(item, source_context=source_context, tool_context=tool_context)
            )
            for item in value
        )
    return value


def _content_safe_report(report: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy a report before removing raw user and evidence content."""

    safe_report = cast(dict[str, Any], _safe_copy(copy.deepcopy(report)))
    if "mcpUrl" in safe_report:
        mcp_url = safe_report["mcpUrl"]
        if not isinstance(mcp_url, str):
            raise ValueError(_MCP_URL_ERROR)
        safe_report["mcpUrl"] = _validate_mcp_url(mcp_url)
    return safe_report


class _RedirectRejectedError(RuntimeError):
    """Raised when the MCP endpoint returns any redirect response."""


async def _reject_redirect(response: httpx.Response) -> None:
    if 300 <= response.status_code < 400:
        raise _RedirectRejectedError(
            "MCP endpoint returned a redirect; refusing to certify a redirected endpoint."
        )


def _non_following_http_client(
    timeout: httpx.Timeout,
    *,
    redirect_hook: Callable[[httpx.Response], Awaitable[None]] = _reject_redirect,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        event_hooks={"response": [redirect_hook]},
    )


async def probe_session(
    session: ClientSession,
    command: OrchestrationCommand,
    *,
    expected_agent: str = DEFAULT_EXPECTED_AGENT,
    expected_source_ids: tuple[str, ...] = DEFAULT_EXPECTED_SOURCE_IDS,
) -> dict[str, Any]:
    """Exercise initialize, discovery, capabilities, and the one-tool Cowork demo."""

    initialized = await session.initialize()
    listed = await session.list_tools()
    listed_resources = await session.list_resources()
    tools_by_name = {tool.name: tool for tool in listed.tools}
    tool_names = set(tools_by_name)
    resources_by_uri = {str(resource.uri): resource for resource in listed_resources.resources}
    formation_resource = resources_by_uri.get(FORMATION_RESOURCE_URI)
    formation_html = ""
    formation_read_mime = None
    if formation_resource is not None:
        resource_read = await session.read_resource(formation_resource.uri)
        if resource_read.contents:
            resource_content = resource_read.contents[0]
            formation_read_mime = resource_content.model_dump(by_alias=True).get("mimeType")
            formation_html = str(getattr(resource_content, "text", ""))
    employee_resource = resources_by_uri.get(EMPLOYEE_OPERATIONS_RESOURCE_URI)
    employee_html = ""
    employee_read_mime = None
    if employee_resource is not None:
        resource_read = await session.read_resource(employee_resource.uri)
        if resource_read.contents:
            resource_content = resource_read.contents[0]
            employee_read_mime = resource_content.model_dump(by_alias=True).get("mimeType")
            employee_html = str(getattr(resource_content, "text", ""))
    vacation_resource = resources_by_uri.get(VACATION_RING_RESOURCE_URI)
    vacation_html = ""
    vacation_read_mime = None
    if vacation_resource is not None:
        resource_read = await session.read_resource(vacation_resource.uri)
        if resource_read.contents:
            resource_content = resource_read.contents[0]
            vacation_read_mime = resource_content.model_dump(by_alias=True).get("mimeType")
            vacation_html = str(getattr(resource_content, "text", ""))
    command_payload = command.model_dump(mode="json", by_alias=True)

    capabilities_call = await session.call_tool("capabilities")
    capabilities = ProductCapabilities.model_validate(
        _structured_content(capabilities_call, "capabilities")
    )
    search_call = await session.call_tool("search_agents", {"command": command_payload})
    search = AgentSearchPresentation.model_validate(
        _structured_content(search_call, "search_agents")
    )
    search_payload_bytes = _payload_bytes(search_call)

    progress_events: list[dict[str, Any]] = []
    orchestration_started = time.perf_counter()

    async def capture_progress(
        progress: float,
        total: float | None,
        message: str | None,
    ) -> None:
        if message is not None:
            progress_events.append(
                {
                    "progress": progress,
                    "total": total,
                    "message": message,
                    "elapsedMs": int((time.perf_counter() - orchestration_started) * 1_000),
                }
            )

    orchestration_call = await session.call_tool(
        "run_single_agent_demo",
        {"question": command.request.intent},
        progress_callback=capture_progress,
    )
    orchestration_elapsed_ms = int((time.perf_counter() - orchestration_started) * 1_000)
    orchestration = OrchestrationPresentation.model_validate(
        _structured_content(orchestration_call, "run_single_agent_demo")
    )
    orchestration_payload_bytes = _payload_bytes(orchestration_call)
    execution_call = await session.call_tool(
        "get_execution",
        {"execution_id": orchestration.execution_id},
    )
    snapshot = ExecutionSnapshot.model_validate(
        _structured_content(execution_call, "get_execution")
    )
    if snapshot.summary is None:
        raise RuntimeError("The completed MCP execution is missing its audit summary.")
    canonical = OrchestrationResult(summary=snapshot.summary, events=snapshot.events)

    expected_tools = set(MCP_TOOLS)
    top_candidate = search.candidates[0] if search.candidates else None
    selected_ids = tuple(agent.agent_id for agent in orchestration.selected_agents)
    lifecycle_events = tuple(event.payload.type for event in canonical.events)
    progress_messages = tuple(str(event["message"]) for event in progress_events)
    progress_values = tuple(float(event["progress"]) for event in progress_events)
    first_progress_ms = int(progress_events[0]["elapsedMs"]) if progress_events else None
    expected_progress = (
        "Matching eligible specialists",
        "Selected ",
        "Retrieving approved evidence",
        "Building the cited answer",
        "Completed",
    )
    progress_index = 0
    for message in progress_messages:
        if (
            progress_index < len(expected_progress)
            and expected_progress[progress_index].casefold() in message.casefold()
        ):
            progress_index += 1
    progress_jargon = (
        "request.accepted",
        "catalog.searched",
        "candidates.ranked",
        "glob",
        ".json",
        "spilled",
    )
    read_only_tools = (
        "search_agents",
        "orchestrate",
        "run_single_agent_demo",
        "get_execution",
        "capabilities",
        *EMPLOYEE_MCP_TOOLS,
    )
    read_only_annotations = all(
        (annotations := tools_by_name[name].annotations) is not None
        and annotations.readOnlyHint is True
        and annotations.destructiveHint is False
        for name in read_only_tools
    )
    cancel_annotations = tools_by_name["cancel_execution"].annotations
    feedback_annotations = tools_by_name["submit_feedback"].annotations
    formation_meta = {
        "ui": {
            "resourceUri": FORMATION_RESOURCE_URI,
            "visibility": ["model", "app"],
        }
    }
    employee_meta = {
        "ui": {
            "resourceUri": EMPLOYEE_OPERATIONS_RESOURCE_URI,
            "visibility": ["model", "app"],
        }
    }
    vacation_meta = {
        "ui": {
            "resourceUri": VACATION_RING_RESOURCE_URI,
            "visibility": ["model", "app"],
        }
    }
    formation_markers = (
        'aria-live="polite"',
        "prefers-reduced-motion",
        "ui/initialize",
        "ui/notifications/tool-result",
        "ui/notifications/size-changed",
        '"tools/call"',
        "Visible gaps",
        "Evidence-backed formulation map",
        'data-answer-flow": "formulation"',
        "Selected specialist",
    )
    forbidden_formation_dependencies = (
        "<script src=",
        "<link ",
        "innerHTML",
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "<iframe",
        "ResizeObserver",
    )
    employee_markers = (
        'aria-live="polite"',
        "prefers-reduced-motion",
        "ui/initialize",
        "ui/notifications/tool-result",
        "ui/notifications/size-changed",
        "ui/open-link",
        '"tools/call"',
        "open_employee_experience",
        "prepare_employee_action",
        "No enterprise action was executed",
        "No private chain-of-thought",
    )
    vacation_markers = (
        'aria-live="polite"',
        "prefers-reduced-motion",
        "ui/initialize",
        "ui/notifications/tool-result",
        "ui/notifications/size-changed",
        "ui/open-link",
        "compact_ring",
        "svg.dataset.vacationRings",
        "employee-vacation-leave",
        'availableDisplayModes: ["inline"]',
        "Selected by Agent IQ",
    )
    employee_tool_names = tuple(
        name
        for name in EMPLOYEE_MCP_TOOLS
        if name not in {"check_vacation_balance", "run_vacation_balance_demo"}
    )
    checks = {
        "initialize": bool(str(initialized.protocolVersion)),
        "toolContract": tool_names == expected_tools,
        "mcpAppToolMetadata": all(
            tools_by_name[name].meta == formation_meta
            for name in ("search_agents", "orchestrate", "run_single_agent_demo")
        )
        and all(
            tools_by_name[name].meta == vacation_meta
            for name in ("check_vacation_balance", "run_vacation_balance_demo")
        )
        and all(tools_by_name[name].meta == employee_meta for name in employee_tool_names),
        "mcpAppResource": (
            formation_resource is not None
            and formation_resource.model_dump(by_alias=True).get("mimeType") == FORMATION_MIME_TYPE
            and formation_read_mime == FORMATION_MIME_TYPE
            and formation_html.startswith("<!doctype html>")
            and len(formation_html.encode("utf-8")) <= 64 * 1024
        ),
        "mcpAppSelfContained": (
            all(marker in formation_html for marker in formation_markers)
            and not any(
                dependency in formation_html for dependency in forbidden_formation_dependencies
            )
        ),
        "employeeMcpAppResource": (
            employee_resource is not None
            and employee_resource.model_dump(by_alias=True).get("mimeType")
            == EMPLOYEE_OPERATIONS_MIME_TYPE
            and employee_read_mime == EMPLOYEE_OPERATIONS_MIME_TYPE
            and employee_html.startswith("<!doctype html>")
            and len(employee_html.encode("utf-8")) <= 96 * 1024
        ),
        "employeeMcpAppSelfContained": (
            all(marker in employee_html for marker in employee_markers)
            and not any(
                dependency in employee_html for dependency in forbidden_formation_dependencies
            )
        ),
        "vacationMcpAppResource": (
            vacation_resource is not None
            and vacation_resource.model_dump(by_alias=True).get("mimeType")
            == VACATION_RING_MIME_TYPE
            and vacation_read_mime == VACATION_RING_MIME_TYPE
            and vacation_html.startswith("<!doctype html>")
            and len(vacation_html.encode("utf-8")) <= 24 * 1024
        ),
        "vacationMcpAppSelfContained": (
            all(marker in vacation_html for marker in vacation_markers)
            and not any(
                dependency in vacation_html for dependency in forbidden_formation_dependencies
            )
            and "ui/request-display-mode" not in vacation_html
            and '"tools/call"' not in vacation_html
        ),
        "safetyAnnotations": (
            read_only_annotations
            and cancel_annotations is not None
            and cancel_annotations.readOnlyHint is False
            and cancel_annotations.destructiveHint is True
            and cancel_annotations.idempotentHint is True
            and feedback_annotations is not None
            and feedback_annotations.readOnlyHint is False
            and feedback_annotations.destructiveHint is False
            and feedback_annotations.idempotentHint is True
        ),
        "capabilities": (
            capabilities.product == "Agent IQ"
            and "mcp" in capabilities.ingress_transports
            and capabilities.southbound_transport == "native_a2a"
            and set(capabilities.tools) == set(PRODUCT_TOOLS)
            and capabilities.anonymous_canary
        ),
        "searchTop1": (
            top_candidate is not None
            and top_candidate.agent_id == expected_agent
            and top_candidate.eligible
        ),
        "searchPayloadBudget": search_payload_bytes <= SEARCH_PAYLOAD_BUDGET,
        "orchestrationSelected": selected_ids == (expected_agent,),
        "orchestrationCompleted": orchestration.status.value == "completed",
        "orchestrationPayloadBudget": (orchestration_payload_bytes <= ORCHESTRATION_PAYLOAD_BUDGET),
        "compactProjectionParity": (
            orchestration.execution_id == canonical.summary.execution_id
            and orchestration.status is canonical.summary.status
            and selected_ids
            == tuple(reason.agent_id for reason in canonical.summary.selection.selected)
            and orchestration.answer == public_text(canonical.summary.synthesis.answer)
            and tuple(citation.source_id for citation in orchestration.citations)
            == tuple(record.source_id for record in canonical.summary.synthesis.evidence.records)
        ),
        "fullAuditRetrieved": snapshot.summary.execution_id == orchestration.execution_id,
        "nativeA2ASouthbound": all(
            step.transport is TransportProtocol.A2A and step.protocol_version == "1.0"
            for step in canonical.summary.steps
        ),
        "groundingComplete": (
            canonical.summary.synthesis.coverage.ratio == 1
            and bool(orchestration.citations)
            and orchestration.sources_markdown is not None
        ),
        "lifecycleComplete": (
            lifecycle_events
            and lifecycle_events[0] == "request.accepted"
            and lifecycle_events[-1] == "execution.completed"
        ),
        "readableProgress": (
            progress_index == len(expected_progress)
            and bool(progress_values)
            and list(progress_values) == sorted(progress_values)
            and all(event["total"] == 100 for event in progress_events)
            and not any(
                jargon in " ".join(progress_messages).casefold() for jargon in progress_jargon
            )
        ),
        "firstProgressLatency": (first_progress_ms is not None and first_progress_ms <= 1_000),
    }
    ux_case = load_cowork_ux_cases()[0].model_copy(
        update={
            "intent": command.request.intent,
            "expected_agent_ids": (expected_agent,),
            "expected_source_ids": expected_source_ids,
        }
    )
    ux_result = evaluate_cowork_ux(
        ux_case,
        CoworkUxObservation(
            transcript_id=f"connector-{command.request.request_id}",
            case_id=ux_case.case_id,
            tool_calls=("run_single_agent_demo",),
            approval_count=0,
            spill_recovery_count=0,
            progress_events=progress_messages,
            first_progress_ms=first_progress_ms,
            completion_ms=orchestration_elapsed_ms,
            payload_bytes=orchestration_payload_bytes,
            selected_agent_ids=selected_ids,
            selection_reasons=tuple(agent.reason for agent in orchestration.selected_agents),
            answer=orchestration.answer,
            source_ids=tuple(citation.source_id for citation in orchestration.citations),
            citation_count=len(orchestration.citations),
        ),
    )
    checks["coworkUx"] = ux_result.passes
    failures = [name for name, passed in checks.items() if not passed]
    return {
        "schemaVersion": "1.0",
        "generatedAt": datetime.now(UTC).isoformat(),
        "status": "pass" if not failures else "fail",
        "checks": checks,
        "failures": failures,
        "handshake": {
            "protocolVersion": initialized.protocolVersion,
            "serverName": initialized.serverInfo.name,
            "serverVersion": initialized.serverInfo.version,
            "tools": sorted(tool_names),
            "toolMeta": {name: tool.meta for name, tool in sorted(tools_by_name.items())},
            "toolAnnotations": {
                name: (
                    tool.annotations.model_dump(mode="json", by_alias=True, exclude_none=True)
                    if tool.annotations is not None
                    else None
                )
                for name, tool in sorted(tools_by_name.items())
            },
            "resources": [
                resource.model_dump(mode="json", by_alias=True, exclude_none=True)
                for resource in listed_resources.resources
            ],
            "formationResource": {
                "uri": FORMATION_RESOURCE_URI,
                "mimeType": formation_read_mime,
                "bytes": len(formation_html.encode("utf-8")),
            },
            "employeeOperationsResource": {
                "uri": EMPLOYEE_OPERATIONS_RESOURCE_URI,
                "mimeType": employee_read_mime,
                "bytes": len(employee_html.encode("utf-8")),
            },
            "vacationRingResource": {
                "uri": VACATION_RING_RESOURCE_URI,
                "mimeType": vacation_read_mime,
                "bytes": len(vacation_html.encode("utf-8")),
            },
        },
        "capabilities": capabilities.model_dump(mode="json", by_alias=True),
        "search": {
            "requestId": search.request_id,
            "intent": command.request.intent,
            "candidateCount": search.total_candidates,
            "returnedCandidateCount": len(search.candidates),
            "payloadBytes": search_payload_bytes,
            "payloadBudgetBytes": SEARCH_PAYLOAD_BUDGET,
            "topCandidate": (
                {
                    "agentId": top_candidate.agent_id,
                    "displayName": top_candidate.display_name,
                    "rank": top_candidate.rank,
                    "eligible": top_candidate.eligible,
                    "explanation": top_candidate.reason,
                }
                if top_candidate is not None
                else None
            ),
        },
        "orchestration": {
            "tool": "run_single_agent_demo",
            "executionId": orchestration.execution_id,
            "status": orchestration.status.value,
            "selectedAgentIds": list(selected_ids),
            "lifecycleEvents": list(lifecycle_events),
            "progressEvents": progress_events,
            "firstProgressMs": first_progress_ms,
            "completionMs": orchestration_elapsed_ms,
            "payloadBytes": orchestration_payload_bytes,
            "payloadBudgetBytes": ORCHESTRATION_PAYLOAD_BUDGET,
            "fullAuditPayloadBytes": _payload_bytes(execution_call),
            "detailsAvailableVia": orchestration.details_available_via,
            "steps": [
                {
                    "agentId": step.agent_id,
                    "status": step.status.value,
                    "transport": step.transport.value,
                    "protocolVersion": step.protocol_version,
                    "evidenceCount": len(step.evidence_ids),
                }
                for step in canonical.summary.steps
            ],
            "answer": orchestration.answer,
            "citationCount": len(orchestration.citations),
            "sourcesMarkdown": orchestration.sources_markdown,
        },
        "coworkUx": {
            "status": "pass" if ux_result.passes else "fail",
            "failedGates": list(ux_result.failures),
            "gates": [
                {
                    "name": gate.name,
                    "passes": gate.passes,
                    "actual": gate.actual,
                    "required": gate.required,
                }
                for gate in ux_result.gates
            ],
        },
    }


async def probe_endpoint(
    mcp_url: str,
    command: OrchestrationCommand,
    *,
    expected_agent: str = DEFAULT_EXPECTED_AGENT,
    expected_source_ids: tuple[str, ...] = DEFAULT_EXPECTED_SOURCE_IDS,
    timeout_seconds: float = 360,
) -> dict[str, Any]:
    """Open a Cowork-equivalent Streamable HTTP session and run the probe."""

    canonical_url = _validate_mcp_url(mcp_url)
    timeout = httpx.Timeout(timeout_seconds)
    redirect_seen = False

    async def track_redirect(response: httpx.Response) -> None:
        nonlocal redirect_seen
        if 300 <= response.status_code < 400:
            redirect_seen = True
        await _reject_redirect(response)

    result: dict[str, Any] | None = None
    try:
        async with (
            _non_following_http_client(timeout, redirect_hook=track_redirect) as http_client,
            streamable_http_client(canonical_url, http_client=http_client) as (
                read_stream,
                write_stream,
                _,
            ),
            ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=timeout_seconds),
            ) as session,
        ):
            result = await probe_session(
                session,
                command,
                expected_agent=expected_agent,
                expected_source_ids=expected_source_ids,
            )
    finally:
        if redirect_seen:
            raise _RedirectRejectedError(
                "MCP endpoint returned a redirect; refusing to certify a redirected endpoint."
            )
    if result is None:
        raise RuntimeError("MCP probe did not produce a result.")
    return result


def write_report(report: dict[str, Any], output_root: Path = DEFAULT_REPORT_ROOT) -> Path:
    """Persist a content-safe report and atomically update the latest pointer."""

    safe_report = _content_safe_report(report)
    if "mcpUrl" in safe_report:
        mcp_url = safe_report["mcpUrl"]
        if not isinstance(mcp_url, str):
            raise ValueError(_MCP_URL_ERROR)
        safe_report["mcpUrl"] = _validate_mcp_url(mcp_url)

    text = json.dumps(safe_report, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    output_root.mkdir(parents=True, exist_ok=True)
    with _REPORT_WRITE_LOCK:
        path = _new_run_path(output_root)
        _atomic_write_text(path, text)
        _atomic_write_text(output_root / "latest.json", text)
        _prune_run_reports(output_root)
    return path


def _new_run_path(output_root: Path) -> Path:
    for _ in range(100):
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        candidate = output_root / f"{RUN_REPORT_PREFIX}{stamp}-{uuid.uuid4().hex}.json"
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not allocate a unique Cowork connector probe report name.")


def _atomic_write_text(path: Path, text: str) -> None:
    temporary_path: Path | None = None
    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            with suppress(FileNotFoundError):
                temporary_path.unlink()


def _prune_run_reports(output_root: Path) -> None:
    run_reports = sorted(
        (
            path
            for path in output_root.iterdir()
            if path.is_file() and _RUN_REPORT_NAME.fullmatch(path.name)
        ),
        key=lambda path: path.name,
    )
    for path in run_reports[:-MAX_RUN_REPORTS]:
        path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp-url")
    parser.add_argument("--intent", default=DEFAULT_INTENT)
    parser.add_argument("--expected-agent", default=DEFAULT_EXPECTED_AGENT)
    parser.add_argument("--expected-source-id", action="append", default=[])
    parser.add_argument("--request-id")
    parser.add_argument("--timeout-seconds", type=float, default=360)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()

    mcp_url = _validate_mcp_url(arguments.mcp_url) if arguments.mcp_url else mcp_url_from_manifest()
    command = build_probe_command(
        intent=arguments.intent,
        request_id=arguments.request_id,
    )
    report = asyncio.run(
        probe_endpoint(
            mcp_url,
            command,
            expected_agent=arguments.expected_agent,
            expected_source_ids=(
                tuple(arguments.expected_source_id)
                if arguments.expected_source_id
                else DEFAULT_EXPECTED_SOURCE_IDS
            ),
            timeout_seconds=arguments.timeout_seconds,
        )
    )
    report["mcpUrl"] = mcp_url
    if not arguments.no_report:
        report["reportPath"] = str(write_report(report, arguments.output_root))
    if arguments.json:
        print(json.dumps(_content_safe_report(report), indent=2, sort_keys=True))
    else:
        print(f"Cowork connector probe: {report['status'].upper()}")
        print(f"MCP: {mcp_url}")
        print(f"Selected: {', '.join(report['orchestration']['selectedAgentIds'])}")
        if not arguments.no_report:
            print(f"Report: {report['reportPath']}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
