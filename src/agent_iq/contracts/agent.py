"""Canonical agent, skill, source, health, and trust contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AnyHttpUrl, AnyUrl, Field, field_validator, model_validator

from .artifacts import ArtifactKind
from .base import (
    ContractModel,
    Identifier,
    NonEmptyText,
    SchemaVersion,
    Sha256Digest,
    ShortText,
    Timestamp,
    VersionText,
    require_unique_text,
)
from .evidence import EvidenceCapability


class CatalogKind(StrEnum):
    AGENT_365 = "agent365"
    FOUNDRY = "foundry"
    REMOTE_A2A = "remote_a2a"
    WORK_IQ = "work_iq"
    COPILOT_STUDIO = "copilot_studio"
    EXTERNAL = "external"
    LOCAL = "local"


class CatalogAuthority(StrEnum):
    REGISTRY_OF_RECORD = "registry_of_record"
    AUTHORITATIVE = "authoritative"
    PROJECTION = "projection"
    DECLARED = "declared"


class TransportProtocol(StrEnum):
    A2A = "a2a"
    WORK_IQ = "work_iq"
    MCP = "mcp"


class InteractionMode(StrEnum):
    SYNC = "sync"
    ASYNC = "async"


class CallbackMode(StrEnum):
    POLL = "poll"
    WEBHOOK = "webhook"
    PUSH = "push"
    TEAMS = "teams"
    EMAIL = "email"


class TrustState(StrEnum):
    UNKNOWN = "unknown"
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    BLOCKED = "blocked"


class RiskLevel(StrEnum):
    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class HealthStatus(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ProviderDescriptor(ContractModel):
    name: ShortText
    url: AnyHttpUrl | None = None


class Label(ContractModel):
    key: Identifier
    value: ShortText


class AgentImplementation(ContractModel):
    """Informational implementation metadata, never a routing dependency."""

    framework: ShortText | None = None
    authoring_platform: ShortText | None = None
    hosting_platform: ShortText | None = None


class SkillDescriptor(ContractModel):
    skill_id: Identifier
    name: ShortText
    description: NonEmptyText
    tags: tuple[ShortText, ...] = ()
    examples: tuple[NonEmptyText, ...] = ()
    input_modes: tuple[ShortText, ...] = ("text/plain",)
    output_modes: tuple[ShortText, ...] = ("text/plain",)

    @field_validator("tags", "examples", "input_modes", "output_modes")
    @classmethod
    def validate_unique_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Skill values")


class InvocationCapability(ContractModel):
    protocol: TransportProtocol
    protocol_version: VersionText
    endpoint: AnyHttpUrl | None = None
    streaming: bool = False
    push_notifications: bool = False
    task_management: bool = False
    interaction_modes: tuple[InteractionMode, ...] = (InteractionMode.SYNC,)
    callback_modes: tuple[CallbackMode, ...] = ()
    content_types: tuple[ShortText, ...] = ("text/plain",)
    artifact_kinds: tuple[ArtifactKind, ...] = ()
    evidence: EvidenceCapability | None = None

    @field_validator("content_types", "artifact_kinds")
    @classmethod
    def validate_capability_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Capability values")


class CatalogSource(ContractModel):
    source_id: Identifier
    kind: CatalogKind
    authority: CatalogAuthority
    source_agent_id: ShortText
    source_uri: AnyUrl | None = None
    fetched_at: Timestamp
    expires_at: Timestamp | None = None
    etag: ShortText | None = None

    @model_validator(mode="after")
    def validate_expiry(self) -> CatalogSource:
        if self.expires_at is not None and self.expires_at <= self.fetched_at:
            raise ValueError("Catalog source expiry must be after fetch time.")
        return self


class TrustMetadata(ContractModel):
    identity_id: ShortText | None = None
    owner_ids: tuple[ShortText, ...] = ()
    state: TrustState = TrustState.UNKNOWN
    admin_approved: bool = False
    signed: bool = False
    risk_level: RiskLevel = RiskLevel.UNKNOWN
    data_classifications: tuple[ShortText, ...] = ()
    allowed_geographies: tuple[ShortText, ...] = ()
    policy_version: VersionText | None = None

    @field_validator("owner_ids", "data_classifications", "allowed_geographies")
    @classmethod
    def validate_unique_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Trust values")


class HealthFact(ContractModel):
    name: Identifier
    value: ShortText


class HealthRecord(ContractModel):
    status: HealthStatus = HealthStatus.UNKNOWN
    checked_at: Timestamp
    expires_at: Timestamp | None = None
    facts: tuple[HealthFact, ...] = ()

    @model_validator(mode="after")
    def validate_expiry(self) -> HealthRecord:
        if self.expires_at is not None and self.expires_at <= self.checked_at:
            raise ValueError("Health expiry must be after check time.")
        return self


class AgentVersion(ContractModel):
    version: VersionText
    card_hash: Sha256Digest | None = None
    published_at: Timestamp | None = None


class AgentDescriptor(ContractModel):
    schema_version: SchemaVersion = "1.0"
    agent_id: Identifier
    name: ShortText
    description: NonEmptyText
    provider: ProviderDescriptor
    version: AgentVersion
    skills: tuple[SkillDescriptor, ...] = Field(min_length=1)
    capabilities: tuple[InvocationCapability, ...] = Field(min_length=1)
    source: CatalogSource
    trust: TrustMetadata = Field(default_factory=TrustMetadata)
    health: HealthRecord | None = None
    implementation: AgentImplementation = Field(default_factory=AgentImplementation)
    tags: tuple[ShortText, ...] = ()
    knowledge_binding_ids: tuple[Identifier, ...] = ()
    labels: tuple[Label, ...] = ()

    @field_validator("tags", "knowledge_binding_ids")
    @classmethod
    def validate_unique_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Agent values")

    @model_validator(mode="after")
    def validate_unique_nested_ids(self) -> AgentDescriptor:
        skill_ids = [skill.skill_id.casefold() for skill in self.skills]
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError("Agent skill IDs must be unique.")

        capability_ids = [
            (capability.protocol.value, capability.protocol_version)
            for capability in self.capabilities
        ]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("Agent protocol/version capabilities must be unique.")
        return self
