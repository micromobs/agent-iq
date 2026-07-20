"""Validated logical-agent selection for reusable hosted runtime images."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Literal

from pydantic import Field

from agent_iq.catalog.assets import (
    KnowledgeBindingDefinition,
    RuntimeAgentDefinition,
)
from agent_iq.catalog.loader import ConsumerRuntimeAssets, load_consumer_runtime_assets
from agent_iq.contracts.agent import AgentDescriptor
from agent_iq.contracts.base import (
    ContractModel,
    Identifier,
    SchemaVersion,
    Sha256Digest,
    ShortText,
)

HOST_LOGICAL_AGENT_ID_ENV = "AIQ_HOST_LOGICAL_AGENT_ID"
HOST_RUNTIME_FAMILY_ENV = "AIQ_HOST_RUNTIME_FAMILY"
HOST_DESCRIPTOR_SHA256_ENV = "AIQ_HOST_DESCRIPTOR_SHA256"
HOST_TOOLBOX_NAME_ENV = "AIQ_HOST_TOOLBOX_NAME"
LANGGRAPH_RUNTIME_FAMILY = "langgraph"


class HostDescriptorError(ValueError):
    """The requested logical agent cannot safely run in this host image."""


class RuntimeHostDescriptor(ContractModel):
    """Content-safe startup contract for one logical agent."""

    schema_version: SchemaVersion = "1.0"
    logical_agent_id: Identifier
    runtime_family: Literal["langgraph"]
    authoring_framework: ShortText
    descriptor_sha256: Sha256Digest
    knowledge_binding_id: Identifier
    toolbox_name: Identifier
    tool_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    artifact_output_kinds: tuple[Identifier, ...] = Field(default_factory=tuple)


def descriptor_for_agent(
    agent_id: str,
    *,
    assets: ConsumerRuntimeAssets | None = None,
) -> RuntimeHostDescriptor:
    """Resolve and fingerprint one canonical LangGraph logical agent."""

    resolved_assets = assets or load_consumer_runtime_assets()
    descriptor = _agent_descriptor(agent_id, resolved_assets)
    runtime = _runtime_definition(agent_id, resolved_assets)
    binding = _knowledge_binding(runtime.knowledge_binding_id, resolved_assets)
    framework = descriptor.implementation.framework
    if framework is None or framework.casefold() != "langgraph":
        raise HostDescriptorError(
            f"Agent {agent_id!r} uses {framework!r}, not the LangGraph runtime family."
        )
    return RuntimeHostDescriptor(
        logical_agent_id=agent_id,
        runtime_family=LANGGRAPH_RUNTIME_FAMILY,
        authoring_framework=framework,
        descriptor_sha256=_descriptor_sha256(descriptor, runtime, binding),
        knowledge_binding_id=runtime.knowledge_binding_id,
        toolbox_name=f"{agent_id}-knowledge",
        tool_ids=tuple(tool.descriptor.tool_id for tool in runtime.tools),
        artifact_output_kinds=tuple(kind.value for kind in runtime.artifact_output_kinds),
    )


def descriptor_from_environment(
    environment: Mapping[str, str],
    *,
    assets: ConsumerRuntimeAssets | None = None,
) -> RuntimeHostDescriptor:
    """Resolve a logical agent and fail closed on descriptor or family drift."""

    agent_id = _required_environment(environment, HOST_LOGICAL_AGENT_ID_ENV)
    expected_family = _required_environment(environment, HOST_RUNTIME_FAMILY_ENV)
    expected_sha256 = _required_environment(environment, HOST_DESCRIPTOR_SHA256_ENV)
    expected_toolbox = _required_environment(environment, HOST_TOOLBOX_NAME_ENV)
    descriptor = descriptor_for_agent(agent_id, assets=assets)
    if expected_family != descriptor.runtime_family:
        raise HostDescriptorError(
            f"{HOST_RUNTIME_FAMILY_ENV} does not match the canonical runtime family."
        )
    if expected_sha256 != descriptor.descriptor_sha256:
        raise HostDescriptorError(
            f"{HOST_DESCRIPTOR_SHA256_ENV} does not match the canonical descriptor."
        )
    if expected_toolbox != descriptor.toolbox_name:
        raise HostDescriptorError(
            f"{HOST_TOOLBOX_NAME_ENV} does not match the logical agent toolbox."
        )
    return descriptor


def _descriptor_sha256(
    descriptor: AgentDescriptor,
    runtime: RuntimeAgentDefinition,
    binding: KnowledgeBindingDefinition,
) -> str:
    payload = {
        "descriptor": descriptor.model_dump(mode="json", by_alias=True),
        "runtime": runtime.model_dump(mode="json", by_alias=True),
        "knowledgeBinding": binding.model_dump(mode="json", by_alias=True),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"sha256:{hashlib.sha256(encoded.encode('ascii')).hexdigest()}"


def _agent_descriptor(agent_id: str, assets: ConsumerRuntimeAssets) -> AgentDescriptor:
    try:
        return next(
            descriptor for descriptor in assets.catalog.agents if descriptor.agent_id == agent_id
        )
    except StopIteration as exc:
        raise HostDescriptorError(f"Unknown canonical agent {agent_id!r}.") from exc


def _runtime_definition(agent_id: str, assets: ConsumerRuntimeAssets) -> RuntimeAgentDefinition:
    try:
        return next(runtime for runtime in assets.runtime.agents if runtime.agent_id == agent_id)
    except StopIteration as exc:
        raise HostDescriptorError(f"Missing runtime definition for {agent_id!r}.") from exc


def _knowledge_binding(
    binding_id: str,
    assets: ConsumerRuntimeAssets,
) -> KnowledgeBindingDefinition:
    try:
        return next(
            binding
            for binding in assets.knowledge_bindings.bindings
            if binding.knowledge_binding_id == binding_id
        )
    except StopIteration as exc:
        raise HostDescriptorError(f"Missing knowledge binding {binding_id!r}.") from exc


def _required_environment(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise HostDescriptorError(f"{name} is required.")
    return value
