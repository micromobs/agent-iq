from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from agent_iq.catalog.live_demo import ensure_live_demo_inventory
from agent_iq.contracts import ErrorCode
from agent_iq.errors import AgentIqOperationError


@dataclass(frozen=True)
class InventoryRecord:
    agent_id: str
    status: str = "active"


class InventoryCatalog:
    def __init__(self, records: tuple[InventoryRecord, ...]) -> None:
        self.records = records
        self.refreshes: list[bool] = []

    async def list_inventory(
        self,
        *,
        refresh: bool = False,
    ) -> tuple[InventoryRecord, ...]:
        self.refreshes.append(refresh)
        return self.records


def test_demo_preflight_counts_project_inventory_but_requires_only_candidates() -> None:
    records = (
        InventoryRecord("consumer-formulation-rd", status="enabled"),
        *(InventoryRecord(f"project-agent-{index}") for index in range(19)),
    )
    catalog = InventoryCatalog(records)

    result = asyncio.run(
        ensure_live_demo_inventory(catalog, frozenset({"consumer-formulation-rd"}))
    )

    assert result.active_project_agents == 20
    assert result.indexed_candidates == 1
    assert catalog.refreshes == [True]


def test_demo_preflight_rejects_missing_canonical_candidate() -> None:
    catalog = InventoryCatalog(
        tuple(InventoryRecord(f"project-agent-{index}") for index in range(20))
    )

    with pytest.raises(AgentIqOperationError) as caught:
        asyncio.run(ensure_live_demo_inventory(catalog, frozenset({"consumer-formulation-rd"})))

    assert caught.value.error.code is ErrorCode.CATALOG_UNAVAILABLE
    assert "consumer-formulation-rd" in caught.value.error.message
