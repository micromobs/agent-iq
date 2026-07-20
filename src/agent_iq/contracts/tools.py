"""Framework-neutral contracts for narrow, governed agent tools."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from .artifacts import ArtifactKind
from .base import ContractModel, Identifier, NonEmptyText, SchemaVersion, ShortText


class ToolBackend(StrEnum):
    FOUNDRY_IQ = "foundry_iq"
    SAP = "sap"


class ToolEffect(StrEnum):
    READ = "read"
    PROPOSE_WRITE = "propose_write"
    WRITE = "write"


class ToolIdempotency(StrEnum):
    SAFE = "safe"
    REQUIRES_KEY = "requires_key"
    NEVER_RETRY = "never_retry"


class ToolParameterType(StrEnum):
    TEXT = "text"
    DATE = "date"


class ToolParameter(ContractModel):
    parameter_id: Identifier
    description: ShortText
    value_type: ToolParameterType = ToolParameterType.TEXT
    required: bool = True


class ToolTelemetryPolicy(ContractModel):
    capture_latency: bool = True
    capture_cost: bool = True
    capture_arguments: bool = False


class ToolDescriptor(ContractModel):
    schema_version: SchemaVersion = "1.0"
    tool_id: Identifier
    name: ShortText
    description: NonEmptyText
    backend: ToolBackend
    parameters: tuple[ToolParameter, ...] = Field(min_length=1)
    authorization_scopes: tuple[ShortText, ...] = Field(min_length=1)
    effect: ToolEffect = ToolEffect.READ
    approval_required: bool = False
    idempotency: ToolIdempotency = ToolIdempotency.SAFE
    produces_evidence: bool = True
    artifact_kinds: tuple[ArtifactKind, ...] = ()
    telemetry: ToolTelemetryPolicy = Field(default_factory=ToolTelemetryPolicy)

    @field_validator("authorization_scopes", "artifact_kinds")
    @classmethod
    def validate_unique_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = [str(value).casefold() for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Tool capability values must be unique.")
        return values

    @model_validator(mode="after")
    def validate_governance(self) -> ToolDescriptor:
        parameter_ids = [parameter.parameter_id for parameter in self.parameters]
        if len(parameter_ids) != len(set(parameter_ids)):
            raise ValueError("Tool parameter IDs must be unique.")
        if self.effect is not ToolEffect.READ and not self.approval_required:
            raise ValueError("A tool with write effects requires explicit approval.")
        if self.effect is ToolEffect.WRITE and self.idempotency is ToolIdempotency.SAFE:
            raise ValueError("A write tool cannot claim unconditional retry safety.")
        if not self.produces_evidence and self.artifact_kinds:
            raise ValueError("Artifact-producing tools must also produce evidence.")
        return self
