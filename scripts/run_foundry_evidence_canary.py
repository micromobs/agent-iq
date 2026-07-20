"""Certify the D8 structured-evidence channel against the live hero agent."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import os
import re
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from agent_iq.catalog import (
    FoundryCatalogAdapter,
    authored_card_projection,
    load_consumer_assets,
)
from agent_iq.config import canonicalize_foundry_project_endpoint
from agent_iq.connectors import DefaultAzureBearerTokenProvider
from agent_iq.contracts import (
    EVIDENCE_EXTENSION_URI,
    CallerContext,
    ErrorCode,
    EvidenceFetchRequest,
)
from agent_iq.errors import AgentIqOperationError
from agent_iq.evidence import (
    A2AEventShapeRecorder,
    CompanionEvidenceStore,
    EvidenceRecordMapper,
    FoundryIQEvidenceRetriever,
    RetrievedEvidenceSource,
    StructuredEvidenceDispatcher,
)
from agent_iq.runtime import NativeA2ARequest, NativeA2ARuntime, NativeOnlyDispatcher

AGENT_ID = "consumer-formulation-rd"
PROMPT = "Which enzymes power BrightWash Cold cleaning? Cite at least two distinct sources."
RUN_REPORT_PREFIX = "foundry-evidence-"
MAX_RUN_REPORTS = 20
_PHASE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_RUN_REPORT_NAME = re.compile(
    rf"^{re.escape(RUN_REPORT_PREFIX)}[A-Za-z0-9][A-Za-z0-9_-]*-"
    r"\d{8}T\d{12}Z-[0-9a-f]{32}\.json$"
)
_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SEARCH_SERVICE_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,58}[a-z0-9])?$")
_GATE_NAMES = (
    "catalogCapability",
    "servedCardExtensionOmitted",
    "responsesCompleted",
    "evidenceRecordCount",
    "requiredStableSources",
    "unknownSourceRejected",
    "callerIsolation",
    "responseMetadataCorrelated",
    "nativeA2ATextOnly",
    "companionSelected",
)


@dataclass(frozen=True, slots=True)
class ResponsesShape:
    output_item_types: tuple[str, ...]
    structured_output_count: int
    structured_output_keys: tuple[str, ...]
    completed: bool


async def _responses_shape(
    endpoint: str,
    token: str,
) -> ResponsesShape:
    item_types: list[str] = []
    structured_keys: set[str] = set()
    structured_count = 0
    completed = False
    async with (
        httpx.AsyncClient(
            timeout=httpx.Timeout(300.0),
            follow_redirects=False,
            trust_env=False,
        ) as client,
        client.stream(
            "POST",
            endpoint,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"input": PROMPT, "stream": True},
        ) as response,
    ):
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            raw = line[6:]
            if raw == "[DONE]":
                continue
            event = json.loads(raw)
            event_type = event.get("type")
            if event_type == "response.output_item.done":
                item = event.get("item")
                if isinstance(item, dict):
                    item_type = str(item.get("type") or "unknown")
                    item_types.append(item_type)
                    if item_type == "structured_outputs":
                        structured_count += 1
                        output = item.get("output")
                        if isinstance(output, dict):
                            structured_keys.update(str(key) for key in output)
            elif event_type == "response.completed":
                completed = True
            elif event_type == "response.failed":
                raise RuntimeError("The live Responses protocol invocation failed.")
    return ResponsesShape(
        output_item_types=tuple(item_types),
        structured_output_count=structured_count,
        structured_output_keys=tuple(sorted(structured_keys)),
        completed=completed,
    )


def _evidence_extension(payload: dict[str, Any]) -> dict[str, Any] | None:
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, dict):
        return None
    extensions = capabilities.get("extensions")
    if not isinstance(extensions, list):
        return None
    return next(
        (
            extension
            for extension in extensions
            if isinstance(extension, dict) and extension.get("uri") == EVIDENCE_EXTENSION_URI
        ),
        None,
    )


async def certify(arguments: argparse.Namespace) -> dict[str, Any]:
    project_endpoint = canonicalize_foundry_project_endpoint(
        arguments.project_endpoint,
        expected_account=arguments.account_name,
        expected_project=arguments.project_name,
    )
    if not _SEARCH_SERVICE_NAME.fullmatch(arguments.search_service):
        raise ValueError("Search service must be a canonical lowercase Azure resource name.")
    search_endpoint = f"https://{arguments.search_service}.search.windows.net"
    assets = load_consumer_assets()
    descriptor = next(agent for agent in assets.catalog.agents if agent.agent_id == AGENT_ID)
    token_provider = DefaultAzureBearerTokenProvider()
    catalog = FoundryCatalogAdapter(
        project_endpoint,
        token_provider=token_provider,
        expected_descriptors=(descriptor,),
    )
    shape_recorder = A2AEventShapeRecorder()
    runtime = NativeA2ARuntime(
        project_endpoint,
        token_provider=token_provider,
        catalog=catalog,
        event_text=shape_recorder,
    )
    retriever = FoundryIQEvidenceRetriever(
        search_endpoint,
        assets=assets,
    )
    mapper = EvidenceRecordMapper(assets)
    store = CompanionEvidenceStore()
    evidence_runtime = StructuredEvidenceDispatcher(
        NativeOnlyDispatcher(runtime),
        retriever,
        mapper=mapper,
        store=store,
        minimum_records=2,
    )
    caller = CallerContext(subject_id="d8-canary-caller", tenant_id="d8-canary-tenant")
    correlation_id = "d8-final"
    request = NativeA2ARequest(
        agent_id=AGENT_ID,
        prompt=PROMPT,
        correlation_id=correlation_id,
        deadline_seconds=240,
    )
    try:
        card = await runtime.negotiate_card(AGENT_ID, deadline_seconds=60)
        catalog_extension = _evidence_extension(authored_card_projection(descriptor))
        served_extension = _evidence_extension(dict(card.payload))
        token = await token_provider.get_token("https://ai.azure.com/.default")
        responses = await _responses_shape(
            (
                f"{project_endpoint}/agents/{AGENT_ID}/endpoint/"
                "protocols/openai/responses?api-version=v1"
            ),
            token,
        )
        catalog_extension_delivery = (
            catalog_extension.get("params", {}).get("delivery")
            if isinstance(catalog_extension, dict)
            and isinstance(catalog_extension.get("params"), dict)
            else None
        )
        served_extension_delivery = (
            served_extension.get("params", {}).get("delivery")
            if isinstance(served_extension, dict)
            and isinstance(served_extension.get("params"), dict)
            else None
        )
        result = await evidence_runtime.dispatch(request, caller=caller)
        if result.evidence is None:
            raise RuntimeError("The native result did not carry evidence response metadata.")
        fetch_request = EvidenceFetchRequest.from_reference(result.evidence)
        envelope = evidence_runtime.fetch_evidence(fetch_request, caller=caller)

        isolation: dict[str, bool] = {}
        isolation_cases = {
            "caller": (
                fetch_request,
                CallerContext(
                    subject_id="d8-other-caller",
                    tenant_id=caller.tenant_id,
                ),
            ),
            "tenant": (
                fetch_request,
                CallerContext(
                    subject_id=caller.subject_id,
                    tenant_id="d8-other-tenant",
                ),
            ),
            "invocation": (
                fetch_request.model_copy(update={"invocation_id": "inv-other"}),
                caller,
            ),
            "correlation": (
                fetch_request.model_copy(update={"correlation_id": "d8-other"}),
                caller,
            ),
        }
        for name, (isolated_request, isolated_caller) in isolation_cases.items():
            try:
                evidence_runtime.fetch_evidence(
                    isolated_request,
                    caller=isolated_caller,
                )
            except AgentIqOperationError as exc:
                isolation[name] = exc.error.code is ErrorCode.AUTHORIZATION_DENIED
            else:
                isolation[name] = False

        try:
            mapper.map(
                invocation_id="inv-unknown-source",
                agent_id=AGENT_ID,
                references=(
                    RetrievedEvidenceSource(
                        local_reference_id="0",
                        source_id="unknown-source",
                        title="Unknown",
                        knowledge_source="consumer-product-innovation-kb",
                    ),
                ),
            )
        except AgentIqOperationError as exc:
            unknown_source_rejected = exc.error.code is ErrorCode.EVIDENCE_UNAVAILABLE
        else:
            unknown_source_rejected = False

        a2a = shape_recorder.summary()
        source_ids = [record.source_id for record in envelope.records]
        required_sources = "consumer-brightwash-cold" in source_ids and any(
            source in source_ids
            for source in (
                "consumer-comparison-method",
                "consumer-clearwave-cold",
            )
        )
        common_gates = {
            "catalogCapability": catalog_extension_delivery == "companion",
            "servedCardExtensionOmitted": served_extension_delivery is None,
            "responsesCompleted": responses.completed,
            "evidenceRecordCount": len(envelope.records) >= 2,
            "requiredStableSources": required_sources,
            "unknownSourceRejected": unknown_source_rejected,
            "callerIsolation": all(isolation.values()),
            "responseMetadataCorrelated": (
                result.evidence.invocation_id == result.invocation_id
                and result.evidence.correlation_id == correlation_id
            ),
        }
        phase_gates = {
            "nativeA2ATextOnly": (a2a.text_part_count >= 1 and not a2a.preserves_structured_parts),
            "companionSelected": result.evidence.delivery.value == "companion",
        }
        gates = {**common_gates, **phase_gates}
        status = "pass" if all(gates.values()) else "fail"
        now = datetime.now(UTC)
        return {
            "schemaVersion": "1.0",
            "status": status,
            "phase": "final",
            "recordedAt": now.isoformat(),
            "transportDecision": {
                "selected": "companion",
                "reason": (
                    "Foundry incoming A2A supports text modality only; structured "
                    "evidence is correlated through Agent IQ."
                ),
                "officialSource": (
                    "https://learn.microsoft.com/azure/foundry/agents/how-to/"
                    "enable-agent-to-agent-endpoint"
                ),
                "githubSourceRevision": (
                    "04c093649faec3beea023e8407d5af4c8119f7be"  # pragma: allowlist secret
                ),
            },
            "agent": {
                "agentId": result.agent_id,
                "agentVersion": result.agent_version,
                "protocolVersion": result.protocol_version,
                "cardProtocolVersion": card.protocol_version,
                "catalogEvidenceExtension": catalog_extension_delivery,
                "servedEvidenceExtension": served_extension_delivery,
                "servedEvidenceExtensionPreserved": served_extension_delivery is not None,
                "platformOmittedFields": list(card.state.platform_omitted_fields),
            },
            "nativeA2A": {
                **asdict(a2a),
                "preservesStructuredParts": a2a.preserves_structured_parts,
                "answerLength": len(result.answer),
                "answerHash": _digest(result.answer),
                "answerHasReferenceMarker": "[ref_id:" in result.answer,
            },
            "responses": asdict(responses),
            "evidence": {
                "delivery": result.evidence.delivery.value,
                "recordCount": len(envelope.records),
                "records": [
                    {
                        "sourceId": record.source_id,
                        "localReferenceId": record.local_reference_id,
                        "title": record.title,
                        "excerptLength": len(record.excerpt),
                        "contentHash": record.content_hash,
                        "hasUri": record.locator.uri is not None,
                    }
                    for record in envelope.records
                ],
                "unknownSourceRejected": unknown_source_rejected,
                "isolation": isolation,
            },
            "prompt": {
                "length": len(PROMPT),
                "hash": _digest(PROMPT),
            },
            "gates": gates,
        }
    finally:
        retriever.close()
        await catalog.close()
        await token_provider.close()


def _digest(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _write_report(report: dict[str, Any], directory: Path) -> Path:
    persisted = _persisted_report(report)
    phase = _phase_name(persisted.get("phase"))
    directory.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(persisted, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    path = _new_run_path(directory, phase)
    _atomic_write(path, rendered)
    _atomic_write(directory / f"latest-{phase}.json", rendered)
    if phase == "final":
        _atomic_write(directory / "latest.json", rendered)
    _prune_phase_reports(directory, phase)
    return path


def _phase_name(value: object) -> str:
    if not isinstance(value, str) or _PHASE_NAME.fullmatch(value) is None:
        raise ValueError("Foundry evidence report phase must be a safe non-empty name.")
    return value


def _new_run_path(directory: Path, phase: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    prefix = f"{RUN_REPORT_PREFIX}{phase}-"
    for _ in range(100):
        candidate = directory / f"{prefix}{stamp}-{uuid.uuid4().hex}.json"
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not allocate a unique Foundry evidence report name.")


def _run_name_pattern(phase: str) -> re.Pattern[str]:
    return re.compile(
        rf"^{re.escape(RUN_REPORT_PREFIX + phase + '-')}"
        r"\d{8}T\d{12}Z-[0-9a-f]{32}\.json$"
    )


def _prune_phase_reports(directory: Path, phase: str) -> None:
    pattern = _run_name_pattern(phase)
    run_paths = sorted(
        (path for path in directory.iterdir() if path.is_file() and pattern.fullmatch(path.name)),
        key=lambda path: path.name,
        reverse=True,
    )
    for path in run_paths[MAX_RUN_REPORTS:]:
        path.unlink(missing_ok=True)


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


def _persisted_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Construct the only schema permitted to reach disk or JSON stdout."""

    transport = _mapping(report.get("transportDecision"))
    agent = _mapping(report.get("agent"))
    native = _mapping(report.get("nativeA2A"))
    responses = _mapping(report.get("responses"))
    evidence = _mapping(report.get("evidence"))
    prompt = _mapping(report.get("prompt"))
    official_source = _safe_https_url(transport.get("officialSource"))

    persisted: dict[str, Any] = {
        "schemaVersion": _string(report.get("schemaVersion")),
        "status": _string(report.get("status")),
        "phase": _string(report.get("phase")),
        "recordedAt": _string(report.get("recordedAt")),
        "transportDecision": _typed_fields(
            transport,
            {
                "selected": str,
                "githubSourceRevision": str,
            },
        ),
        "agent": _typed_fields(
            agent,
            {
                "agentId": str,
                "agentVersion": str,
                "protocolVersion": str,
                "cardProtocolVersion": str,
                "catalogEvidenceExtension": str,
                "servedEvidenceExtension": str,
                "servedEvidenceExtensionPreserved": bool,
            },
        ),
        "nativeA2A": _native_summary(native),
        "responses": _responses_summary(responses),
        "evidence": _evidence_summary(evidence),
        "prompt": _hash_length(prompt),
        "gates": _boolean_fields(_mapping(report.get("gates")), _GATE_NAMES),
        "persistence": {
            "contentSafe": True,
            "schema": "foundry-evidence-content-safe-1.0",
        },
    }
    if official_source is not None:
        persisted["transportDecision"]["officialSource"] = official_source
    omitted_field_count = _sequence_length(agent.get("platformOmittedFields"))
    if omitted_field_count is not None:
        persisted["agent"]["platformOmittedFieldCount"] = omitted_field_count
    return persisted


def _native_summary(native: Mapping[str, Any]) -> dict[str, Any]:
    summary = _typed_fields(
        native,
        {
            "event_count": int,
            "text_part_count": int,
            "data_part_count": int,
            "artifact_count": int,
            "preservesStructuredParts": bool,
            "answerLength": int,
            "answerHasReferenceMarker": bool,
        },
    )
    answer_hash = _sha256_value(native.get("answerHash"))
    if answer_hash is not None:
        summary["answerHash"] = answer_hash
    for source_name, count_name in (
        ("payload_kinds", "payloadKindCount"),
        ("metadata_keys", "metadataKeyCount"),
        ("data_keys", "dataKeyCount"),
    ):
        count = _sequence_length(native.get(source_name))
        if count is not None:
            summary[count_name] = count
    return summary


def _responses_summary(responses: Mapping[str, Any]) -> dict[str, Any]:
    summary = _typed_fields(
        responses,
        {
            "structured_output_count": int,
            "completed": bool,
        },
    )
    for source_name, count_name in (
        ("output_item_types", "outputItemTypeCount"),
        ("structured_output_keys", "structuredOutputKeyCount"),
    ):
        count = _sequence_length(responses.get(source_name))
        if count is not None:
            summary[count_name] = count
    return summary


def _evidence_summary(evidence: Mapping[str, Any]) -> dict[str, Any]:
    records = evidence.get("records")
    record_values = records if isinstance(records, list) else []
    return {
        **_typed_fields(
            evidence,
            {
                "delivery": str,
                "recordCount": int,
                "unknownSourceRejected": bool,
            },
        ),
        "records": [
            _persisted_evidence_record(record)
            for record in record_values
            if isinstance(record, Mapping)
        ],
        "isolation": _boolean_fields(
            _mapping(evidence.get("isolation")),
            ("caller", "tenant", "invocation", "correlation"),
        ),
    }


def _persisted_evidence_record(record: Mapping[str, Any]) -> dict[str, Any]:
    persisted = _typed_fields(
        record,
        {
            "excerptLength": int,
            "hasUri": bool,
        },
    )
    content_hash = _sha256_value(record.get("contentHash"))
    if content_hash is not None:
        persisted["contentHash"] = content_hash
    for source_name, persisted_name in (
        ("sourceId", "sourceId"),
        ("localReferenceId", "localReferenceId"),
        ("title", "title"),
    ):
        value = record.get(source_name)
        if isinstance(value, str):
            persisted[f"{persisted_name}Sha256"] = _digest(value)
            persisted[f"{persisted_name}Length"] = len(value)
    return persisted


def _hash_length(value: Mapping[str, Any]) -> dict[str, Any]:
    persisted: dict[str, Any] = {}
    length = value.get("length")
    digest = _sha256_value(value.get("hash"))
    if isinstance(length, int) and not isinstance(length, bool) and length >= 0:
        persisted["length"] = length
    if digest is not None:
        persisted["hash"] = digest
    return persisted


def _typed_fields(
    value: Mapping[str, Any],
    fields: Mapping[str, type[str] | type[int] | type[bool]],
) -> dict[str, Any]:
    persisted: dict[str, Any] = {}
    for name, expected_type in fields.items():
        child = value.get(name)
        if expected_type is int:
            if isinstance(child, int) and not isinstance(child, bool) and child >= 0:
                persisted[name] = child
        elif isinstance(child, expected_type):
            persisted[name] = child
    return persisted


def _boolean_fields(value: Mapping[str, Any], names: tuple[str, ...]) -> dict[str, bool]:
    return {name: child for name in names if isinstance((child := value.get(name)), bool)}


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _sequence_length(value: object) -> int | None:
    if isinstance(value, (list, tuple)):
        return len(value)
    return None


def _sha256_value(value: object) -> str | None:
    if isinstance(value, str) and _SHA256_DIGEST.fullmatch(value) is not None:
        return value
    return None


def _safe_https_url(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\\" in value
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        return None
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.netloc
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
        or hostname.casefold() == "localhost"
        or hostname.casefold().endswith((".localhost", ".local", ".internal"))
    ):
        return None
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return value
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return None
    return value


def _print_result(
    report: Mapping[str, Any],
    path: Path,
    *,
    json_output: bool,
) -> None:
    if json_output:
        print(json.dumps(_persisted_report(report), indent=2, sort_keys=True, ensure_ascii=True))
    else:
        print(f"Foundry structured evidence: {str(report['status']).upper()} ({path})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--account-name", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--search-service", required=True)
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("reports/evals/evidence"),
    )
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()
    try:
        arguments.project_endpoint = canonicalize_foundry_project_endpoint(
            arguments.project_endpoint,
            expected_account=arguments.account_name,
            expected_project=arguments.project_name,
        )
        if not _SEARCH_SERVICE_NAME.fullmatch(arguments.search_service):
            raise ValueError("Search service must be a canonical lowercase Azure resource name.")
    except ValueError as exc:
        parser.error(str(exc))
    report = asyncio.run(certify(arguments))
    path = _write_report(report, arguments.report_dir)
    _print_result(report, path, json_output=arguments.json)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
