"""Deterministic offline projection of the canonical agent catalog."""

from __future__ import annotations

import json
from typing import Any

from .assets import AgentCatalogAsset


def build_registry_projection(catalog: AgentCatalogAsset) -> dict[str, Any]:
    """Return a stable offline registry projection sorted by canonical agent ID."""

    return {
        "schemaVersion": catalog.schema_version,
        "catalogId": catalog.catalog_id,
        "publishedAt": catalog.published_at.isoformat(),
        "agents": [
            agent.model_dump(mode="json", by_alias=True)
            for agent in sorted(catalog.agents, key=lambda item: item.agent_id)
        ],
    }


def render_registry_projection(catalog: AgentCatalogAsset) -> str:
    """Serialize a deterministic projection for tests and offline development."""

    return (
        json.dumps(
            build_registry_projection(catalog),
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
