"""Canonical catalog projections and inventory gates for live demos."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from agent_iq.contracts import AgentDescriptor, ErrorCode
from agent_iq.errors import operation_error

from .assets import AgentCatalogAsset

VACATION_AGENT_ID = "employee-vacation-leave"
SINGLE_AGENT_DEMO_ID = "consumer-formulation-rd"
GENERIC_PREFIX = "consumer-"
LIVE_PREFIX = "pg-"
MINIMUM_LIVE_PROJECT_AGENTS = 20
ENABLED_INVENTORY_STATES = frozenset({"active", "enabled"})


class LiveInventoryRecord(Protocol):
    @property
    def agent_id(self) -> str: ...

    @property
    def status(self) -> str: ...


class LiveInventoryCatalog(Protocol):
    async def list_inventory(
        self,
        *,
        refresh: bool = False,
    ) -> Sequence[LiveInventoryRecord]: ...


@dataclass(frozen=True, slots=True)
class LiveDemoInventory:
    """Content-safe facts proven immediately before demo matching."""

    active_project_agents: int
    indexed_candidates: int


def _live_descriptor(descriptor: AgentDescriptor) -> AgentDescriptor:
    if not descriptor.agent_id.startswith(GENERIC_PREFIX):
        raise ValueError(f"Unsupported live-demo descriptor ID {descriptor.agent_id!r}.")
    agent_id = f"{LIVE_PREFIX}{descriptor.agent_id.removeprefix(GENERIC_PREFIX)}"
    return descriptor.model_copy(
        update={
            "agent_id": agent_id,
            "source": descriptor.source.model_copy(
                update={
                    "source_id": "live-foundry-demo-source",
                    "source_agent_id": agent_id,
                }
            ),
        }
    )


def build_live_vacation_demo_catalog(catalog: AgentCatalogAsset) -> AgentCatalogAsset:
    """Project domain descriptors onto active IDs and retain employee specialists."""

    generic = tuple(agent for agent in catalog.agents if agent.agent_id.startswith(GENERIC_PREFIX))
    employee = tuple(agent for agent in catalog.agents if agent.agent_id.startswith("employee-"))
    if (
        len(generic) != 20
        or not employee
        or VACATION_AGENT_ID not in {agent.agent_id for agent in employee}
    ):
        raise ValueError(
            "The live employee demo requires 20 domain agents and the vacation specialist."
        )
    agents = (*(_live_descriptor(agent) for agent in generic), *employee)
    return AgentCatalogAsset(
        catalog_id="live-vacation-demo",
        published_at=catalog.published_at,
        agents=agents,
    )


def build_canonical_single_agent_demo_catalog(catalog: AgentCatalogAsset) -> AgentCatalogAsset:
    """Keep the proven immutable Foundry formulation binding for the single-agent demo."""

    agents = tuple(agent for agent in catalog.agents if agent.agent_id == SINGLE_AGENT_DEMO_ID)
    if len(agents) != 1:
        raise ValueError("The single-agent demo requires one canonical formulation agent.")
    return AgentCatalogAsset(
        catalog_id="canonical-single-agent-demo",
        published_at=catalog.published_at,
        agents=agents,
    )


async def ensure_live_demo_inventory(
    catalog: LiveInventoryCatalog,
    candidate_ids: frozenset[str],
) -> LiveDemoInventory:
    """Fail before matching unless every indexed candidate is active in Foundry."""

    records = await catalog.list_inventory(refresh=True)
    active_ids = frozenset(
        record.agent_id
        for record in records
        if record.status.casefold() in ENABLED_INVENTORY_STATES
    )
    if len(active_ids) < MINIMUM_LIVE_PROJECT_AGENTS:
        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            "The Foundry project has fewer than 20 enabled agents for the live demo.",
        )
    missing = candidate_ids - active_ids
    if missing:
        raise operation_error(
            ErrorCode.CATALOG_UNAVAILABLE,
            (
                f"The live demo catalog has {len(missing)} inactive or missing candidate "
                f"agent{'s' if len(missing) != 1 else ''}: {', '.join(sorted(missing))}."
            ),
        )
    return LiveDemoInventory(
        active_project_agents=len(active_ids),
        indexed_candidates=len(candidate_ids),
    )
