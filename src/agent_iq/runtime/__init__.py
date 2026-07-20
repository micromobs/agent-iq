"""Agent invocation and durable task runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .adapters import NativeA2ARuntime, NegotiatedAgentCard
    from .dispatcher import NativeA2AInvoker, NativeOnlyDispatcher
    from .models import IdempotencyPolicy, NativeA2ARequest, NativeA2AResult
    from .telemetry import (
        LoggingToolTelemetry,
        MemoryRuntimeTelemetry,
        MemoryToolTelemetry,
        NullRuntimeTelemetry,
        NullToolTelemetry,
        RuntimeTelemetryEvent,
        RuntimeTelemetrySink,
        ToolTelemetryEvent,
        ToolTelemetrySink,
    )

__all__ = [
    "IdempotencyPolicy",
    "LoggingToolTelemetry",
    "MemoryRuntimeTelemetry",
    "MemoryToolTelemetry",
    "NativeA2AInvoker",
    "NativeA2ARequest",
    "NativeA2AResult",
    "NativeA2ARuntime",
    "NativeOnlyDispatcher",
    "NegotiatedAgentCard",
    "NullRuntimeTelemetry",
    "NullToolTelemetry",
    "RuntimeTelemetryEvent",
    "RuntimeTelemetrySink",
    "ToolTelemetryEvent",
    "ToolTelemetrySink",
]


def __getattr__(name: str) -> Any:
    """Keep hosted-runtime imports lean while preserving the public API."""

    if name in {"NativeA2ARuntime", "NegotiatedAgentCard"}:
        from .adapters import NativeA2ARuntime, NegotiatedAgentCard

        return {
            "NativeA2ARuntime": NativeA2ARuntime,
            "NegotiatedAgentCard": NegotiatedAgentCard,
        }[name]
    if name in {"NativeA2AInvoker", "NativeOnlyDispatcher"}:
        from .dispatcher import NativeA2AInvoker, NativeOnlyDispatcher

        return {
            "NativeA2AInvoker": NativeA2AInvoker,
            "NativeOnlyDispatcher": NativeOnlyDispatcher,
        }[name]
    if name in {"IdempotencyPolicy", "NativeA2ARequest", "NativeA2AResult"}:
        from .models import IdempotencyPolicy, NativeA2ARequest, NativeA2AResult

        return {
            "IdempotencyPolicy": IdempotencyPolicy,
            "NativeA2ARequest": NativeA2ARequest,
            "NativeA2AResult": NativeA2AResult,
        }[name]
    if name in {
        "LoggingToolTelemetry",
        "MemoryRuntimeTelemetry",
        "MemoryToolTelemetry",
        "NullRuntimeTelemetry",
        "NullToolTelemetry",
        "RuntimeTelemetryEvent",
        "RuntimeTelemetrySink",
        "ToolTelemetryEvent",
        "ToolTelemetrySink",
    }:
        from .telemetry import (
            LoggingToolTelemetry,
            MemoryRuntimeTelemetry,
            MemoryToolTelemetry,
            NullRuntimeTelemetry,
            NullToolTelemetry,
            RuntimeTelemetryEvent,
            RuntimeTelemetrySink,
            ToolTelemetryEvent,
            ToolTelemetrySink,
        )

        return {
            "LoggingToolTelemetry": LoggingToolTelemetry,
            "MemoryRuntimeTelemetry": MemoryRuntimeTelemetry,
            "MemoryToolTelemetry": MemoryToolTelemetry,
            "NullRuntimeTelemetry": NullRuntimeTelemetry,
            "NullToolTelemetry": NullToolTelemetry,
            "RuntimeTelemetryEvent": RuntimeTelemetryEvent,
            "RuntimeTelemetrySink": RuntimeTelemetrySink,
            "ToolTelemetryEvent": ToolTelemetryEvent,
            "ToolTelemetrySink": ToolTelemetrySink,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
