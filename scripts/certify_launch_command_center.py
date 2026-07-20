"""Certify the five-specialist EU cold-water launch command center over Cowork MCP."""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from agent_iq.api.mcp_apps import FORMATION_RESOURCE_URI
from agent_iq.contracts import (
    ArtifactKind,
    CallerContext,
    ContextKind,
    ContextReference,
    ExecutionSnapshot,
    MatchFilters,
    MatchRequest,
    OrchestrationCommand,
    OrchestrationPresentation,
    StepExecutionStatus,
    TransportProtocol,
)
from agent_iq.contracts.events import (
    ApprovalResolvedPayload,
    TaskCompletedPayload,
    TaskHandedOffPayload,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_ROOT = ROOT / "reports" / "evals" / "launch-command-center"
DEFAULT_BASE_URL = os.environ.get("AGENT_IQ_ORCHESTRATOR_BASE_URL", "")
SCENARIO_PATH = ROOT / "evals" / "launch_command_center.json"
RUN_CERTIFICATE_PREFIX = "launch-command-center-"
MAX_RUN_CERTIFICATES = 20
_RUN_CERTIFICATE_NAME = re.compile(
    rf"^{re.escape(RUN_CERTIFICATE_PREFIX)}\d{{8}}T\d{{12}}Z-[0-9a-f]{{32}}\.json$"
)
_LATEST_WRITE_LOCK = Lock()
_CONTENT_FIELDS = frozenset(
    {
        "answer",
        "intent",
        "prompt",
        "prompts",
        "query",
        "question",
        "rehearsalprompts",
        "sourcecontent",
        "sourcesmarkdown",
        "sourcetext",
    }
)
_TOOL_ARGUMENT_FIELDS = frozenset(
    {
        "args",
        "arguments",
        "callarguments",
        "rawarguments",
        "toolargs",
        "toolarguments",
        "toolinput",
        "toolparameters",
    }
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


def _validate_mcp_url(value: str) -> str:
    """Return the canonical MCP endpoint derived from an accepted canary origin."""

    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("MCP URL must be the canary HTTPS endpoint ending in /mcp.")
    parsed = urlsplit(value)
    if parsed.path != "/mcp" or parsed.query or parsed.fragment or "?" in value or "#" in value:
        raise ValueError("MCP URL must be the canary HTTPS endpoint ending in /mcp.")
    base_url = _validate_base_url(urlunsplit((parsed.scheme, parsed.netloc, "", "", "")))
    return f"{base_url}/mcp"


def _load_scenario() -> dict[str, Any]:
    try:
        scenario = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load launch scenario at {SCENARIO_PATH}: {exc}") from exc
    if (
        not isinstance(scenario, dict)
        or scenario.get("schemaVersion") != "1.0"
        or not isinstance(scenario.get("intent"), str)
        or not scenario["intent"].strip()
        or not isinstance(scenario.get("rehearsalPrompts"), list)
        or len(scenario["rehearsalPrompts"]) != 5
        or not all(
            isinstance(prompt, str) and prompt.strip() for prompt in scenario["rehearsalPrompts"]
        )
    ):
        raise ValueError("Launch command-center scenario is malformed.")
    return scenario


SCENARIO = _load_scenario()
LAUNCH_INTENT = str(SCENARIO["intent"])
EXPECTED_AGENTS = (
    "consumer-formulation-rd",
    "consumer-brand-to-brand-compare",
    "consumer-demand-forecasting",
    "consumer-manufacturing-operations",
    "consumer-regulatory-affairs",
)
EXPECTED_CONTEXT = {
    "consumer-formulation-rd": "workiq-formula-decision",
    "consumer-brand-to-brand-compare": "workiq-brand-review",
    "consumer-demand-forecasting": "workiq-demand-plan",
    "consumer-manufacturing-operations": "workiq-plant-review",
    "consumer-regulatory-affairs": "workiq-regulatory-review",
}
UNAPPROVED_CONTEXT_ID = "workiq-unapproved-private-draft"
PAYLOAD_BUDGET = 16 * 1024
FALLBACK_TERMS = (
    "fallback",
    "in-process",
    "in process",
    "mock",
    "stub",
    "success-shaped",
)
HERO_RESPONSIBILITIES = {
    "consumer-formulation-rd": "formula stability and launch-claim evidence",
    "consumer-brand-to-brand-compare": "BrightWash Cold versus ClearWave comparison",
    "consumer-demand-forecasting": "Germany launch demand forecast",
    "consumer-manufacturing-operations": "plant capacity and line OEE",
    "consumer-regulatory-affairs": "EU registration and claim disposition",
}
SCENARIO_PHASES = (
    {
        "phaseId": "formula-and-claims",
        "kind": "agent-analysis",
        "agentId": "consumer-formulation-rd",
    },
    {
        "phaseId": "brand-comparison",
        "kind": "agent-analysis",
        "agentId": "consumer-brand-to-brand-compare",
    },
    {
        "phaseId": "demand-forecast",
        "kind": "agent-analysis",
        "agentId": "consumer-demand-forecasting",
    },
    {
        "phaseId": "plant-readiness",
        "kind": "agent-analysis",
        "agentId": "consumer-manufacturing-operations",
    },
    {
        "phaseId": "regulatory-review",
        "kind": "agent-analysis",
        "agentId": "consumer-regulatory-affairs",
    },
    {
        "phaseId": "approval-gate",
        "kind": "control",
        "requiresApproval": True,
        "mutationAllowed": False,
    },
    {
        "phaseId": "post-approval-monitoring",
        "kind": "durable-handoff",
        "requiresApproval": True,
        "mutationAllowed": False,
    },
)


def _enterprise_plane_status() -> dict[str, dict[str, Any]]:
    """Describe unavailable enterprise data planes without implying live access."""

    return {
        "workIq": {
            "availability": "fixture",
            "mode": "fixture",
            "sourceMode": "fixture",
            "live": False,
            "label": "SYNTHETIC METADATA ONLY - NOT LIVE WORK IQ",
            "reason": "No M365 source content is supplied to this certificate.",
        },
        "foundryIq": {
            "availability": "fixture",
            "mode": "fixture",
            "sourceMode": "fixture",
            "live": False,
            "label": "DETERMINISTIC FIXTURE - NOT LIVE FOUNDRY IQ",
            "reason": "Knowledge evidence is represented by the deterministic test adapter.",
        },
        "sap": {
            "availability": "fixture",
            "mode": "fixture",
            "sourceMode": "fixture",
            "live": False,
            "label": "DETERMINISTIC FIXTURE - NOT LIVE SAP",
            "reason": "Demand, orders, capacity, and OEE are not live SAP reads.",
        },
    }


def _sha256_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def build_launch_command(
    *,
    request_id: str | None = None,
    requested_at: datetime | None = None,
) -> OrchestrationCommand:
    """Build a caller-bound command with synthetic, content-free Work IQ references."""

    subject_id = "d12-launch-command-center-caller"
    tenant_id = "d12-launch-command-center-tenant"
    references = tuple(
        ContextReference(
            context_id=context_id,
            kind=ContextKind.WORK_IQ,
            source_uri=f"https://workiq.example.invalid/launch/{context_id}",
            subject_id=subject_id,
            tenant_id=tenant_id,
            permission_scope="workiq:launch-readiness:read",
            approved=True,
            sensitivity="synthetic-confidential",
            allowed_agent_ids=(agent_id,),
            priority=index,
        )
        for index, (agent_id, context_id) in enumerate(EXPECTED_CONTEXT.items(), start=1)
    )
    references += (
        ContextReference(
            context_id=UNAPPROVED_CONTEXT_ID,
            kind=ContextKind.WORK_IQ,
            source_uri="https://workiq.example.invalid/launch/private-draft",
            subject_id=subject_id,
            tenant_id=tenant_id,
            permission_scope="workiq:launch-readiness:read",
            approved=False,
            sensitivity="synthetic-highly-confidential",
            allowed_agent_ids=EXPECTED_AGENTS,
            priority=0,
        ),
    )
    return OrchestrationCommand(
        request=MatchRequest(
            request_id=request_id or f"d12-launch-{int(time.time())}",
            intent=LAUNCH_INTENT,
            caller=CallerContext(
                subject_id=subject_id,
                tenant_id=tenant_id,
                permissions=("agent-iq:orchestrate", "workiq:launch-readiness:read"),
                data_classifications=("synthetic-confidential",),
                geographies=("DE", "EU"),
            ),
            filters=MatchFilters(
                max_candidates=20,
                max_selected=5,
                required_protocols=(TransportProtocol.A2A,),
            ),
            requested_at=requested_at or datetime.now(UTC),
        ),
        context_references=references,
    )


def _structured(result: CallToolResult, tool_name: str) -> dict[str, Any]:
    if result.isError or not isinstance(result.structuredContent, dict):
        raise RuntimeError(f"Remote MCP tool {tool_name} failed.")
    return result.structuredContent


def _payload_bytes(result: CallToolResult) -> int:
    structured = json.dumps(
        result.structuredContent,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    text = "".join(item.text for item in result.content if isinstance(item, TextContent)).encode(
        "utf-8"
    )
    return len(structured) + len(text)


def _handoff_facts(
    snapshot: ExecutionSnapshot,
    summary: Any,
    selected_ids: tuple[str, ...],
) -> dict[str, Any]:
    handoffs = tuple(
        event.payload
        for event in snapshot.events
        if isinstance(event.payload, TaskHandedOffPayload)
    )
    completions = tuple(
        event.payload
        for event in snapshot.events
        if isinstance(event.payload, TaskCompletedPayload)
    )
    task_ids = tuple(payload.task_id for payload in handoffs)
    callback_modes = tuple(
        sorted({mode.value for payload in handoffs for mode in payload.callback_modes})
    )
    return {
        "status": "durable" if handoffs else "not_required_for_sync_analysis",
        "taskIds": list(task_ids),
        "completedTaskIds": sorted({payload.task_id for payload in completions}),
        "callbackModes": list(callback_modes),
        "resumeFacts": {
            "executionId": summary.execution_id,
            "requestId": summary.request_id,
            "planId": summary.plan.plan_id,
            "selectedAgentIds": list(selected_ids),
        },
        "postApprovalMonitoring": {
            "status": "not_started_requires_approval",
            "requiresApproval": True,
            "mutationAllowed": False,
        },
    }


def _approval_facts(
    snapshot: ExecutionSnapshot,
    summary: Any,
    *,
    read_only_tool: bool,
) -> dict[str, Any]:
    mutation_artifacts = tuple(
        artifact
        for step in summary.steps
        for envelope in step.artifacts
        if envelope.artifact.kind is ArtifactKind.SAP_CHANGE_PROPOSAL
        for artifact in (envelope.artifact,)
    )
    resolved_approvals = tuple(
        event.payload
        for event in snapshot.events
        if isinstance(event.payload, ApprovalResolvedPayload)
    )
    return {
        "requiredBeforeMutation": True,
        "analysisOnly": True,
        "readOnlyTool": read_only_tool,
        "mutationArtifactsObserved": len(mutation_artifacts),
        "mutationArtifactsRequireApproval": all(
            artifact.approval_required is True for artifact in mutation_artifacts
        ),
        "approvalResolutionsObserved": [
            {
                "approvalId": payload.approval_id,
                "status": payload.status,
            }
            for payload in resolved_approvals
        ],
        "status": "read_only_no_mutation",
    }


def _fixture_planes_are_explicit(planes: dict[str, dict[str, Any]]) -> bool:
    expected = {"workIq", "foundryIq", "sap"}
    return set(planes) == expected and all(
        plane.get("mode") == "fixture"
        and plane.get("availability") == "fixture"
        and plane.get("live") is False
        and "not live" in str(plane.get("label", "")).casefold()
        for plane in planes.values()
    )


def _redact_text_field(
    container: dict[str, Any],
    key: str,
    *,
    field_path: str,
    redacted_fields: list[str],
) -> None:
    if key not in container:
        return
    value = container.pop(key)
    redacted_fields.append(field_path)
    if isinstance(value, str):
        container[f"{key}Sha256"] = _sha256_text(value)
        container[f"{key}Length"] = len(value)


def _redact_text_list(
    container: dict[str, Any],
    key: str,
    *,
    field_path: str,
    redacted_fields: list[str],
) -> None:
    if key not in container:
        return
    value = container.pop(key)
    redacted_fields.append(field_path)
    if isinstance(value, list):
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        container[f"{key}Sha256"] = _sha256_text(serialized)
        container[f"{key}Length"] = len(serialized)


def _normalized_key(key: object) -> str:
    return "".join(character for character in str(key).casefold() if character.isalnum())


def _is_credential_field(key: str) -> bool:
    return key in _CREDENTIAL_FIELDS or key.endswith(
        ("apikey", "authorization", "credential", "password", "secret", "token")
    )


def _redact_nested_sensitive_fields(
    value: Any,
    *,
    field_path: str,
    redacted_fields: list[str],
) -> None:
    if isinstance(value, dict):
        for key in list(value):
            item = value[key]
            normalized = _normalized_key(key)
            child_path = f"{field_path}.{key}" if field_path else str(key)
            if normalized in _CONTEXT_ID_FIELDS and isinstance(item, list):
                value[key] = [_sha256_text(str(context_id)) for context_id in item]
                redacted_fields.append(child_path)
            elif (
                normalized in _CONTENT_FIELDS
                or normalized in _TOOL_ARGUMENT_FIELDS
                or _is_credential_field(normalized)
                or normalized in _CONTEXT_ID_FIELDS
            ):
                serialized = (
                    item
                    if isinstance(item, str)
                    else json.dumps(item, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                )
                value.pop(key)
                value[f"{key}Sha256"] = _sha256_text(serialized)
                value[f"{key}Length"] = len(serialized)
                redacted_fields.append(child_path)
            else:
                _redact_nested_sensitive_fields(
                    item,
                    field_path=child_path,
                    redacted_fields=redacted_fields,
                )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _redact_nested_sensitive_fields(
                item,
                field_path=f"{field_path}[{index}]",
                redacted_fields=redacted_fields,
            )


def _content_safe_report(report: dict[str, Any]) -> dict[str, Any]:
    """Remove raw user, evidence, tool, context, and credential content."""

    safe = copy.deepcopy(report)
    if "baseUrl" in safe:
        base_url = safe["baseUrl"]
        if not isinstance(base_url, str):
            raise ValueError("Persisted base URL must be a string.")
        safe["baseUrl"] = _validate_base_url(base_url)
    if "mcpUrl" in safe:
        mcp_url = safe["mcpUrl"]
        if not isinstance(mcp_url, str):
            raise ValueError("Persisted MCP URL must be a string.")
        safe["mcpUrl"] = _validate_mcp_url(mcp_url)
    redacted_fields: list[str] = []

    for key in ("intent", "prompt", "query", "question"):
        _redact_text_field(
            safe,
            key,
            field_path=key,
            redacted_fields=redacted_fields,
        )

    scenario = safe.get("scenario")
    if isinstance(scenario, dict):
        for key in ("intent", "prompt", "query", "question"):
            _redact_text_field(
                scenario,
                key,
                field_path=f"scenario.{key}",
                redacted_fields=redacted_fields,
            )
        for key in ("rehearsalPrompts", "prompts"):
            _redact_text_list(
                scenario,
                key,
                field_path=f"scenario.{key}",
                redacted_fields=redacted_fields,
            )

    execution = safe.get("execution")
    if isinstance(execution, dict):
        for key in ("answer", "sourcesMarkdown", "intent", "prompt", "query", "question"):
            _redact_text_field(
                execution,
                key,
                field_path=f"execution.{key}",
                redacted_fields=redacted_fields,
            )
        for key in ("rehearsalPrompts", "prompts"):
            _redact_text_list(
                execution,
                key,
                field_path=f"execution.{key}",
                redacted_fields=redacted_fields,
            )

    _redact_nested_sensitive_fields(
        safe,
        field_path="",
        redacted_fields=redacted_fields,
    )

    if redacted_fields:
        existing = safe.get("persistence")
        persistence = existing if isinstance(existing, dict) else {}
        persistence.update(
            {
                "contentSafe": True,
                "redactedFields": sorted(redacted_fields),
            }
        )
        if any(field.endswith(".intent") or field == "intent" for field in redacted_fields):
            persistence.update(
                {
                    "rawIntentPersisted": False,
                    "intentHashedOnPersist": True,
                }
            )
        safe["persistence"] = persistence
    return safe


async def certify_session(
    session: ClientSession,
    command: OrchestrationCommand,
) -> dict[str, Any]:
    """Run and grade one launch command-center execution."""

    initialized = await session.initialize()
    listed = await session.list_tools()
    tools = {tool.name: tool for tool in listed.tools}
    progress_events: list[dict[str, Any]] = []
    started = time.perf_counter()

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
                    "elapsedMs": int((time.perf_counter() - started) * 1_000),
                }
            )

    command_payload = command.model_dump(mode="json", by_alias=True)
    orchestration_call = await session.call_tool(
        "orchestrate",
        {"command": command_payload},
        progress_callback=capture_progress,
    )
    completion_ms = int((time.perf_counter() - started) * 1_000)
    payload_bytes = _payload_bytes(orchestration_call)
    presentation = OrchestrationPresentation.model_validate(
        _structured(orchestration_call, "orchestrate")
    )
    audit_call = await session.call_tool(
        "get_execution",
        {"execution_id": presentation.execution_id},
    )
    snapshot = ExecutionSnapshot.model_validate(_structured(audit_call, "get_execution"))
    if snapshot.summary is None:
        raise RuntimeError("The launch execution completed without an audit summary.")
    summary = snapshot.summary

    selected_ids = tuple(item.agent_id for item in summary.selection.selected)
    plan_agent_ids = tuple(step.agent_id for step in summary.plan.steps)
    expected_step_ids = tuple(f"step-{index}" for index in range(1, len(EXPECTED_AGENTS) + 1))
    expected_dependencies = tuple(
        () if index == 0 else (f"step-{index}",) for index in range(len(EXPECTED_AGENTS))
    )
    actual_dependencies = tuple(step.depends_on for step in summary.plan.steps)
    work_iq_context = {
        step.agent_id: tuple(
            item.context_id for item in step.context_inputs if item.kind is ContextKind.WORK_IQ
        )
        for step in summary.plan.steps
    }
    expected_work_iq_context = {
        agent_id: (context_id,) for agent_id, context_id in EXPECTED_CONTEXT.items()
    }
    all_context_ids = {
        item.context_id for step in summary.plan.steps for item in step.context_inputs
    }
    expected_prior_context = tuple(
        () if index == 0 else (f"step-{index}-result",) for index in range(len(EXPECTED_AGENTS))
    )
    actual_prior_context = tuple(
        tuple(
            item.context_id for item in step.context_inputs if item.kind is ContextKind.PRIOR_STEP
        )
        for step in summary.plan.steps
    )
    record_agents = {record.agent_id for record in summary.synthesis.evidence.records}
    records_by_id = {record.evidence_id: record for record in summary.synthesis.evidence.records}
    citation_agents = {
        records_by_id[citation.evidence_id].agent_id
        for citation in summary.synthesis.evidence.citations
        if citation.evidence_id in records_by_id
    }
    citation_evidence_ids = {
        citation.evidence_id for citation in summary.synthesis.evidence.citations
    }
    citation_numbers = tuple(citation.number for citation in summary.synthesis.evidence.citations)
    progress_messages = tuple(event["message"] for event in progress_events)
    progress_values = tuple(float(event["progress"]) for event in progress_events)
    selected_names = tuple(item.display_name for item in presentation.selected_agents)
    answer = presentation.answer
    sources_markdown = presentation.sources_markdown or ""
    expected_headings = tuple(f"### {name}" for name in selected_names)
    event_types = tuple(event.payload.type for event in snapshot.events)
    plane_status = _enterprise_plane_status()
    read_only_tool = (
        "orchestrate" in tools
        and tools["orchestrate"].annotations is not None
        and tools["orchestrate"].annotations.readOnlyHint is True
        and tools["orchestrate"].annotations.destructiveHint is False
    )
    approval_facts = _approval_facts(
        snapshot,
        summary,
        read_only_tool=read_only_tool,
    )
    handoff_facts = _handoff_facts(snapshot, summary, selected_ids)
    persisted_lifecycle = (
        bool(event_types)
        and event_types[0] == "request.accepted"
        and event_types[-1] == "execution.completed"
        and tuple(event.sequence for event in snapshot.events) == tuple(range(len(event_types)))
    )
    jargon = (
        "request.accepted",
        "catalog.searched",
        "candidates.ranked",
        "execution.completed",
        "spilled",
        "glob",
        ".json",
    )
    formation_meta = {
        "ui": {
            "resourceUri": FORMATION_RESOURCE_URI,
            "visibility": ["model", "app"],
        }
    }

    checks = {
        "initialize": bool(str(initialized.protocolVersion)),
        "formationApp": (
            tools["orchestrate"].meta == formation_meta
            and tools["orchestrate"].annotations is not None
            and tools["orchestrate"].annotations.readOnlyHint is True
            and tools["orchestrate"].annotations.destructiveHint is False
        ),
        "exactFiveAgentFormation": (
            selected_ids == EXPECTED_AGENTS and tuple(HERO_RESPONSIBILITIES) == EXPECTED_AGENTS
        ),
        "publicSelectionReasons": (
            len(summary.selection.selected) == len(EXPECTED_AGENTS)
            and all(
                reason.agent_id in HERO_RESPONSIBILITIES
                and reason.work_item_id.strip()
                and reason.summary.strip()
                for reason in summary.selection.selected
            )
        ),
        "sequentialDecisionDag": (
            plan_agent_ids == EXPECTED_AGENTS
            and tuple(step.step_id for step in summary.plan.steps) == expected_step_ids
            and actual_dependencies == expected_dependencies
            and summary.plan.max_concurrency == len(EXPECTED_AGENTS)
        ),
        "leastContextBindings": work_iq_context == expected_work_iq_context,
        "contextMinimized": all(
            len(step.context_inputs) == 1 + len(step.depends_on)
            and all(item.approved for item in step.context_inputs)
            for step in summary.plan.steps
        ),
        "priorStepHandoffs": actual_prior_context == expected_prior_context,
        "unapprovedContextExcluded": UNAPPROVED_CONTEXT_ID not in all_context_ids,
        "zeroMutationApprovals": all(not step.approval_required for step in summary.plan.steps),
        "approvalBeforeMutation": (
            approval_facts["analysisOnly"] is True
            and approval_facts["readOnlyTool"] is True
            and approval_facts["mutationArtifactsObserved"] == 0
            and approval_facts["mutationArtifactsRequireApproval"] is True
            and not approval_facts["approvalResolutionsObserved"]
        ),
        "allStepsCompleted": (
            summary.status.value == "completed"
            and all(step.status is StepExecutionStatus.SUCCEEDED for step in summary.steps)
        ),
        "nativeA2AOnly": all(
            step.transport is TransportProtocol.A2A
            and step.protocol_version == "1.0"
            and step.invocation_id is not None
            for step in summary.steps
        ),
        "groundingComplete": (
            summary.synthesis.status.value == "complete"
            and summary.synthesis.coverage.ratio == 1
            and summary.synthesis.coverage.grounded_claims
            == summary.synthesis.coverage.total_claims
            and record_agents == set(EXPECTED_AGENTS)
            and citation_agents == set(EXPECTED_AGENTS)
            and all(step.evidence_ids for step in summary.steps)
            and citation_evidence_ids <= set(records_by_id)
            and citation_numbers == tuple(range(1, len(citation_numbers) + 1))
            and len(summary.synthesis.evidence.citations) >= len(EXPECTED_AGENTS)
            and bool(sources_markdown.strip())
        ),
        "fiveReadinessSections": all(heading in answer for heading in expected_headings),
        "noContextLeakage": (
            "workiq.example.invalid" not in f"{answer}\n{sources_markdown}"
            and UNAPPROVED_CONTEXT_ID not in f"{answer}\n{sources_markdown}"
        ),
        "explicitFixturePlaneStatus": _fixture_planes_are_explicit(plane_status),
        "persistedLifecycle": persisted_lifecycle,
        "durableHandoffFacts": (
            handoff_facts["resumeFacts"]["executionId"] == summary.execution_id
            and handoff_facts["resumeFacts"]["requestId"] == summary.request_id
            and handoff_facts["resumeFacts"]["planId"] == summary.plan.plan_id
            and handoff_facts["resumeFacts"]["selectedAgentIds"] == list(selected_ids)
            and (
                (
                    handoff_facts["status"] == "not_required_for_sync_analysis"
                    and not handoff_facts["taskIds"]
                )
                or (
                    handoff_facts["status"] == "durable"
                    and bool(handoff_facts["taskIds"])
                    and bool(handoff_facts["callbackModes"])
                )
            )
        ),
        "noHiddenFallback": (
            all(step.invocation_id is not None for step in summary.steps)
            and not any(
                token in f"{answer}\n{sources_markdown}\n{' '.join(progress_messages)}".casefold()
                for token in FALLBACK_TERMS
            )
        ),
        "compactPayload": payload_bytes <= PAYLOAD_BUDGET,
        "readableProgress": (
            len(progress_messages) >= 9
            and progress_messages[0] == "Matching eligible specialists"
            and progress_messages[-1] == "Completed"
            and sum(
                message.startswith("Retrieving approved evidence from")
                for message in progress_messages
            )
            == len(EXPECTED_AGENTS)
            and list(progress_values) == sorted(progress_values)
            and all(event["total"] == 100 for event in progress_events)
            and not any(token in " ".join(progress_messages).casefold() for token in jargon)
        ),
        "withinCommandCenterDeadline": completion_ms <= 300_000,
    }
    failures = [name for name, passed in checks.items() if not passed]
    return {
        "schemaVersion": "1.0",
        "generatedAt": datetime.now(UTC).isoformat(),
        "status": "pass" if not failures else "fail",
        "checks": checks,
        "failures": failures,
        "scenario": {
            "name": "EU Cold-Water Launch Command Center",
            "intent": LAUNCH_INTENT,
            "analysisOnly": True,
            "heroAgentIds": list(EXPECTED_AGENTS),
            "heroResponsibilities": HERO_RESPONSIBILITIES,
            "lifecyclePhases": [dict(phase) for phase in SCENARIO_PHASES],
            "contextMode": "synthetic_metadata_only",
            "contextDisclosure": (
                "Work IQ references are synthetic, caller-bound metadata used only to "
                "certify least-context routing; no M365 source content was supplied. "
                "Foundry IQ and SAP data planes are deterministic fixtures and are not live."
            ),
            "enterpriseDataPlanes": plane_status,
            "mutationPolicy": (
                "This certificate performs analysis only. A separate mutation action "
                "requires an explicit approval before any SAP change."
            ),
            "fallbackPolicy": {
                "allowed": False,
                "mode": "fail_closed",
                "acceptedPath": "native_a2a_with_structured_evidence",
            },
        },
        "execution": {
            "executionId": summary.execution_id,
            "requestId": summary.request_id,
            "planId": summary.plan.plan_id,
            "status": summary.status.value,
            "durationMs": summary.duration_ms,
            "completionMs": completion_ms,
            "payloadBytes": payload_bytes,
            "payloadBudgetBytes": PAYLOAD_BUDGET,
            "selectedAgentIds": list(selected_ids),
            "selectionReasons": {
                reason.agent_id: reason.summary for reason in summary.selection.selected
            },
            "plan": [
                {
                    "stepId": step.step_id,
                    "agentId": step.agent_id,
                    "dependsOn": list(step.depends_on),
                    "contextIds": [item.context_id for item in step.context_inputs],
                    "approvalRequired": step.approval_required,
                }
                for step in summary.plan.steps
            ],
            "steps": [
                {
                    "stepId": step.step_id,
                    "agentId": step.agent_id,
                    "status": step.status.value,
                    "transport": step.transport.value,
                    "protocolVersion": step.protocol_version,
                    "durationMs": step.duration_ms,
                    "evidenceCount": len(step.evidence_ids),
                }
                for step in summary.steps
            ],
            "lifecycle": {
                "eventCount": len(snapshot.events),
                "eventTypes": list(event_types),
                "firstEvent": event_types[0] if event_types else None,
                "lastEvent": event_types[-1] if event_types else None,
            },
            "approvalGate": approval_facts,
            "handoff": handoff_facts,
            "progressEvents": progress_events,
            "sourceIds": sorted(
                {record.source_id for record in summary.synthesis.evidence.records}
            ),
            "citationCount": len(summary.synthesis.evidence.citations),
            "citationAgentIds": sorted(citation_agents),
            "evidenceByAgent": {
                agent_id: sorted(
                    record.evidence_id
                    for record in summary.synthesis.evidence.records
                    if record.agent_id == agent_id
                )
                for agent_id in EXPECTED_AGENTS
            },
            "answer": answer,
            "sourcesMarkdown": presentation.sources_markdown,
        },
        "persistence": {
            "contentSafe": True,
            "rawAnswerPersisted": False,
            "rawSourcesMarkdownPersisted": False,
            "rawIntentPersisted": False,
            "intentHashedOnPersist": True,
            "contextIdsHashedOnPersist": True,
        },
    }


async def certify_endpoint(
    mcp_url: str,
    command: OrchestrationCommand,
    *,
    timeout_seconds: float = 360,
) -> dict[str, Any]:
    """Connect to the remote Cowork MCP endpoint and certify one scenario."""

    mcp_url = _validate_mcp_url(mcp_url)
    timeout = httpx.Timeout(timeout_seconds)
    async with (
        _non_following_http_client(timeout) as http_client,
        streamable_http_client(mcp_url, http_client=http_client) as (
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
        return await certify_session(session, command)


async def _reject_redirect(response: httpx.Response) -> None:
    if response.is_redirect:
        raise RuntimeError("Remote MCP endpoint returned a redirect; certification fails closed.")


def _non_following_http_client(timeout: httpx.Timeout) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        event_hooks={"response": [_reject_redirect]},
    )


def write_report(
    report: dict[str, Any],
    output_root: Path = DEFAULT_REPORT_ROOT,
) -> Path:
    """Persist a sanitized launch command-center certificate."""

    safe_report = _content_safe_report(report)
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
    raise RuntimeError("Could not allocate a unique launch command-center report name.")


def _atomic_write_text(path: Path, text: str) -> None:
    temporary_name: str | None = None
    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        with os.fdopen(file_descriptor, "w", encoding="ascii", newline="") as stream:
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
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        required=not bool(DEFAULT_BASE_URL),
        type=_base_url_argument,
    )
    parser.add_argument("--request-id")
    parser.add_argument("--timeout-seconds", type=float, default=360)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()

    base_url = _validate_base_url(arguments.base_url)
    mcp_url = _validate_mcp_url(f"{base_url}/mcp")
    command = build_launch_command(request_id=arguments.request_id)
    report = asyncio.run(
        certify_endpoint(
            mcp_url,
            command,
            timeout_seconds=arguments.timeout_seconds,
        )
    )
    report["mcpUrl"] = mcp_url
    if not arguments.no_report:
        report["reportPath"] = str(write_report(report, arguments.output_root))
    if arguments.json:
        print(json.dumps(_content_safe_report(report), indent=2, sort_keys=True))
    else:
        print(f"Launch command center: {report['status'].upper()}")
        print(f"Selected: {', '.join(report['execution']['selectedAgentIds'])}")
        print(f"Completion: {report['execution']['completionMs']} ms")
        if not arguments.no_report:
            print(f"Report: {report['reportPath']}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
