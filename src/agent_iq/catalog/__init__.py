"""Canonical Agent IQ catalog and projections."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .adapters import FoundryAgentRecord, FoundryCardState, FoundryCatalogAdapter
    from .cards import (
        CardProjectionError,
        authored_card_projection,
        card_hash,
        drifted_card_fields,
        foundry_card_projection,
        served_card_projection,
    )
from .assets import (
    AgentCatalogAsset,
    DiscoveryCase,
    KnowledgeBindingsAsset,
    KnowledgeDocument,
    RuntimeCatalogAsset,
)
from .live_demo import (
    LiveDemoInventory,
    build_canonical_single_agent_demo_catalog,
    build_live_vacation_demo_catalog,
    ensure_live_demo_inventory,
)
from .loader import (
    CatalogDataError,
    ConsumerAssets,
    ConsumerRuntimeAssets,
    load_consumer_assets,
    load_consumer_runtime_assets,
)
from .registry_projection import build_registry_projection, render_registry_projection

__all__ = [
    "AgentCatalogAsset",
    "CardProjectionError",
    "CatalogDataError",
    "ConsumerAssets",
    "ConsumerRuntimeAssets",
    "DiscoveryCase",
    "FoundryAgentRecord",
    "FoundryCardState",
    "FoundryCatalogAdapter",
    "KnowledgeBindingsAsset",
    "KnowledgeDocument",
    "LiveDemoInventory",
    "RuntimeCatalogAsset",
    "authored_card_projection",
    "build_canonical_single_agent_demo_catalog",
    "build_live_vacation_demo_catalog",
    "build_registry_projection",
    "card_hash",
    "drifted_card_fields",
    "ensure_live_demo_inventory",
    "foundry_card_projection",
    "load_consumer_assets",
    "load_consumer_runtime_assets",
    "render_registry_projection",
    "served_card_projection",
]


def __getattr__(name: str) -> Any:
    """Avoid loading cloud adapters inside generated hosted-agent artifacts."""

    if name in {"FoundryAgentRecord", "FoundryCardState", "FoundryCatalogAdapter"}:
        from .adapters import FoundryAgentRecord, FoundryCardState, FoundryCatalogAdapter

        return {
            "FoundryAgentRecord": FoundryAgentRecord,
            "FoundryCardState": FoundryCardState,
            "FoundryCatalogAdapter": FoundryCatalogAdapter,
        }[name]
    if name in {
        "CardProjectionError",
        "authored_card_projection",
        "card_hash",
        "drifted_card_fields",
        "foundry_card_projection",
        "served_card_projection",
    }:
        from .cards import (
            CardProjectionError,
            authored_card_projection,
            card_hash,
            drifted_card_fields,
            foundry_card_projection,
            served_card_projection,
        )

        return {
            "CardProjectionError": CardProjectionError,
            "authored_card_projection": authored_card_projection,
            "card_hash": card_hash,
            "drifted_card_fields": drifted_card_fields,
            "foundry_card_projection": foundry_card_projection,
            "served_card_projection": served_card_projection,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
