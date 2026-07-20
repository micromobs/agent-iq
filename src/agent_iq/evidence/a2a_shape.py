"""Content-safe shape inspection for official A2A SDK response events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from google.protobuf.json_format import MessageToDict

from agent_iq.runtime.adapters.native_a2a import event_text_from_sdk


@dataclass(frozen=True, slots=True)
class A2AEventShape:
    payload_kind: str
    part_types: tuple[str, ...]
    artifact_count: int
    metadata_keys: tuple[str, ...]
    data_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class A2AShapeSummary:
    event_count: int
    payload_kinds: tuple[str, ...]
    text_part_count: int
    data_part_count: int
    artifact_count: int
    metadata_keys: tuple[str, ...]
    data_keys: tuple[str, ...]

    @property
    def preserves_structured_parts(self) -> bool:
        return self.data_part_count > 0


class A2AEventShapeRecorder:
    """Record event structure while delegating public text extraction."""

    def __init__(self) -> None:
        self.events: list[A2AEventShape] = []

    def __call__(self, event: Any) -> str:
        payload = MessageToDict(event, preserving_proto_field_name=True)
        self.events.append(describe_a2a_event(payload))
        return event_text_from_sdk(event)

    def summary(self) -> A2AShapeSummary:
        part_types = [part_type for event in self.events for part_type in event.part_types]
        return A2AShapeSummary(
            event_count=len(self.events),
            payload_kinds=tuple(dict.fromkeys(event.payload_kind for event in self.events)),
            text_part_count=part_types.count("text"),
            data_part_count=part_types.count("data"),
            artifact_count=sum(event.artifact_count for event in self.events),
            metadata_keys=tuple(
                sorted({key for event in self.events for key in event.metadata_keys})
            ),
            data_keys=tuple(sorted({key for event in self.events for key in event.data_keys})),
        )


def describe_a2a_event(payload: Mapping[str, Any]) -> A2AEventShape:
    payload_kind = next(iter(payload), "unknown")
    parts: list[Mapping[str, Any]] = []
    artifact_count = 0
    body = payload.get(payload_kind)
    if isinstance(body, Mapping):
        if payload_kind == "message":
            parts.extend(_parts(body))
        elif payload_kind == "task":
            artifacts = body.get("artifacts")
            if isinstance(artifacts, list):
                artifact_count = len(artifacts)
                for artifact in artifacts:
                    if isinstance(artifact, Mapping):
                        parts.extend(_parts(artifact))
            status = body.get("status")
            if isinstance(status, Mapping):
                message = status.get("message")
                if isinstance(message, Mapping):
                    parts.extend(_parts(message))
        elif payload_kind == "artifact_update":
            artifact = body.get("artifact")
            if isinstance(artifact, Mapping):
                artifact_count = 1
                parts.extend(_parts(artifact))
        elif payload_kind == "status_update":
            status = body.get("status")
            if isinstance(status, Mapping):
                message = status.get("message")
                if isinstance(message, Mapping):
                    parts.extend(_parts(message))
    part_types = tuple(_part_type(part) for part in parts)
    return A2AEventShape(
        payload_kind=payload_kind,
        part_types=part_types,
        artifact_count=artifact_count,
        metadata_keys=tuple(sorted(_collect_mapping_keys(payload, "metadata"))),
        data_keys=tuple(sorted(_collect_mapping_keys(payload, "data"))),
    )


def _parts(value: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_parts = value.get("parts")
    if not isinstance(raw_parts, list):
        return []
    return [part for part in raw_parts if isinstance(part, Mapping)]


def _part_type(part: Mapping[str, Any]) -> str:
    for candidate in ("text", "data", "raw", "url"):
        if candidate in part:
            return candidate
    return "unknown"


def _collect_mapping_keys(value: Any, field: str) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        nested = value.get(field)
        if isinstance(nested, Mapping):
            keys.update(str(key) for key in nested)
        for child in value.values():
            keys.update(_collect_mapping_keys(child, field))
    elif isinstance(value, list):
        for child in value:
            keys.update(_collect_mapping_keys(child, field))
    return keys
