"""Strict native-A2A certification for a Microsoft Foundry hosted-agent canary."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import re
import tempfile
import time
import uuid
from collections.abc import Callable, Generator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unicodedata import category
from urllib.parse import urlsplit

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_artifact_text, get_message_text
from a2a.types import Message, Part, Role, SendMessageRequest
from azure.identity import DefaultAzureCredential
from google.protobuf.json_format import MessageToDict

from agent_iq.catalog import CardProjectionError, card_hash, served_card_projection
from agent_iq.config import canonicalize_foundry_project_endpoint
from agent_iq.connectors.entra import isolated_default_azure_credential
from agent_iq.runtime.host_descriptor import (
    HOST_DESCRIPTOR_SHA256_ENV,
    HOST_LOGICAL_AGENT_ID_ENV,
    HOST_RUNTIME_FAMILY_ENV,
    HOST_TOOLBOX_NAME_ENV,
)

AZURE_AI_SCOPE = "https://ai.azure.com/.default"
CARD_PATH = "agentCard/v1.0"
DEFAULT_AGENT_ID = "consumer-formulation-rd"
DEFAULT_PROMPT = (
    "Which enzymes power BrightWash Cold's cold-water cleaning? "
    "Cite the source by its exact title and reference marker."
)
DEFAULT_REQUIRED_TERMS = ("protease", "amylase", "mannanase", "lipase")
DEFAULT_REQUIRED_SOURCE = "BrightWash Cold active system"
DEFAULT_REQUIRED_MODEL = "gpt-5.4-mini"
_AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_A2A_PROTOCOL_BINDING = "JSONRPC"
_A2A_PROTOCOL_VERSION = "1.0"
_SOURCE_REFERENCE = re.compile(r"(?:\[ref_id:\d+\]|【[^】]+†[^】]+】)", re.IGNORECASE)
_POSSESSIVE = re.compile(r"(?<=\w)['\u2019]s\b")
_EVIDENCE_TOKEN = re.compile(r"[a-z0-9]+")
_SAFE_RESPONSE_HEADERS = {
    "content-type",
    "traceparent",
    "x-ms-correlation-request-id",
    "x-ms-request-id",
    "x-request-id",
}


class CanaryProbeError(RuntimeError):
    """The live canary did not satisfy its strict certification contract."""


class _FoundryBearerAuth(httpx.Auth):
    """Attach a bearer token only to exact, pre-approved Foundry requests."""

    def __init__(
        self,
        token_provider: Callable[[], str],
        allowed_requests: frozenset[tuple[str, str]],
    ) -> None:
        self._token_provider = token_provider
        self._allowed_requests = allowed_requests

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        target = (request.method.upper(), str(request.url))
        if target not in self._allowed_requests:
            raise CanaryProbeError(
                "Refusing to authorize a request outside the canonical Foundry canary endpoints."
            )
        token = self._token_provider()
        if (
            not isinstance(token, str)
            or not token
            or any(char.isspace() or category(char) == "Cc" for char in token)
        ):
            raise CanaryProbeError("The Foundry token provider returned an invalid token.")
        request.headers["Authorization"] = f"Bearer {token}"
        yield request


class HttpTraceRecorder:
    """Capture transport metadata without request bodies or authorization headers."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def capture(self, response: httpx.Response) -> None:
        request = response.request
        self.events.append(
            {
                "operation": _trace_operation(request),
                "method": request.method,
                "url": str(request.url),
                "statusCode": response.status_code,
                "responseHeaders": {
                    name.casefold(): value
                    for name, value in response.headers.items()
                    if name.casefold() in _SAFE_RESPONSE_HEADERS
                },
            }
        )


def _trace_operation(request: httpx.Request) -> str:
    path = request.url.path.casefold()
    if path.endswith("/agentcard/v1.0"):
        return "foundry.a2a.card.fetch"
    if path.endswith("/endpoint/protocols/a2a") and request.method == "POST":
        return "foundry.a2a.message.send"
    if "/versions/" in path:
        return "foundry.agent.version.get"
    if "/agents/" in path:
        return "foundry.agent.get"
    return "foundry.http"


def _sha256_json(payload: Mapping[str, Any]) -> str:
    return card_hash(payload)


def _immutable_agent_version(value: object) -> str:
    if value is None or isinstance(value, bool):
        raise CanaryProbeError("Expected Foundry agent version is missing.")
    version = str(value)
    if (
        not version
        or version != version.strip()
        or version.startswith("@")
        or any(char.isspace() or category(char) == "Cc" for char in version)
        or any(delimiter in version for delimiter in ("/", "\\", "?", "#", "%"))
    ):
        raise CanaryProbeError(
            "Expected Foundry agent version must be an exact immutable identifier."
        )
    return version


def _foundry_a2a_urls(
    project: str,
    agent_id: str,
    agent_version: str,
) -> tuple[str, str, str, str]:
    if (
        not isinstance(agent_id, str)
        or agent_id != agent_id.strip()
        or not _AGENT_ID.fullmatch(agent_id)
        or any(char.isspace() or category(char) == "Cc" for char in agent_id)
    ):
        raise CanaryProbeError("Foundry agent ID contains unsafe or noncanonical characters.")
    version = _immutable_agent_version(agent_version)
    agent_url = f"{project}/agents/{agent_id}?api-version=v1"
    version_url = f"{project}/agents/{agent_id}/versions/{version}?api-version=v1"
    interface_url = f"{project}/agents/{agent_id}/endpoint/protocols/a2a"
    return agent_url, version_url, interface_url, f"{interface_url}/{CARD_PATH}"


def _allowed_foundry_requests(
    agent_url: str,
    version_url: str,
    interface_url: str,
    card_url: str,
) -> frozenset[tuple[str, str]]:
    return frozenset(
        {
            ("GET", agent_url),
            ("GET", version_url),
            ("GET", card_url),
            ("POST", interface_url),
        }
    )


def _validate_foundry_interface_url(value: object, expected_url: str) -> None:
    field_name = "Foundry Agent Card supportedInterfaces URL"
    if not isinstance(value, str):
        raise CanaryProbeError(f"{field_name} must be a URL string.")
    if (
        not value
        or value != value.strip()
        or any(char.isspace() or category(char) == "Cc" for char in value)
        or "\\" in value
        or "%" in value
    ):
        raise CanaryProbeError(f"{field_name} contains unsafe or noncanonical characters.")
    if "?" in value or "#" in value:
        raise CanaryProbeError(f"{field_name} must not contain a query or fragment.")

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise CanaryProbeError(f"{field_name} is not a valid URL.") from exc
    expected = urlsplit(expected_url)

    if parsed.scheme != "https" or not parsed.netloc or not parsed.hostname:
        raise CanaryProbeError(f"{field_name} must be an absolute HTTPS URL.")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise CanaryProbeError(f"{field_name} must not contain credentials.")
    if port is not None or ":" in parsed.netloc:
        raise CanaryProbeError(f"{field_name} must not contain a port.")
    if parsed.netloc != expected.netloc or parsed.path != expected.path or value != expected_url:
        raise CanaryProbeError(
            f"{field_name} must exactly match the canonical endpoint for the expected agent."
        )


def _trusted_invocation_card(
    card: Any,
    payload: Mapping[str, Any],
    *,
    expected_agent_id: str,
    expected_interface_url: str,
) -> Any:
    if payload.get("name") != expected_agent_id:
        raise CanaryProbeError("Foundry Agent Card name does not match the expected agent.")
    interfaces = payload.get("supportedInterfaces")
    if not isinstance(interfaces, list) or not interfaces:
        raise CanaryProbeError("Foundry Agent Card must expose supported interfaces.")

    canonical_interfaces = []
    allowed_platform_interfaces = {
        (_A2A_PROTOCOL_BINDING, _A2A_PROTOCOL_VERSION),
        (_A2A_PROTOCOL_BINDING, "0.3"),
        ("HTTP+JSON", "0.3"),
    }
    for interface in interfaces:
        if not isinstance(interface, Mapping):
            raise CanaryProbeError("Foundry Agent Card supported interface must be an object.")
        binding = interface.get("protocolBinding")
        version = interface.get("protocolVersion")
        if (binding, version) not in allowed_platform_interfaces:
            raise CanaryProbeError("Foundry Agent Card exposed an unsupported protocol interface.")
        if interface.get("tenant") not in (None, ""):
            raise CanaryProbeError("Foundry Agent Card must not override the invocation tenant.")
        _validate_foundry_interface_url(interface.get("url"), expected_interface_url)
        if binding == _A2A_PROTOCOL_BINDING and version == _A2A_PROTOCOL_VERSION:
            canonical_interfaces.append(interface)

    if len(canonical_interfaces) != 1:
        raise CanaryProbeError(
            "Foundry Agent Card must expose exactly one canonical A2A v1.0 JSONRPC interface."
        )

    try:
        trusted_card = type(card)()
        trusted_card.CopyFrom(card)
        trusted_card.ClearField("supported_interfaces")
        trusted_interface = trusted_card.supported_interfaces.add()
        trusted_interface.url = expected_interface_url
        trusted_interface.protocol_binding = _A2A_PROTOCOL_BINDING
        trusted_interface.protocol_version = _A2A_PROTOCOL_VERSION
    except (AttributeError, TypeError, ValueError) as exc:
        raise CanaryProbeError("Foundry Agent Card could not be safely reconstructed.") from exc
    return trusted_card


def _load_mapping(path: Path) -> dict[str, Any]:
    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise CanaryProbeError(f"{path} must contain a JSON object.")
    return cast(dict[str, Any], loaded)


def _card_payload(card: Any) -> dict[str, Any]:
    return MessageToDict(card)


def card_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Project dynamic served-card JSON onto the canonical authored fields."""

    try:
        return served_card_projection(payload)
    except CardProjectionError as exc:
        raise CanaryProbeError(str(exc)) from exc


def source_title_present(source: str, answer: str) -> bool:
    """Match source titles despite harmless citation-rendering punctuation."""

    def normalize(value: str) -> str:
        without_possessives = _POSSESSIVE.sub("", value.casefold()).replace("&", " and ")
        return " ".join(_EVIDENCE_TOKEN.findall(without_possessives))

    return normalize(source) in normalize(answer)


def required_term_present(term_spec: str, answer: str) -> bool:
    """Accept an explicit pipe-delimited set of equivalent grounded terms."""

    normalized = answer.casefold()
    alternatives = (term.strip().casefold() for term in term_spec.split("|"))
    return any(term and term in normalized for term in alternatives)


def _event_text(event: Any) -> str:
    which = event.WhichOneof("payload")
    if which == "message":
        return get_message_text(event.message, delimiter=" ")
    if which == "task":
        artifacts = [
            get_artifact_text(artifact, delimiter=" ") for artifact in event.task.artifacts
        ]
        text = " ".join(part for part in artifacts if part).strip()
        if text:
            return text
        if event.task.status.HasField("message"):
            return get_message_text(event.task.status.message, delimiter=" ")
        return ""
    if which == "artifact_update":
        return get_artifact_text(event.artifact_update.artifact, delimiter=" ")
    if which == "status_update" and event.status_update.status.HasField("message"):
        return get_message_text(event.status_update.status.message, delimiter=" ")
    return ""


def _agent_metadata(
    payload: Mapping[str, Any],
    agent_id: str,
    expected_version: str,
) -> dict[str, Any]:
    version = _immutable_agent_version(payload.get("version"))
    if version != expected_version:
        raise CanaryProbeError(
            f"Foundry agent {agent_id!r} returned version {version!r}, "
            f"not expected version {expected_version!r}."
        )
    definition = payload.get("definition")
    code = (
        _field(definition, "code_configuration", "codeConfiguration")
        if isinstance(definition, dict)
        else None
    )
    container = (
        _field(definition, "container_configuration", "containerConfiguration")
        if isinstance(definition, dict)
        else None
    )
    identity = _field(payload, "instance_identity", "instanceIdentity")
    environment = (
        _field(definition, "environment_variables", "environmentVariables")
        if isinstance(definition, dict)
        else None
    )
    protocols = (
        _field(definition, "protocol_versions", "protocolVersions")
        if isinstance(definition, dict)
        else None
    )
    version_metadata = payload.get("metadata")
    responses_version = (
        next(
            (
                protocol.get("version")
                for protocol in protocols
                if isinstance(protocol, dict) and protocol.get("protocol") == "responses"
            ),
            None,
        )
        if isinstance(protocols, list)
        else None
    )
    image = container.get("image") if isinstance(container, dict) else None
    pinned_image_digest = (
        image.rsplit("@", maxsplit=1)[-1]
        if isinstance(image, str) and "@sha256:" in image
        else None
    )
    reported_image_digest = (
        _field(definition, "image_digest", "imageDigest") if isinstance(definition, dict) else None
    )
    image_digest = reported_image_digest or pinned_image_digest
    deployment_mode = "container" if isinstance(container, dict) else "code"
    return {
        "id": agent_id,
        "version": version,
        "status": payload.get("status"),
        "kind": definition.get("kind") if isinstance(definition, dict) else None,
        "deploymentMode": deployment_mode,
        "responsesProtocolVersion": responses_version,
        "runtime": code.get("runtime") if isinstance(code, dict) else None,
        "modelDeployment": (
            (
                environment.get("AGENT_IQ_AZURE_MODEL_DEPLOYMENT")
                or environment.get("AZURE_AI_MODEL_DEPLOYMENT_NAME")
            )
            if isinstance(environment, dict)
            else None
        ),
        "toolboxName": (
            environment.get(HOST_TOOLBOX_NAME_ENV)
            or environment.get("TOOLBOX_NAME")
            or environment.get("AGENT_IQ_TOOLBOX_NAME")
            if isinstance(environment, dict)
            else None
        ),
        "logicalAgentId": (
            environment.get(HOST_LOGICAL_AGENT_ID_ENV)
            or environment.get("AGENT_IQ_LOGICAL_AGENT_ID")
            if isinstance(environment, dict)
            else None
        )
        or (
            version_metadata.get("agentIqLogicalAgentId")
            if isinstance(version_metadata, dict)
            else None
        ),
        "runtimeFamily": (
            environment.get(HOST_RUNTIME_FAMILY_ENV) or environment.get("AGENT_IQ_RUNTIME_FAMILY")
            if isinstance(environment, dict)
            else None
        )
        or (
            version_metadata.get("agentIqRuntimeFamily")
            if isinstance(version_metadata, dict)
            else None
        ),
        "descriptorSha256": (
            environment.get(HOST_DESCRIPTOR_SHA256_ENV)
            or environment.get("AGENT_IQ_DESCRIPTOR_SHA256")
            if isinstance(environment, dict)
            else None
        )
        or (
            version_metadata.get("agentIqDescriptorSha256")
            if isinstance(version_metadata, dict)
            else None
        ),
        "instanceIdentity": (
            _field(identity, "principal_id", "principalId") if isinstance(identity, dict) else None
        ),
        "codeContentHash": (
            _field(code, "content_hash", "contentHash") if isinstance(code, dict) else None
        ),
        "image": image,
        "imageDigest": image_digest,
        "imageDigestStatus": (
            "reported"
            if reported_image_digest
            else "pinned_in_image_reference"
            if pinned_image_digest
            else "not_exposed_for_code_deploy"
            if deployment_mode == "code"
            else "not_reported_for_image_deploy"
        ),
    }


def _field(payload: Mapping[str, Any], snake_case: str, camel_case: str) -> Any:
    return payload.get(snake_case, payload.get(camel_case))


def _assert_exact_version_traffic(payload: Mapping[str, Any], expected_version: str) -> None:
    endpoint = _field(payload, "agent_endpoint", "agentEndpoint")
    selector = (
        _field(endpoint, "version_selector", "versionSelector")
        if isinstance(endpoint, Mapping)
        else None
    )
    rules = (
        _field(selector, "version_selection_rules", "versionSelectionRules")
        if isinstance(selector, Mapping)
        else None
    )
    if not isinstance(rules, list) or len(rules) != 1 or not isinstance(rules[0], Mapping):
        raise CanaryProbeError(
            "Foundry agent traffic must contain exactly one immutable version rule."
        )
    rule = rules[0]
    version = _immutable_agent_version(_field(rule, "agent_version", "agentVersion"))
    percentage = _field(rule, "traffic_percentage", "trafficPercentage")
    if (
        _field(rule, "type", "type") != "FixedRatio"
        or version != expected_version
        or isinstance(percentage, bool)
        or not isinstance(percentage, int)
        or percentage != 100
    ):
        raise CanaryProbeError(
            f"Foundry agent traffic is not bound 100% to version {expected_version!r}."
        )


async def probe_canary(
    *,
    project_endpoint: str,
    account_name: str,
    project_name: str,
    agent_id: str,
    agent_version: str,
    canonical_card_path: Path,
    token: str | None = None,
    token_provider: Callable[[], str] | None = None,
    build_record_path: Path | None = None,
    prompt: str = DEFAULT_PROMPT,
    required_terms: Sequence[str] = DEFAULT_REQUIRED_TERMS,
    required_sources: Sequence[str] = (DEFAULT_REQUIRED_SOURCE,),
    require_source_titles: bool = True,
    required_model: str = DEFAULT_REQUIRED_MODEL,
    logical_agent_id: str | None = None,
    expected_toolbox_name: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Run one clean native-A2A card-to-grounded-answer certification.

    An injected transport is a trusted dependency-injection boundary; it must not route requests
    through an untrusted proxy or follow redirects.
    """

    try:
        project = canonicalize_foundry_project_endpoint(
            project_endpoint,
            expected_account=account_name,
            expected_project=project_name,
        )
    except ValueError as exc:
        raise CanaryProbeError(str(exc)) from exc
    version = _immutable_agent_version(agent_version)
    agent_url, version_url, interface_url, card_url = _foundry_a2a_urls(
        project,
        agent_id,
        version,
    )
    allowed_requests = _allowed_foundry_requests(
        agent_url,
        version_url,
        interface_url,
        card_url,
    )
    if (token is None) == (token_provider is None):
        raise CanaryProbeError("Provide exactly one Foundry token or token provider.")
    get_token = token_provider if token_provider is not None else lambda: cast(str, token)
    canonical = card_projection(_load_mapping(canonical_card_path))
    build_record = _load_mapping(build_record_path) if build_record_path else {}
    expected_logical_agent = logical_agent_id or build_record.get("logicalAgentId") or agent_id
    expected_toolbox = (
        expected_toolbox_name
        or build_record.get("expectedToolboxName")
        or f"{expected_logical_agent}-knowledge"
    )
    expected_image = build_record.get("image")
    expected_descriptor_sha256 = build_record.get("descriptorSha256")
    trace = HttpTraceRecorder()
    timeout = httpx.Timeout(180.0)
    started = time.perf_counter()

    async with httpx.AsyncClient(
        auth=_FoundryBearerAuth(get_token, allowed_requests),
        timeout=timeout,
        event_hooks={"response": [trace.capture]},
        transport=transport,
        follow_redirects=False,
        trust_env=False,
    ) as http:
        agent_response = await http.get(agent_url)
        agent_response.raise_for_status()
        _assert_exact_version_traffic(agent_response.json(), version)
        version_response = await http.get(version_url)
        version_response.raise_for_status()
        metadata = _agent_metadata(version_response.json(), agent_id, version)

        resolver = A2ACardResolver(
            httpx_client=http,
            base_url=interface_url,
            agent_card_path=CARD_PATH,
        )
        card = await resolver.get_agent_card()
        served_payload = _card_payload(card)
        trusted_card = _trusted_invocation_card(
            card,
            served_payload,
            expected_agent_id=agent_id,
            expected_interface_url=interface_url,
        )
        served = card_projection(served_payload)

        client = await create_client(
            trusted_card,
            client_config=ClientConfig(
                httpx_client=http,
                streaming=False,
                supported_protocol_bindings=["JSONRPC"],
            ),
        )
        request = SendMessageRequest(
            message=Message(
                role=Role.ROLE_USER,
                message_id=str(uuid.uuid4()),
                parts=[Part(text=prompt)],
            )
        )
        pieces: list[str] = []
        try:
            async for event in client.send_message(request):
                text = _event_text(event).strip()
                if text:
                    pieces.append(text)
            final_agent_response = await http.get(agent_url)
            final_agent_response.raise_for_status()
            _assert_exact_version_traffic(final_agent_response.json(), version)
        finally:
            await client.close()

    answer = " ".join(dict.fromkeys(pieces)).strip()
    missing_terms = [term for term in required_terms if not required_term_present(term, answer)]
    missing_sources = [
        source for source in required_sources if not source_title_present(source, answer)
    ]
    trace_json = json.dumps(trace.events, sort_keys=True, ensure_ascii=True)
    operations = {str(event["operation"]) for event in trace.events}
    is_container = metadata["deploymentMode"] == "container"
    checks = {
        "agentActive": metadata["status"] == "active",
        "hostedKind": metadata["kind"] == "hosted",
        "responsesProtocolV2": metadata["responsesProtocolVersion"] == "2.0.0",
        "pythonRuntimeIs313": is_container or metadata["runtime"] == "python_3_13",
        "modelDeploymentMatches": metadata["modelDeployment"] == required_model,
        "canonicalToolboxBound": metadata["toolboxName"] == expected_toolbox,
        "codeContentHashPresent": is_container or bool(metadata["codeContentHash"]),
        "deploymentContentPresent": (
            bool(metadata["imageDigest"]) if is_container else bool(metadata["codeContentHash"])
        ),
        "containerImagePinned": not is_container
        or (
            isinstance(metadata["image"], str)
            and "@sha256:" in metadata["image"]
            and bool(metadata["imageDigest"])
        ),
        "imageMatchesBuild": expected_image is None or metadata["image"] == expected_image,
        "logicalAgentMatches": not is_container
        or metadata["logicalAgentId"] == expected_logical_agent,
        "runtimeFamilyMatches": not is_container or metadata["runtimeFamily"] == "langgraph",
        "descriptorFingerprintMatches": expected_descriptor_sha256 is None
        or metadata["descriptorSha256"] == expected_descriptor_sha256,
        "cardNameMatches": served_payload.get("name") == agent_id,
        "cardVersionIsV1": served.get("version") == "1.0.0",
        "cardMatchesCanonical": served == canonical,
        "nativeCardTracePresent": "foundry.a2a.card.fetch" in operations,
        "nativeMessageTracePresent": "foundry.a2a.message.send" in operations,
        "answerNonempty": bool(answer),
        "requiredTermsPresent": not missing_terms,
        "sourceTitlesPresent": not missing_sources,
        "sourceTitlePolicySatisfied": not require_source_titles or not missing_sources,
        "sourceReferencePresent": bool(_SOURCE_REFERENCE.search(answer)),
        "traceContainsNoPrompt": prompt.casefold() not in trace_json.casefold(),
        "traceContainsNoToken": token is None or token not in trace_json,
        "traceContainsNoAuthorization": "authorization" not in trace_json.casefold(),
        "trafficBoundToCertifiedVersion": True,
    }
    failures = [
        name for name, passed in checks.items() if not passed and name != "sourceTitlesPresent"
    ]
    return {
        "schemaVersion": "1.0",
        "status": "pass" if not failures else "fail",
        "checkedAt": datetime.now(UTC).isoformat(),
        "sdk": {
            "name": "a2a-sdk",
            "version": importlib.metadata.version("a2a-sdk"),
        },
        "transport": {
            "protocol": "a2a",
            "protocolVersion": "1.0",
            "binding": "JSONRPC",
            "authentication": "Entra",
            "fallbackUsed": False,
        },
        "agent": metadata,
        "build": {
            "buildHash": build_record.get("buildHash"),
            "sourceSampleCommit": build_record.get("sourceSampleCommit"),
            "buildMode": build_record.get("buildMode", metadata["deploymentMode"]),
            "logicalAgentId": expected_logical_agent,
            "descriptorSha256": expected_descriptor_sha256,
            "image": expected_image,
            "imageDigest": build_record.get("imageDigest"),
        },
        "card": {
            "canonicalHash": _sha256_json(canonical),
            "servedHash": _sha256_json(served),
            "matchesCanonical": served == canonical,
            "skillCount": len(served["skills"]),
        },
        "invocation": {
            "promptHash": f"sha256:{hashlib.sha256(prompt.encode()).hexdigest()}",
            "answer": answer,
            "requiredTerms": list(required_terms),
            "missingTerms": missing_terms,
            "requiredSources": list(required_sources),
            "missingSources": missing_sources,
            "sourceTitleMode": "required" if require_source_titles else "advisory",
            "latencyMs": round((time.perf_counter() - started) * 1000, 2),
        },
        "trace": {
            "events": trace.events,
            "payloadsCaptured": False,
        },
        "checks": checks,
        "failures": failures,
    }


def _content_safe_report(payload: Mapping[str, Any]) -> dict[str, Any]:
    invocation = payload.get("invocation")
    invocation_data = invocation if isinstance(invocation, Mapping) else {}
    answer = invocation_data.get("answer")
    answer_text = answer if isinstance(answer, str) else ""
    checks = payload.get("checks")
    check_data = checks if isinstance(checks, Mapping) else {}
    transport = payload.get("transport")
    transport_data = transport if isinstance(transport, Mapping) else {}
    agent = payload.get("agent")
    agent_data = agent if isinstance(agent, Mapping) else {}
    card = payload.get("card")
    card_data = card if isinstance(card, Mapping) else {}
    trace = payload.get("trace")
    trace_data = trace if isinstance(trace, Mapping) else {}
    events = trace_data.get("events")
    event_data = events if isinstance(events, list) else []
    build = payload.get("build")
    build_data = build if isinstance(build, Mapping) else {}

    return {
        "schemaVersion": payload.get("schemaVersion"),
        "status": payload.get("status"),
        "checkedAt": payload.get("checkedAt"),
        "transport": {
            name: transport_data.get(name)
            for name in (
                "protocol",
                "protocolVersion",
                "binding",
                "authentication",
                "fallbackUsed",
            )
        },
        "agent": {
            name: agent_data.get(name)
            for name in (
                "version",
                "status",
                "kind",
                "deploymentMode",
                "responsesProtocolVersion",
                "runtime",
                "imageDigestStatus",
            )
        },
        "build": {"buildMode": build_data.get("buildMode")},
        "card": {
            name: card_data.get(name)
            for name in ("canonicalHash", "servedHash", "matchesCanonical", "skillCount")
        },
        "invocation": {
            "answerHash": f"sha256:{hashlib.sha256(answer_text.encode('utf-8')).hexdigest()}",
            "answerLength": len(answer_text),
            "requiredTermCount": _sequence_length(invocation_data.get("requiredTerms")),
            "missingTermCount": _sequence_length(invocation_data.get("missingTerms")),
            "requiredSourceCount": _sequence_length(invocation_data.get("requiredSources")),
            "missingSourceCount": _sequence_length(invocation_data.get("missingSources")),
            "sourceTitleMode": invocation_data.get("sourceTitleMode"),
            "latencyMs": invocation_data.get("latencyMs"),
        },
        "trace": {
            "eventCount": len(event_data),
            "payloadsCaptured": trace_data.get("payloadsCaptured") is True,
        },
        "checks": {
            str(name): value for name, value in check_data.items() if isinstance(value, bool)
        },
        "failures": [
            str(name)
            for name, value in check_data.items()
            if isinstance(value, bool) and not value and name != "sourceTitlesPresent"
        ],
    }


def _sequence_length(value: Any) -> int:
    return len(value) if isinstance(value, (list, tuple)) else 0


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        json.dumps(
            _content_safe_report(payload),
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
        )
        + "\n"
    )
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


def cli_probe(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Certify a native Foundry A2A canary.")
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--account-name", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--agent-id", default=DEFAULT_AGENT_ID)
    parser.add_argument("--agent-version", required=True)
    parser.add_argument("--canonical-card", type=Path, required=True)
    parser.add_argument("--build-record", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--required-term", action="append", default=[])
    parser.add_argument("--required-source", action="append", default=[])
    parser.add_argument("--allow-missing-source-title", action="store_true")
    parser.add_argument("--required-model", default=DEFAULT_REQUIRED_MODEL)
    parser.add_argument("--logical-agent-id")
    parser.add_argument("--expected-toolbox-name")
    arguments = parser.parse_args(argv)

    try:
        project_endpoint = canonicalize_foundry_project_endpoint(
            arguments.project_endpoint,
            expected_account=arguments.account_name,
            expected_project=arguments.project_name,
        )
        agent_version = _immutable_agent_version(arguments.agent_version)
    except ValueError as exc:
        parser.error(str(exc))
    except CanaryProbeError as exc:
        parser.error(str(exc))
    credential = isolated_default_azure_credential(
        credential_factory=DefaultAzureCredential,
    )

    def token_provider() -> str:
        return credential.get_token(AZURE_AI_SCOPE).token

    try:
        result = asyncio.run(
            probe_canary(
                project_endpoint=project_endpoint,
                account_name=arguments.account_name,
                project_name=arguments.project_name,
                agent_id=arguments.agent_id,
                agent_version=agent_version,
                canonical_card_path=arguments.canonical_card,
                token_provider=token_provider,
                build_record_path=arguments.build_record,
                prompt=arguments.prompt,
                required_terms=arguments.required_term or DEFAULT_REQUIRED_TERMS,
                required_sources=arguments.required_source or (DEFAULT_REQUIRED_SOURCE,),
                require_source_titles=not arguments.allow_missing_source_title,
                required_model=arguments.required_model,
                logical_agent_id=arguments.logical_agent_id,
                expected_toolbox_name=arguments.expected_toolbox_name,
            )
        )
    finally:
        credential.close()

    _write_json(arguments.output, result)
    print(
        f"Foundry native-A2A canary: {result['status'].upper()} "
        f"agent={arguments.agent_id} version={result['agent']['version']} "
        f"card={result['card']['servedHash']} output={arguments.output}"
    )
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(cli_probe())
