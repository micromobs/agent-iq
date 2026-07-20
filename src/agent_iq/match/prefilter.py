"""Eligibility filtering that runs before lexical or semantic retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agent_iq.contracts import (
    AgentDescriptor,
    EligibilityDecision,
    HealthStatus,
    MatchRequest,
    TrustState,
)


class EligibilityPolicy(Protocol):
    def evaluate(
        self,
        request: MatchRequest,
        agent: AgentDescriptor,
    ) -> EligibilityDecision: ...


class DevelopmentEligibilityPolicy:
    """Demo policy that permits unknown trust but still blocks explicit denials."""

    version = "development-v1"

    def evaluate(
        self,
        request: MatchRequest,
        agent: AgentDescriptor,
    ) -> EligibilityDecision:
        blocked: list[str] = []
        facts = ["development policy", "eligibility checked before ranking"]
        filters = request.filters

        if filters.allowed_agent_ids and agent.agent_id not in filters.allowed_agent_ids:
            blocked.append("agent is not in the request allowlist")
        if agent.agent_id in filters.excluded_agent_ids:
            blocked.append("agent is excluded by the request")
        if agent.trust.state is TrustState.BLOCKED:
            blocked.append("agent trust state is blocked")
        if agent.health is not None and agent.health.status is HealthStatus.UNHEALTHY:
            blocked.append("agent health state is unhealthy")

        supported_protocols = {capability.protocol for capability in agent.capabilities}
        missing_protocols = set(filters.required_protocols) - supported_protocols
        if missing_protocols:
            blocked.append("agent lacks a required protocol")

        supported_modes = {
            mode for capability in agent.capabilities for mode in capability.interaction_modes
        }
        missing_modes = set(filters.required_interaction_modes) - supported_modes
        if missing_modes:
            blocked.append("agent lacks a required interaction mode")

        return EligibilityDecision(
            eligible=not blocked,
            policy_facts=tuple(facts) if not blocked else (),
            blocked_reasons=tuple(blocked),
            policy_version=self.version,
        )


@dataclass(frozen=True, slots=True)
class PrefilterResult:
    eligible: tuple[AgentDescriptor, ...]
    decisions: dict[str, EligibilityDecision]


def prefilter_agents(
    request: MatchRequest,
    agents: tuple[AgentDescriptor, ...],
    policy: EligibilityPolicy,
) -> PrefilterResult:
    decisions = {agent.agent_id: policy.evaluate(request, agent) for agent in agents}
    eligible = tuple(agent for agent in agents if decisions[agent.agent_id].eligible)
    return PrefilterResult(eligible=eligible, decisions=decisions)
