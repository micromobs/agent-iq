"""Versioned runtime capability-ledger contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from .base import ContractModel, Identifier, SchemaVersion, ShortText, Timestamp


class ProbeStatus(StrEnum):
    """Normalized live capability result."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNAUTHORIZED = "unauthorized"
    NOT_CONFIGURED = "not_configured"
    MUTATION_REQUIRED = "mutation_required"
    PROBE_ERROR = "probe_error"


class ProbeMode(StrEnum):
    """How the live observation was obtained."""

    SDK = "sdk"
    ARM = "arm"
    DATA_PLANE = "data_plane"
    CERTIFICATE = "certificate"
    DERIVED = "derived"
    CLASSIFIED = "classified"


class CapabilityObservation(ContractModel):
    """One documented capability and its observed tenant state."""

    capability_id: Identifier
    category: Identifier
    documented_status: Identifier
    observed_status: ProbeStatus
    probe_mode: ProbeMode
    required: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class CapabilityScope(ContractModel):
    """Non-identifying scope retained in a capability report."""

    location: ShortText
    target_models: tuple[ShortText, ...]
    project_configured: bool
    canary_configured: bool


class CapabilityContentPolicy(ContractModel):
    """Sensitive classes that the report is forbidden to persist."""

    persists_resource_names: Literal[False] = False
    persists_resource_identifiers: Literal[False] = False
    persists_endpoints: Literal[False] = False
    persists_prompts: Literal[False] = False
    persists_payloads: Literal[False] = False
    persists_tokens: Literal[False] = False
    persists_raw_errors: Literal[False] = False


class CapabilitySummary(ContractModel):
    """Aggregate gate result."""

    capability_count: int = Field(ge=0)
    required_count: int = Field(ge=0)
    required_available_count: int = Field(ge=0)
    required_failures: tuple[Identifier, ...] = ()
    probe_errors: tuple[Identifier, ...] = ()
    status_counts: dict[ProbeStatus, int]


class CapabilityLedger(ContractModel):
    """Content-safe capability certificate."""

    schema_version: SchemaVersion = "1.0"
    status: Literal["pass", "fail"]
    checked_at: Timestamp
    scope: CapabilityScope
    content_policy: CapabilityContentPolicy
    sdk: dict[Identifier, ShortText]
    summary: CapabilitySummary
    capabilities: tuple[CapabilityObservation, ...]
