"""Read-only Microsoft Foundry runtime capability probes."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from unicodedata import category
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from azure.ai.projects import AIProjectClient
from azure.core.credentials import TokenCredential
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from azure.core.pipeline.policies import RedirectPolicy
from azure.core.pipeline.transport import RequestsTransport
from azure.identity import DefaultAzureCredential

from agent_iq.config import canonicalize_foundry_project_endpoint
from agent_iq.connectors.entra import isolated_default_azure_credential
from agent_iq.contracts.capabilities import (
    CapabilityLedger,
    CapabilityObservation,
    ProbeMode,
    ProbeStatus,
)

ARM_SCOPE = "https://management.azure.com/.default"
FOUNDRY_SCOPE = "https://ai.azure.com/.default"
ARM_API_VERSION = "2025-06-01"
USAGE_API_VERSION = "2023-05-01"
DEFAULT_TARGET_MODELS = ("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-terra")


@dataclass(frozen=True)
class CapabilityProbeConfig:
    """Non-secret coordinates used only while probing."""

    project_endpoint: str
    subscription_id: str
    resource_group: str
    account_name: str
    project_name: str
    location: str
    canary_agent_name: str
    certificate_path: Path | None = None
    target_models: tuple[str, ...] = DEFAULT_TARGET_MODELS

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "project_endpoint",
            canonicalize_foundry_project_endpoint(
                self.project_endpoint,
                expected_account=self.account_name,
                expected_project=self.project_name,
            ),
        )


class _ModelLike(Protocol):
    def as_dict(self) -> Mapping[str, Any]: ...


class ProbeFailure(RuntimeError):
    """Sanitized transport failure without response text."""

    def __init__(
        self,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__("Capability probe failed.")
        self.status_code = status_code
        self.error_code = error_code


class AuthenticatedJsonReader:
    """Minimal bearer-authenticated JSON reader."""

    def __init__(
        self,
        *,
        credential: TokenCredential,
        scope: str,
        client: httpx.Client | None = None,
        foundry_project_endpoint: str | None = None,
    ) -> None:
        if scope == FOUNDRY_SCOPE and foundry_project_endpoint is None:
            raise ValueError("Foundry JSON readers must be bound to a project endpoint.")
        self._credential = credential
        self._scope = scope
        self._client = client or httpx.Client(
            timeout=60.0,
            follow_redirects=False,
            trust_env=False,
        )
        self._owns_client = client is None
        self._foundry_project_endpoint = (
            canonicalize_foundry_project_endpoint(foundry_project_endpoint)
            if foundry_project_endpoint is not None
            else None
        )

    def get(self, url: str) -> Mapping[str, Any]:
        request_url = url
        if self._foundry_project_endpoint is not None:
            request_url = _canonicalize_foundry_request_url(
                url,
                project_endpoint=self._foundry_project_endpoint,
            )
        token = self._credential.get_token(self._scope).token
        response = self._client.get(
            request_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            follow_redirects=False,
        )
        if not 200 <= response.status_code < 300:
            raise ProbeFailure(status_code=response.status_code)
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ProbeFailure(status_code=response.status_code) from exc
        if not isinstance(payload, Mapping):
            raise ProbeFailure(status_code=response.status_code)
        return payload

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def _canonicalize_foundry_request_url(url: str, *, project_endpoint: str) -> str:
    project = canonicalize_foundry_project_endpoint(project_endpoint)
    if (
        not url
        or url != url.strip()
        or any(char.isspace() or category(char) == "Cc" for char in url)
        or "\\" in url
        or "#" in url
    ):
        raise ValueError("Foundry request URL contains unsafe characters.")
    try:
        parsed = urlsplit(url)
        project_url = urlsplit(project)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Foundry request URL is invalid.") from exc
    if (
        parsed.scheme.casefold() != "https"
        or parsed.hostname is None
        or parsed.hostname.casefold() != project_url.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
        or port is not None
        or "%" in parsed.netloc
        or "%" in parsed.path
        or "//" in parsed.path
        or any(segment in {".", ".."} for segment in parsed.path.split("/"))
        or not parsed.path.startswith(f"{project_url.path}/")
    ):
        raise ValueError("Foundry request URL is outside the bound project endpoint.")
    return urlunsplit(("https", project_url.netloc, parsed.path, parsed.query, ""))


def _mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "as_dict"):
        payload = value.as_dict()
        if isinstance(payload, Mapping):
            return payload
    raise ProbeFailure()


def _error_facts(exc: Exception) -> tuple[ProbeStatus, dict[str, Any]]:
    status_code = getattr(exc, "status_code", None)
    error_code = getattr(exc, "error_code", None)
    if isinstance(exc, HttpResponseError):
        error = getattr(exc, "error", None)
        candidate = getattr(error, "code", None)
        if isinstance(candidate, str):
            error_code = candidate
    if isinstance(exc, ClientAuthenticationError) or status_code in {401, 403}:
        status = ProbeStatus.UNAUTHORIZED
    elif status_code == 404:
        status = ProbeStatus.UNAVAILABLE
    else:
        status = ProbeStatus.PROBE_ERROR
    details: dict[str, Any] = {"errorClass": type(exc).__name__}
    if isinstance(status_code, int):
        details["httpStatus"] = status_code
    if isinstance(error_code, str) and error_code:
        details["errorCode"] = error_code
    return status, details


def _observation(
    capability_id: str,
    *,
    category: str,
    documented_status: str,
    observed_status: ProbeStatus,
    probe_mode: ProbeMode,
    required: bool = False,
    details: Mapping[str, Any] | None = None,
) -> CapabilityObservation:
    return CapabilityObservation(
        capability_id=capability_id,
        category=category,
        documented_status=documented_status,
        observed_status=observed_status,
        probe_mode=probe_mode,
        required=required,
        details=dict(details or {}),
    )


def _failed_observation(
    capability_id: str,
    *,
    category: str,
    documented_status: str,
    probe_mode: ProbeMode,
    required: bool,
    exc: Exception,
) -> CapabilityObservation:
    status, details = _error_facts(exc)
    return _observation(
        capability_id,
        category=category,
        documented_status=documented_status,
        observed_status=status,
        probe_mode=probe_mode,
        required=required,
        details=details,
    )


def _probe_collection(
    capability_id: str,
    *,
    category: str,
    documented_status: str,
    required: bool,
    loader: Callable[[], Iterable[object]],
) -> CapabilityObservation:
    try:
        first = next(iter(loader()), None)
    except Exception as exc:
        return _failed_observation(
            capability_id,
            category=category,
            documented_status=documented_status,
            probe_mode=ProbeMode.SDK,
            required=required,
            exc=exc,
        )
    return _observation(
        capability_id,
        category=category,
        documented_status=documented_status,
        observed_status=ProbeStatus.AVAILABLE,
        probe_mode=ProbeMode.SDK,
        required=required,
        details={"apiReachable": True, "configured": first is not None},
    )


def _probe_mapping(
    capability_id: str,
    *,
    category: str,
    documented_status: str,
    probe_mode: ProbeMode,
    required: bool,
    loader: Callable[[], Mapping[str, Any]],
    normalizer: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> tuple[CapabilityObservation, Mapping[str, Any] | None]:
    try:
        payload = loader()
        details = normalizer(payload)
    except Exception as exc:
        return (
            _failed_observation(
                capability_id,
                category=category,
                documented_status=documented_status,
                probe_mode=probe_mode,
                required=required,
                exc=exc,
            ),
            None,
        )
    return (
        _observation(
            capability_id,
            category=category,
            documented_status=documented_status,
            observed_status=ProbeStatus.AVAILABLE,
            probe_mode=probe_mode,
            required=required,
            details=details,
        ),
        payload,
    )


def _values(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = payload.get("value")
    if raw is None:
        raw = payload.get("data")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _normalize_account(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    properties = payload.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    network = properties.get("networkAcls")
    network = network if isinstance(network, Mapping) else {}
    private_endpoints = properties.get("privateEndpointConnections")
    virtual_networks = network.get("virtualNetworkRules")
    identity = payload.get("identity")
    identity = identity if isinstance(identity, Mapping) else {}
    return {
        "location": payload.get("location"),
        "kind": payload.get("kind"),
        "publicNetworkAccess": properties.get("publicNetworkAccess"),
        "networkDefaultAction": network.get("defaultAction"),
        "virtualNetworkRuleCount": (
            len(virtual_networks) if isinstance(virtual_networks, list) else 0
        ),
        "privateEndpointCount": (
            len(private_endpoints) if isinstance(private_endpoints, list) else 0
        ),
        "identityType": identity.get("type"),
    }


def _normalize_deployments(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    deployments: list[dict[str, Any]] = []
    for item in _values(payload):
        properties = item.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        model = properties.get("model")
        model = model if isinstance(model, Mapping) else {}
        sku = item.get("sku")
        sku = sku if isinstance(sku, Mapping) else {}
        deployments.append(
            {
                "model": model.get("name"),
                "modelVersion": model.get("version"),
                "sku": sku.get("name"),
                "provisioningState": properties.get("provisioningState"),
            }
        )
    deployments.sort(
        key=lambda item: (
            str(item.get("model") or ""),
            str(item.get("modelVersion") or ""),
        )
    )
    return {"deploymentCount": len(deployments), "deployments": deployments}


def _normalize_capacity(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    allowed = ("default", "minimum", "maximum", "step")
    return {key: value[key] for key in allowed if key in value}


def _normalize_model_offer(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    properties = payload.get("properties")
    properties = properties if isinstance(properties, Mapping) else payload
    capabilities = properties.get("capabilities")
    capabilities = capabilities if isinstance(capabilities, Mapping) else {}
    selected_capabilities = {}
    for key in (
        "agentsV2",
        "assistants",
        "chatCompletion",
        "responses",
        "priorityTierSkus",
        "maxContextToken",
    ):
        if key in capabilities:
            selected_capabilities[key] = capabilities[key]
    skus: list[dict[str, Any]] = []
    raw_skus = payload.get("skus")
    for raw_sku in raw_skus if isinstance(raw_skus, list) else []:
        if not isinstance(raw_sku, Mapping):
            continue
        skus.append(
            {
                "name": raw_sku.get("name"),
                "capacity": _normalize_capacity(raw_sku.get("capacity")),
            }
        )
    skus.sort(key=lambda sku: str(sku.get("name") or ""))
    deprecation = properties.get("deprecation")
    deprecation = deprecation if isinstance(deprecation, Mapping) else {}
    return {
        "listed": True,
        "version": payload.get("version"),
        "lifecycleStatus": properties.get("lifecycleStatus"),
        "capabilities": selected_capabilities,
        "skus": skus,
        "deprecationDate": deprecation.get("inference"),
    }


def _target_model_details(
    payload: Mapping[str, Any],
    target_models: Sequence[str],
) -> dict[str, Mapping[str, Any]]:
    indexed = {
        str(item.get("name")): item
        for item in _values(payload)
        if isinstance(item.get("name"), str)
    }
    return {
        model: (
            _normalize_model_offer(indexed[model])
            if model in indexed
            else {"listed": False, "skus": [], "capabilities": {}}
        )
        for model in target_models
    }


def _normalize_model_catalog(
    payload: Mapping[str, Any],
    target_models: Sequence[str],
) -> Mapping[str, Any]:
    details = _target_model_details(payload, target_models)
    return {
        "catalogEntryCount": len(_values(payload)),
        "targetModelCount": len(target_models),
        "listedTargetModelCount": sum(bool(item.get("listed")) for item in details.values()),
    }


def _target_quota_records(
    payload: Mapping[str, Any],
    target_models: Sequence[str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in _values(payload):
        name = item.get("name")
        name = name if isinstance(name, Mapping) else {}
        value = name.get("value")
        if not isinstance(value, str):
            continue
        model = next(
            (
                target
                for target in target_models
                if value.casefold().endswith(f".{target.casefold()}")
            ),
            None,
        )
        if model is None:
            continue
        records.append(
            {
                "model": model,
                "sku": value.removeprefix("OpenAI.").removesuffix(f".{model}"),
                "currentValue": item.get("currentValue"),
                "limit": item.get("limit"),
                "unit": item.get("unit"),
            }
        )
    records.sort(key=lambda record: (str(record["model"]), str(record["sku"])))
    return records


def _normalize_quota(
    payload: Mapping[str, Any],
    target_models: Sequence[str],
) -> Mapping[str, Any]:
    records = _target_quota_records(payload, target_models)
    models_with_quota = {
        str(record["model"])
        for record in records
        if isinstance(record.get("limit"), (int, float)) and record["limit"] > 0
    }
    return {
        "targetQuotaCount": len(records),
        "modelsWithPositiveQuota": len(models_with_quota),
        "quotas": records,
    }


def _normalize_canary(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    versions = payload.get("versions")
    versions = versions if isinstance(versions, Mapping) else {}
    latest = versions.get("latest")
    latest = latest if isinstance(latest, Mapping) else {}
    definition = latest.get("definition")
    definition = definition if isinstance(definition, Mapping) else {}
    endpoint = payload.get("agentEndpoint")
    if endpoint is None:
        endpoint = payload.get("agent_endpoint")
    endpoint = endpoint if isinstance(endpoint, Mapping) else {}
    protocols = endpoint.get("protocols")
    protocol_versions = definition.get("protocolVersions")
    if protocol_versions is None:
        protocol_versions = definition.get("protocol_versions")
    normalized_versions = []
    for raw_version in protocol_versions if isinstance(protocol_versions, list) else []:
        try:
            version = _mapping(raw_version)
        except ProbeFailure:
            continue
        normalized_versions.append(
            {
                "protocol": version.get("protocol"),
                "version": version.get("version"),
            }
        )
    return {
        "state": payload.get("state"),
        "versionStatus": latest.get("status"),
        "version": latest.get("version"),
        "kind": definition.get("kind"),
        "endpointProtocols": sorted(protocols) if isinstance(protocols, list) else [],
        "containerProtocolVersions": normalized_versions,
        "instanceIdentityPresent": bool(payload.get("instanceIdentity"))
        or bool(payload.get("instance_identity")),
    }


def _normalize_card(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    capabilities = payload.get("capabilities")
    capabilities = capabilities if isinstance(capabilities, Mapping) else {}
    interfaces = payload.get("supportedInterfaces")
    return {
        "protocolVersion": payload.get("protocolVersion"),
        "streaming": capabilities.get("streaming"),
        "pushNotifications": capabilities.get("pushNotifications"),
        "interfaceCount": len(interfaces) if isinstance(interfaces, list) else 0,
    }


def _certificate_evidence(path: Path | None) -> Mapping[str, Any]:
    if path is None or not path.is_file():
        return {"configured": False}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ProbeFailure()
    responses = payload.get("responses")
    responses = responses if isinstance(responses, Mapping) else {}
    a2a = payload.get("a2a")
    a2a = a2a if isinstance(a2a, Mapping) else {}
    transport = a2a.get("transport")
    transport = transport if isinstance(transport, Mapping) else {}
    return {
        "configured": True,
        "certificateStatus": payload.get("status"),
        "checkedAt": payload.get("checkedAt"),
        "responsesStatus": responses.get("status"),
        "responsesTransport": responses.get("transport"),
        "a2aStatus": a2a.get("status"),
        "a2aProtocolVersion": transport.get("protocolVersion"),
        "a2aFallbackUsed": transport.get("fallbackUsed"),
    }


def _model_observations(
    model_details: Mapping[str, Mapping[str, Any]],
) -> list[CapabilityObservation]:
    observations = []
    for model, details in model_details.items():
        listed = bool(details.get("listed"))
        observations.append(
            _observation(
                f"models.{model}",
                category="models",
                documented_status="announced_available",
                observed_status=(ProbeStatus.AVAILABLE if listed else ProbeStatus.UNAVAILABLE),
                probe_mode=ProbeMode.DERIVED,
                required=True,
                details={"model": model, **details},
            )
        )
    return observations


def _placement_observation(
    placement: str,
    *,
    sku_prefixes: Sequence[str],
    model_details: Mapping[str, Mapping[str, Any]],
    quota_records: Sequence[Mapping[str, Any]],
) -> CapabilityObservation:
    catalog_models: set[str] = set()
    for model, details in model_details.items():
        skus = details.get("skus")
        if not isinstance(skus, list):
            continue
        if any(
            isinstance(sku, Mapping)
            and isinstance(sku.get("name"), str)
            and any(
                str(sku["name"]).casefold().startswith(prefix.casefold()) for prefix in sku_prefixes
            )
            for sku in skus
        ):
            catalog_models.add(model)
    quota_models = {
        str(record["model"])
        for record in quota_records
        if isinstance(record.get("sku"), str)
        and any(
            str(record["sku"]).casefold().startswith(prefix.casefold()) for prefix in sku_prefixes
        )
        and isinstance(record.get("limit"), (int, float))
        and record["limit"] > 0
    }
    required_models = set(model_details)
    available = bool(required_models) and required_models.issubset(catalog_models & quota_models)
    return _observation(
        f"placement.{placement}",
        category="residency",
        documented_status="supported_by_offer",
        observed_status=(ProbeStatus.AVAILABLE if available else ProbeStatus.UNAVAILABLE),
        probe_mode=ProbeMode.DERIVED,
        details={
            "targetModelCount": len(required_models),
            "catalogSupportedModelCount": len(catalog_models),
            "positiveQuotaModelCount": len(quota_models),
        },
    )


def _classified_observation(
    capability_id: str,
    *,
    category: str,
    documented_status: str,
    status: ProbeStatus,
    reason_code: str,
) -> CapabilityObservation:
    return _observation(
        capability_id,
        category=category,
        documented_status=documented_status,
        observed_status=status,
        probe_mode=ProbeMode.CLASSIFIED,
        details={"reasonCode": reason_code},
    )


def _protocol_observations(
    *,
    canary_observation: CapabilityObservation,
    card_observation: CapabilityObservation,
    certificate: Mapping[str, Any],
) -> list[CapabilityObservation]:
    endpoint_protocols = canary_observation.details.get("endpointProtocols")
    endpoint_protocols = set(endpoint_protocols) if isinstance(endpoint_protocols, list) else set()
    certificate_configured = bool(certificate.get("configured"))
    responses_certified = (
        certificate.get("certificateStatus") == "pass"
        and certificate.get("responsesStatus") == "pass"
    )
    a2a_certified = (
        certificate.get("certificateStatus") == "pass"
        and certificate.get("a2aStatus") == "pass"
        and certificate.get("a2aFallbackUsed") is False
    )
    if "responses" not in endpoint_protocols:
        responses_status = ProbeStatus.UNAVAILABLE
    elif not certificate_configured:
        responses_status = ProbeStatus.NOT_CONFIGURED
    else:
        responses_status = ProbeStatus.AVAILABLE if responses_certified else ProbeStatus.PROBE_ERROR
    if (
        "a2a" not in endpoint_protocols
        or card_observation.observed_status is not ProbeStatus.AVAILABLE
    ):
        a2a_status = ProbeStatus.UNAVAILABLE
    elif not certificate_configured:
        a2a_status = ProbeStatus.NOT_CONFIGURED
    else:
        a2a_status = ProbeStatus.AVAILABLE if a2a_certified else ProbeStatus.PROBE_ERROR
    return [
        _observation(
            "hosted.responses",
            category="hosted_runtime",
            documented_status="supported",
            observed_status=responses_status,
            probe_mode=ProbeMode.CERTIFICATE,
            required=True,
            details={
                "endpointAdvertised": "responses" in endpoint_protocols,
                "certified": responses_certified,
                "transport": certificate.get("responsesTransport"),
                "certificateCheckedAt": certificate.get("checkedAt"),
            },
        ),
        _observation(
            "hosted.a2a",
            category="hosted_runtime",
            documented_status="public_preview",
            observed_status=a2a_status,
            probe_mode=ProbeMode.CERTIFICATE,
            required=True,
            details={
                "endpointAdvertised": "a2a" in endpoint_protocols,
                "cardReachable": card_observation.observed_status is ProbeStatus.AVAILABLE,
                "certified": a2a_certified,
                "protocolVersion": certificate.get("a2aProtocolVersion"),
                "fallbackUsed": certificate.get("a2aFallbackUsed"),
                "certificateCheckedAt": certificate.get("checkedAt"),
            },
        ),
    ]


def _summary(
    observations: Sequence[CapabilityObservation],
) -> tuple[str, Mapping[str, Any]]:
    counts = {
        status.value: sum(observation.observed_status is status for observation in observations)
        for status in ProbeStatus
    }
    required = [observation for observation in observations if observation.required]
    failures = [
        observation.capability_id
        for observation in required
        if observation.observed_status is not ProbeStatus.AVAILABLE
    ]
    probe_errors = [
        observation.capability_id
        for observation in observations
        if observation.observed_status is ProbeStatus.PROBE_ERROR
    ]
    status = "pass" if not failures and not probe_errors else "fail"
    return status, {
        "capabilityCount": len(observations),
        "requiredCount": len(required),
        "requiredAvailableCount": len(required) - len(failures),
        "requiredFailures": failures,
        "probeErrors": probe_errors,
        "statusCounts": counts,
    }


def _validate_content_safety(
    ledger: CapabilityLedger,
    config: CapabilityProbeConfig,
) -> None:
    serialized = json.dumps(
        ledger.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    ).casefold()
    restricted = (
        config.project_endpoint,
        config.subscription_id,
        config.resource_group,
        config.account_name,
        config.project_name,
        config.canary_agent_name,
    )
    if any(
        len(value.strip()) >= 8 and value.strip().casefold() in serialized for value in restricted
    ):
        raise RuntimeError("Capability ledger violated its content-safety allowlist.")


def _sdk_versions() -> dict[str, str]:
    versions = {}
    for package in ("azure-ai-projects", "azure-identity", "httpx"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def _arm_url(config: CapabilityProbeConfig, suffix: str) -> str:
    base = f"https://management.azure.com/subscriptions/{quote(config.subscription_id, safe='')}"
    if suffix.startswith("/providers/"):
        return f"{base}{suffix}"
    return (
        f"{base}/resourceGroups/{quote(config.resource_group, safe='')}"
        "/providers/Microsoft.CognitiveServices/accounts/"
        f"{quote(config.account_name, safe='')}{suffix}"
    )


def collect_capability_ledger(
    config: CapabilityProbeConfig,
    *,
    project_client: AIProjectClient,
    arm_reader: AuthenticatedJsonReader,
    foundry_reader: AuthenticatedJsonReader,
) -> CapabilityLedger:
    """Run the read-only probe set and return a content-safe ledger."""

    project_endpoint = canonicalize_foundry_project_endpoint(
        config.project_endpoint,
        expected_account=config.account_name,
        expected_project=config.project_name,
    )
    observations = [
        _probe_collection(
            "hosted.agents",
            category="hosted_runtime",
            documented_status="generally_available",
            required=True,
            loader=lambda: project_client.agents.list(limit=1),
        ),
        _probe_collection(
            "hosted.sessions",
            category="hosted_runtime",
            documented_status="preview",
            required=True,
            loader=lambda: project_client.agents.list_sessions(
                config.canary_agent_name,
                limit=1,
            ),
        ),
        _probe_collection(
            "toolboxes.inventory",
            category="tools",
            documented_status="preview",
            required=True,
            loader=lambda: project_client.toolboxes.list(limit=1),
        ),
        _probe_collection(
            "memory.stores",
            category="context",
            documented_status="preview",
            required=False,
            loader=lambda: project_client.beta.memory_stores.list(limit=1),
        ),
        _probe_collection(
            "routines.inventory",
            category="automation",
            documented_status="preview",
            required=False,
            loader=lambda: project_client.beta.routines.list(limit=1),
        ),
        _probe_collection(
            "schedules.inventory",
            category="automation",
            documented_status="preview",
            required=False,
            loader=lambda: project_client.beta.schedules.list(),
        ),
        _probe_collection(
            "skills.inventory",
            category="tools",
            documented_status="preview",
            required=False,
            loader=lambda: project_client.beta.skills.list(limit=1),
        ),
        _probe_collection(
            "models.operations",
            category="models",
            documented_status="preview",
            required=False,
            loader=lambda: project_client.beta.models.list(),
        ),
    ]

    account_url = _arm_url(config, f"?api-version={ARM_API_VERSION}")
    account_observation, account_payload = _probe_mapping(
        "account.metadata",
        category="account",
        documented_status="generally_available",
        probe_mode=ProbeMode.ARM,
        required=True,
        loader=lambda: arm_reader.get(account_url),
        normalizer=_normalize_account,
    )
    observations.append(account_observation)

    deployments_url = _arm_url(
        config,
        f"/deployments?api-version={ARM_API_VERSION}",
    )
    deployment_observation, _ = _probe_mapping(
        "models.deployments",
        category="models",
        documented_status="generally_available",
        probe_mode=ProbeMode.ARM,
        required=True,
        loader=lambda: arm_reader.get(deployments_url),
        normalizer=_normalize_deployments,
    )
    observations.append(deployment_observation)

    models_url = _arm_url(
        config,
        f"/models?api-version={ARM_API_VERSION}",
    )
    catalog_observation, catalog_payload = _probe_mapping(
        "models.catalog",
        category="models",
        documented_status="generally_available",
        probe_mode=ProbeMode.ARM,
        required=True,
        loader=lambda: arm_reader.get(models_url),
        normalizer=lambda payload: _normalize_model_catalog(
            payload,
            config.target_models,
        ),
    )
    observations.append(catalog_observation)
    model_details = (
        _target_model_details(catalog_payload, config.target_models)
        if catalog_payload is not None
        else {
            model: {"listed": False, "skus": [], "capabilities": {}}
            for model in config.target_models
        }
    )
    observations.extend(_model_observations(model_details))

    usage_url = _arm_url(
        config,
        (
            "/providers/Microsoft.CognitiveServices/locations/"
            f"{quote(config.location, safe='')}/usages"
            f"?api-version={USAGE_API_VERSION}"
        ),
    )
    quota_observation, quota_payload = _probe_mapping(
        "models.quota",
        category="models",
        documented_status="generally_available",
        probe_mode=ProbeMode.ARM,
        required=True,
        loader=lambda: arm_reader.get(usage_url),
        normalizer=lambda payload: _normalize_quota(
            payload,
            config.target_models,
        ),
    )
    quota_records = (
        _target_quota_records(quota_payload, config.target_models)
        if quota_payload is not None
        else []
    )
    if quota_observation.observed_status is ProbeStatus.AVAILABLE and not all(
        any(
            record["model"] == model
            and isinstance(record.get("limit"), (int, float))
            and record["limit"] > 0
            for record in quota_records
        )
        for model in config.target_models
    ):
        quota_observation = quota_observation.model_copy(
            update={"observed_status": ProbeStatus.NOT_CONFIGURED}
        )
    observations.append(quota_observation)
    observations.extend(
        [
            _placement_observation(
                "global",
                sku_prefixes=("Global",),
                model_details=model_details,
                quota_records=quota_records,
            ),
            _placement_observation(
                "data_zone",
                sku_prefixes=("DataZone",),
                model_details=model_details,
                quota_records=quota_records,
            ),
            _placement_observation(
                "regional",
                sku_prefixes=("Standard", "Regional"),
                model_details=model_details,
                quota_records=quota_records,
            ),
        ]
    )

    try:
        canary_payload = _mapping(project_client.agents.get(config.canary_agent_name))
        canary_observation = _observation(
            "hosted.canary_definition",
            category="hosted_runtime",
            documented_status="generally_available",
            observed_status=ProbeStatus.AVAILABLE,
            probe_mode=ProbeMode.SDK,
            required=True,
            details=_normalize_canary(canary_payload),
        )
    except Exception as exc:
        canary_observation = _failed_observation(
            "hosted.canary_definition",
            category="hosted_runtime",
            documented_status="generally_available",
            probe_mode=ProbeMode.SDK,
            required=True,
            exc=exc,
        )
    observations.append(canary_observation)

    card_url = (
        f"{project_endpoint}/agents/"
        f"{quote(config.canary_agent_name, safe='')}"
        "/endpoint/protocols/a2a/agentCard/v1.0"
    )
    card_observation, _ = _probe_mapping(
        "hosted.a2a_card",
        category="hosted_runtime",
        documented_status="public_preview",
        probe_mode=ProbeMode.DATA_PLANE,
        required=True,
        loader=lambda: foundry_reader.get(card_url),
        normalizer=_normalize_card,
    )
    observations.append(card_observation)

    try:
        certificate = _certificate_evidence(config.certificate_path)
    except Exception as exc:
        certificate = {"configured": False}
        observations.append(
            _failed_observation(
                "hosted.protocol_certificate",
                category="hosted_runtime",
                documented_status="local_evidence",
                probe_mode=ProbeMode.CERTIFICATE,
                required=True,
                exc=exc,
            )
        )
    else:
        observations.append(
            _observation(
                "hosted.protocol_certificate",
                category="hosted_runtime",
                documented_status="local_evidence",
                observed_status=(
                    ProbeStatus.AVAILABLE
                    if certificate.get("configured")
                    else ProbeStatus.NOT_CONFIGURED
                ),
                probe_mode=ProbeMode.CERTIFICATE,
                required=True,
                details=certificate,
            )
        )
    observations.extend(
        _protocol_observations(
            canary_observation=canary_observation,
            card_observation=card_observation,
            certificate=certificate,
        )
    )

    private_endpoint_count = (
        account_observation.details.get("privateEndpointCount")
        if account_payload is not None
        else None
    )
    observations.append(
        _observation(
            "network.private_endpoint",
            category="network",
            documented_status="generally_available",
            observed_status=(
                ProbeStatus.AVAILABLE
                if isinstance(private_endpoint_count, int) and private_endpoint_count > 0
                else ProbeStatus.NOT_CONFIGURED
            ),
            probe_mode=ProbeMode.DERIVED,
            details={
                "privateEndpointCount": (
                    private_endpoint_count if isinstance(private_endpoint_count, int) else 0
                )
            },
        )
    )
    observations.extend(
        [
            _classified_observation(
                "hosted.invocations",
                category="hosted_runtime",
                documented_status="preview",
                status=ProbeStatus.MUTATION_REQUIRED,
                reason_code="invocation_creation_required",
            ),
            _classified_observation(
                "hosted.activity",
                category="hosted_runtime",
                documented_status="preview",
                status=ProbeStatus.MUTATION_REQUIRED,
                reason_code="activity_identifier_required",
            ),
            _classified_observation(
                "hosted.background_cancel",
                category="hosted_runtime",
                documented_status="preview",
                status=ProbeStatus.MUTATION_REQUIRED,
                reason_code="background_invocation_required",
            ),
            _classified_observation(
                "hosted.websocket",
                category="hosted_runtime",
                documented_status="private_preview",
                status=ProbeStatus.MUTATION_REQUIRED,
                reason_code="region_preview_invocation_required",
            ),
            _classified_observation(
                "distribution.microsoft_365",
                category="distribution",
                documented_status="early_access_preview",
                status=ProbeStatus.MUTATION_REQUIRED,
                reason_code="publishing_and_admin_approval_required",
            ),
        ]
    )

    status, summary = _summary(observations)
    return CapabilityLedger(
        status=status,
        checked_at=datetime.now(UTC),
        scope={
            "location": config.location,
            "targetModels": list(config.target_models),
            "projectConfigured": bool(config.project_endpoint),
            "canaryConfigured": bool(config.canary_agent_name),
        },
        content_policy={
            "persistsResourceNames": False,
            "persistsResourceIdentifiers": False,
            "persistsEndpoints": False,
            "persistsPrompts": False,
            "persistsPayloads": False,
            "persistsTokens": False,
            "persistsRawErrors": False,
        },
        sdk=_sdk_versions(),
        summary=summary,
        capabilities=observations,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe Foundry capabilities without mutating cloud state."
    )
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--subscription-id", required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--account-name", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--location", required=True)
    parser.add_argument("--canary-agent", required=True)
    parser.add_argument("--certificate", type=Path)
    parser.add_argument(
        "--target-model",
        action="append",
        dest="target_models",
        default=[],
    )
    parser.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    target_models = tuple(args.target_models) or DEFAULT_TARGET_MODELS
    try:
        config = CapabilityProbeConfig(
            project_endpoint=args.project_endpoint,
            subscription_id=args.subscription_id,
            resource_group=args.resource_group,
            account_name=args.account_name,
            project_name=args.project_name,
            location=args.location,
            canary_agent_name=args.canary_agent,
            certificate_path=args.certificate,
            target_models=target_models,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    credential = isolated_default_azure_credential(
        credential_factory=DefaultAzureCredential,
    )
    project_client = AIProjectClient(
        endpoint=config.project_endpoint,
        credential=credential,
        allow_preview=True,
        transport=RequestsTransport(use_env_settings=False),
        redirect_policy=RedirectPolicy(permit_redirects=False),
    )
    arm_reader = AuthenticatedJsonReader(
        credential=credential,
        scope=ARM_SCOPE,
    )
    foundry_reader = AuthenticatedJsonReader(
        credential=credential,
        scope=FOUNDRY_SCOPE,
        foundry_project_endpoint=config.project_endpoint,
    )
    try:
        ledger = collect_capability_ledger(
            config,
            project_client=project_client,
            arm_reader=arm_reader,
            foundry_reader=foundry_reader,
        )
        _validate_content_safety(ledger, config)
    finally:
        foundry_reader.close()
        arm_reader.close()
        project_client.close()
        credential.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            ledger.model_dump(mode="json", by_alias=True),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": ledger.status,
                "checkedAt": ledger.checked_at.isoformat(),
                **ledger.summary.model_dump(mode="json", by_alias=True),
                "report": str(args.out),
            },
            sort_keys=True,
        )
    )
    return 0 if ledger.status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
