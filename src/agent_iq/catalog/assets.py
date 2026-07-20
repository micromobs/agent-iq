"""Validated source-data assets used to build the canonical catalog."""

from __future__ import annotations

from string import Formatter
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from agent_iq.contracts.agent import AgentDescriptor
from agent_iq.contracts.artifacts import ArtifactKind
from agent_iq.contracts.base import (
    ContractModel,
    Identifier,
    NonEmptyText,
    SchemaVersion,
    ShortText,
    Timestamp,
)
from agent_iq.contracts.sap import SapReadRecord, SapResourceKind, SapSourceMode
from agent_iq.contracts.tools import ToolBackend, ToolDescriptor

PromptAddendum = Annotated[str, StringConstraints(strip_whitespace=True, max_length=16_384)]


class AgentCatalogAsset(ContractModel):
    schema_version: SchemaVersion = "1.0"
    catalog_id: Identifier
    published_at: Timestamp
    agents: tuple[AgentDescriptor, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_agent_ids(self) -> AgentCatalogAsset:
        agent_ids = [agent.agent_id for agent in self.agents]
        if len(agent_ids) != len(set(agent_ids)):
            raise ValueError("Catalog agent IDs must be unique.")
        return self


class RuntimeToolDefinition(ContractModel):
    descriptor: ToolDescriptor
    query_template: PromptAddendum = ""
    sap_resource: SapResourceKind | None = None

    @model_validator(mode="after")
    def validate_backend(self) -> RuntimeToolDefinition:
        if self.descriptor.backend is ToolBackend.FOUNDRY_IQ:
            if not self.query_template:
                raise ValueError("A Foundry IQ tool requires a query template.")
            if self.sap_resource is not None:
                raise ValueError("A Foundry IQ tool cannot declare a SAP resource.")
            fields = {
                field_name
                for _, field_name, _, _ in Formatter().parse(self.query_template)
                if field_name is not None
            }
            parameters = {parameter.parameter_id for parameter in self.descriptor.parameters}
            if fields != parameters:
                raise ValueError(
                    "Foundry IQ query placeholders must exactly match tool parameters."
                )
        elif self.sap_resource is None:
            raise ValueError("A SAP tool requires a typed SAP resource.")
        elif self.query_template:
            raise ValueError("A SAP tool cannot use a model-facing query template.")
        elif len(self.descriptor.authorization_scopes) != 1:
            raise ValueError("A SAP tool requires exactly one read authorization scope.")
        return self


class RuntimeAgentDefinition(ContractModel):
    agent_id: Identifier
    knowledge_binding_id: Identifier
    prompt_addendum: PromptAddendum = ""
    tools: tuple[RuntimeToolDefinition, ...] = ()
    artifact_output_kinds: tuple[ArtifactKind, ...] = ()

    @model_validator(mode="after")
    def validate_tool_ids(self) -> RuntimeAgentDefinition:
        tool_ids = [tool.descriptor.tool_id for tool in self.tools]
        if len(tool_ids) != len(set(tool_ids)):
            raise ValueError("Runtime tool IDs must be unique per agent.")
        return self


class RuntimeCatalogAsset(ContractModel):
    schema_version: SchemaVersion = "1.0"
    agents: tuple[RuntimeAgentDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_agent_ids(self) -> RuntimeCatalogAsset:
        agent_ids = [agent.agent_id for agent in self.agents]
        if len(agent_ids) != len(set(agent_ids)):
            raise ValueError("Runtime agent IDs must be unique.")
        return self


class KnowledgeBindingDefinition(ContractModel):
    knowledge_binding_id: Identifier
    corpus_ids: tuple[Identifier, ...] = Field(min_length=1)


class KnowledgeBindingsAsset(ContractModel):
    schema_version: SchemaVersion = "1.0"
    bindings: tuple[KnowledgeBindingDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_binding_ids(self) -> KnowledgeBindingsAsset:
        binding_ids = [binding.knowledge_binding_id for binding in self.bindings]
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("Knowledge binding IDs must be unique.")
        return self


class SapFixtureAsset(ContractModel):
    schema_version: SchemaVersion = "1.0"
    source_label: ShortText
    generated_at: Timestamp
    records: tuple[SapReadRecord, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_fixture_provenance(self) -> SapFixtureAsset:
        if "not live sap" not in self.source_label.casefold():
            raise ValueError("SAP fixture assets must be visibly labelled NOT LIVE SAP.")
        if any(
            record.reference.source_mode is not SapSourceMode.FIXTURE for record in self.records
        ):
            raise ValueError("SAP fixture records must identify fixture provenance.")
        return self


class DiscoveryCase(ContractModel):
    case_id: Identifier
    intent: NonEmptyText
    expected_agent_ids: tuple[Identifier, ...] = Field(min_length=1)
    note: NonEmptyText


class KnowledgeDocument(ContractModel):
    document_id: Identifier
    corpus_id: Identifier
    title: ShortText
    segment: ShortText
    category: ShortText
    content: NonEmptyText
