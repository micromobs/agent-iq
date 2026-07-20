"""Image-based Foundry deployment for descriptor-selected Agent IQ hosts."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import re
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

import httpx
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AgentEndpointProtocol,
    ContainerConfiguration,
    HostedAgentDefinition,
    ProtocolVersionRecord,
)
from azure.core.credentials import TokenCredential
from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.core.pipeline.policies import RedirectPolicy
from azure.core.pipeline.transport import RequestsTransport
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobClient, BlobLeaseClient
from openai import DefaultHttpxClient
from pydantic import Field, field_validator, model_validator

from agent_iq.catalog.cards import card_hash, foundry_card_projection
from agent_iq.catalog.loader import load_consumer_runtime_assets
from agent_iq.config import Environment, Settings, canonicalize_foundry_project_endpoint
from agent_iq.connectors.entra import isolated_default_azure_credential
from agent_iq.contracts.base import (
    ContractModel,
    Identifier,
    NonEmptyText,
    SchemaVersion,
    Sha256Digest,
)
from agent_iq.runtime.host_descriptor import (
    HOST_DESCRIPTOR_SHA256_ENV,
    HOST_LOGICAL_AGENT_ID_ENV,
    HOST_RUNTIME_FAMILY_ENV,
    HOST_TOOLBOX_NAME_ENV,
    RuntimeHostDescriptor,
    descriptor_for_agent,
)

HOSTED_RESPONSES_PROTOCOL_VERSION = "2.0.0"
FOUNDRY_API_VERSION = "v1"
FOUNDRY_TOKEN_SCOPE = "https://ai.azure.com/.default"
DEFAULT_DEPLOY_TIMEOUT_SECONDS = 900.0
_SECRET_ENV_MARKERS = ("API_KEY", "PASSWORD", "SECRET", "ACCESS_TOKEN", "CONNECTION_STRING")
_RESERVED_ENV_PREFIXES = ("AGENT_", "FOUNDRY_")


class FoundryHostDeploymentError(RuntimeError):
    """An image host cannot be safely planned, deployed, or certified."""

    def __init__(
        self,
        message: str,
        *,
        evidence: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.evidence = dict(evidence) if evidence is not None else None


class FoundryMutationLease(Protocol):
    """Exclusive authority held by one Agent IQ Foundry mutation operation."""

    @property
    def resource_sha256(self) -> str:
        """Return the hash-bound lease resource identity."""

    def assert_held(self) -> None:
        """Fail unless the caller still owns the exclusive mutation lease."""


class AzureBlobMutationLease:
    """Infinite Azure Blob lease used to serialize Foundry traffic writers."""

    def __init__(
        self,
        blob_url: str,
        *,
        credential: TokenCredential,
        account_name: str,
        project_name: str,
        agent_id: str,
    ) -> None:
        self._blob_url = _canonical_mutation_lease_blob_url(
            blob_url,
            account_name=account_name,
            project_name=project_name,
            agent_id=agent_id,
        )
        self._credential = credential
        self._blob: BlobClient | None = None
        self._lease: BlobLeaseClient | None = None

    @property
    def resource_sha256(self) -> str:
        return _mutation_lease_resource_sha256(self._blob_url)

    def __enter__(self) -> AzureBlobMutationLease:
        self._blob = BlobClient.from_blob_url(
            self._blob_url,
            credential=self._credential,
            transport=RequestsTransport(use_env_settings=False),
            redirect_policy=RedirectPolicy(permit_redirects=False),
        )
        try:
            self._lease = self._blob.acquire_lease(
                lease_duration=-1,
                lease_id=str(uuid.uuid4()),
            )
        except AzureError as exc:
            self._blob.close()
            self._blob = None
            self._lease = None
            raise FoundryHostDeploymentError(
                "The exclusive Foundry mutation lease could not be acquired."
            ) from exc
        return self

    def assert_held(self) -> None:
        if self._lease is None:
            raise FoundryHostDeploymentError("The exclusive Foundry mutation lease is not held.")
        try:
            self._lease.renew()
        except AzureError as exc:
            raise FoundryHostDeploymentError(
                "The exclusive Foundry mutation lease was lost."
            ) from exc

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> Literal[False]:
        del exc_type, traceback
        release_error: AzureError | None = None
        try:
            if self._lease is not None:
                self._lease.release()
        except AzureError as caught:
            release_error = caught
        finally:
            if self._blob is not None:
                self._blob.close()
            self._blob = None
            self._lease = None
        if release_error is not None:
            message = "The Foundry operation completed without releasing its mutation lease."
            if exc_value is not None:
                message = (
                    "The Foundry operation failed and its exclusive mutation lease "
                    "also could not be released."
                )
            raise FoundryHostDeploymentError(message) from release_error
        return False


class FoundryHostPlan(ContractModel):
    """Secret-free immutable input to one Foundry hosted-agent version."""

    schema_version: SchemaVersion = "1.0"
    logical_agent_id: Identifier
    deployment_agent_id: Identifier
    isolated_canary: bool = True
    project_endpoint: NonEmptyText
    image: NonEmptyText
    image_digest: Sha256Digest
    model_deployment: Identifier
    search_service: Identifier
    runtime_descriptor: RuntimeHostDescriptor
    environment_variables: dict[str, str] = Field(min_length=1)
    agent_card: dict[str, Any]

    @field_validator("project_endpoint", mode="before")
    @classmethod
    def canonicalize_project_endpoint(cls, value: object) -> str:
        return canonicalize_foundry_project_endpoint(value)

    @model_validator(mode="after")
    def validate_immutable_plan(self) -> FoundryHostPlan:
        """Reject mutable images, in-place canaries, drift, and secret-shaped env."""

        if self.image.rsplit("@", maxsplit=1)[-1] != self.image_digest:
            raise ValueError("The host image must be pinned to its declared SHA-256 digest.")
        if self.isolated_canary and self.deployment_agent_id == self.logical_agent_id:
            raise ValueError("An isolated canary cannot replace the routed logical agent.")
        if self.runtime_descriptor.logical_agent_id != self.logical_agent_id:
            raise ValueError("The runtime descriptor does not match the logical agent.")
        forbidden = sorted(
            name
            for name in self.environment_variables
            if any(marker in name.upper() for marker in _SECRET_ENV_MARKERS)
        )
        if forbidden:
            raise ValueError(f"Host environment contains secret-shaped fields: {forbidden!r}.")
        reserved = sorted(
            name
            for name in self.environment_variables
            if name.upper().startswith(_RESERVED_ENV_PREFIXES)
        )
        if reserved:
            raise ValueError(f"Host environment contains platform-reserved fields: {reserved!r}.")
        return self


def create_host_plan(
    *,
    logical_agent_id: str,
    deployment_agent_id: str,
    project_endpoint: str,
    account_name: str,
    project_name: str,
    image: str,
    model_deployment: str,
    search_service: str,
    isolated_canary: bool = True,
) -> FoundryHostPlan:
    """Build the deterministic deployment contract for one logical agent."""

    if "@sha256:" not in image:
        raise FoundryHostDeploymentError("Foundry host images must use an immutable digest.")
    image_digest = image.rsplit("@", maxsplit=1)[-1]
    try:
        normalized_endpoint = canonicalize_foundry_project_endpoint(
            project_endpoint,
            expected_account=account_name,
            expected_project=project_name,
        )
    except ValueError as exc:
        raise FoundryHostDeploymentError(str(exc)) from exc
    assets = load_consumer_runtime_assets()
    descriptor = descriptor_for_agent(logical_agent_id, assets=assets)
    toolbox_endpoint = (
        f"{normalized_endpoint}/toolboxes/{descriptor.toolbox_name}/mcp?api-version=v1"
    )
    environment = {
        "AIQ_ENVIRONMENT": Environment.CANARY.value,
        HOST_LOGICAL_AGENT_ID_ENV: descriptor.logical_agent_id,
        HOST_RUNTIME_FAMILY_ENV: descriptor.runtime_family,
        HOST_DESCRIPTOR_SHA256_ENV: descriptor.descriptor_sha256,
        HOST_TOOLBOX_NAME_ENV: descriptor.toolbox_name,
        "AZURE_AI_PROJECT_ENDPOINT": normalized_endpoint,
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": model_deployment,
        "TOOLBOX_NAME": descriptor.toolbox_name,
        "TOOLBOX_ENDPOINT": toolbox_endpoint,
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "false",
    }
    agent_card = foundry_card_projection(
        next(item for item in assets.catalog.agents if item.agent_id == logical_agent_id)
    )
    try:
        return FoundryHostPlan(
            logical_agent_id=logical_agent_id,
            deployment_agent_id=deployment_agent_id,
            isolated_canary=isolated_canary,
            project_endpoint=normalized_endpoint,
            image=image,
            image_digest=image_digest,
            model_deployment=model_deployment,
            search_service=search_service,
            runtime_descriptor=descriptor,
            environment_variables=environment,
            agent_card=agent_card,
        )
    except ValueError as exc:
        raise FoundryHostDeploymentError(str(exc)) from exc


def hosted_agent_definition(plan: FoundryHostPlan) -> HostedAgentDefinition:
    """Create the current image-based Foundry Responses definition."""

    return HostedAgentDefinition(
        cpu="1",
        memory="2Gi",
        container_configuration=ContainerConfiguration(image=plan.image),
        environment_variables=dict(plan.environment_variables),
        protocol_versions=[
            ProtocolVersionRecord(
                protocol=AgentEndpointProtocol.RESPONSES,
                version=HOSTED_RESPONSES_PROTOCOL_VERSION,
            )
        ],
    )


def incoming_a2a_patch(
    plan: FoundryHostPlan,
    *,
    agent_version: str,
) -> dict[str, Any]:
    """Build the explicit Entra-authenticated Responses plus A2A endpoint patch."""

    version = _immutable_agent_version(agent_version, "Agent version")
    return {
        "agent_endpoint": {
            "version_selector": {
                "version_selection_rules": [
                    {
                        "type": "FixedRatio",
                        "agent_version": version,
                        "traffic_percentage": 100,
                    }
                ]
            },
            "protocols": ["responses", "a2a"],
            "authorization_schemes": [
                {
                    "type": "Entra",
                    "isolation_key_source": {"kind": "Entra"},
                }
            ],
        },
        "agent_card": dict(plan.agent_card),
    }


def deploy_host_plan(
    plan: FoundryHostPlan,
    *,
    credential: TokenCredential,
    mutation_lease: FoundryMutationLease,
    account_name: str,
    project_name: str,
    timeout_seconds: float = DEFAULT_DEPLOY_TIMEOUT_SECONDS,
    operation_id: str | None = None,
) -> dict[str, Any]:
    """Create a hosted version, wait for readiness, and enable incoming A2A."""

    validated_operation_id = _operation_id(operation_id or str(uuid.uuid4()))
    project_endpoint = _bound_project_endpoint(
        plan,
        account_name=account_name,
        project_name=project_name,
    )
    client = _project_client(project_endpoint, credential)
    try:
        mutation_lease.assert_held()
        previous = _existing_agent_state(client, plan.deployment_agent_id)
        versions_before = (
            _agent_version_inventory(client, plan.deployment_agent_id)
            if previous is not None
            else []
        )
        traffic_before = previous["versionSelectionRules"] if previous is not None else []
        mutation_lease.assert_held()
        created = client.agents.create_version(
            plan.deployment_agent_id,
            definition=hosted_agent_definition(plan),
            description=str(plan.agent_card["description"]),
            metadata={
                "agentIqDeploymentMode": "container",
                "agentIqLogicalAgentId": plan.logical_agent_id,
                "agentIqRuntimeFamily": plan.runtime_descriptor.runtime_family,
                "agentIqDescriptorSha256": plan.runtime_descriptor.descriptor_sha256,
                "agentIqImageDigest": plan.image_digest,
                "agentIqOperationId": validated_operation_id,
            },
        )
        version = _immutable_agent_version(getattr(created, "version", None), "Created version")
        try:
            active = _wait_for_active_version(
                client,
                plan.deployment_agent_id,
                version,
                timeout_seconds=timeout_seconds,
                mutation_lease=mutation_lease,
            )
            versions_after = _agent_version_inventory(client, plan.deployment_agent_id)
            _assert_single_created_version(
                before=versions_before,
                after=versions_after,
                created_version=version,
            )
            _assert_owned_created_version(
                client,
                plan,
                created_version=version,
                operation_id=validated_operation_id,
            )
            current_before_patch = _existing_agent_state(client, plan.deployment_agent_id)
            expected_before_patch = (
                traffic_before if previous is not None else [_fixed_version_rule(version)]
            )
            if (
                current_before_patch is None
                or current_before_patch["versionSelectionRules"] != expected_before_patch
            ):
                raise FoundryHostDeploymentError(
                    "Host deployment traffic changed before endpoint activation."
                )
            _patch_agent_endpoint(
                plan,
                credential=credential,
                mutation_lease=mutation_lease,
                agent_version=version,
                expected_current_rules=expected_before_patch,
                account_name=account_name,
                project_name=project_name,
            )
            routed = _existing_agent_state(client, plan.deployment_agent_id)
            if routed is None or routed["versionSelectionRules"] != [_fixed_version_rule(version)]:
                raise FoundryHostDeploymentError(
                    "Host deployment traffic is not fixed to the operation-created version."
                )
            if _agent_version_inventory(client, plan.deployment_agent_id) != versions_after:
                raise FoundryHostDeploymentError(
                    "Host deployment version inventory drifted after endpoint activation."
                )
            mutation_lease.assert_held()
        except (AzureError, FoundryHostDeploymentError, httpx.HTTPError) as exc:
            failure_record = _seal_host_deployment_record(
                {
                    "schemaVersion": "3.0",
                    "evidenceType": "agent-iq.descriptor-host-deployment",
                    "operationId": validated_operation_id,
                    "status": "failed",
                    "deploymentAgentId": plan.deployment_agent_id,
                    "logicalAgentId": plan.logical_agent_id,
                    "projectEndpoint": plan.project_endpoint,
                    "createdVersion": version,
                    "createdVersionRetention": "required",
                    "agentExistedBefore": previous is not None,
                    "versionInventoryBefore": versions_before,
                    "trafficBefore": traffic_before,
                    "cloudState": "uncertain",
                    "compensatingMutationAttempted": False,
                    "mutationLease": {
                        "type": "azure_blob_infinite_lease",
                        "resourceSha256": mutation_lease.resource_sha256,
                    },
                    "plan": plan.model_dump(mode="json", by_alias=True),
                    "failedAt": datetime.now(UTC).isoformat(),
                }
            )
            raise FoundryHostDeploymentError(
                f"Host version {version!r} failed; no compensating traffic or deletion "
                "mutation was attempted, and the immutable version was retained.",
                evidence=failure_record,
            ) from exc
        active_mapping = _as_mapping(active)
        principal_id = _instance_principal_id(active_mapping)
        created_at = datetime.now(UTC).isoformat()
        traffic_after = [_fixed_version_rule(version)]
        lease_resource_sha256 = mutation_lease.resource_sha256
        rollback_action = (
            "restore_prior_traffic_retain_created_version"
            if previous is not None
            else "retain_isolated_created_version"
        )
        record = {
            "schemaVersion": "3.0",
            "evidenceType": "agent-iq.descriptor-host-deployment",
            "operationId": validated_operation_id,
            "status": "active",
            "buildMode": "container",
            "logicalAgentId": plan.logical_agent_id,
            "deploymentAgentId": plan.deployment_agent_id,
            "version": version,
            "instancePrincipalId": principal_id,
            "projectEndpoint": plan.project_endpoint,
            "runtimeFamily": plan.runtime_descriptor.runtime_family,
            "descriptorSha256": plan.runtime_descriptor.descriptor_sha256,
            "expectedToolboxName": plan.runtime_descriptor.toolbox_name,
            "image": plan.image,
            "imageDigest": plan.image_digest,
            "cardHash": card_hash(plan.agent_card),
            "agentCard": plan.agent_card,
            "protocols": {
                "host": {
                    "protocol": "responses",
                    "version": HOSTED_RESPONSES_PROTOCOL_VERSION,
                },
                "endpoint": ["responses", "a2a"],
                "a2aWire": "1.0",
            },
            "previousState": previous,
            "agentExistedBefore": previous is not None,
            "versionInventoryBefore": versions_before,
            "versionInventoryAfter": versions_after,
            "trafficBefore": traffic_before,
            "trafficAfter": traffic_after,
            "createdVersionRetention": "required",
            "mutationLease": {
                "type": "azure_blob_infinite_lease",
                "resourceSha256": lease_resource_sha256,
            },
            "rollback": {
                "action": rollback_action,
                "createdVersion": version,
                "createdVersionRetention": "required",
                "agentExistedBefore": previous is not None,
                "versionInventoryBefore": versions_before,
                "versionInventoryAfter": versions_after,
                "trafficBefore": traffic_before,
                "trafficAfter": traffic_after,
                "mutationLeaseResourceSha256": lease_resource_sha256,
            },
            "plan": plan.model_dump(mode="json", by_alias=True),
            "createdAt": created_at,
        }
        return _seal_host_deployment_record(record)
    finally:
        client.close()


def rollback_host(
    record: Mapping[str, Any],
    *,
    credential: TokenCredential,
    mutation_lease: FoundryMutationLease,
    account_name: str,
    project_name: str,
    record_path: Path | None = None,
) -> dict[str, Any]:
    """Restore prior traffic while retaining the operation-created version."""

    validated = _validate_host_deployment_record(record, record_path=record_path)
    plan = validated["plan"]
    operation_id = validated["operationId"]
    created_version = validated["createdVersion"]
    versions_before = validated["versionInventoryBefore"]
    versions_after = validated["versionInventoryAfter"]
    traffic_before = validated["trafficBefore"]
    traffic_after = validated["trafficAfter"]
    agent_existed_before = validated["agentExistedBefore"]
    lease_resource_sha256 = validated["mutationLeaseResourceSha256"]
    if mutation_lease.resource_sha256 != lease_resource_sha256:
        raise FoundryHostDeploymentError(
            "Rollback mutation lease does not match the sealed deployment record."
        )
    project_endpoint = _bound_project_endpoint(
        plan,
        account_name=account_name,
        project_name=project_name,
    )
    client = _project_client(project_endpoint, credential)
    try:
        mutation_lease.assert_held()
        current = _existing_agent_state(client, plan.deployment_agent_id)
        if current is None:
            raise FoundryHostDeploymentError(
                "Rollback refused because the recorded agent no longer exists."
            )
        current_traffic = current["versionSelectionRules"]
        if current_traffic != traffic_after:
            raise FoundryHostDeploymentError(
                "Rollback refused because live traffic drifted from the recorded deployment."
            )
        current_versions = _agent_version_inventory(client, plan.deployment_agent_id)
        if current_versions != versions_after:
            raise FoundryHostDeploymentError(
                "Rollback refused because the live version inventory drifted."
            )
        _assert_owned_created_version(
            client,
            plan,
            created_version=created_version,
            operation_id=operation_id,
        )

        if not agent_existed_before:
            if versions_before or versions_after != [created_version]:
                raise FoundryHostDeploymentError(
                    "Rollback refused because sole ownership of the new agent is unproven."
                )
            mutation_lease.assert_held()
            return {
                "status": "retained",
                "action": "retain_isolated_created_version",
                "operationId": operation_id,
                "createdVersion": created_version,
                "createdVersionRetained": True,
                "versionSelectionRules": traffic_after,
            }

        _restore_agent_traffic(
            plan,
            credential=credential,
            mutation_lease=mutation_lease,
            version_selection_rules=traffic_before,
            expected_current_rules=traffic_after,
            account_name=account_name,
            project_name=project_name,
        )
        current_after = _existing_agent_state(client, plan.deployment_agent_id)
        if current_after is None:
            raise FoundryHostDeploymentError(
                "Rollback failed because the recorded agent disappeared after traffic restoration."
            )
        if current_after["versionSelectionRules"] != traffic_before:
            raise FoundryHostDeploymentError(
                "Rollback failed because prior traffic was not restored exactly."
            )
        remaining_versions = _agent_version_inventory(client, plan.deployment_agent_id)
        if remaining_versions != versions_after:
            raise FoundryHostDeploymentError(
                "Rollback failed because the retained version inventory drifted."
            )
        _assert_owned_created_version(
            client,
            plan,
            created_version=created_version,
            operation_id=operation_id,
        )
        mutation_lease.assert_held()
        return {
            "status": "rolled_back",
            "action": "restore_prior_traffic_retain_created_version",
            "operationId": operation_id,
            "createdVersion": created_version,
            "createdVersionRetained": True,
            "versionSelectionRules": traffic_before,
        }
    finally:
        client.close()


async def probe_toolbox(
    *,
    project_endpoint: str,
    account_name: str,
    project_name: str,
    toolbox_name: str,
) -> dict[str, Any]:
    """Verify the pre-existing managed toolbox before creating a host version."""

    from agent_iq.connectors.foundry_toolbox import load_toolbox_tools

    try:
        project_endpoint = canonicalize_foundry_project_endpoint(
            project_endpoint,
            expected_account=account_name,
            expected_project=project_name,
        )
    except ValueError as exc:
        raise FoundryHostDeploymentError(str(exc)) from exc
    settings = Settings(
        environment=Environment.CANARY,
        foundry_project_endpoint=project_endpoint,
        azure_ai_account=account_name,
        azure_ai_project=project_name,
        toolbox_name=toolbox_name,
        toolbox_endpoint=(
            f"{project_endpoint.rstrip('/')}/toolboxes/{toolbox_name}/mcp?api-version=v1"
        ),
        _env_file=None,
    )
    tools = await load_toolbox_tools(settings)
    tool_names = sorted(tool.name for tool in tools)
    required_tool = "knowledge_base_retrieve"
    knowledge_present = any(
        name == required_tool
        or name.endswith(f".{required_tool}")
        or name.endswith(f"___{required_tool}")
        for name in tool_names
    )
    return {
        "status": "pass" if knowledge_present else "fail",
        "toolboxName": toolbox_name,
        "toolCount": len(tool_names),
        "tools": tool_names,
        "knowledgeRetrievePresent": knowledge_present,
    }


def probe_responses_stream(
    responses_client: Any,
    *,
    model: str,
    prompt: str,
    required_terms: Sequence[str] = (),
) -> dict[str, Any]:
    """Certify incremental Responses events without depending on A2A streaming."""

    started = time.perf_counter()
    event_types: list[str] = []
    deltas: list[str] = []
    first_delta_ms: int | None = None
    completed = False
    with responses_client.responses.stream(model=model, input=prompt) as stream:
        for event in stream:
            event_type = str(getattr(event, "type", "unknown"))
            event_types.append(event_type)
            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", "")
                if isinstance(delta, str) and delta:
                    if first_delta_ms is None:
                        first_delta_ms = round((time.perf_counter() - started) * 1000)
                    deltas.append(delta)
            elif event_type == "response.completed":
                completed = True
        final = stream.get_final_response()
    answer = "".join(deltas).strip()
    if not answer:
        final_text = getattr(final, "output_text", "")
        if isinstance(final_text, str):
            answer = final_text.strip()
    folded = answer.casefold()
    missing_terms = [term for term in required_terms if term.casefold() not in folded]
    duration_ms = round((time.perf_counter() - started) * 1000)
    checks = {
        "incrementalEventsPresent": bool(deltas),
        "completedEventPresent": completed,
        "answerPresent": bool(answer),
        "requiredTermsPresent": not missing_terms,
    }
    return {
        "schemaVersion": "1.0",
        "status": "pass" if all(checks.values()) else "fail",
        "transport": "foundry-responses-sse",
        "checks": checks,
        "eventCount": len(event_types),
        "deltaCount": len(deltas),
        "eventTypes": event_types,
        "firstDeltaMs": first_delta_ms,
        "durationMs": duration_ms,
        "answer": answer,
        "missingTerms": missing_terms,
    }


def _probe_live_responses(
    plan: FoundryHostPlan,
    *,
    credential: TokenCredential,
    account_name: str,
    project_name: str,
    agent_version: str,
    prompt: str,
    required_terms: Sequence[str],
) -> dict[str, Any]:
    version = _immutable_agent_version(agent_version, "Certified agent version")
    _assert_current_traffic(
        plan,
        credential=credential,
        account_name=account_name,
        project_name=project_name,
        expected_rules=[
            {
                "type": "FixedRatio",
                "agent_version": version,
                "traffic_percentage": 100,
            }
        ],
    )
    project_endpoint = _bound_project_endpoint(
        plan,
        account_name=account_name,
        project_name=project_name,
    )
    project_client = _project_client(project_endpoint, credential)
    try:
        with DefaultHttpxClient(follow_redirects=False, trust_env=False) as http_client:
            openai_client = project_client.get_openai_client(
                agent_name=plan.deployment_agent_id,
                http_client=http_client,
            )
            try:
                result = probe_responses_stream(
                    openai_client,
                    model=plan.model_deployment,
                    prompt=prompt,
                    required_terms=required_terms,
                )
                result["agentVersion"] = version
                return result
            finally:
                openai_client.close()
    finally:
        project_client.close()


def _wait_for_active_version(
    client: AIProjectClient,
    agent_id: str,
    version: str,
    *,
    timeout_seconds: float,
    mutation_lease: FoundryMutationLease,
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    while True:
        mutation_lease.assert_held()
        details = client.agents.get_version(agent_id, version)
        observed_value = (
            details.get("version")
            if isinstance(details, Mapping)
            else getattr(details, "version", None)
        )
        observed_version = _immutable_agent_version(
            observed_value,
            "Validated version",
        )
        if observed_version != version:
            raise FoundryHostDeploymentError(
                f"Foundry returned version {observed_version!r} while validating {version!r}."
            )
        status = _status_text(details.status)
        if status == "active":
            return details
        if status == "failed":
            platform_error = _provisioning_error_text(details)
            raise FoundryHostDeploymentError(
                f"Foundry host {agent_id!r} version {version!r} failed provisioning"
                f"{platform_error}."
            )
        if time.monotonic() >= deadline:
            raise FoundryHostDeploymentError(
                f"Foundry host {agent_id!r} did not become active within "
                f"{timeout_seconds:.0f} seconds."
            )
        time.sleep(10.0)


def _provisioning_error_text(details: Any) -> str:
    error = (
        details.get("error") if isinstance(details, Mapping) else getattr(details, "error", None)
    )
    if error is None:
        return ""
    if isinstance(error, Mapping):
        code = error.get("code")
        message = error.get("message")
    else:
        code = getattr(error, "code", None)
        message = getattr(error, "message", None)
    parts = [str(value).strip() for value in (code, message) if str(value or "").strip()]
    return f" ({': '.join(parts)})" if parts else ""


def _patch_agent_endpoint(
    plan: FoundryHostPlan,
    *,
    credential: TokenCredential,
    mutation_lease: FoundryMutationLease,
    agent_version: str,
    expected_current_rules: object,
    account_name: str,
    project_name: str,
) -> None:
    version = _immutable_agent_version(agent_version, "Agent version")
    expected_rules = [
        {
            "type": "FixedRatio",
            "agent_version": version,
            "traffic_percentage": 100,
        }
    ]
    _patch_and_verify_traffic(
        plan,
        credential=credential,
        mutation_lease=mutation_lease,
        account_name=account_name,
        project_name=project_name,
        payload=incoming_a2a_patch(plan, agent_version=version),
        expected_current_rules=expected_current_rules,
        expected_rules=expected_rules,
    )


def _restore_agent_traffic(
    plan: FoundryHostPlan,
    *,
    credential: TokenCredential,
    mutation_lease: FoundryMutationLease,
    version_selection_rules: object,
    expected_current_rules: object,
    account_name: str,
    project_name: str,
) -> None:
    rules = _normalize_version_selection_rules(
        version_selection_rules,
        "Version selection rules",
    )
    _patch_and_verify_traffic(
        plan,
        credential=credential,
        mutation_lease=mutation_lease,
        account_name=account_name,
        project_name=project_name,
        payload={
            "agent_endpoint": {
                "version_selector": {
                    "version_selection_rules": rules,
                }
            }
        },
        expected_current_rules=expected_current_rules,
        expected_rules=rules,
    )


def _patch_and_verify_traffic(
    plan: FoundryHostPlan,
    *,
    credential: TokenCredential,
    mutation_lease: FoundryMutationLease,
    account_name: str,
    project_name: str,
    payload: Mapping[str, Any],
    expected_current_rules: object,
    expected_rules: object,
) -> None:
    project_endpoint = _bound_project_endpoint(
        plan,
        account_name=account_name,
        project_name=project_name,
    )
    agent_url = (
        f"{project_endpoint}/agents/{plan.deployment_agent_id}?api-version={FOUNDRY_API_VERSION}"
    )
    _assert_current_traffic(
        plan,
        credential=credential,
        account_name=account_name,
        project_name=project_name,
        expected_rules=expected_current_rules,
    )
    mutation_lease.assert_held()
    token = credential.get_token(FOUNDRY_TOKEN_SCOPE).token
    mutation_lease.assert_held()
    with _isolated_http_client() as http:
        response = http.patch(
            agent_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=dict(payload),
        )
        response.raise_for_status()
    mutation_lease.assert_held()
    _assert_current_traffic(
        plan,
        credential=credential,
        account_name=account_name,
        project_name=project_name,
        expected_rules=expected_rules,
        token=token,
    )


def _assert_current_traffic(
    plan: FoundryHostPlan,
    *,
    credential: TokenCredential,
    account_name: str,
    project_name: str,
    expected_rules: object,
    token: str | None = None,
) -> None:
    project_endpoint = _bound_project_endpoint(
        plan,
        account_name=account_name,
        project_name=project_name,
    )
    access_token = token or credential.get_token(FOUNDRY_TOKEN_SCOPE).token
    with _isolated_http_client() as http:
        response = http.get(
            (
                f"{project_endpoint}/agents/{plan.deployment_agent_id}"
                f"?api-version={FOUNDRY_API_VERSION}"
            ),
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise FoundryHostDeploymentError(
            "Foundry agent traffic verification did not return JSON."
        ) from exc
    if not isinstance(payload, Mapping):
        raise FoundryHostDeploymentError(
            "Foundry agent traffic verification must return an object."
        )
    observed = _traffic_rules_from_agent_payload(payload)
    expected = _normalize_version_selection_rules(
        expected_rules,
        "Expected version selection rules",
    )
    if observed != expected:
        raise FoundryHostDeploymentError(
            f"Foundry agent traffic did not bind to the exact requested versions: "
            f"expected {expected!r}, observed {observed!r}."
        )


def _bound_project_endpoint(
    plan: FoundryHostPlan,
    *,
    account_name: str,
    project_name: str,
) -> str:
    try:
        return canonicalize_foundry_project_endpoint(
            plan.project_endpoint,
            expected_account=account_name,
            expected_project=project_name,
        )
    except ValueError as exc:
        raise FoundryHostDeploymentError(str(exc)) from exc


def _canonical_mutation_lease_blob_url(
    value: object,
    *,
    account_name: str,
    project_name: str,
    agent_id: str,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\\" in value
        or "%" in value
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise FoundryHostDeploymentError(
            "Mutation lease URL must be a canonical credential-free Azure Blob URL."
        )
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise FoundryHostDeploymentError(
            "Mutation lease URL must be a canonical credential-free Azure Blob URL."
        ) from exc
    suffix = ".blob.core.windows.net"
    if (
        parsed.scheme != "https"
        or hostname is None
        or parsed.netloc != hostname
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not hostname.endswith(suffix)
    ):
        raise FoundryHostDeploymentError(
            "Mutation lease URL must be a canonical credential-free Azure Blob URL."
        )
    storage_account = hostname[: -len(suffix)]
    if (
        len(storage_account) < 3
        or len(storage_account) > 24
        or not storage_account.isascii()
        or not storage_account.isalnum()
        or storage_account.casefold() != storage_account
    ):
        raise FoundryHostDeploymentError("Mutation lease storage account is invalid.")
    path_parts = parsed.path.split("/")
    if len(path_parts) != 7 or path_parts[0]:
        raise FoundryHostDeploymentError(
            "Mutation lease URL is not bound to the expected Foundry agent."
        )
    container, product, lane, bound_account, bound_project, blob_name = path_parts[1:]
    if (
        re.fullmatch(r"[a-z0-9](?:[a-z0-9]|-(?!-)){1,61}[a-z0-9]", container) is None
        or product != "agent-iq"
        or lane != "foundry"
        or bound_account != account_name
        or bound_project != project_name
        or blob_name != f"{agent_id}.lock"
    ):
        raise FoundryHostDeploymentError(
            "Mutation lease URL is not bound to the expected Foundry agent."
        )
    canonical = (
        f"https://{storage_account}{suffix}/{container}/agent-iq/foundry/"
        f"{account_name}/{project_name}/{agent_id}.lock"
    )
    if value != canonical:
        raise FoundryHostDeploymentError("Mutation lease URL is not canonical.")
    return canonical


def _mutation_lease_resource_sha256(blob_url: str) -> str:
    return f"sha256:{hashlib.sha256(blob_url.encode('utf-8')).hexdigest()}"


def _project_client(
    project_endpoint: str,
    credential: TokenCredential,
) -> AIProjectClient:
    """Create an SDK client that cannot inherit proxies or follow redirects."""

    return AIProjectClient(
        endpoint=project_endpoint,
        credential=credential,
        allow_preview=True,
        transport=RequestsTransport(use_env_settings=False),
        redirect_policy=RedirectPolicy(permit_redirects=False),
    )


def _isolated_http_client() -> httpx.Client:
    """Create a credential-bearing HTTP client isolated from process proxy settings."""

    return httpx.Client(
        timeout=60.0,
        follow_redirects=False,
        trust_env=False,
    )


def _existing_agent_state(
    client: AIProjectClient,
    agent_id: str,
) -> dict[str, Any] | None:
    try:
        details = _as_mapping(client.agents.get(agent_id))
    except ResourceNotFoundError:
        return None
    rules = _traffic_rules_from_agent_payload(details)
    return {
        "versionSelectionRules": rules,
        "routedVersions": [str(rule["agent_version"]) for rule in rules],
    }


def _fixed_version_rule(version: str) -> dict[str, Any]:
    return {
        "type": "FixedRatio",
        "agent_version": _immutable_agent_version(version, "Agent version"),
        "traffic_percentage": 100,
    }


def _agent_version_inventory(
    client: AIProjectClient,
    agent_id: str,
) -> list[str]:
    versions: list[str] = []
    for item in client.agents.list_versions(agent_id, limit=100, include_drafts=True):
        value = getattr(item, "version", None)
        if value is None:
            value = _as_mapping(item).get("version")
        versions.append(_immutable_agent_version(value, "Live agent version"))
    if len(versions) != len(set(versions)):
        raise FoundryHostDeploymentError("Foundry returned duplicate agent versions.")
    return sorted(versions)


def _assert_single_created_version(
    *,
    before: Sequence[str],
    after: Sequence[str],
    created_version: str,
) -> None:
    expected = sorted([*before, created_version])
    if list(after) != expected or created_version in before:
        raise FoundryHostDeploymentError(
            "Host deployment did not add exactly one operation-owned version."
        )


def _assert_owned_created_version(
    client: AIProjectClient,
    plan: FoundryHostPlan,
    *,
    created_version: str,
    operation_id: str,
) -> None:
    details = _as_mapping(client.agents.get_version(plan.deployment_agent_id, created_version))
    observed_version = _immutable_agent_version(
        details.get("version"),
        "Created version ownership response",
    )
    if observed_version != created_version:
        raise FoundryHostDeploymentError(
            "Rollback refused because created-version ownership returned a different version."
        )
    metadata = _mapping(details.get("metadata"), "Created version metadata")
    expected = {
        "agentIqOperationId": operation_id,
        "agentIqDeploymentMode": "container",
        "agentIqLogicalAgentId": plan.logical_agent_id,
        "agentIqRuntimeFamily": plan.runtime_descriptor.runtime_family,
        "agentIqDescriptorSha256": plan.runtime_descriptor.descriptor_sha256,
        "agentIqImageDigest": plan.image_digest,
    }
    if any(metadata.get(name) != value for name, value in expected.items()):
        raise FoundryHostDeploymentError(
            "Rollback refused because created-version ownership could not be proven."
        )


def _operation_id(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise FoundryHostDeploymentError("Operation ID must be a canonical UUID.") from exc
    canonical = str(parsed)
    if parsed.version != 4 or value != canonical:
        raise FoundryHostDeploymentError("Operation ID must be a canonical UUIDv4.")
    return canonical


def _normalized_version_inventory(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise FoundryHostDeploymentError(f"{label} must be an array.")
    normalized = [
        _immutable_agent_version(item, f"{label}[{index}]") for index, item in enumerate(value)
    ]
    if normalized != sorted(normalized) or len(normalized) != len(set(normalized)):
        raise FoundryHostDeploymentError(f"{label} must be sorted and unique.")
    return normalized


def _record_digest(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _seal_host_deployment_record(payload: Mapping[str, Any]) -> dict[str, Any]:
    if "integrity" in payload:
        raise FoundryHostDeploymentError("Deployment record is already sealed.")
    sealed = dict(payload)
    sealed["integrity"] = {
        "algorithm": "sha256",
        "payloadSha256": _record_digest(payload),
    }
    return sealed


def _validate_host_deployment_record(
    record: Mapping[str, Any],
    *,
    record_path: Path | None = None,
) -> dict[str, Any]:
    payload = dict(record)
    integrity = _mapping(payload.pop("integrity", None), "Deployment record integrity")
    if (
        integrity.get("algorithm") != "sha256"
        or not isinstance(integrity.get("payloadSha256"), str)
        or not hmac.compare_digest(
            integrity["payloadSha256"],
            _record_digest(payload),
        )
    ):
        raise FoundryHostDeploymentError("Deployment record integrity validation failed.")
    if (
        payload.get("schemaVersion") != "3.0"
        or payload.get("evidenceType") != "agent-iq.descriptor-host-deployment"
        or payload.get("status") != "active"
        or payload.get("createdVersionRetention") != "required"
    ):
        raise FoundryHostDeploymentError("Deployment record type or status is invalid.")

    operation_id = _operation_id(str(payload.get("operationId", "")))
    if record_path is not None:
        expected_name = f"{operation_id}.deployment.json"
        if record_path.name.casefold() == "latest.json" or record_path.name != expected_name:
            raise FoundryHostDeploymentError(
                "Rollback requires the explicit operation-bound deployment record filename."
            )

    plan = FoundryHostPlan.model_validate(payload.get("plan"))
    created_version = _immutable_agent_version(
        payload.get("version"),
        "Recorded created version",
    )
    if (
        payload.get("deploymentAgentId") != plan.deployment_agent_id
        or payload.get("logicalAgentId") != plan.logical_agent_id
        or payload.get("projectEndpoint") != plan.project_endpoint
    ):
        raise FoundryHostDeploymentError(
            "Deployment record target does not match its immutable host plan."
        )
    versions_before = _normalized_version_inventory(
        payload.get("versionInventoryBefore"),
        "Recorded prior version inventory",
    )
    versions_after = _normalized_version_inventory(
        payload.get("versionInventoryAfter"),
        "Recorded deployed version inventory",
    )
    _assert_single_created_version(
        before=versions_before,
        after=versions_after,
        created_version=created_version,
    )
    agent_existed_before = payload.get("agentExistedBefore")
    if not isinstance(agent_existed_before, bool):
        raise FoundryHostDeploymentError("Deployment record agent ownership flag is invalid.")
    if agent_existed_before:
        traffic_before = _normalize_version_selection_rules(
            payload.get("trafficBefore"),
            "Recorded prior traffic",
        )
    else:
        if payload.get("trafficBefore") != [] or versions_before:
            raise FoundryHostDeploymentError(
                "A newly created agent cannot claim pre-existing state."
            )
        traffic_before = []
    traffic_after = _normalize_version_selection_rules(
        payload.get("trafficAfter"),
        "Recorded deployed traffic",
    )
    if any(rule["agent_version"] not in versions_before for rule in traffic_before):
        raise FoundryHostDeploymentError(
            "Recorded prior traffic references a version outside the prior inventory."
        )
    if traffic_after != [_fixed_version_rule(created_version)]:
        raise FoundryHostDeploymentError(
            "Deployment record traffic is not fixed to its created version."
        )
    lease_binding = _mapping(payload.get("mutationLease"), "Mutation lease binding")
    lease_resource_sha256 = lease_binding.get("resourceSha256")
    if (
        set(lease_binding) != {"type", "resourceSha256"}
        or lease_binding.get("type") != "azure_blob_infinite_lease"
        or not isinstance(lease_resource_sha256, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", lease_resource_sha256) is None
    ):
        raise FoundryHostDeploymentError("Deployment record mutation lease binding is invalid.")
    rollback = _mapping(payload.get("rollback"), "rollback")
    rollback_action = (
        "restore_prior_traffic_retain_created_version"
        if agent_existed_before
        else "retain_isolated_created_version"
    )
    expected_rollback = {
        "action": rollback_action,
        "createdVersion": created_version,
        "createdVersionRetention": "required",
        "agentExistedBefore": agent_existed_before,
        "versionInventoryBefore": versions_before,
        "versionInventoryAfter": versions_after,
        "trafficBefore": traffic_before,
        "trafficAfter": traffic_after,
        "mutationLeaseResourceSha256": lease_resource_sha256,
    }
    if rollback != expected_rollback:
        raise FoundryHostDeploymentError("Deployment record rollback binding is inconsistent.")
    return {
        "plan": plan,
        "operationId": operation_id,
        "createdVersion": created_version,
        "agentExistedBefore": agent_existed_before,
        "versionInventoryBefore": versions_before,
        "versionInventoryAfter": versions_after,
        "trafficBefore": traffic_before,
        "trafficAfter": traffic_after,
        "mutationLeaseResourceSha256": lease_resource_sha256,
    }


def _traffic_rules_from_agent_payload(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    endpoint = _mapping(_field(payload, "agent_endpoint", "agentEndpoint"), "agent endpoint")
    selector = _mapping(
        _field(endpoint, "version_selector", "versionSelector"),
        "agent endpoint version selector",
    )
    return _normalize_version_selection_rules(
        _field(selector, "version_selection_rules", "versionSelectionRules"),
        "Agent endpoint version selection rules",
    )


def _normalize_version_selection_rules(
    value: object,
    label: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise FoundryHostDeploymentError(f"{label} must be a non-empty array.")
    normalized: list[dict[str, Any]] = []
    total_traffic = 0
    for index, raw_rule in enumerate(value):
        rule = _mapping(raw_rule, f"{label}[{index}]")
        rule_type = _field(rule, "type", "type")
        if rule_type != "FixedRatio":
            raise FoundryHostDeploymentError(f"{label}[{index}] must use the FixedRatio rule type.")
        version = _immutable_agent_version(
            _field(rule, "agent_version", "agentVersion"),
            f"{label}[{index}] agent version",
        )
        percentage = _field(rule, "traffic_percentage", "trafficPercentage")
        if isinstance(percentage, bool) or not isinstance(percentage, int):
            raise FoundryHostDeploymentError(
                f"{label}[{index}] traffic percentage must be an integer."
            )
        if percentage < 1 or percentage > 100:
            raise FoundryHostDeploymentError(
                f"{label}[{index}] traffic percentage must be between 1 and 100."
            )
        total_traffic += percentage
        normalized.append(
            {
                "type": "FixedRatio",
                "agent_version": version,
                "traffic_percentage": percentage,
            }
        )
    if total_traffic != 100:
        raise FoundryHostDeploymentError(f"{label} traffic percentages must total 100.")
    return normalized


def _immutable_agent_version(value: object, label: str) -> str:
    if value is None or isinstance(value, bool):
        raise FoundryHostDeploymentError(f"{label} is missing.")
    version = str(value)
    if (
        not version
        or version != version.strip()
        or version.startswith("@")
        or any(character.isspace() or ord(character) < 32 for character in version)
        or any(delimiter in version for delimiter in ("/", "\\", "?", "#"))
    ):
        raise FoundryHostDeploymentError(
            f"{label} must be an exact immutable version identifier, not an alias."
        )
    return version


def _instance_principal_id(details: Mapping[str, Any]) -> str | None:
    identity = details.get("instance_identity", details.get("instanceIdentity"))
    if not isinstance(identity, Mapping):
        return None
    principal_id = identity.get("principal_id", identity.get("principalId"))
    return str(principal_id) if principal_id else None


def _status_text(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").casefold()


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        result = as_dict()
        if isinstance(result, Mapping):
            return dict(result)
    raise FoundryHostDeploymentError(
        f"Expected a mapping-compatible SDK result, received {type(value).__name__}."
    )


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise FoundryHostDeploymentError(f"{label} must be an object.")
    return dict(value)


def _field(payload: Mapping[str, Any], snake_case: str, camel_case: str) -> Any:
    return payload.get(snake_case, payload.get(camel_case))


def _read_plan(path: Path) -> FoundryHostPlan:
    return FoundryHostPlan.model_validate_json(path.read_text(encoding="utf-8-sig"))


def _content_safe_responses_report(payload: Mapping[str, Any]) -> dict[str, Any]:
    answer = payload.get("answer")
    answer_text = answer if isinstance(answer, str) else ""
    checks = payload.get("checks")
    check_data = checks if isinstance(checks, Mapping) else {}
    missing_terms = payload.get("missingTerms")
    event_types = payload.get("eventTypes")
    return {
        "schemaVersion": payload.get("schemaVersion"),
        "status": payload.get("status"),
        "transport": payload.get("transport"),
        "agentVersion": payload.get("agentVersion"),
        "checks": {
            str(name): value for name, value in check_data.items() if isinstance(value, bool)
        },
        "eventCount": payload.get("eventCount"),
        "deltaCount": payload.get("deltaCount"),
        "eventTypeCount": len(event_types) if isinstance(event_types, list) else 0,
        "firstDeltaMs": payload.get("firstDeltaMs"),
        "durationMs": payload.get("durationMs"),
        "answerHash": f"sha256:{hashlib.sha256(answer_text.encode('utf-8')).hexdigest()}",
        "answerLength": len(answer_text),
        "missingTermCount": len(missing_terms) if isinstance(missing_terms, list) else 0,
    }


def _validate_report_urls(payload: Any, *, field_name: str = "report") -> None:
    if isinstance(payload, Mapping):
        for name, value in payload.items():
            child_name = f"{field_name}.{name}"
            if isinstance(value, str) and str(name).casefold().endswith(("url", "endpoint")):
                _validate_https_url(value, field_name=child_name)
            else:
                _validate_report_urls(value, field_name=child_name)
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            _validate_report_urls(value, field_name=f"{field_name}[{index}]")


def _validate_https_url(value: str, *, field_name: str) -> None:
    if (
        not value
        or value != value.strip()
        or "\\" in value
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise FoundryHostDeploymentError(f"{field_name} is not a safe HTTPS URL.")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise FoundryHostDeploymentError(f"{field_name} is not a safe HTTPS URL.") from exc
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.netloc
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise FoundryHostDeploymentError(f"{field_name} is not a safe HTTPS URL.")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _validate_report_urls(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
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


def _credential() -> DefaultAzureCredential:
    return isolated_default_azure_credential(
        exclude_interactive_browser_credential=True,
        credential_factory=DefaultAzureCredential,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--logical-agent-id", required=True)
    plan.add_argument("--deployment-agent-id", required=True)
    plan.add_argument("--project-endpoint", required=True)
    plan.add_argument("--account-name", required=True)
    plan.add_argument("--project-name", required=True)
    plan.add_argument("--image", required=True)
    plan.add_argument("--model-deployment", required=True)
    plan.add_argument("--search-service", required=True)
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--card-output", type=Path)

    deploy = subparsers.add_parser("deploy")
    deploy.add_argument("--plan", type=Path, required=True)
    deploy.add_argument("--account-name", required=True)
    deploy.add_argument("--project-name", required=True)
    deploy.add_argument("--mutation-lease-blob-url", required=True)
    deploy.add_argument("--operation-id", required=True)
    deploy.add_argument("--output", type=Path, required=True)
    deploy.add_argument("--timeout-seconds", type=float, default=DEFAULT_DEPLOY_TIMEOUT_SECONDS)

    toolbox = subparsers.add_parser("probe-toolbox")
    toolbox.add_argument("--project-endpoint", required=True)
    toolbox.add_argument("--account-name", required=True)
    toolbox.add_argument("--project-name", required=True)
    toolbox.add_argument("--toolbox-name", required=True)
    toolbox.add_argument("--output", type=Path, required=True)

    responses = subparsers.add_parser("probe-responses")
    responses.add_argument("--plan", type=Path, required=True)
    responses.add_argument("--account-name", required=True)
    responses.add_argument("--project-name", required=True)
    responses.add_argument("--agent-version", required=True)
    responses.add_argument("--prompt", required=True)
    responses.add_argument("--required-term", action="append", default=[])
    responses.add_argument("--output", type=Path, required=True)

    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("--record", type=Path, required=True)
    rollback.add_argument("--account-name", required=True)
    rollback.add_argument("--project-name", required=True)
    rollback.add_argument("--mutation-lease-blob-url", required=True)
    rollback.add_argument("--output", type=Path, required=True)

    validate_rollback = subparsers.add_parser("validate-rollback")
    validate_rollback.add_argument("--record", type=Path, required=True)
    validate_rollback.add_argument("--account-name", required=True)
    validate_rollback.add_argument("--project-name", required=True)
    validate_rollback.add_argument("--mutation-lease-blob-url", required=True)
    validate_rollback.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "plan":
        new_plan = create_host_plan(
            logical_agent_id=args.logical_agent_id,
            deployment_agent_id=args.deployment_agent_id,
            project_endpoint=args.project_endpoint,
            account_name=args.account_name,
            project_name=args.project_name,
            image=args.image,
            model_deployment=args.model_deployment,
            search_service=args.search_service,
        )
        _write_json(args.output, new_plan.model_dump(mode="json", by_alias=True))
        if args.card_output is not None:
            _write_json(args.card_output, new_plan.agent_card)
        return 0

    if args.command == "validate-rollback":
        loaded_record = json.loads(args.record.read_text(encoding="utf-8-sig"))
        if not isinstance(loaded_record, Mapping):
            raise FoundryHostDeploymentError("Deployment record must be an object.")
        validated = _validate_host_deployment_record(
            loaded_record,
            record_path=args.record,
        )
        validated_plan = validated["plan"]
        _bound_project_endpoint(
            validated_plan,
            account_name=args.account_name,
            project_name=args.project_name,
        )
        lease_blob_url = _canonical_mutation_lease_blob_url(
            args.mutation_lease_blob_url,
            account_name=args.account_name,
            project_name=args.project_name,
            agent_id=validated_plan.deployment_agent_id,
        )
        lease_resource_sha256 = _mutation_lease_resource_sha256(lease_blob_url)
        if lease_resource_sha256 != validated["mutationLeaseResourceSha256"]:
            raise FoundryHostDeploymentError(
                "Rollback mutation lease does not match the sealed deployment record."
            )
        _write_json(
            args.output,
            {
                "status": "pass",
                "operationId": validated["operationId"],
                "deploymentAgentId": validated_plan.deployment_agent_id,
                "projectEndpoint": validated_plan.project_endpoint,
                "createdVersion": validated["createdVersion"],
                "mutationLeaseResourceSha256": lease_resource_sha256,
            },
        )
        return 0

    if args.command == "probe-toolbox":
        result = asyncio.run(
            probe_toolbox(
                project_endpoint=args.project_endpoint,
                account_name=args.account_name,
                project_name=args.project_name,
                toolbox_name=args.toolbox_name,
            )
        )
        _write_json(args.output, result)
        return 0 if result["knowledgeRetrievePresent"] else 1

    loaded_plan = _read_plan(args.plan) if hasattr(args, "plan") else None
    record: Mapping[str, Any] | None = None
    validated_record: dict[str, Any] | None = None
    if args.command == "rollback":
        loaded_record = json.loads(args.record.read_text(encoding="utf-8-sig"))
        if not isinstance(loaded_record, Mapping):
            raise FoundryHostDeploymentError("Deployment record must be an object.")
        record = loaded_record
        validated_record = _validate_host_deployment_record(
            record,
            record_path=args.record,
        )
        loaded_plan = validated_record["plan"]
    if args.command == "deploy":
        operation_id = _operation_id(args.operation_id)
        expected_output_name = f"{operation_id}.deployment.json"
        if args.output.name != expected_output_name:
            raise FoundryHostDeploymentError(
                "Deployment output must use its operation-bound record filename."
            )
    assert loaded_plan is not None
    _bound_project_endpoint(
        loaded_plan,
        account_name=args.account_name,
        project_name=args.project_name,
    )
    mutation_lease_blob_url: str | None = None
    if args.command in {"deploy", "rollback"}:
        mutation_lease_blob_url = _canonical_mutation_lease_blob_url(
            args.mutation_lease_blob_url,
            account_name=args.account_name,
            project_name=args.project_name,
            agent_id=loaded_plan.deployment_agent_id,
        )
        if (
            validated_record is not None
            and _mutation_lease_resource_sha256(mutation_lease_blob_url)
            != validated_record["mutationLeaseResourceSha256"]
        ):
            raise FoundryHostDeploymentError(
                "Rollback mutation lease does not match the sealed deployment record."
            )
    credential = _credential()
    try:
        if args.command in {"deploy", "rollback"}:
            assert mutation_lease_blob_url is not None
            with AzureBlobMutationLease(
                mutation_lease_blob_url,
                credential=credential,
                account_name=args.account_name,
                project_name=args.project_name,
                agent_id=loaded_plan.deployment_agent_id,
            ) as mutation_lease:
                if args.command == "deploy":
                    try:
                        result = deploy_host_plan(
                            loaded_plan,
                            credential=credential,
                            mutation_lease=mutation_lease,
                            account_name=args.account_name,
                            project_name=args.project_name,
                            timeout_seconds=args.timeout_seconds,
                            operation_id=operation_id,
                        )
                    except FoundryHostDeploymentError as exc:
                        if exc.evidence is not None:
                            _write_json(args.output, exc.evidence)
                        raise
                else:
                    assert record is not None
                    result = rollback_host(
                        record,
                        credential=credential,
                        mutation_lease=mutation_lease,
                        account_name=args.account_name,
                        project_name=args.project_name,
                        record_path=args.record,
                    )
                _write_json(args.output, result)
            return 0 if result.get("status") in {"active", "rolled_back", "retained"} else 1
        if args.command == "probe-responses":
            assert loaded_plan is not None
            result = _probe_live_responses(
                loaded_plan,
                credential=credential,
                account_name=args.account_name,
                project_name=args.project_name,
                agent_version=args.agent_version,
                prompt=args.prompt,
                required_terms=args.required_term,
            )
            result = _content_safe_responses_report(result)
        else:
            raise FoundryHostDeploymentError(f"Unsupported host command: {args.command}")
        _write_json(args.output, result)
        return 0 if result.get("status") in {"active", "pass"} else 1
    finally:
        credential.close()


if __name__ == "__main__":
    raise SystemExit(main())
