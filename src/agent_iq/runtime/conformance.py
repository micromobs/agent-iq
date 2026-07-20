"""Live native-A2A conformance evaluation with content-safe evidence."""

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
from typing import Any

from agent_iq.catalog import FoundryCatalogAdapter, load_consumer_assets
from agent_iq.connectors import DefaultAzureBearerTokenProvider
from agent_iq.errors import AgentIqOperationError

from .adapters import NativeA2ARuntime
from .models import NativeA2ARequest
from .telemetry import MemoryRuntimeTelemetry, RuntimeTelemetryEvent

HERO_AGENT_ID = "consumer-formulation-rd"
HERO_PROMPT = (
    "Which enzymes power BrightWash Cold's cold-water cleaning? "
    "Answer only from the connected knowledge base and cite the source."
)
RUN_REPORT_PREFIX = "foundry-a2a-conformance-"
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


async def evaluate_foundry_a2a(
    project_endpoint: str,
    *,
    hero_agent_id: str = HERO_AGENT_ID,
    hero_prompt: str = HERO_PROMPT,
) -> dict[str, Any]:
    """Negotiate every canonical consumer card and invoke one hero through native A2A."""

    descriptors = tuple(
        descriptor
        for descriptor in load_consumer_assets().catalog.agents
        if descriptor.agent_id.startswith("consumer-")
    )
    expected_ids = tuple(sorted(descriptor.agent_id for descriptor in descriptors))
    if len(expected_ids) != 20:
        raise ValueError("Live consumer conformance requires exactly 20 canonical agents.")

    telemetry = MemoryRuntimeTelemetry()
    token_provider = DefaultAzureBearerTokenProvider()
    catalog = FoundryCatalogAdapter(
        project_endpoint,
        token_provider=token_provider,
        expected_descriptors=descriptors,
        cache_ttl_seconds=3600.0,
    )
    runtime = NativeA2ARuntime(
        project_endpoint,
        token_provider=token_provider,
        catalog=catalog,
        telemetry=telemetry,
    )
    failures: list[str] = []
    cards: list[dict[str, Any]] = []
    hero: dict[str, Any] | None = None
    live_consumer_ids: tuple[str, ...] = ()

    try:
        try:
            inventory = await catalog.list_agents(refresh=True)
        except AgentIqOperationError as exc:
            failures.append(_safe_failure("inventory", exc))
            inventory = ()

        live_consumer_ids = tuple(
            sorted(agent.agent_id for agent in inventory if agent.agent_id.startswith("consumer-"))
        )
        if live_consumer_ids != expected_ids:
            failures.append("inventory: live consumer IDs do not exactly match the canonical 20.")

        if not failures:
            for agent_id in expected_ids:
                try:
                    negotiated = await runtime.negotiate_card(agent_id)
                except AgentIqOperationError as exc:
                    failures.append(_safe_failure(f"card:{agent_id}", exc))
                    continue
                cards.append(
                    {
                        "agentId": agent_id,
                        "agentVersion": negotiated.agent.version,
                        "protocolVersion": negotiated.protocol_version,
                        "health": negotiated.state.health.value,
                        "servedHash": negotiated.state.served_hash,
                        "expectedHash": negotiated.state.expected_hash,
                        "driftedFields": list(negotiated.state.drifted_fields),
                        "platformOmittedFields": list(negotiated.state.platform_omitted_fields),
                    }
                )

        if len(cards) == len(expected_ids) and not failures:
            try:
                result = await runtime.invoke(
                    NativeA2ARequest(
                        agent_id=hero_agent_id,
                        prompt=hero_prompt,
                        deadline_seconds=180.0,
                    )
                )
            except AgentIqOperationError as exc:
                failures.append(_safe_failure(f"invoke:{hero_agent_id}", exc))
            else:
                has_reference = "[ref_id:" in result.answer or "[1]" in result.answer
                if not has_reference:
                    failures.append("hero invocation: answer has no source-reference marker.")
                hero = {
                    "agentId": result.agent_id,
                    "agentVersion": result.agent_version,
                    "protocolVersion": result.protocol_version,
                    "durationMs": result.duration_ms,
                    "attemptCount": result.attempt_count,
                    "answerLength": len(result.answer),
                    "answerSha256": _sha256_text(result.answer),
                    "sourceReferencePresent": has_reference,
                }
    finally:
        await runtime.close()
        await catalog.close()
        await token_provider.close()

    card_ids = tuple(sorted(card["agentId"] for card in cards))
    status = "pass" if not failures else "fail"
    return {
        "schemaVersion": "1.0",
        "generatedAt": datetime.now(UTC).isoformat(),
        "status": status,
        "projectEndpoint": project_endpoint.rstrip("/"),
        "checks": {
            "exactCanonicalConsumerInventory": live_consumer_ids == expected_ids,
            "consumerAgentCount": len(live_consumer_ids),
            "allCardsNegotiated": card_ids == expected_ids,
            "allCardsCanonical": len(cards) == 20
            and all(not card["driftedFields"] for card in cards),
            "heroInvoked": hero is not None,
            "heroSourceReferencePresent": (
                hero is not None and bool(hero["sourceReferencePresent"])
            ),
            "nativeOnly": True,
        },
        "liveConsumerAgentIds": list(live_consumer_ids),
        "cards": cards,
        "heroInvocation": hero,
        "telemetry": [_safe_event(event) for event in telemetry.events],
        "failures": failures,
    }


def write_foundry_a2a_report(
    payload: dict[str, Any],
    report_directory: Path,
) -> Path:
    """Atomically persist the current safe conformance evidence."""

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
    raise RuntimeError("Could not allocate a unique Foundry A2A conformance report name.")


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


def _safe_failure(stage: str, error: AgentIqOperationError) -> str:
    return f"{stage}: {error.error.code.value}: {error.error.message}"


def _safe_event(event: RuntimeTelemetryEvent) -> dict[str, Any]:
    return {
        "name": event.name,
        "timestamp": event.occurred_at.isoformat(),
        "invocationId": event.invocation_id,
        "agentId": event.agent_id,
        "attempt": event.attempt,
        "attributes": dict(event.attributes),
    }


def _sha256_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"
