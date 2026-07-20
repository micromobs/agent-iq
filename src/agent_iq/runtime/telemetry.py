"""Safe structured telemetry hooks for the native A2A runtime."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, TypeAlias

TelemetryValue: TypeAlias = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class RuntimeTelemetryEvent:
    name: str
    occurred_at: datetime
    invocation_id: str
    agent_id: str
    attempt: int
    attributes: tuple[tuple[str, TelemetryValue], ...] = ()

    @classmethod
    def create(
        cls,
        name: str,
        *,
        invocation_id: str,
        agent_id: str,
        attempt: int,
        **attributes: TelemetryValue,
    ) -> RuntimeTelemetryEvent:
        return cls(
            name=name,
            occurred_at=datetime.now(UTC),
            invocation_id=invocation_id,
            agent_id=agent_id,
            attempt=attempt,
            attributes=tuple(sorted(attributes.items())),
        )


class RuntimeTelemetrySink(Protocol):
    async def emit(self, event: RuntimeTelemetryEvent) -> None: ...


class NullRuntimeTelemetry:
    async def emit(self, event: RuntimeTelemetryEvent) -> None:
        del event


class MemoryRuntimeTelemetry:
    """Test and diagnostic sink; events contain metadata, never prompt or answer text."""

    def __init__(self) -> None:
        self.events: list[RuntimeTelemetryEvent] = []

    async def emit(self, event: RuntimeTelemetryEvent) -> None:
        self.events.append(event)


@dataclass(frozen=True, slots=True)
class ToolTelemetryEvent:
    """Content-safe tool execution metadata with no arguments or result text."""

    occurred_at: datetime
    agent_id: str
    tool_id: str
    backend: str
    status: str
    duration_ms: int | None
    cost_units: float | None
    cost_capture_status: str
    source_mode: str | None = None
    arguments_captured: bool = False

    @classmethod
    def create(
        cls,
        *,
        agent_id: str,
        tool_id: str,
        backend: str,
        status: str,
        duration_ms: int | None,
        cost_units: float | None,
        cost_capture_status: str,
        source_mode: str | None = None,
    ) -> ToolTelemetryEvent:
        return cls(
            occurred_at=datetime.now(UTC),
            agent_id=agent_id,
            tool_id=tool_id,
            backend=backend,
            status=status,
            duration_ms=duration_ms,
            cost_units=cost_units,
            cost_capture_status=cost_capture_status,
            source_mode=source_mode,
        )


class ToolTelemetrySink(Protocol):
    async def emit(self, event: ToolTelemetryEvent) -> None: ...


class NullToolTelemetry:
    async def emit(self, event: ToolTelemetryEvent) -> None:
        del event


class LoggingToolTelemetry:
    """Emit content-safe JSON that hosted runtime logging can collect."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("agent_iq.tool")

    async def emit(self, event: ToolTelemetryEvent) -> None:
        payload = {
            "event": "agent_iq.tool",
            "occurredAt": event.occurred_at.isoformat().replace("+00:00", "Z"),
            "agentId": event.agent_id,
            "toolId": event.tool_id,
            "backend": event.backend,
            "status": event.status,
            "durationMs": event.duration_ms,
            "costUnits": event.cost_units,
            "costCaptureStatus": event.cost_capture_status,
            "sourceMode": event.source_mode,
            "argumentsCaptured": event.arguments_captured,
        }
        self._logger.info(json.dumps(payload, sort_keys=True, ensure_ascii=True))


class MemoryToolTelemetry:
    def __init__(self) -> None:
        self.events: list[ToolTelemetryEvent] = []

    async def emit(self, event: ToolTelemetryEvent) -> None:
        self.events.append(event)
