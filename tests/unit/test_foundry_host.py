"""Descriptor-driven Foundry container host tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.storage.blob import BlobClient

from agent_iq.deployments import foundry_host

AGENT_ID = "consumer-formulation-rd"
DEPLOYMENT_ID = "consumer-formulation-rd-host-canary"
PROJECT = "https://example.services.ai.azure.com/api/projects/test"
ACCOUNT_NAME = "example"
PROJECT_NAME = "test"
DIGEST = f"sha256:{'a' * 64}"
IMAGE = f"example.azurecr.io/agent-iq/foundry-langgraph-host@{DIGEST}"
OPERATION_ID = "12345678-1234-4234-8234-123456789abc"
LEASE_URL = (
    "https://leases.blob.core.windows.net/locks/agent-iq/foundry/"
    f"{ACCOUNT_NAME}/{PROJECT_NAME}/{DEPLOYMENT_ID}.lock"
)


class HeldLease:
    def __init__(self, *, held: bool = True) -> None:
        self.held = held
        self.assertions = 0

    @property
    def resource_sha256(self) -> str:
        return foundry_host._mutation_lease_resource_sha256(LEASE_URL)

    def assert_held(self) -> None:
        self.assertions += 1
        if not self.held:
            raise foundry_host.FoundryHostDeploymentError(
                "The exclusive Foundry mutation lease was lost."
            )


def _plan() -> foundry_host.FoundryHostPlan:
    return foundry_host.create_host_plan(
        logical_agent_id=AGENT_ID,
        deployment_agent_id=DEPLOYMENT_ID,
        project_endpoint=PROJECT,
        account_name=ACCOUNT_NAME,
        project_name=PROJECT_NAME,
        image=IMAGE,
        model_deployment="gpt-current",
        search_service="example-search",
    )


def _deployment_record(
    *,
    plan: foundry_host.FoundryHostPlan | None = None,
    existed_before: bool = True,
    operation_id: str = OPERATION_ID,
) -> dict[str, Any]:
    target_plan = plan or _plan()
    versions_before = ["5", "6"] if existed_before else []
    versions_after = [*versions_before, "7"]
    traffic_before = (
        [
            {
                "type": "FixedRatio",
                "agent_version": "5",
                "traffic_percentage": 25,
            },
            {
                "type": "FixedRatio",
                "agent_version": "6",
                "traffic_percentage": 75,
            },
        ]
        if existed_before
        else []
    )
    traffic_after = [
        {
            "type": "FixedRatio",
            "agent_version": "7",
            "traffic_percentage": 100,
        }
    ]
    lease_sha256 = foundry_host._mutation_lease_resource_sha256(LEASE_URL)
    rollback_action = (
        "restore_prior_traffic_retain_created_version"
        if existed_before
        else "retain_isolated_created_version"
    )
    payload = {
        "schemaVersion": "3.0",
        "evidenceType": "agent-iq.descriptor-host-deployment",
        "operationId": operation_id,
        "status": "active",
        "logicalAgentId": target_plan.logical_agent_id,
        "deploymentAgentId": target_plan.deployment_agent_id,
        "version": "7",
        "projectEndpoint": target_plan.project_endpoint,
        "agentExistedBefore": existed_before,
        "versionInventoryBefore": versions_before,
        "versionInventoryAfter": versions_after,
        "trafficBefore": traffic_before,
        "trafficAfter": traffic_after,
        "createdVersionRetention": "required",
        "mutationLease": {
            "type": "azure_blob_infinite_lease",
            "resourceSha256": lease_sha256,
        },
        "rollback": {
            "action": rollback_action,
            "createdVersion": "7",
            "createdVersionRetention": "required",
            "agentExistedBefore": existed_before,
            "versionInventoryBefore": versions_before,
            "versionInventoryAfter": versions_after,
            "trafficBefore": traffic_before,
            "trafficAfter": traffic_after,
            "mutationLeaseResourceSha256": lease_sha256,
        },
        "plan": target_plan.model_dump(mode="json", by_alias=True),
    }
    return foundry_host._seal_host_deployment_record(payload)


def test_plan_uses_one_immutable_image_and_canonical_descriptor() -> None:
    plan = _plan()

    assert plan.image == IMAGE
    assert plan.image_digest == DIGEST
    assert plan.logical_agent_id == AGENT_ID
    assert plan.deployment_agent_id == DEPLOYMENT_ID
    assert plan.project_endpoint == PROJECT
    assert plan.runtime_descriptor.runtime_family == "langgraph"
    assert plan.environment_variables["AIQ_HOST_LOGICAL_AGENT_ID"] == AGENT_ID
    assert (
        plan.environment_variables["AIQ_HOST_DESCRIPTOR_SHA256"]
        == plan.runtime_descriptor.descriptor_sha256
    )
    assert plan.environment_variables["AIQ_HOST_TOOLBOX_NAME"] == f"{AGENT_ID}-knowledge"
    assert "AGENT_IQ_ENVIRONMENT" not in plan.environment_variables
    assert "FOUNDRY_PROJECT_ENDPOINT" not in plan.environment_variables
    assert plan.environment_variables["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] == (
        "false"
    )
    assert not any(
        marker in name
        for name in plan.environment_variables
        for marker in ("API_KEY", "PASSWORD", "SECRET", "ACCESS_TOKEN", "CONNECTION_STRING")
    )


def test_plan_rejects_mutable_or_in_place_canary_images() -> None:
    with pytest.raises(foundry_host.FoundryHostDeploymentError, match="immutable digest"):
        foundry_host.create_host_plan(
            logical_agent_id=AGENT_ID,
            deployment_agent_id=DEPLOYMENT_ID,
            project_endpoint=PROJECT,
            account_name=ACCOUNT_NAME,
            project_name=PROJECT_NAME,
            image="example.azurecr.io/host:latest",
            model_deployment="gpt-current",
            search_service="example-search",
        )
    with pytest.raises(foundry_host.FoundryHostDeploymentError, match="cannot replace"):
        foundry_host.create_host_plan(
            logical_agent_id=AGENT_ID,
            deployment_agent_id=AGENT_ID,
            project_endpoint=PROJECT,
            account_name=ACCOUNT_NAME,
            project_name=PROJECT_NAME,
            image=IMAGE,
            model_deployment="gpt-current",
            search_service="example-search",
        )


@pytest.mark.parametrize(
    "blob_url",
    [
        LEASE_URL.replace("https://", "http://"),
        LEASE_URL.replace("leases.blob", "LEASES.blob"),
        LEASE_URL.replace(DEPLOYMENT_ID, "consumer-other"),
        f"{LEASE_URL}?sig=forbidden",
        LEASE_URL.replace("https://", "https://user@"),
    ],
)
def test_mutation_lease_url_rejects_unbound_or_noncanonical_values(blob_url: str) -> None:
    with pytest.raises(foundry_host.FoundryHostDeploymentError, match="Mutation lease"):
        foundry_host._canonical_mutation_lease_blob_url(
            blob_url,
            account_name=ACCOUNT_NAME,
            project_name=PROJECT_NAME,
            agent_id=DEPLOYMENT_ID,
        )


def test_blob_mutation_lease_acquires_renews_releases_and_isolates_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Lease:
        def renew(self) -> None:
            calls.append("renew")

        def release(self) -> None:
            calls.append("release")

    class Blob:
        def acquire_lease(self, *, lease_duration: int, lease_id: str) -> Lease:
            assert lease_duration == -1
            assert str(uuid.UUID(lease_id)) == lease_id
            calls.append("acquire")
            return Lease()

        def close(self) -> None:
            calls.append("close")

    def from_blob_url(blob_url: str, **kwargs: Any) -> Blob:
        assert blob_url == LEASE_URL
        assert kwargs["transport"]._use_env_settings is False
        assert kwargs["redirect_policy"].allow is False
        calls.append("client")
        return Blob()

    monkeypatch.setattr(BlobClient, "from_blob_url", staticmethod(from_blob_url))
    lease = foundry_host.AzureBlobMutationLease(
        LEASE_URL,
        credential=cast(Any, SimpleNamespace()),
        account_name=ACCOUNT_NAME,
        project_name=PROJECT_NAME,
        agent_id=DEPLOYMENT_ID,
    )

    with lease:
        lease.assert_held()

    assert lease.resource_sha256 == foundry_host._mutation_lease_resource_sha256(LEASE_URL)
    assert calls == ["client", "acquire", "renew", "release", "close"]


def test_blob_mutation_lease_acquisition_failure_closes_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed = False

    class Blob:
        def acquire_lease(self, **kwargs: Any) -> None:
            del kwargs
            raise AzureError("already leased")

        def close(self) -> None:
            nonlocal closed
            closed = True

    monkeypatch.setattr(
        BlobClient,
        "from_blob_url",
        staticmethod(lambda *args, **kwargs: Blob()),
    )
    lease = foundry_host.AzureBlobMutationLease(
        LEASE_URL,
        credential=cast(Any, SimpleNamespace()),
        account_name=ACCOUNT_NAME,
        project_name=PROJECT_NAME,
        agent_id=DEPLOYMENT_ID,
    )

    with pytest.raises(foundry_host.FoundryHostDeploymentError, match="could not be acquired"):
        lease.__enter__()
    assert closed is True


def test_plan_validation_rejects_endpoint_digest_descriptor_and_secret_drift() -> None:
    plan = _plan()
    payload = plan.model_dump(mode="python")

    invalid_endpoint = {**payload, "projectEndpoint": "http://example.test"}
    with pytest.raises(ValueError, match="absolute HTTPS"):
        foundry_host.FoundryHostPlan.model_validate(invalid_endpoint)
    spoofed_endpoint = {
        **payload,
        "projectEndpoint": "https://example.services.ai.azure.com.attacker.test/api/projects/test",
    }
    with pytest.raises(ValueError, match="host must end"):
        foundry_host.FoundryHostPlan.model_validate(spoofed_endpoint)

    invalid_digest = {**payload, "imageDigest": f"sha256:{'b' * 64}"}
    with pytest.raises(ValueError, match="pinned to its declared"):
        foundry_host.FoundryHostPlan.model_validate(invalid_digest)

    invalid_descriptor = {
        **payload,
        "runtimeDescriptor": plan.runtime_descriptor.model_copy(
            update={"logical_agent_id": "consumer-other"}
        ),
    }
    with pytest.raises(ValueError, match="descriptor does not match"):
        foundry_host.FoundryHostPlan.model_validate(invalid_descriptor)

    forbidden_name = "EXAMPLE_" + "API" + "_KEY"
    invalid_environment = {
        **payload,
        "environmentVariables": {
            **plan.environment_variables,
            forbidden_name: "placeholder",
        },
    }
    with pytest.raises(ValueError, match="secret-shaped"):
        foundry_host.FoundryHostPlan.model_validate(invalid_environment)

    reserved_environment = {
        **payload,
        "environmentVariables": {
            **plan.environment_variables,
            "AGENT_CUSTOM_SETTING": "forbidden",
        },
    }
    with pytest.raises(ValueError, match="platform-reserved"):
        foundry_host.FoundryHostPlan.model_validate(reserved_environment)


def test_sdk_definition_keeps_responses_and_a2a_version_namespaces_separate() -> None:
    plan = _plan()
    definition = foundry_host.hosted_agent_definition(plan)
    patch = foundry_host.incoming_a2a_patch(plan, agent_version="7")

    assert definition.container_configuration is not None
    assert definition.container_configuration.image == IMAGE
    assert definition.protocol_versions is not None
    assert definition.protocol_versions[0].protocol == "responses"
    assert definition.protocol_versions[0].version == "2.0.0"
    assert patch["agent_endpoint"]["protocols"] == ["responses", "a2a"]
    assert patch["agent_endpoint"]["authorization_schemes"] == [
        {
            "type": "Entra",
            "isolation_key_source": {"kind": "Entra"},
        }
    ]
    assert patch["agent_endpoint"]["version_selector"]["version_selection_rules"][0] == {
        "type": "FixedRatio",
        "agent_version": "7",
        "traffic_percentage": 100,
    }
    assert patch["agent_card"] == plan.agent_card
    with pytest.raises(foundry_host.FoundryHostDeploymentError, match="not an alias"):
        foundry_host.incoming_a2a_patch(plan, agent_version="@latest")


def test_deployment_records_identity_digest_and_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()

    class Version:
        status = "active"
        version = "7"

        def as_dict(self) -> dict[str, Any]:
            return {
                "version": self.version,
                "status": "active",
                "instance_identity": {"principal_id": "principal"},
                "metadata": {
                    "agentIqOperationId": OPERATION_ID,
                    "agentIqDeploymentMode": "container",
                    "agentIqLogicalAgentId": AGENT_ID,
                    "agentIqRuntimeFamily": plan.runtime_descriptor.runtime_family,
                    "agentIqDescriptorSha256": plan.runtime_descriptor.descriptor_sha256,
                    "agentIqImageDigest": DIGEST,
                },
            }

    class Agents:
        def __init__(self) -> None:
            self.versions = ["5", "6"]
            self.routed = False

        def get(self, agent_id: str) -> dict[str, Any]:
            assert agent_id == DEPLOYMENT_ID
            return {
                "agent_endpoint": {
                    "version_selector": {
                        "version_selection_rules": (
                            [
                                {
                                    "type": "FixedRatio",
                                    "agent_version": "7",
                                    "traffic_percentage": 100,
                                }
                            ]
                            if self.routed
                            else [
                                {
                                    "type": "FixedRatio",
                                    "agent_version": "5",
                                    "traffic_percentage": 25,
                                },
                                {
                                    "type": "FixedRatio",
                                    "agent_version": "6",
                                    "traffic_percentage": 75,
                                },
                            ]
                        )
                    }
                }
            }

        def create_version(self, agent_id: str, **kwargs: Any) -> Any:
            assert agent_id == DEPLOYMENT_ID
            assert kwargs["definition"].container_configuration.image == IMAGE
            assert kwargs["metadata"]["agentIqLogicalAgentId"] == AGENT_ID
            assert kwargs["metadata"]["agentIqOperationId"] == OPERATION_ID
            self.versions.append("7")
            return SimpleNamespace(version="7")

        def get_version(self, agent_id: str, version: str) -> Version:
            assert (agent_id, version) == (DEPLOYMENT_ID, "7")
            return Version()

        def list_versions(
            self,
            agent_id: str,
            *,
            limit: int,
            include_drafts: bool,
        ) -> list[Any]:
            assert (agent_id, limit, include_drafts) == (DEPLOYMENT_ID, 100, True)
            return [SimpleNamespace(version=version) for version in self.versions]

    clients: list[Any] = []

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["endpoint"] == PROJECT
            assert kwargs["allow_preview"] is True
            assert kwargs["transport"]._use_env_settings is False
            assert kwargs["redirect_policy"].allow is False
            self.agents = Agents()
            self.closed = False
            clients.append(self)

        def close(self) -> None:
            self.closed = True

    patched: list[str] = []

    def patch_endpoint(
        plan: Any,
        credential: Any,
        mutation_lease: Any,
        agent_version: str,
        expected_current_rules: object,
        account_name: str,
        project_name: str,
    ) -> None:
        del plan, credential, mutation_lease, expected_current_rules, account_name, project_name
        patched.append(agent_version)
        clients[-1].agents.routed = True

    monkeypatch.setattr(foundry_host, "AIProjectClient", Client)
    monkeypatch.setattr(foundry_host, "_patch_agent_endpoint", patch_endpoint)
    lease = HeldLease()

    result = foundry_host.deploy_host_plan(
        plan,
        credential=SimpleNamespace(),
        mutation_lease=lease,
        account_name=ACCOUNT_NAME,
        project_name=PROJECT_NAME,
        timeout_seconds=1,
        operation_id=OPERATION_ID,
    )

    assert result["status"] == "active"
    assert result["version"] == "7"
    assert result["instancePrincipalId"] == "principal"
    assert result["logicalAgentId"] == AGENT_ID
    assert result["imageDigest"] == DIGEST
    assert result["operationId"] == OPERATION_ID
    assert result["versionInventoryBefore"] == ["5", "6"]
    assert result["versionInventoryAfter"] == ["5", "6", "7"]
    assert result["rollback"] == {
        "action": "restore_prior_traffic_retain_created_version",
        "createdVersion": "7",
        "createdVersionRetention": "required",
        "agentExistedBefore": True,
        "versionInventoryBefore": ["5", "6"],
        "versionInventoryAfter": ["5", "6", "7"],
        "trafficBefore": [
            {
                "type": "FixedRatio",
                "agent_version": "5",
                "traffic_percentage": 25,
            },
            {
                "type": "FixedRatio",
                "agent_version": "6",
                "traffic_percentage": 75,
            },
        ],
        "trafficAfter": [
            {
                "type": "FixedRatio",
                "agent_version": "7",
                "traffic_percentage": 100,
            }
        ],
        "mutationLeaseResourceSha256": lease.resource_sha256,
    }
    assert result["integrity"]["algorithm"] == "sha256"
    foundry_host._validate_host_deployment_record(result)
    assert patched == ["7"]
    assert lease.assertions > 0


def test_deployment_route_failure_retains_created_version_without_compensation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted: list[tuple[str, str, bool]] = []
    versions: list[str] = []

    class Version:
        status = "active"
        version = "7"

        def as_dict(self) -> dict[str, Any]:
            return {
                "status": self.status,
                "version": self.version,
                "metadata": {
                    "agentIqOperationId": OPERATION_ID,
                    "agentIqDeploymentMode": "container",
                    "agentIqLogicalAgentId": AGENT_ID,
                    "agentIqRuntimeFamily": _plan().runtime_descriptor.runtime_family,
                    "agentIqDescriptorSha256": _plan().runtime_descriptor.descriptor_sha256,
                    "agentIqImageDigest": DIGEST,
                },
            }

    class Agents:
        def get(self, agent_id: str) -> dict[str, Any]:
            if not versions:
                raise ResourceNotFoundError(f"{agent_id} missing")
            return {
                "agent_endpoint": {
                    "version_selector": {
                        "version_selection_rules": [
                            {
                                "type": "FixedRatio",
                                "agent_version": "7",
                                "traffic_percentage": 100,
                            }
                        ]
                    }
                }
            }

        def create_version(self, agent_id: str, **kwargs: Any) -> Any:
            del agent_id, kwargs
            versions.append("7")
            return SimpleNamespace(version="7")

        def get_version(self, agent_id: str, version: str) -> Version:
            assert (agent_id, version) == (DEPLOYMENT_ID, "7")
            return Version()

        def list_versions(
            self,
            agent_id: str,
            *,
            limit: int,
            include_drafts: bool,
        ) -> list[Any]:
            assert (agent_id, limit, include_drafts) == (DEPLOYMENT_ID, 100, True)
            return [SimpleNamespace(version=version) for version in versions]

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs
            self.agents = Agents()

        def close(self) -> None:
            return None

    monkeypatch.setattr(foundry_host, "AIProjectClient", Client)
    monkeypatch.setattr(
        foundry_host,
        "_patch_agent_endpoint",
        lambda plan, **kwargs: (_ for _ in ()).throw(
            foundry_host.FoundryHostDeploymentError("traffic verification failed")
        ),
    )

    with pytest.raises(
        foundry_host.FoundryHostDeploymentError, match="immutable version was retained"
    ) as caught:
        foundry_host.deploy_host_plan(
            _plan(),
            credential=SimpleNamespace(),
            mutation_lease=HeldLease(),
            account_name=ACCOUNT_NAME,
            project_name=PROJECT_NAME,
            timeout_seconds=1,
            operation_id=OPERATION_ID,
        )

    assert deleted == []
    assert versions == ["7"]
    evidence = caught.value.evidence
    assert evidence is not None
    assert evidence["status"] == "failed"
    assert evidence["createdVersion"] == "7"
    assert evidence["cloudState"] == "uncertain"
    assert evidence["compensatingMutationAttempted"] is False


def test_deployment_lease_loss_precedes_live_state_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads = 0

    class Agents:
        def get(self, agent_id: str) -> None:
            nonlocal reads
            del agent_id
            reads += 1

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs
            self.agents = Agents()

        def close(self) -> None:
            return None

    monkeypatch.setattr(foundry_host, "AIProjectClient", Client)
    with pytest.raises(foundry_host.FoundryHostDeploymentError, match="lease was lost"):
        foundry_host.deploy_host_plan(
            _plan(),
            credential=SimpleNamespace(),
            mutation_lease=HeldLease(held=False),
            account_name=ACCOUNT_NAME,
            project_name=PROJECT_NAME,
            operation_id=OPERATION_ID,
        )
    assert reads == 0


def test_rollback_restores_traffic_and_retains_created_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _deployment_record()
    versions = ["5", "6", "7"]
    restored: list[list[dict[str, Any]]] = []
    traffic = list(record["trafficAfter"])

    class Agents:
        def get(self, agent_id: str) -> dict[str, Any]:
            assert agent_id == DEPLOYMENT_ID
            return {
                "agent_endpoint": {
                    "version_selector": {
                        "version_selection_rules": traffic,
                    }
                }
            }

        def list_versions(
            self,
            agent_id: str,
            *,
            limit: int,
            include_drafts: bool,
        ) -> list[Any]:
            assert (agent_id, limit, include_drafts) == (DEPLOYMENT_ID, 100, True)
            return [SimpleNamespace(version=version) for version in versions]

        def get_version(self, agent_id: str, version: str) -> dict[str, Any]:
            assert (agent_id, version) == (DEPLOYMENT_ID, "7")
            return {
                "version": "7",
                "metadata": {
                    "agentIqOperationId": OPERATION_ID,
                    "agentIqDeploymentMode": "container",
                    "agentIqLogicalAgentId": AGENT_ID,
                    "agentIqRuntimeFamily": _plan().runtime_descriptor.runtime_family,
                    "agentIqDescriptorSha256": _plan().runtime_descriptor.descriptor_sha256,
                    "agentIqImageDigest": DIGEST,
                },
            }

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["endpoint"] == PROJECT
            self.agents = Agents()

        def close(self) -> None:
            return None

    monkeypatch.setattr(foundry_host, "AIProjectClient", Client)

    def restore_traffic(plan: Any, **kwargs: Any) -> None:
        del plan
        restored.append(list(kwargs["version_selection_rules"]))
        traffic[:] = kwargs["version_selection_rules"]

    monkeypatch.setattr(foundry_host, "_restore_agent_traffic", restore_traffic)

    result = foundry_host.rollback_host(
        record,
        credential=SimpleNamespace(),
        mutation_lease=HeldLease(),
        account_name=ACCOUNT_NAME,
        project_name=PROJECT_NAME,
    )

    assert result["action"] == "restore_prior_traffic_retain_created_version"
    assert result["operationId"] == OPERATION_ID
    assert versions == ["5", "6", "7"]
    assert restored == [record["trafficBefore"]]


def test_rollback_retains_created_version_for_new_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _deployment_record(existed_before=False)
    versions = ["7"]

    class Agents:
        def get(self, agent_id: str) -> dict[str, Any]:
            return {
                "agent_endpoint": {
                    "version_selector": {
                        "version_selection_rules": record["trafficAfter"],
                    }
                }
            }

        def list_versions(
            self,
            agent_id: str,
            *,
            limit: int,
            include_drafts: bool,
        ) -> list[Any]:
            assert (agent_id, limit, include_drafts) == (DEPLOYMENT_ID, 100, True)
            return [SimpleNamespace(version=version) for version in versions]

        def get_version(self, agent_id: str, version: str) -> dict[str, Any]:
            assert (agent_id, version) == (DEPLOYMENT_ID, "7")
            return {
                "version": "7",
                "metadata": {
                    "agentIqOperationId": OPERATION_ID,
                    "agentIqDeploymentMode": "container",
                    "agentIqLogicalAgentId": AGENT_ID,
                    "agentIqRuntimeFamily": _plan().runtime_descriptor.runtime_family,
                    "agentIqDescriptorSha256": _plan().runtime_descriptor.descriptor_sha256,
                    "agentIqImageDigest": DIGEST,
                },
            }

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs
            self.agents = Agents()

        def close(self) -> None:
            return None

    monkeypatch.setattr(foundry_host, "AIProjectClient", Client)
    result = foundry_host.rollback_host(
        record,
        credential=SimpleNamespace(),
        mutation_lease=HeldLease(),
        account_name=ACCOUNT_NAME,
        project_name=PROJECT_NAME,
    )

    assert result["status"] == "retained"
    assert result["action"] == "retain_isolated_created_version"
    assert result["createdVersionRetained"] is True
    assert versions == ["7"]


def test_rollback_record_rejects_tamper_and_mutable_filename_before_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    record = _deployment_record()
    client_calls = 0

    def forbidden_client(**kwargs: Any) -> object:
        nonlocal client_calls
        del kwargs
        client_calls += 1
        raise AssertionError("Invalid rollback record reached the Foundry client.")

    monkeypatch.setattr(foundry_host, "AIProjectClient", forbidden_client)
    tampered = {**record, "version": "8"}
    with pytest.raises(foundry_host.FoundryHostDeploymentError, match="integrity"):
        foundry_host.rollback_host(
            tampered,
            credential=SimpleNamespace(),
            mutation_lease=HeldLease(),
            account_name=ACCOUNT_NAME,
            project_name=PROJECT_NAME,
        )
    with pytest.raises(foundry_host.FoundryHostDeploymentError, match="operation-bound"):
        foundry_host.rollback_host(
            record,
            credential=SimpleNamespace(),
            mutation_lease=HeldLease(),
            account_name=ACCOUNT_NAME,
            project_name=PROJECT_NAME,
            record_path=tmp_path / "latest.json",
        )
    assert client_calls == 0


def test_rollback_rejects_wrong_lease_binding_before_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_calls = 0

    class WrongLease(HeldLease):
        @property
        def resource_sha256(self) -> str:
            return f"sha256:{'b' * 64}"

    def forbidden_client(**kwargs: Any) -> object:
        nonlocal client_calls
        del kwargs
        client_calls += 1
        raise AssertionError("Wrong lease binding reached the Foundry client.")

    monkeypatch.setattr(foundry_host, "AIProjectClient", forbidden_client)
    with pytest.raises(foundry_host.FoundryHostDeploymentError, match="does not match"):
        foundry_host.rollback_host(
            _deployment_record(),
            credential=SimpleNamespace(),
            mutation_lease=WrongLease(),
            account_name=ACCOUNT_NAME,
            project_name=PROJECT_NAME,
        )
    assert client_calls == 0


def test_rollback_fails_when_agent_disappears_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mutations: list[str] = []

    class Agents:
        def get(self, agent_id: str) -> None:
            raise ResourceNotFoundError(f"{agent_id} missing")

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs
            self.agents = Agents()

        def close(self) -> None:
            return None

    monkeypatch.setattr(foundry_host, "AIProjectClient", Client)
    monkeypatch.setattr(
        foundry_host,
        "_restore_agent_traffic",
        lambda *args, **kwargs: mutations.append("restore"),
    )
    with pytest.raises(foundry_host.FoundryHostDeploymentError, match="no longer exists"):
        foundry_host.rollback_host(
            _deployment_record(),
            credential=SimpleNamespace(),
            mutation_lease=HeldLease(),
            account_name=ACCOUNT_NAME,
            project_name=PROJECT_NAME,
        )
    assert mutations == []


@pytest.mark.parametrize("drift", ["traffic", "inventory", "ownership"])
def test_rollback_refuses_live_drift_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    record = _deployment_record()
    mutations: list[str] = []

    class Agents:
        def get(self, agent_id: str) -> dict[str, Any]:
            assert agent_id == DEPLOYMENT_ID
            rules = (
                [
                    {
                        "type": "FixedRatio",
                        "agent_version": "8",
                        "traffic_percentage": 100,
                    }
                ]
                if drift == "traffic"
                else record["trafficAfter"]
            )
            return {"agent_endpoint": {"version_selector": {"version_selection_rules": rules}}}

        def list_versions(
            self,
            agent_id: str,
            *,
            limit: int,
            include_drafts: bool,
        ) -> list[Any]:
            assert (agent_id, limit, include_drafts) == (DEPLOYMENT_ID, 100, True)
            versions = ["5", "6", "7", "8"] if drift == "inventory" else ["5", "6", "7"]
            return [SimpleNamespace(version=version) for version in versions]

        def get_version(self, agent_id: str, version: str) -> dict[str, Any]:
            assert (agent_id, version) == (DEPLOYMENT_ID, "7")
            return {
                "version": "7",
                "metadata": {
                    "agentIqOperationId": "87654321-4321-4321-8321-cba987654321"
                    if drift == "ownership"
                    else OPERATION_ID,
                    "agentIqDeploymentMode": "container",
                    "agentIqLogicalAgentId": AGENT_ID,
                    "agentIqRuntimeFamily": _plan().runtime_descriptor.runtime_family,
                    "agentIqDescriptorSha256": _plan().runtime_descriptor.descriptor_sha256,
                    "agentIqImageDigest": DIGEST,
                },
            }

        def delete(self, agent_id: str, *, force: bool) -> None:
            del agent_id, force
            mutations.append("delete")

        def delete_version(self, agent_id: str, version: str, *, force: bool) -> None:
            del agent_id, version, force
            mutations.append("delete_version")

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs
            self.agents = Agents()

        def close(self) -> None:
            return None

    monkeypatch.setattr(foundry_host, "AIProjectClient", Client)
    monkeypatch.setattr(
        foundry_host,
        "_restore_agent_traffic",
        lambda **kwargs: mutations.append("restore"),
    )
    with pytest.raises(foundry_host.FoundryHostDeploymentError, match=drift):
        foundry_host.rollback_host(
            record,
            credential=SimpleNamespace(),
            mutation_lease=HeldLease(),
            account_name=ACCOUNT_NAME,
            project_name=PROJECT_NAME,
        )
    assert mutations == []


def test_toolbox_probe_reports_required_tool_presence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_iq.connectors import foundry_toolbox

    monkeypatch.setenv("AGENT_IQ_AZURE_AI_ACCOUNT", "example")
    monkeypatch.setenv("AGENT_IQ_AZURE_AI_PROJECT", "test")
    tool_names = ["knowledge_base___knowledge_base_retrieve", "web_search"]

    async def load_tools(settings: Any) -> list[Any]:
        assert str(settings.foundry_project_endpoint) == PROJECT
        assert settings.toolbox_name == f"{AGENT_ID}-knowledge"
        assert str(settings.toolbox_endpoint).endswith(
            f"/toolboxes/{AGENT_ID}-knowledge/mcp?api-version=v1"
        )
        return [SimpleNamespace(name=name) for name in tool_names]

    monkeypatch.setattr(foundry_toolbox, "load_toolbox_tools", load_tools)
    passing = asyncio.run(
        foundry_host.probe_toolbox(
            project_endpoint=PROJECT,
            account_name=ACCOUNT_NAME,
            project_name=PROJECT_NAME,
            toolbox_name=f"{AGENT_ID}-knowledge",
        )
    )
    tool_names[:] = ["web_search"]
    failing = asyncio.run(
        foundry_host.probe_toolbox(
            project_endpoint=PROJECT,
            account_name=ACCOUNT_NAME,
            project_name=PROJECT_NAME,
            toolbox_name=f"{AGENT_ID}-knowledge",
        )
    )

    assert passing["status"] == "pass"
    assert passing["knowledgeRetrievePresent"] is True
    assert passing["tools"] == ["knowledge_base___knowledge_base_retrieve", "web_search"]
    assert failing["status"] == "fail"
    assert failing["knowledgeRetrievePresent"] is False


def test_responses_probe_requires_real_incremental_events() -> None:
    events = [
        SimpleNamespace(type="response.created"),
        SimpleNamespace(type="response.output_text.delta", delta="protease and "),
        SimpleNamespace(type="response.output_text.delta", delta="amylase"),
        SimpleNamespace(type="response.completed"),
    ]

    class Stream:
        def __enter__(self) -> Stream:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def __iter__(self) -> Any:
            return iter(events)

        def get_final_response(self) -> Any:
            return SimpleNamespace(output_text="protease and amylase")

    request: dict[str, Any] = {}

    def stream(**kwargs: Any) -> Stream:
        request.update(kwargs)
        return Stream()

    responses = SimpleNamespace(stream=stream)
    client = SimpleNamespace(responses=responses)

    result = foundry_host.probe_responses_stream(
        client,
        model="gpt-current",
        prompt="Which enzymes?",
        required_terms=("protease", "amylase"),
    )

    assert request == {"model": "gpt-current", "input": "Which enzymes?"}
    assert result["status"] == "pass"
    assert result["transport"] == "foundry-responses-sse"
    assert result["deltaCount"] == 2
    assert result["checks"]["incrementalEventsPresent"] is True


def test_live_responses_probe_uses_final_text_fallback_and_closes_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = [
        SimpleNamespace(type="response.created"),
        SimpleNamespace(type="response.completed"),
    ]

    class Stream:
        def __enter__(self) -> Stream:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def __iter__(self) -> Any:
            return iter(events)

        def get_final_response(self) -> Any:
            return SimpleNamespace(output_text="Fallback grounded answer")

    class OpenAIClient:
        def __init__(self) -> None:
            self.responses = SimpleNamespace(stream=lambda **kwargs: Stream())
            self.closed = False

        def close(self) -> None:
            self.closed = True

    openai_client = OpenAIClient()

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["endpoint"] == PROJECT
            assert kwargs["allow_preview"] is True
            self.closed = False

        def get_openai_client(
            self,
            *,
            agent_name: str,
            http_client: Any,
        ) -> OpenAIClient:
            assert agent_name == DEPLOYMENT_ID
            assert http_client.trust_env is False
            assert http_client.follow_redirects is False
            return openai_client

        def close(self) -> None:
            self.closed = True

    clients: list[Client] = []

    def client_factory(**kwargs: Any) -> Client:
        client = Client(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(foundry_host, "AIProjectClient", client_factory)
    verified_versions: list[str] = []
    monkeypatch.setattr(
        foundry_host,
        "_assert_current_traffic",
        lambda plan, credential, account_name, project_name, expected_rules: (
            verified_versions.append(expected_rules[0]["agent_version"])
        ),
    )
    result = foundry_host._probe_live_responses(
        _plan(),
        credential=SimpleNamespace(),
        account_name=ACCOUNT_NAME,
        project_name=PROJECT_NAME,
        agent_version="7",
        prompt="Give a grounded answer.",
        required_terms=("grounded",),
    )

    assert result["status"] == "fail"
    assert result["agentVersion"] == "7"
    assert verified_versions == ["7"]
    assert result["answer"] == "Fallback grounded answer"
    assert result["checks"]["completedEventPresent"] is True
    assert result["checks"]["incrementalEventsPresent"] is False
    assert result["missingTerms"] == []
    assert openai_client.closed
    assert clients[0].closed


def test_active_wait_classifies_failure_timeout_and_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Agents:
        def __init__(self, statuses: list[str], *, error: Any = None) -> None:
            self.statuses = statuses
            self.error = error

        def get_version(self, agent_id: str, version: str) -> Any:
            assert (agent_id, version) == (DEPLOYMENT_ID, "7")
            return SimpleNamespace(
                status=self.statuses.pop(0),
                error=self.error,
                version=version,
            )

    failed: Any = SimpleNamespace(
        agents=Agents(
            ["failed"],
            error=SimpleNamespace(code="ImageError", message="Registry access denied"),
        )
    )
    with pytest.raises(
        foundry_host.FoundryHostDeploymentError,
        match="ImageError: Registry access denied",
    ):
        foundry_host._wait_for_active_version(
            failed,
            DEPLOYMENT_ID,
            "7",
            timeout_seconds=1,
            mutation_lease=HeldLease(),
        )

    timed_out: Any = SimpleNamespace(agents=Agents(["provisioning"]))
    with pytest.raises(foundry_host.FoundryHostDeploymentError, match="did not become active"):
        foundry_host._wait_for_active_version(
            timed_out,
            DEPLOYMENT_ID,
            "7",
            timeout_seconds=0,
            mutation_lease=HeldLease(),
        )

    sleeps: list[float] = []
    monkeypatch.setattr("agent_iq.deployments.foundry_host.time.sleep", sleeps.append)
    pending: Any = SimpleNamespace(agents=Agents(["provisioning", "active"]))
    active = foundry_host._wait_for_active_version(
        pending,
        DEPLOYMENT_ID,
        "7",
        timeout_seconds=60,
        mutation_lease=HeldLease(),
    )
    assert active.status == "active"
    assert sleeps == [10.0]

    mismatched: Any = SimpleNamespace(
        agents=SimpleNamespace(
            get_version=lambda agent_id, version: SimpleNamespace(
                status="active",
                version="8",
            )
        )
    )
    with pytest.raises(foundry_host.FoundryHostDeploymentError, match="while validating"):
        foundry_host._wait_for_active_version(
            mismatched,
            DEPLOYMENT_ID,
            "7",
            timeout_seconds=1,
            mutation_lease=HeldLease(),
        )


def test_endpoint_patch_uses_entra_token_and_expected_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Credential:
        def get_token(self, scope: str) -> Any:
            assert scope == foundry_host.FOUNDRY_TOKEN_SCOPE
            return SimpleNamespace(token="test-token")

    class Response:
        def __init__(self, payload: dict[str, Any] | None = None) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            captured["raised"] = True

        def json(self) -> dict[str, Any]:
            assert self.payload is not None
            return self.payload

    def patch(url: str, **kwargs: Any) -> Response:
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    def get(url: str, **kwargs: Any) -> Response:
        captured["verificationUrl"] = url
        captured["verificationHeaders"] = kwargs["headers"]
        return Response(
            {
                "agent_endpoint": {
                    "version_selector": {
                        "version_selection_rules": [
                            {
                                "type": "FixedRatio",
                                "agent_version": "7",
                                "traffic_percentage": 100,
                            }
                        ]
                    }
                }
            }
        )

    class Http:
        def __enter__(self) -> Http:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def patch(self, url: str, **kwargs: Any) -> Response:
            return patch(url, **kwargs)

        def get(self, url: str, **kwargs: Any) -> Response:
            return get(url, **kwargs)

    monkeypatch.setattr(foundry_host, "_isolated_http_client", Http)
    credential: Any = Credential()
    foundry_host._patch_agent_endpoint(
        _plan(),
        credential=credential,
        mutation_lease=HeldLease(),
        agent_version="7",
        expected_current_rules=[foundry_host._fixed_version_rule("7")],
        account_name=ACCOUNT_NAME,
        project_name=PROJECT_NAME,
    )

    assert captured["url"] == f"{PROJECT}/agents/{DEPLOYMENT_ID}?api-version=v1"
    assert captured["verificationUrl"] == captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer test-token"
    assert captured["verificationHeaders"]["Authorization"] == "Bearer test-token"
    assert (
        captured["json"]["agent_endpoint"]["version_selector"]["version_selection_rules"][0][
            "agent_version"
        ]
        == "7"
    )
    assert captured["raised"] is True


def test_endpoint_patch_revalidates_tampered_plan_before_token_or_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_calls = 0
    http_calls = 0

    class Credential:
        def get_token(self, scope: str) -> Any:
            nonlocal token_calls
            del scope
            token_calls += 1
            return SimpleNamespace(token="must-not-be-used")

    def patch(*args: Any, **kwargs: Any) -> None:
        nonlocal http_calls
        del args, kwargs
        http_calls += 1

    tampered = _plan().model_copy(
        update={"project_endpoint": "https://attacker.example/api/projects/test"}
    )
    monkeypatch.setattr("agent_iq.deployments.foundry_host.httpx.patch", patch)
    credential: Any = Credential()

    with pytest.raises(foundry_host.FoundryHostDeploymentError, match="host must end"):
        foundry_host._patch_agent_endpoint(
            tampered,
            credential=credential,
            mutation_lease=HeldLease(),
            agent_version="7",
            expected_current_rules=[foundry_host._fixed_version_rule("7")],
            account_name=ACCOUNT_NAME,
            project_name=PROJECT_NAME,
        )

    assert token_calls == 0
    assert http_calls == 0


def test_mapping_helpers_fail_closed_on_sdk_shape_drift() -> None:
    class MissingAgents:
        def get(self, agent_id: str) -> Any:
            raise ResourceNotFoundError(f"{agent_id} missing")

    missing_client: Any = SimpleNamespace(agents=MissingAgents())
    assert foundry_host._existing_agent_state(missing_client, DEPLOYMENT_ID) is None

    existing_client: Any = SimpleNamespace(
        agents=SimpleNamespace(
            get=lambda agent_id: {
                "agentEndpoint": {
                    "versionSelector": {
                        "versionSelectionRules": [
                            {
                                "type": "FixedRatio",
                                "agentVersion": "5",
                                "trafficPercentage": 25,
                            },
                            {
                                "type": "FixedRatio",
                                "agentVersion": "6",
                                "trafficPercentage": 75,
                            },
                        ]
                    }
                }
            }
        )
    )
    assert foundry_host._existing_agent_state(existing_client, DEPLOYMENT_ID) == {
        "versionSelectionRules": [
            {
                "type": "FixedRatio",
                "agent_version": "5",
                "traffic_percentage": 25,
            },
            {
                "type": "FixedRatio",
                "agent_version": "6",
                "traffic_percentage": 75,
            },
        ],
        "routedVersions": ["5", "6"],
    }
    with pytest.raises(foundry_host.FoundryHostDeploymentError, match="not an alias"):
        foundry_host._normalize_version_selection_rules(
            [
                {
                    "type": "FixedRatio",
                    "agent_version": "@latest",
                    "traffic_percentage": 100,
                }
            ],
            "rules",
        )
    assert foundry_host._instance_principal_id({}) is None
    assert (
        foundry_host._instance_principal_id(
            {"instanceIdentity": {"principalId": "camel-principal"}}
        )
        == "camel-principal"
    )
    assert foundry_host._status_text(SimpleNamespace(value="ACTIVE")) == "active"

    class InvalidResult:
        def as_dict(self) -> str:
            return "not-a-mapping"

    with pytest.raises(foundry_host.FoundryHostDeploymentError, match="mapping-compatible"):
        foundry_host._as_mapping(InvalidResult())
    with pytest.raises(foundry_host.FoundryHostDeploymentError, match="must be an object"):
        foundry_host._mapping(None, "payload")


@pytest.mark.parametrize("percentage", [100.0, "100", True, False])
def test_host_traffic_rules_reject_noninteger_percentages(percentage: object) -> None:
    with pytest.raises(foundry_host.FoundryHostDeploymentError, match="must be an integer"):
        foundry_host._normalize_version_selection_rules(
            [
                {
                    "type": "FixedRatio",
                    "agent_version": "7",
                    "traffic_percentage": percentage,
                }
            ],
            "rules",
        )


def test_cli_plan_deploy_probe_and_rollback_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    card_path = tmp_path / "card.json"
    assert (
        foundry_host.main(
            [
                "plan",
                "--logical-agent-id",
                AGENT_ID,
                "--deployment-agent-id",
                DEPLOYMENT_ID,
                "--project-endpoint",
                PROJECT,
                "--account-name",
                ACCOUNT_NAME,
                "--project-name",
                PROJECT_NAME,
                "--image",
                IMAGE,
                "--model-deployment",
                "gpt-current",
                "--search-service",
                "example-search",
                "--output",
                str(plan_path),
                "--card-output",
                str(card_path),
            ]
        )
        == 0
    )
    assert json.loads(plan_path.read_text(encoding="utf-8"))["logicalAgentId"] == AGENT_ID
    assert json.loads(card_path.read_text(encoding="utf-8"))["description"]

    class Credential:
        def __init__(self) -> None:
            self.close_count = 0

        def close(self) -> None:
            self.close_count += 1

    credential = Credential()
    monkeypatch.setattr(foundry_host, "_credential", lambda: credential)

    lease_exit_count = 0

    class MutationLease(HeldLease):
        def __init__(
            self,
            blob_url: str,
            *,
            credential: Any,
            account_name: str,
            project_name: str,
            agent_id: str,
        ) -> None:
            super().__init__()
            assert blob_url == LEASE_URL
            assert credential is not None
            assert (account_name, project_name, agent_id) == (
                ACCOUNT_NAME,
                PROJECT_NAME,
                DEPLOYMENT_ID,
            )

        def __enter__(self) -> MutationLease:
            return self

        def __exit__(self, *args: object) -> None:
            nonlocal lease_exit_count
            lease_exit_count += 1

    monkeypatch.setattr(foundry_host, "AzureBlobMutationLease", MutationLease)

    def deploy(
        plan: foundry_host.FoundryHostPlan,
        *,
        credential: Any,
        mutation_lease: Any,
        account_name: str,
        project_name: str,
        timeout_seconds: float,
        operation_id: str,
    ) -> dict[str, Any]:
        assert plan.deployment_agent_id == DEPLOYMENT_ID
        assert (account_name, project_name) == (ACCOUNT_NAME, PROJECT_NAME)
        assert timeout_seconds == 1.0
        assert operation_id == OPERATION_ID
        mutation_lease.assert_held()
        return _deployment_record(plan=plan, operation_id=operation_id)

    monkeypatch.setattr(foundry_host, "deploy_host_plan", deploy)
    deploy_output = tmp_path / f"{OPERATION_ID}.deployment.json"
    assert (
        foundry_host.main(
            [
                "deploy",
                "--plan",
                str(plan_path),
                "--account-name",
                ACCOUNT_NAME,
                "--project-name",
                PROJECT_NAME,
                "--mutation-lease-blob-url",
                LEASE_URL,
                "--operation-id",
                OPERATION_ID,
                "--output",
                str(deploy_output),
                "--timeout-seconds",
                "1",
            ]
        )
        == 0
    )
    assert (
        json.loads(deploy_output.read_text(encoding="utf-8"))["plan"]["deploymentAgentId"]
        == DEPLOYMENT_ID
    )

    failure_evidence = {
        "schemaVersion": "3.0",
        "evidenceType": "agent-iq.descriptor-host-deployment",
        "operationId": OPERATION_ID,
        "status": "failed",
    }

    def fail_deploy(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        raise foundry_host.FoundryHostDeploymentError(
            "deployment failed",
            evidence=failure_evidence,
        )

    monkeypatch.setattr(foundry_host, "deploy_host_plan", fail_deploy)
    failure_dir = tmp_path / "failed"
    failure_dir.mkdir()
    failure_output = failure_dir / f"{OPERATION_ID}.deployment.json"
    with pytest.raises(foundry_host.FoundryHostDeploymentError, match="deployment failed"):
        foundry_host.main(
            [
                "deploy",
                "--plan",
                str(plan_path),
                "--account-name",
                ACCOUNT_NAME,
                "--project-name",
                PROJECT_NAME,
                "--mutation-lease-blob-url",
                LEASE_URL,
                "--operation-id",
                OPERATION_ID,
                "--output",
                str(failure_output),
                "--timeout-seconds",
                "1",
            ]
        )
    assert json.loads(failure_output.read_text(encoding="utf-8")) == failure_evidence
    assert lease_exit_count == 2

    async def probe_toolbox(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["toolbox_name"] == f"{AGENT_ID}-knowledge"
        return {"status": "fail", "knowledgeRetrievePresent": False}

    monkeypatch.setattr(foundry_host, "probe_toolbox", probe_toolbox)
    toolbox_output = tmp_path / "toolbox.json"
    assert (
        foundry_host.main(
            [
                "probe-toolbox",
                "--project-endpoint",
                PROJECT,
                "--account-name",
                ACCOUNT_NAME,
                "--project-name",
                PROJECT_NAME,
                "--toolbox-name",
                f"{AGENT_ID}-knowledge",
                "--output",
                str(toolbox_output),
            ]
        )
        == 1
    )

    def probe_live_responses(
        plan: Any,
        credential: Any,
        account_name: str,
        project_name: str,
        agent_version: str,
        prompt: str,
        required_terms: tuple[str, ...],
    ) -> dict[str, Any]:
        del plan, credential, account_name, project_name
        return {
            "schemaVersion": "1.0",
            "status": "pass",
            "transport": "foundry-responses-sse",
            "agentVersion": agent_version,
            "answer": "SENTINEL_RAW_ANSWER",
            "prompt": "SENTINEL_RAW_PROMPT",
            "requiredTerms": list(required_terms),
            "missingTerms": ["SENTINEL_RAW_SOURCE_MARKDOWN"],
            "eventTypes": ["response.completed"],
            "checks": {"completedEventPresent": True},
        }

    monkeypatch.setattr(foundry_host, "_probe_live_responses", probe_live_responses)
    responses_output = tmp_path / "responses.json"
    assert (
        foundry_host.main(
            [
                "probe-responses",
                "--plan",
                str(plan_path),
                "--account-name",
                ACCOUNT_NAME,
                "--project-name",
                PROJECT_NAME,
                "--agent-version",
                "7",
                "--prompt",
                "Grounded answer",
                "--required-term",
                "grounded",
                "--output",
                str(responses_output),
            ]
        )
        == 0
    )
    responses_text = responses_output.read_text(encoding="utf-8")
    responses_report = json.loads(responses_text)
    assert responses_report["answerHash"] == (
        "sha256:" + hashlib.sha256(b"SENTINEL_RAW_ANSWER").hexdigest()
    )
    assert responses_report["agentVersion"] == "7"
    assert responses_report["answerLength"] == len("SENTINEL_RAW_ANSWER")
    assert responses_report["missingTermCount"] == 1
    assert "SENTINEL_RAW_ANSWER" not in responses_text
    assert "SENTINEL_RAW_PROMPT" not in responses_text
    assert "SENTINEL_RAW_SOURCE_MARKDOWN" not in responses_text

    validation_output = tmp_path / "rollback-validation.json"
    assert (
        foundry_host.main(
            [
                "validate-rollback",
                "--record",
                str(deploy_output),
                "--account-name",
                ACCOUNT_NAME,
                "--project-name",
                PROJECT_NAME,
                "--mutation-lease-blob-url",
                LEASE_URL,
                "--output",
                str(validation_output),
            ]
        )
        == 0
    )
    assert credential.close_count == 3

    monkeypatch.setattr(
        foundry_host,
        "rollback_host",
        lambda record, credential, mutation_lease, account_name, project_name, record_path: {
            "status": "rolled_back",
            "action": "restore_prior_traffic_retain_created_version",
        },
    )
    rollback_output = tmp_path / "rollback.json"
    assert (
        foundry_host.main(
            [
                "rollback",
                "--record",
                str(deploy_output),
                "--account-name",
                ACCOUNT_NAME,
                "--project-name",
                PROJECT_NAME,
                "--mutation-lease-blob-url",
                LEASE_URL,
                "--output",
                str(rollback_output),
            ]
        )
        == 0
    )
    assert credential.close_count == 4


def test_valid_but_untrusted_project_is_rejected_before_sdk_or_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_calls = 0
    token_calls = 0
    plan = _plan().model_copy(
        update={"project_endpoint": "https://other.services.ai.azure.com/api/projects/test"}
    )

    class Credential:
        def get_token(self, scope: str) -> Any:
            nonlocal token_calls
            del scope
            token_calls += 1
            return SimpleNamespace(token="must-not-be-used")

    def project_client(*args: Any, **kwargs: Any) -> None:
        nonlocal client_calls
        del args, kwargs
        client_calls += 1

    monkeypatch.setattr(foundry_host, "_project_client", project_client)

    with pytest.raises(foundry_host.FoundryHostDeploymentError, match="does not match"):
        foundry_host.deploy_host_plan(
            plan,
            credential=cast(Any, Credential()),
            mutation_lease=HeldLease(),
            account_name=ACCOUNT_NAME,
            project_name=PROJECT_NAME,
            timeout_seconds=1,
        )

    assert client_calls == 0
    assert token_calls == 0


def test_isolated_http_client_disables_redirects_and_environment_proxies() -> None:
    client = foundry_host._isolated_http_client()
    try:
        assert client.follow_redirects is False
        assert client.trust_env is False
    finally:
        client.close()


def test_report_writer_revalidates_urls_before_atomic_write(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    unsafe_userinfo_url = (
        "https://" + "synthetic-user" + ":" + "synthetic-password" + "@example.test/report"
    )

    with pytest.raises(foundry_host.FoundryHostDeploymentError, match="safe HTTPS URL"):
        foundry_host._write_json(
            output,
            {"status": "pass", "reportUrl": unsafe_userinfo_url},
        )

    assert not output.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_host_dockerfile_is_linux_amd64_pinned_and_checks_at_startup() -> None:
    dockerfile = (
        Path(__file__).resolve().parents[2] / "deployments" / "foundry" / "host" / "Dockerfile"
    ).read_text(encoding="utf-8")

    assert "FROM --platform=linux/amd64" in dockerfile
    assert "@sha256:" in dockerfile.splitlines()[0]
    assert "uv sync --frozen --no-dev --group foundry" in dockerfile
    assert "USER agentiq" in dockerfile
    assert "EXPOSE 8088" in dockerfile
    assert "agent_iq.runtime.host_bootstrap" in dockerfile
