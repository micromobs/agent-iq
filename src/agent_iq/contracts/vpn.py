"""Strict VPN recovery guidance preserved across text-only A2A."""

from __future__ import annotations

import json
from enum import StrEnum
from json import JSONDecodeError

from pydantic import Field, ValidationError, model_validator

from .base import ContractModel, Identifier, NonEmptyText, SchemaVersion, ShortText, Timestamp

VPN_RECOVERY_JSON_START = "VPN_RECOVERY_JSON_START"
VPN_RECOVERY_JSON_END = "VPN_RECOVERY_JSON_END"


class VpnRecoveryOutcomeKind(StrEnum):
    """The governed recovery outcomes displayed on the compact VPN flow."""

    RECONNECT = "reconnect"
    RESTART = "restart"
    NETWORK_RESET = "network_reset"
    HELP_DESK = "help_desk"


class VpnRecoveryOutcome(ContractModel):
    """One governed recovery outcome in the aggregate guidance record."""

    outcome_id: VpnRecoveryOutcomeKind
    label: ShortText
    count: int = Field(ge=1)


class VpnRecoveryGuidance(ContractModel):
    """Machine-validated aggregate VPN guidance returned by the IT specialist."""

    schema_version: SchemaVersion = "1.0"
    agent_id: Identifier
    title: ShortText
    population: int = Field(ge=1)
    period_start: Timestamp
    period_end: Timestamp
    scope: ShortText
    outcomes: tuple[VpnRecoveryOutcome, ...] = Field(min_length=4, max_length=4)
    recommended_first_action: ShortText
    escalation_boundary: NonEmptyText
    guidance_note: ShortText
    read_only: bool
    freshness: Timestamp
    source_system: ShortText
    source_record_id: Identifier
    source_title: ShortText
    fixture_data: bool = True

    @model_validator(mode="after")
    def validate_guidance(self) -> VpnRecoveryGuidance:
        if self.agent_id != "employee-it-helpdesk":
            raise ValueError("VPN guidance must use agent 'employee-it-helpdesk'.")
        if self.scope.casefold() != "global":
            raise ValueError("VPN guidance must declare the global scope explicitly.")
        if self.period_end < self.period_start:
            raise ValueError("VPN guidance period_end must be on or after period_start.")
        if sum(outcome.count for outcome in self.outcomes) != self.population:
            raise ValueError("VPN recovery outcome counts must sum to the population.")
        if tuple(outcome.outcome_id for outcome in self.outcomes) != (
            VpnRecoveryOutcomeKind.RECONNECT,
            VpnRecoveryOutcomeKind.RESTART,
            VpnRecoveryOutcomeKind.NETWORK_RESET,
            VpnRecoveryOutcomeKind.HELP_DESK,
        ):
            raise ValueError(
                "VPN recovery outcomes must be ordered reconnect, restart, "
                "network_reset, help_desk."
            )
        note = self.guidance_note.casefold()
        if "guidance" not in note or "device diagnosis" not in note:
            raise ValueError(
                "VPN guidance must say that it is guidance rather than a device diagnosis."
            )
        if not self.read_only:
            raise ValueError("VPN guidance must remain read-only.")
        if not self.fixture_data:
            raise ValueError("VPN guidance fixtures must remain explicitly synthetic.")
        return self


def parse_vpn_recovery(answer: str) -> VpnRecoveryGuidance:
    """Extract and validate one marked VPN recovery record from grounded text."""

    if answer.count(VPN_RECOVERY_JSON_START) != 1 or answer.count(VPN_RECOVERY_JSON_END) != 1:
        raise ValueError("The A2A answer must contain exactly one marked VPN recovery record.")
    start = answer.index(VPN_RECOVERY_JSON_START) + len(VPN_RECOVERY_JSON_START)
    end = answer.index(VPN_RECOVERY_JSON_END, start)
    payload = answer[start:end].strip()
    if not payload:
        raise ValueError("The marked VPN recovery record is empty.")
    try:
        decoded = json.loads(payload)
    except JSONDecodeError as exc:
        raise ValueError("The marked VPN recovery record is not valid JSON.") from exc
    try:
        return VpnRecoveryGuidance.model_validate(decoded)
    except ValidationError as exc:
        raise ValueError("The marked VPN recovery record does not satisfy its contract.") from exc
