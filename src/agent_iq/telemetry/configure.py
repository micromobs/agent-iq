"""Content-safe Azure Monitor OpenTelemetry bootstrap."""

from __future__ import annotations

import os
import threading

_LOCK = threading.Lock()
_CONFIGURED = False


def configure_agent_iq_telemetry() -> bool:
    """Configure Azure Monitor when a connection string is injected at runtime."""

    global _CONFIGURED
    connection_string = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not connection_string:
        return False
    with _LOCK:
        if _CONFIGURED:
            return True
        from azure.monitor.opentelemetry import configure_azure_monitor
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource

        configure_azure_monitor(
            connection_string=connection_string,
            resource=Resource.create({SERVICE_NAME: "agent-iq-orchestrator"}),
        )
        _CONFIGURED = True
        return True
