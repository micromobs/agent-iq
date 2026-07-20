"""Self-contained MCP App resources for Agent IQ."""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files

FORMATION_RESOURCE_URI = "ui://agent-iq/formation.html"
FORMATION_MIME_TYPE = "text/html;profile=mcp-app"
EMPLOYEE_OPERATIONS_RESOURCE_URI = "ui://agent-iq/employee-operations.html"
EMPLOYEE_OPERATIONS_MIME_TYPE = "text/html;profile=mcp-app"
HISTORICAL_RESOURCE_TEMPLATE_URI = "ui://agent-iq/{legacy_widget}.html"
LEGACY_VACATION_RING_RESOURCE_URI = "ui://agent-iq/vacation-ring.html"
VACATION_RING_RESOURCE_URI = "ui://agent-iq/vacation-ring-v2.html"
VACATION_RING_MIME_TYPE = "text/html;profile=mcp-app"


@lru_cache(maxsize=1)
def formation_widget_html() -> str:
    """Load the packaged Agent IQ formation view."""
    return files("agent_iq.api").joinpath("widgets", "formation.html").read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def employee_operations_widget_html() -> str:
    """Load the packaged Agent IQ employee operations view."""

    return (
        files("agent_iq.api")
        .joinpath("widgets", "employee-operations.html")
        .read_text(encoding="utf-8")
    )


@lru_cache(maxsize=1)
def vacation_ring_widget_html() -> str:
    """Load the packaged compact vacation-ring view."""

    return (
        files("agent_iq.api").joinpath("widgets", "vacation-ring.html").read_text(encoding="utf-8")
    )


@lru_cache(maxsize=1)
def historical_widget_html() -> str:
    """Load the safe compatibility view for a retired historical resource."""

    return (
        files("agent_iq.api")
        .joinpath("widgets", "historical-result.html")
        .read_text(encoding="utf-8")
    )
