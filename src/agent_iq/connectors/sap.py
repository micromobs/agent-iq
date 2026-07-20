"""Governed SAP read boundary with an explicitly labelled fixture implementation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Protocol

from agent_iq.catalog.assets import SapFixtureAsset
from agent_iq.contracts import ErrorCode, SapReadResult, SapResourceKind
from agent_iq.errors import operation_error

_RESOURCE_SCOPES = {
    SapResourceKind.DEMAND_FACT: "sap:s4:demand:read",
    SapResourceKind.SALES_ORDER: "sap:s4:sales-orders:read",
    SapResourceKind.INVENTORY_POSITION: "sap:s4:inventory:read",
    SapResourceKind.MATERIAL_FACT: "sap:s4:materials:read",
    SapResourceKind.PRODUCTION_ORDER: "sap:s4:production-orders:read",
    SapResourceKind.PLANT_CAPACITY: "sap:s4:plant-capacity:read",
    SapResourceKind.OPERATIONAL_STATUS: "sap:s4:operations:read",
}
_RESOURCE_ARGUMENTS = {
    SapResourceKind.DEMAND_FACT: {
        "product_id",
        "market",
        "start_date",
        "end_date",
    },
    SapResourceKind.SALES_ORDER: {
        "product_id",
        "market",
        "start_date",
        "end_date",
    },
    SapResourceKind.INVENTORY_POSITION: {"product_id", "location_id"},
    SapResourceKind.MATERIAL_FACT: {"material_id", "plant_id"},
    SapResourceKind.PRODUCTION_ORDER: {
        "product_id",
        "plant_id",
        "start_date",
        "end_date",
    },
    SapResourceKind.PLANT_CAPACITY: {"plant_id", "start_date", "end_date"},
    SapResourceKind.OPERATIONAL_STATUS: {"plant_id", "line_id"},
}
_EXACT_FILTER_FIELDS = {
    "line_id",
    "location_id",
    "market",
    "material_id",
    "plant_id",
    "product_id",
}


class SapReader(Protocol):
    def read(
        self,
        resource_kind: SapResourceKind,
        arguments: Mapping[str, object],
        *,
        authorization_scope: str,
    ) -> SapReadResult: ...


class FixtureSapReader:
    """Read deterministic records while preserving unmistakable fixture provenance."""

    def __init__(self, asset: SapFixtureAsset) -> None:
        self._asset = asset

    def read(
        self,
        resource_kind: SapResourceKind,
        arguments: Mapping[str, object],
        *,
        authorization_scope: str,
    ) -> SapReadResult:
        if authorization_scope != _RESOURCE_SCOPES[resource_kind]:
            raise operation_error(
                ErrorCode.AUTHORIZATION_DENIED,
                "The SAP tool grant does not authorize the requested resource.",
                correlation_id=resource_kind.value,
            )
        _validate_arguments(arguments, resource_kind)
        records = tuple(
            record
            for record in self._asset.records
            if record.resource_kind is resource_kind
            and _matches(record.model_dump(mode="python", by_alias=False), arguments)
        )
        if not records:
            raise operation_error(
                ErrorCode.EVIDENCE_UNAVAILABLE,
                "The deterministic SAP fixture has no matching record.",
                correlation_id=resource_kind.value,
            )
        if any(record.reference.authorization_scope != authorization_scope for record in records):
            raise operation_error(
                ErrorCode.AUTHORIZATION_DENIED,
                "The SAP record authorization scope does not match the tool grant.",
                correlation_id=resource_kind.value,
            )
        return SapReadResult(
            source_mode=records[0].reference.source_mode,
            source_label=self._asset.source_label,
            resource_kind=resource_kind,
            records=records,
            retrieved_at=self._asset.generated_at,
            authorization_scope=authorization_scope,
        )


def _matches(record: Mapping[str, object], arguments: Mapping[str, object]) -> bool:
    for field in _EXACT_FILTER_FIELDS & arguments.keys():
        expected = arguments[field]
        actual = record.get(field)
        if actual is None or str(actual).casefold() != str(expected).casefold():
            return False
    return _overlaps_requested_range(record, arguments)


def _validate_arguments(
    arguments: Mapping[str, object],
    resource_kind: SapResourceKind,
) -> None:
    expected = _RESOURCE_ARGUMENTS[resource_kind]
    unsupported = set(arguments) - expected
    if unsupported:
        raise operation_error(
            ErrorCode.INVALID_REQUEST,
            f"Unsupported SAP filter {sorted(unsupported)[0]!r}.",
            correlation_id=resource_kind.value,
        )
    missing = expected - set(arguments)
    if missing:
        raise operation_error(
            ErrorCode.INVALID_REQUEST,
            f"Missing required SAP filter {sorted(missing)[0]!r}.",
            correlation_id=resource_kind.value,
        )
    start = _date_argument(arguments.get("start_date"))
    end = _date_argument(arguments.get("end_date"))
    if arguments.get("start_date") is not None and start is None:
        raise operation_error(
            ErrorCode.INVALID_REQUEST,
            "SAP start_date must be an ISO-8601 date.",
            correlation_id=resource_kind.value,
        )
    if arguments.get("end_date") is not None and end is None:
        raise operation_error(
            ErrorCode.INVALID_REQUEST,
            "SAP end_date must be an ISO-8601 date.",
            correlation_id=resource_kind.value,
        )
    if start is not None and end is not None and start > end:
        raise operation_error(
            ErrorCode.INVALID_REQUEST,
            "SAP start_date must not be later than end_date.",
            correlation_id=resource_kind.value,
        )


def _overlaps_requested_range(
    record: Mapping[str, object],
    arguments: Mapping[str, object],
) -> bool:
    requested_start = _date_argument(arguments.get("start_date"))
    requested_end = _date_argument(arguments.get("end_date"))
    if requested_start is None and requested_end is None:
        return True
    record_start = _first_date(record, "period_start", "requested_date", "scheduled_start")
    record_end = _first_date(record, "period_end", "requested_date", "scheduled_end")
    if record_start is None or record_end is None:
        return False
    if requested_start is not None and record_end < requested_start:
        return False
    return requested_end is None or record_start <= requested_end


def _first_date(record: Mapping[str, object], *keys: str) -> date | None:
    for key in keys:
        value = record.get(key)
        parsed = _date_argument(value)
        if parsed is not None:
            return parsed
    return None


def _date_argument(value: object | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None
