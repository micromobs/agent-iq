"""Certify a dynamic two-agent plan against live Foundry native A2A."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_iq.catalog import FoundryCatalogAdapter, load_consumer_assets
from agent_iq.config import Settings, canonicalize_foundry_project_endpoint
from agent_iq.connectors import DefaultAzureBearerTokenProvider
from agent_iq.contracts import (
    CallerContext,
    ExecutionStatus,
    ExecutionSummary,
    MatchFilters,
    MatchRequest,
    StepExecutionStatus,
    TransportProtocol,
)
from agent_iq.evidence import (
    CompanionEvidenceStore,
    EvidenceRecordMapper,
    EvidenceResolver,
    FoundryIQEvidenceRetriever,
    StructuredEvidenceDispatcher,
)
from agent_iq.match import MatchEngine
from agent_iq.orchestrate import (
    DagExecutor,
    ExecutionPlanner,
    GroundedSynthesizer,
    OrchestrationEngine,
    PlannerPolicy,
)
from agent_iq.runtime import MemoryRuntimeTelemetry, NativeA2ARuntime, NativeOnlyDispatcher
from agent_iq.runtime.telemetry import RuntimeTelemetryEvent

DEFAULT_PROJECT = os.environ.get("AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT", "")
DEFAULT_ACCOUNT = os.environ.get("AGENT_IQ_AZURE_AI_ACCOUNT", "")
DEFAULT_PROJECT_NAME = os.environ.get("AGENT_IQ_AZURE_AI_PROJECT", "")
DEFAULT_SEARCH = os.environ.get("AGENT_IQ_SEARCH_SERVICE", "")
SCENARIO_ID = "d10-independent-demand-manufacturing"
INTENT = "Improve forecast accuracy with demand sensing and raise OEE on the bottling line"
EXPECTED_AGENTS = (
    "consumer-demand-forecasting",
    "consumer-manufacturing-operations",
)
RUN_REPORT_PREFIX = "live-orchestration-"
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


async def certify(arguments: argparse.Namespace) -> dict[str, Any]:
    assets = load_consumer_assets()
    token_provider = DefaultAzureBearerTokenProvider()
    catalog = FoundryCatalogAdapter(
        arguments.project_endpoint,
        token_provider=token_provider,
        expected_descriptors=assets.catalog.agents,
        cache_ttl_seconds=3600,
    )
    telemetry = MemoryRuntimeTelemetry()
    runtime = NativeA2ARuntime(
        arguments.project_endpoint,
        token_provider=token_provider,
        catalog=catalog,
        telemetry=telemetry,
    )
    retriever = FoundryIQEvidenceRetriever(
        f"https://{arguments.search_service}.search.windows.net",
        assets=assets,
    )
    evidence_dispatcher = StructuredEvidenceDispatcher(
        NativeOnlyDispatcher(runtime),
        retriever,
        mapper=EvidenceRecordMapper(assets),
        store=CompanionEvidenceStore(),
    )
    matcher = MatchEngine(
        assets.catalog,
        semantic_mode="offline",
        settings=Settings(_env_file=None),
    )
    planner = ExecutionPlanner(
        assets.catalog,
        policy=PlannerPolicy(
            max_agents=2,
            max_concurrency=2,
            step_deadline_seconds=arguments.step_deadline,
            overall_deadline_seconds=arguments.overall_deadline,
        ),
    )
    executor = DagExecutor(
        assets.catalog,
        evidence_dispatcher,
        GroundedSynthesizer(EvidenceResolver(arguments.public_source_base)),
    )
    engine = OrchestrationEngine(matcher, planner, executor)
    request = MatchRequest(
        request_id="d10-live-canary",
        intent=INTENT,
        caller=CallerContext(
            subject_id="d10-canary-caller",
            tenant_id="d10-canary-tenant",
        ),
        filters=MatchFilters(
            max_candidates=20,
            max_selected=2,
            required_protocols=(TransportProtocol.A2A,),
        ),
        requested_at=datetime.now(UTC),
    )

    try:
        await catalog.list_agents(refresh=True)
        summary = await engine.orchestrate(request)
        return _certificate(
            summary,
            telemetry.events,
            project_endpoint=arguments.project_endpoint,
            search_service=arguments.search_service,
        )
    finally:
        retriever.close()
        await runtime.close()
        await catalog.close()
        await token_provider.close()


def _certificate(
    summary: ExecutionSummary,
    events: list[RuntimeTelemetryEvent],
    *,
    project_endpoint: str,
    search_service: str,
) -> dict[str, Any]:
    steps_by_id = {step.step_id: step for step in summary.plan.steps}
    dependencies = tuple(
        sorted(
            (
                step.agent_id,
                tuple(steps_by_id[dependency].agent_id for dependency in step.depends_on),
            )
            for step in summary.plan.steps
            if step.depends_on
        )
    )
    selected = tuple(sorted(reason.agent_id for reason in summary.selection.selected))
    completed_agents = {event.agent_id for event in events if event.name == "invocation.completed"}
    step_duration_sum = sum(step.duration_ms for step in summary.steps)
    checks = {
        "exactSelectedAgentSet": selected == tuple(sorted(EXPECTED_AGENTS)),
        "exactDependencyDag": dependencies == (),
        "executionCompleted": summary.status is ExecutionStatus.COMPLETED,
        "allStepsSucceeded": all(
            step.status is StepExecutionStatus.SUCCEEDED for step in summary.steps
        ),
        "nativeA2AOnly": all(
            step.transport is TransportProtocol.A2A and step.protocol_version == "1.0"
            for step in summary.steps
        ),
        "authenticatedEvidenceEveryStep": all(step.evidence_ids for step in summary.steps),
        "groundedCoverageComplete": (
            summary.synthesis.coverage.ratio == 1
            and summary.synthesis.coverage.total_claims == len(summary.steps)
        ),
        "runtimeTelemetryComplete": completed_agents == set(EXPECTED_AGENTS),
        "parallelExecutionObserved": (
            len(summary.steps) == 2
            and all(not step.depends_on for step in summary.plan.steps)
            and summary.duration_ms < step_duration_sum
        ),
    }
    failures = [name for name, passed in checks.items() if not passed]
    failures.extend(
        f"{step.agent_id}:{step.error.code.value}"
        for step in summary.steps
        if step.error is not None
    )
    return {
        "schemaVersion": "1.0",
        "generatedAt": datetime.now(UTC).isoformat(),
        "status": "pass" if not failures else "fail",
        "scenarioId": SCENARIO_ID,
        "projectEndpoint": project_endpoint.rstrip("/"),
        "searchService": search_service,
        "transport": "native_a2a",
        "checks": checks,
        "selectedAgentIds": list(selected),
        "dependencies": [
            {"agentId": agent_id, "dependsOnAgentIds": list(depends_on)}
            for agent_id, depends_on in dependencies
        ],
        "execution": {
            "executionId": summary.execution_id,
            "status": summary.status.value,
            "durationMs": summary.duration_ms,
            "stepDurationSumMs": step_duration_sum,
            "steps": [
                {
                    "stepId": step.step_id,
                    "agentId": step.agent_id,
                    "status": step.status.value,
                    "transport": step.transport.value,
                    "protocolVersion": step.protocol_version,
                    "durationMs": step.duration_ms,
                    "evidenceCount": len(step.evidence_ids),
                    "artifactCount": len(step.artifacts),
                    "errorCode": step.error.code.value if step.error is not None else None,
                }
                for step in summary.steps
            ],
        },
        "synthesis": {
            "status": summary.synthesis.status.value,
            "answerLength": len(summary.synthesis.answer),
            "answerSha256": _sha256(summary.synthesis.answer),
            "claimCount": summary.synthesis.coverage.total_claims,
            "groundedClaimCount": summary.synthesis.coverage.grounded_claims,
            "citationCount": len(summary.synthesis.evidence.citations),
            "issueCount": len(summary.synthesis.issues),
            "conflictCount": len(summary.synthesis.conflict_claim_ids),
        },
        "telemetry": [
            {
                "name": event.name,
                "timestamp": event.occurred_at.isoformat(),
                "invocationId": event.invocation_id,
                "agentId": event.agent_id,
                "attempt": event.attempt,
                "attributes": dict(event.attributes),
            }
            for event in events
        ],
        "failures": failures,
    }


def _write_report(payload: dict[str, Any], report_directory: Path) -> Path:
    _assert_content_safe(payload)
    report_directory.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    run_path = _new_run_path(report_directory)
    _atomic_write(run_path, text)
    _atomic_write(report_directory / "latest.json", text)
    _prune_run_reports(report_directory)
    return run_path


def _new_run_path(report_directory: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    for _ in range(100):
        candidate = report_directory / f"{RUN_REPORT_PREFIX}{stamp}-{uuid.uuid4().hex}.json"
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not allocate a unique live orchestration report name.")


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


def _prune_run_reports(report_directory: Path) -> None:
    run_paths = sorted(
        (
            path
            for path in report_directory.iterdir()
            if path.is_file() and _RUN_REPORT_NAME.fullmatch(path.name)
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    for path in run_paths[MAX_RUN_REPORTS:]:
        path.unlink(missing_ok=True)


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


def _sha256(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _bind_targets(arguments: argparse.Namespace) -> None:
    arguments.project_endpoint = canonicalize_foundry_project_endpoint(
        arguments.project_endpoint,
        expected_account=arguments.account_name,
        expected_project=arguments.project_name,
    )
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,58}[a-z0-9])?", arguments.search_service):
        raise ValueError("Search service is not a canonical Azure resource name.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-endpoint",
        default=DEFAULT_PROJECT,
    )
    parser.add_argument(
        "--account-name",
        default=DEFAULT_ACCOUNT,
    )
    parser.add_argument(
        "--project-name",
        default=DEFAULT_PROJECT_NAME,
    )
    parser.add_argument(
        "--search-service",
        default=DEFAULT_SEARCH,
    )
    parser.add_argument(
        "--public-source-base",
        default="https://agent-iq.example.test",
    )
    parser.add_argument("--step-deadline", type=int, default=240)
    parser.add_argument("--overall-deadline", type=int, default=300)
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("reports/evals/orchestration-live"),
    )
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()
    _bind_targets(arguments)

    payload = asyncio.run(certify(arguments))
    report = _write_report(payload, arguments.report_dir)
    if arguments.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            "Agent IQ live orchestration: "
            f"{payload['status'].upper()} "
            f"agents={len(payload['selectedAgentIds'])}/2 "
            f"report={report.resolve()}"
        )
        for failure in payload["failures"]:
            print(f"FAIL: {failure}")
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
