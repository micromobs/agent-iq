"""Microsoft Foundry hosted-agent catalog adapter."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import CredentialUnavailableError
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from agent_iq.config import canonicalize_foundry_project_endpoint
from agent_iq.connectors import BearerTokenProvider, DefaultAzureBearerTokenProvider
from agent_iq.contracts import AgentDescriptor, ErrorCode, HealthStatus
from agent_iq.contracts.base import Identifier, NonEmptyText, VersionText
from agent_iq.errors import AgentIqOperationError, operation_error

from ..cards import (
    CardProjectionError,
    authored_card_projection,
    card_hash,
    drifted_card_fields,
    foundry_card_projection,
    served_card_projection,
)

AZURE_AI_SCOPE = "https://ai.azure.com/.default"
_IDENTIFIER_ADAPTER = TypeAdapter(Identifier)


class FoundryAgentRecord(BaseModel):
    """Validated live metadata needed to invoke one Foundry hosted agent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: Identifier
    version: VersionText
    status: NonEmptyText
    kind: str | None = None
    responses_protocol_version: str | None = None
    runtime: str | None = None
    model_deployment: str | None = None
    toolbox_name: str | None = None
    instance_identity: str | None = None
    code_content_hash: str | None = None

    def a2a_base_url(self, project_endpoint: str) -> str:
        project = project_endpoint.rstrip("/")
        return f"{project}/agents/{self.agent_id}/endpoint/protocols/a2a"


class FoundryInventoryRecord(BaseModel):
    """Lightweight project inventory entry that does not resolve mutable versions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: Identifier
    status: NonEmptyText


@dataclass(frozen=True, slots=True)
class FoundryCardState:
    agent_id: str
    health: HealthStatus
    served_hash: str
    expected_hash: str | None
    drifted_fields: tuple[str, ...]
    platform_omitted_fields: tuple[str, ...] = ()


class FoundryCatalogAdapter:
    """List hosted agents through Foundry with environment proxy inheritance disabled.

    An injected transport is a trusted dependency-injection boundary; it must not route requests
    through an untrusted proxy or follow redirects.
    """

    def __init__(
        self,
        project_endpoint: str,
        *,
        token_provider: BearerTokenProvider | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        expected_descriptors: tuple[AgentDescriptor, ...] = (),
        cache_ttl_seconds: float = 30.0,
    ) -> None:
        self.project_endpoint = canonicalize_foundry_project_endpoint(project_endpoint)
        if cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds cannot be negative.")
        self._token_provider = token_provider or DefaultAzureBearerTokenProvider()
        self._owns_token_provider = token_provider is None
        self._transport = transport
        self._expected_descriptors = {
            descriptor.agent_id: descriptor for descriptor in expected_descriptors
        }
        if len(self._expected_descriptors) != len(expected_descriptors):
            raise ValueError("Expected Foundry descriptors must have unique agent IDs.")
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache: tuple[FoundryAgentRecord, ...] | None = None
        self._cache_at = 0.0
        self._inventory_cache: tuple[FoundryInventoryRecord, ...] | None = None
        self._inventory_cache_at = 0.0

    async def list_inventory(
        self,
        *,
        refresh: bool = False,
    ) -> tuple[FoundryInventoryRecord, ...]:
        """List project agents without resolving or trusting mutable version selectors."""

        if (
            not refresh
            and self._inventory_cache is not None
            and time.monotonic() - self._inventory_cache_at <= self._cache_ttl_seconds
        ):
            return self._inventory_cache

        token = await self._access_token()
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(30.0),
            transport=self._transport,
            follow_redirects=False,
            trust_env=False,
        ) as http:
            payloads = await self._list_inventory_payloads(http)
        result = tuple(
            sorted(
                (_parse_inventory_record(payload) for payload in payloads),
                key=lambda item: item.agent_id,
            )
        )
        self._inventory_cache = result
        self._inventory_cache_at = time.monotonic()
        return result

    async def list_agents(self, *, refresh: bool = False) -> tuple[FoundryAgentRecord, ...]:
        """Return every project agent, following Foundry pagination safely."""

        if (
            not refresh
            and self._cache is not None
            and time.monotonic() - self._cache_at <= self._cache_ttl_seconds
        ):
            return self._cache

        token = await self._access_token()
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(30.0),
            transport=self._transport,
            follow_redirects=False,
            trust_env=False,
        ) as http:
            payloads = await self._list_inventory_payloads(http)
            records = [
                await self._load_exact_agent(http, _inventory_agent_id(payload))
                for payload in payloads
            ]
            result = tuple(sorted(records, key=lambda item: item.agent_id))
            self._cache = result
            self._cache_at = time.monotonic()
            return result

    async def _list_inventory_payloads(
        self,
        http: httpx.AsyncClient,
    ) -> tuple[dict[str, object], ...]:
        payloads: list[dict[str, object]] = []
        seen_agent_ids: set[str] = set()
        after: str | None = None
        seen_cursors: set[str] = set()
        for _page in range(100):
            params = {"api-version": "v1", "limit": "100"}
            if after is not None:
                params["after"] = after
            payload = await _request_json(
                http,
                f"{self.project_endpoint}/agents",
                params=params,
            )
            page_payloads = _parse_inventory_page(payload)
            for item in page_payloads:
                agent_id = _inventory_agent_id(item)
                if agent_id in seen_agent_ids:
                    raise operation_error(
                        ErrorCode.CATALOG_UNAVAILABLE,
                        "Foundry catalog returned a duplicate agent ID.",
                    )
                seen_agent_ids.add(agent_id)
                payloads.append(item)
            if not payload.get("has_more"):
                return tuple(payloads)
            cursor = payload.get("last_id")
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                raise operation_error(
                    ErrorCode.CATALOG_UNAVAILABLE,
                    "Foundry catalog returned an invalid pagination cursor.",
                )
            seen_cursors.add(cursor)
            after = cursor

        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            "Foundry catalog exceeded the pagination safety limit.",
        )

    async def assert_exact_traffic(
        self,
        agent_id: str,
        expected_version: str,
        *,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        """Require one 100% fixed traffic rule for the expected immutable version."""

        validated_agent_id = _validate_agent_id(agent_id)
        version = _immutable_agent_version(expected_version)
        relative = f"/agents/{quote(validated_agent_id, safe='')}?api-version=v1"
        if http is not None:
            details = await _request_json(http, f"{self.project_endpoint}{relative}")
            _assert_exact_version_traffic(details, validated_agent_id, version)
            return

        token = await self._access_token()
        async with httpx.AsyncClient(
            headers={"Authorization": "Bearer " + token},
            timeout=httpx.Timeout(30.0),
            transport=self._transport,
            follow_redirects=False,
            trust_env=False,
        ) as owned_http:
            details = await _request_json(owned_http, f"{self.project_endpoint}{relative}")
        _assert_exact_version_traffic(details, validated_agent_id, version)

    async def _load_exact_agent(
        self,
        http: httpx.AsyncClient,
        agent_id: str,
    ) -> FoundryAgentRecord:
        encoded_agent_id = quote(agent_id, safe="")
        details = await _request_json(
            http,
            f"{self.project_endpoint}/agents/{encoded_agent_id}",
            params={"api-version": "v1"},
            not_found_agent_id=agent_id,
        )
        version = _routed_version(details, agent_id)
        version_payload = await _request_json(
            http,
            (
                f"{self.project_endpoint}/agents/{encoded_agent_id}/versions/"
                f"{quote(version, safe='')}"
            ),
            params={"api-version": "v1"},
        )
        return _parse_agent_version(agent_id, version, version_payload)

    async def get_agent(
        self,
        agent_id: str,
        *,
        refresh: bool = False,
    ) -> FoundryAgentRecord:
        """Resolve one agent by stable ID or raise a typed not-found failure."""

        validated_agent_id = _validate_agent_id(agent_id)
        cache = self._cache
        if (
            not refresh
            and cache is not None
            and time.monotonic() - self._cache_at <= self._cache_ttl_seconds
        ):
            assert cache is not None
            agent = next(
                (item for item in cache if item.agent_id == validated_agent_id),
                None,
            )
            if agent is None:
                raise operation_error(
                    ErrorCode.AGENT_NOT_FOUND,
                    f"Foundry agent {validated_agent_id!r} was not found.",
                )
            return agent

        token = await self._access_token()
        async with httpx.AsyncClient(
            headers={"Authorization": "Bearer " + token},
            timeout=httpx.Timeout(30.0),
            transport=self._transport,
            follow_redirects=False,
            trust_env=False,
        ) as http:
            return await self._load_exact_agent(http, validated_agent_id)

    def reconcile_card(
        self,
        agent: FoundryAgentRecord,
        payload: Mapping[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> FoundryCardState:
        """Compare a served card with its canonical source descriptor."""

        if payload.get("name") != agent.agent_id:
            raise operation_error(
                ErrorCode.CARD_INVALID,
                "The native A2A Agent Card identity does not match the catalog.",
                correlation_id=correlation_id,
            )
        try:
            served = served_card_projection(payload)
        except CardProjectionError as exc:
            raise operation_error(
                ErrorCode.CARD_INVALID,
                str(exc),
                correlation_id=correlation_id,
            ) from exc
        descriptor = self._expected_descriptors.get(agent.agent_id)
        if descriptor is None:
            return FoundryCardState(
                agent_id=agent.agent_id,
                health=HealthStatus.UNKNOWN,
                served_hash=card_hash(served),
                expected_hash=None,
                drifted_fields=(),
            )
        authored = authored_card_projection(descriptor)
        expected = foundry_card_projection(descriptor)
        platform_omitted = ("capabilities.extensions",) if "capabilities" in authored else ()
        drifted = drifted_card_fields(expected, served)
        return FoundryCardState(
            agent_id=agent.agent_id,
            health=HealthStatus.UNHEALTHY if drifted else HealthStatus.HEALTHY,
            served_hash=card_hash(served),
            expected_hash=card_hash(expected),
            drifted_fields=drifted,
            platform_omitted_fields=platform_omitted,
        )

    async def close(self) -> None:
        if self._owns_token_provider:
            await self._token_provider.close()

    async def _access_token(self) -> str:
        try:
            return await self._token_provider.get_token(AZURE_AI_SCOPE)
        except (ClientAuthenticationError, CredentialUnavailableError) as exc:
            raise operation_error(
                ErrorCode.AUTHENTICATION_REQUIRED,
                "Microsoft Entra authentication for Foundry failed.",
            ) from exc


def _parse_page(payload: Any) -> tuple[str, ...]:
    if not isinstance(payload, Mapping):
        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            "Foundry catalog returned a non-object response.",
        )
    raw_agents = payload.get("data")
    if not isinstance(raw_agents, list):
        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            "Foundry catalog response is missing its data array.",
        )
    return tuple(_parse_agent_id(item) for item in raw_agents)


def _parse_inventory_page(payload: Any) -> tuple[dict[str, object], ...]:
    if not isinstance(payload, Mapping):
        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            "Foundry catalog returned a non-object response.",
        )
    raw_agents = payload.get("data")
    if not isinstance(raw_agents, list):
        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            "Foundry catalog response is missing its data array.",
        )
    records: list[dict[str, object]] = []
    for item in raw_agents:
        if not isinstance(item, Mapping):
            raise operation_error(
                ErrorCode.CATALOG_UNAVAILABLE,
                "Foundry catalog returned an invalid agent record.",
            )
        records.append(dict(item))
    return tuple(records)


def _inventory_agent_id(payload: Mapping[str, object]) -> str:
    return _parse_agent_id(payload)


def _parse_inventory_record(payload: Mapping[str, object]) -> FoundryInventoryRecord:
    status = payload.get("status", payload.get("state", "active"))
    if not isinstance(status, str):
        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            "Foundry catalog returned an invalid agent state.",
        )
    try:
        return FoundryInventoryRecord(
            agent_id=_inventory_agent_id(payload),
            status=status,
        )
    except ValidationError as exc:
        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            "Foundry catalog returned invalid agent inventory metadata.",
        ) from exc


def _parse_agent_id(payload: Any) -> str:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("id"), str):
        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            "Foundry catalog returned an invalid agent record.",
        )
    return _validate_agent_id(payload["id"])


def _validate_agent_id(value: str) -> str:
    try:
        return _IDENTIFIER_ADAPTER.validate_python(value)
    except ValidationError as exc:
        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            "Foundry catalog returned an unsafe agent ID.",
        ) from exc


def _immutable_agent_version(value: object) -> str:
    version = str(value)
    if (
        not version
        or version != version.strip()
        or len(version) > 64
        or version.startswith("@")
        or any(character in version for character in "/\\?#%")
        or any(ord(character) < 32 or ord(character) == 127 for character in version)
    ):
        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            "Foundry catalog returned an invalid immutable agent version.",
        )
    return version


def _field(payload: Mapping[str, Any], snake_case: str, camel_case: str) -> Any:
    return payload.get(snake_case, payload.get(camel_case))


def _routed_version(payload: Any, agent_id: str) -> str:
    if not isinstance(payload, Mapping):
        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            f"Foundry agent {agent_id!r} returned invalid traffic metadata.",
        )
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
        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            f"Foundry agent {agent_id!r} must have one immutable traffic rule.",
        )
    rule = rules[0]
    percentage = _field(rule, "traffic_percentage", "trafficPercentage")
    if (
        rule.get("type") != "FixedRatio"
        or isinstance(percentage, bool)
        or not isinstance(percentage, int)
        or percentage != 100
    ):
        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            f"Foundry agent {agent_id!r} traffic is not fixed at 100%.",
        )
    return _immutable_agent_version(_field(rule, "agent_version", "agentVersion"))


def _assert_exact_version_traffic(
    payload: Any,
    agent_id: str,
    expected_version: str,
) -> None:
    observed = _routed_version(payload, agent_id)
    if observed != expected_version:
        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            (f"Foundry agent {agent_id!r} traffic is not bound to the expected immutable version."),
        )


def _parse_agent_version(
    agent_id: str,
    expected_version: str,
    payload: Any,
) -> FoundryAgentRecord:
    if not isinstance(payload, Mapping):
        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            f"Foundry agent {agent_id!r} returned invalid version metadata.",
        )
    version = _immutable_agent_version(payload.get("version"))
    if version != expected_version:
        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            f"Foundry agent {agent_id!r} returned mismatched version metadata.",
        )
    definition = payload.get("definition")
    definition = definition if isinstance(definition, Mapping) else {}
    code = definition.get("code_configuration")
    code = code if isinstance(code, Mapping) else {}
    environment = definition.get("environment_variables")
    environment = environment if isinstance(environment, Mapping) else {}
    identity = payload.get("instance_identity")
    identity = identity if isinstance(identity, Mapping) else {}
    protocols = definition.get("protocol_versions")
    protocols = protocols if isinstance(protocols, list) else []
    responses_version = next(
        (
            item.get("version")
            for item in protocols
            if isinstance(item, Mapping) and item.get("protocol") == "responses"
        ),
        None,
    )
    try:
        return FoundryAgentRecord(
            agent_id=agent_id,
            version=version,
            status=str(payload.get("status", "")),
            kind=str(definition["kind"]) if definition.get("kind") is not None else None,
            responses_protocol_version=(
                str(responses_version) if responses_version is not None else None
            ),
            runtime=str(code["runtime"]) if code.get("runtime") is not None else None,
            model_deployment=(
                str(environment["AZURE_AI_MODEL_DEPLOYMENT_NAME"])
                if environment.get("AZURE_AI_MODEL_DEPLOYMENT_NAME") is not None
                else None
            ),
            toolbox_name=(
                str(environment["TOOLBOX_NAME"])
                if environment.get("TOOLBOX_NAME") is not None
                else None
            ),
            instance_identity=(
                str(identity["principal_id"]) if identity.get("principal_id") is not None else None
            ),
            code_content_hash=(
                str(code["content_hash"]) if code.get("content_hash") is not None else None
            ),
        )
    except ValidationError as exc:
        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            "Foundry catalog returned an invalid agent record.",
        ) from exc


async def _request_json(
    http: httpx.AsyncClient,
    url: str,
    *,
    params: Mapping[str, str] | None = None,
    not_found_agent_id: str | None = None,
) -> Mapping[str, Any]:
    try:
        response = await http.get(url, params=params)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404 and not_found_agent_id is not None:
            raise operation_error(
                ErrorCode.AGENT_NOT_FOUND,
                f"Foundry agent {not_found_agent_id!r} was not found.",
            ) from exc
        raise _catalog_http_error(exc.response) from exc
    except httpx.TimeoutException as exc:
        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            "Foundry catalog request timed out.",
            retryable=True,
        ) from exc
    except httpx.TransportError as exc:
        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            "Foundry catalog transport failed.",
            retryable=True,
        ) from exc
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            "Foundry catalog returned malformed JSON.",
        ) from exc
    if not isinstance(payload, Mapping):
        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            "Foundry catalog returned a non-object response.",
        )
    return payload


def _catalog_http_error(response: httpx.Response) -> AgentIqOperationError:
    status = response.status_code
    if status == 401:
        return operation_error(
            ErrorCode.AUTHENTICATION_REQUIRED,
            "Foundry catalog rejected the Microsoft Entra token.",
        )
    if status == 403:
        return operation_error(
            ErrorCode.AUTHORIZATION_DENIED,
            "The caller is not authorized to read the Foundry catalog.",
        )
    retryable = status == 408 or status == 429 or status >= 500
    retry_after = _retry_after(response) if retryable else None
    return operation_error(
        ErrorCode.CATALOG_UNAVAILABLE,
        f"Foundry catalog request failed with HTTP {status}.",
        retryable=retryable,
        retry_after_seconds=retry_after,
    )


def _retry_after(response: httpx.Response) -> int | None:
    value = response.headers.get("retry-after")
    if value is None or not value.isdigit():
        return None
    seconds = int(value)
    return min(max(seconds, 1), 86_400)
