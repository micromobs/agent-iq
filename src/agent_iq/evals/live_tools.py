"""Content-safe evaluation of live native-A2A hero tool traces."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from agent_iq.contracts import SapSourceMode, ToolBackend
from agent_iq.contracts.base import (
    ContractModel,
    Identifier,
    NonEmptyText,
    SchemaVersion,
    ShortText,
    require_unique_text,
)
from agent_iq.runtime.telemetry import ToolTelemetryEvent

_OPERATION_ID = re.compile(r"^[0-9a-f]{32}$")
_OPAQUE_TOOL_IDS = {"knowledge_base", "knowledge_base_retrieve"}
RUN_REPORT_PREFIX = "live-tools-"
MAX_RUN_REPORTS = 20
_RUN_REPORT_NAME = re.compile(
    rf"^{re.escape(RUN_REPORT_PREFIX)}\d{{8}}T\d{{12}}Z-[0-9a-f]{{32}}\.json$"
)
_RAW_CONTENT_KEYS = frozenset(
    {
        "answer",
        "args",
        "arguments",
        "callarguments",
        "functionargs",
        "functionarguments",
        "input",
        "kwargs",
        "rawanswer",
        "rawargs",
        "rawarguments",
        "rawinput",
        "rawoutput",
        "rawresponse",
        "rawtoolargs",
        "rawtoolarguments",
        "output",
        "parameters",
        "prompttext",
        "response",
        "toolargs",
        "toolinput",
        "toolarguments",
        "toolparams",
        "token",
        "accesstoken",
        "authorization",
        "userprompt",
    }
)
_REQUIRED_PROBE_CHECKS = (
    "agentActive",
    "hostedKind",
    "responsesProtocolV2",
    "pythonRuntimeIs313",
    "modelDeploymentMatches",
    "canonicalToolboxBound",
    "codeContentHashPresent",
    "cardNameMatches",
    "cardVersionIsV1",
    "cardMatchesCanonical",
    "nativeCardTracePresent",
    "nativeMessageTracePresent",
    "answerNonempty",
    "requiredTermsPresent",
    "sourceTitlePolicySatisfied",
    "traceContainsNoPrompt",
    "traceContainsNoToken",
    "traceContainsNoAuthorization",
)


class LiveToolCase(ContractModel):
    """One live hero invocation and its observable tool expectations."""

    case_id: Identifier
    agent_id: Identifier
    prompt: NonEmptyText
    expected_tool_ids: tuple[Identifier, ...] = Field(min_length=1)
    expected_backend: ToolBackend
    expected_source_mode: SapSourceMode | None = None
    required_answer_terms: tuple[ShortText, ...] = Field(min_length=1)
    required_sources: tuple[ShortText, ...] = ()
    require_source_titles: bool = False
    require_citation: bool = True

    @field_validator("expected_tool_ids", "required_answer_terms", "required_sources")
    @classmethod
    def validate_unique_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Live tool case values")

    @model_validator(mode="after")
    def validate_backend(self) -> LiveToolCase:
        if self.expected_backend is ToolBackend.SAP:
            if self.expected_source_mode is None:
                raise ValueError("A SAP live tool case must declare its source mode.")
        elif self.expected_source_mode is not None:
            raise ValueError("Only SAP live tool cases can declare a source mode.")
        return self


class LiveToolSuite(ContractModel):
    """Versioned set of live hero tool cases."""

    schema_version: SchemaVersion = "1.0"
    suite_id: Literal["consumer-hero-tools-live"] = "consumer-hero-tools-live"
    cases: tuple[LiveToolCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_cases(self) -> LiveToolSuite:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("Live tool case IDs must be unique.")
        return self


class _ToolTelemetryWire(ContractModel):
    event: Literal["agent_iq.tool"]
    occurred_at: datetime
    agent_id: Identifier
    tool_id: Identifier
    backend: str
    status: str
    duration_ms: int | None
    cost_units: float | None
    cost_capture_status: str
    source_mode: str | None = None
    arguments_captured: bool

    def to_event(self) -> ToolTelemetryEvent:
        return ToolTelemetryEvent(
            occurred_at=self.occurred_at,
            agent_id=self.agent_id,
            tool_id=self.tool_id,
            backend=self.backend,
            status=self.status,
            duration_ms=self.duration_ms,
            cost_units=self.cost_units,
            cost_capture_status=self.cost_capture_status,
            source_mode=self.source_mode,
            arguments_captured=self.arguments_captured,
        )


def load_live_tool_suite(path: Path) -> LiveToolSuite:
    """Load a strict live tool suite from JSON."""

    try:
        return LiveToolSuite.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Cannot read live tool cases at {path}: {exc}") from exc


def parse_tool_telemetry_messages(messages: Sequence[str]) -> tuple[ToolTelemetryEvent, ...]:
    """Parse only the content-safe JSON emitted by LoggingToolTelemetry."""

    return tuple(_ToolTelemetryWire.model_validate_json(message).to_event() for message in messages)


def message_operation_id(probe: Mapping[str, Any]) -> str:
    """Extract the native message/send request ID used as the hosted trace operation ID."""

    trace = probe.get("trace")
    if not isinstance(trace, Mapping):
        raise ValueError("The native A2A probe did not contain a trace object.")
    events = trace.get("events")
    if not isinstance(events, list):
        raise ValueError("The native A2A probe did not contain trace events.")
    for event in events:
        if not isinstance(event, Mapping) or event.get("operation") != "foundry.a2a.message.send":
            continue
        headers = event.get("responseHeaders")
        operation_id = headers.get("x-request-id") if isinstance(headers, Mapping) else None
        if isinstance(operation_id, str) and _OPERATION_ID.fullmatch(operation_id):
            return operation_id
    raise ValueError("The native A2A message trace did not expose a valid operation ID.")


def evaluate_live_tool_case(
    case: LiveToolCase,
    probe: Mapping[str, Any],
    telemetry: Sequence[ToolTelemetryEvent],
) -> dict[str, Any]:
    """Evaluate one live result without persisting its prompt, answer, or tool arguments."""

    probe_checks = probe.get("checks")
    invocation = probe.get("invocation")
    transport = probe.get("transport")
    agent = probe.get("agent")
    if not isinstance(probe_checks, Mapping) or not isinstance(invocation, Mapping):
        raise ValueError("The native A2A probe is missing checks or invocation metadata.")
    if not isinstance(transport, Mapping) or not isinstance(agent, Mapping):
        raise ValueError("The native A2A probe is missing transport or agent metadata.")
    answer = invocation.get("answer")
    if not isinstance(answer, str):
        raise ValueError("The native A2A probe answer must be text.")

    operation_id = message_operation_id(probe)
    events = tuple(event for event in telemetry if event.agent_id == case.agent_id)
    observed_tool_ids = {event.tool_id for event in events}
    expected_tool_ids = set(case.expected_tool_ids)
    expected_source_mode = (
        case.expected_source_mode.value if case.expected_source_mode is not None else None
    )
    expected_events = tuple(event for event in events if event.tool_id in expected_tool_ids)

    checks = {
        **{f"probe.{name}": probe_checks.get(name) is True for name in _REQUIRED_PROBE_CHECKS},
        "probe.sourceReferencePolicySatisfied": (
            not case.require_citation or probe_checks.get("sourceReferencePresent") is True
        ),
        "transport.nativeA2AV1": (
            transport.get("protocol") == "a2a"
            and transport.get("protocolVersion") == "1.0"
            and transport.get("binding") == "JSONRPC"
        ),
        "transport.noFallback": transport.get("fallbackUsed") is False,
        "telemetry.present": bool(events),
        "telemetry.exactToolSet": observed_tool_ids == expected_tool_ids,
        "telemetry.expectedToolsCompleted": (
            bool(expected_events) and all(event.status == "completed" for event in expected_events)
        ),
        "telemetry.backendMatches": (
            bool(expected_events)
            and all(event.backend == case.expected_backend.value for event in expected_events)
        ),
        "telemetry.sourceModeMatches": (
            bool(expected_events)
            and all(event.source_mode == expected_source_mode for event in expected_events)
        ),
        "telemetry.argumentsNotCaptured": all(not event.arguments_captured for event in events),
        "telemetry.costStatusExplicit": all(
            event.cost_capture_status in {"captured", "disabled", "unavailable"} for event in events
        ),
        "telemetry.opaqueCatchAllAbsent": not observed_tool_ids.intersection(_OPAQUE_TOOL_IDS),
    }
    failures = [name for name, passed in checks.items() if not passed]
    return {
        "caseId": case.case_id,
        "agentId": case.agent_id,
        "agentVersion": agent.get("version"),
        "status": "pass" if not failures else "fail",
        "operationId": operation_id,
        "promptSha256": _sha256(case.prompt),
        "answerSha256": _sha256(answer),
        "answerLength": len(answer),
        "latencyMs": invocation.get("latencyMs"),
        "expectedToolIds": list(case.expected_tool_ids),
        "observedToolIds": sorted(observed_tool_ids),
        "expectedBackend": case.expected_backend.value,
        "expectedSourceMode": expected_source_mode,
        "toolEvents": [
            {
                "occurredAt": event.occurred_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "agentId": event.agent_id,
                "toolId": event.tool_id,
                "backend": event.backend,
                "status": event.status,
                "durationMs": event.duration_ms,
                "costUnits": event.cost_units,
                "costCaptureStatus": event.cost_capture_status,
                "sourceMode": event.source_mode,
                "argumentsCaptured": event.arguments_captured,
            }
            for event in events
        ],
        "checks": checks,
        "failures": failures,
    }


def build_live_tool_payload(
    case_results: Sequence[Mapping[str, Any]],
    *,
    expected_case_count: int,
    project_endpoint: str,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the aggregate five-hero live certificate."""

    if expected_case_count <= 0:
        raise ValueError("Expected live tool case count must be positive.")
    timestamp = (generated_at or datetime.now(UTC)).astimezone(UTC)
    results = [dict(result) for result in case_results]
    _assert_content_safe(results)
    passed = sum(result.get("status") == "pass" for result in results)
    exact_tool_selections = sum(
        isinstance(result.get("checks"), Mapping)
        and result["checks"].get("telemetry.exactToolSet") is True
        for result in results
    )
    complete_suite = len(results) == expected_case_count
    opaque_absent = all(
        isinstance(result.get("checks"), Mapping)
        and result["checks"].get("telemetry.opaqueCatchAllAbsent") is True
        for result in results
    )
    arguments_safe = all(
        isinstance(result.get("checks"), Mapping)
        and result["checks"].get("telemetry.argumentsNotCaptured") is True
        for result in results
    )
    return {
        "schemaVersion": "1.0",
        "suiteId": "consumer-hero-tools-live",
        "generatedAt": timestamp.isoformat().replace("+00:00", "Z"),
        "status": "pass" if complete_suite and passed == expected_case_count else "fail",
        "projectEndpoint": project_endpoint.rstrip("/"),
        "cases": len(results),
        "expectedCases": expected_case_count,
        "passedCases": passed,
        "exactToolSelection": exact_tool_selections / expected_case_count,
        "completeSuite": complete_suite,
        "opaqueCatchAllSelections": 0 if opaque_absent else 1,
        "argumentsCaptured": not arguments_safe,
        "caseResults": results,
        "failures": [
            {"caseId": result.get("caseId"), "checks": result.get("failures", [])}
            for result in results
            if result.get("status") != "pass"
        ],
    }


def write_live_tool_report(payload: Mapping[str, Any], output_dir: Path) -> Path:
    """Atomically publish timestamped and latest live tool reports."""

    if (
        payload.get("schemaVersion") != "1.0"
        or payload.get("suiteId") != "consumer-hero-tools-live"
    ):
        raise ValueError("Unsupported live tool evaluation payload.")
    generated_at = payload.get("generatedAt")
    if not isinstance(generated_at, str) or not generated_at.strip():
        raise ValueError("Live tool report generatedAt cannot be empty.")
    _assert_content_safe(payload)
    output_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = output_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    run_path = _new_run_path(runs_dir)
    _atomic_write(run_path, text)
    _atomic_write(output_dir / "latest.json", text)
    _prune_run_reports(runs_dir)
    return run_path


def _new_run_path(runs_dir: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    for _ in range(100):
        candidate = runs_dir / f"{RUN_REPORT_PREFIX}{stamp}-{uuid.uuid4().hex}.json"
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not allocate a unique live tool report name.")


def _prune_run_reports(runs_dir: Path) -> None:
    run_paths = sorted(
        (
            path
            for path in runs_dir.iterdir()
            if path.is_file() and _RUN_REPORT_NAME.fullmatch(path.name)
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    for path in run_paths[MAX_RUN_REPORTS:]:
        path.unlink(missing_ok=True)


def _sha256(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _atomic_write(path: Path, text: str) -> None:
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        assert temporary_name is not None
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _assert_content_safe(value: object, *, location: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_name = str(key)
            normalized = re.sub(r"[^a-z0-9]", "", key_name.lower())
            child_location = f"{location}.{key_name}"
            if normalized == "prompt" and not _is_hash_only_prompt(child):
                raise ValueError(f"Raw prompt content is not allowed in {child_location}.")
            if normalized in _RAW_CONTENT_KEYS:
                raise ValueError(f"Raw sensitive content is not allowed in {child_location}.")
            _assert_content_safe(child, location=child_location)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _assert_content_safe(child, location=f"{location}[{index}]")


def _is_hash_only_prompt(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    if set(value) != {"length", "hash"}:
        return False
    return (
        isinstance(value["length"], int)
        and isinstance(value["hash"], str)
        and value["hash"].startswith("sha256:")
    )
