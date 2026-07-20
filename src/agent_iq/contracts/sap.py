"""Typed, provenance-preserving SAP read contracts."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, Field, model_validator

from .base import ContractModel, Identifier, SchemaVersion, ShortText, Timestamp


class SapSourceMode(StrEnum):
    FIXTURE = "fixture"
    LIVE = "live"


class SapResourceKind(StrEnum):
    DEMAND_FACT = "demand_fact"
    SALES_ORDER = "sales_order"
    INVENTORY_POSITION = "inventory_position"
    MATERIAL_FACT = "material_fact"
    PRODUCTION_ORDER = "production_order"
    PLANT_CAPACITY = "plant_capacity"
    OPERATIONAL_STATUS = "operational_status"


class SapObjectReference(ContractModel):
    system_id: Identifier
    object_type: Identifier
    object_id: ShortText
    deep_link: AnyHttpUrl
    source_mode: SapSourceMode
    retrieved_at: Timestamp
    authorization_scope: ShortText
    audit_id: Identifier


class SapDemandFact(ContractModel):
    resource_kind: Literal[SapResourceKind.DEMAND_FACT] = SapResourceKind.DEMAND_FACT
    product_id: Identifier
    market: ShortText
    period_start: date
    period_end: date
    quantity: float = Field(ge=0)
    unit: ShortText
    reference: SapObjectReference


class SapSalesOrder(ContractModel):
    resource_kind: Literal[SapResourceKind.SALES_ORDER] = SapResourceKind.SALES_ORDER
    order_id: Identifier
    product_id: Identifier
    market: ShortText
    requested_date: date
    quantity: float = Field(gt=0)
    unit: ShortText
    status: ShortText
    reference: SapObjectReference


class SapInventoryPosition(ContractModel):
    resource_kind: Literal[SapResourceKind.INVENTORY_POSITION] = SapResourceKind.INVENTORY_POSITION
    product_id: Identifier
    location_id: Identifier
    available_quantity: float = Field(ge=0)
    allocated_quantity: float = Field(ge=0)
    unit: ShortText
    reference: SapObjectReference


class SapMaterialFact(ContractModel):
    resource_kind: Literal[SapResourceKind.MATERIAL_FACT] = SapResourceKind.MATERIAL_FACT
    material_id: Identifier
    plant_id: Identifier
    description: ShortText
    status: ShortText
    reference: SapObjectReference


class SapProductionOrder(ContractModel):
    resource_kind: Literal[SapResourceKind.PRODUCTION_ORDER] = SapResourceKind.PRODUCTION_ORDER
    production_order_id: Identifier
    product_id: Identifier
    plant_id: Identifier
    scheduled_start: date
    scheduled_end: date
    quantity: float = Field(gt=0)
    unit: ShortText
    status: ShortText
    reference: SapObjectReference


class SapPlantCapacity(ContractModel):
    resource_kind: Literal[SapResourceKind.PLANT_CAPACITY] = SapResourceKind.PLANT_CAPACITY
    plant_id: Identifier
    period_start: date
    period_end: date
    available_hours: float = Field(ge=0)
    committed_hours: float = Field(ge=0)
    reference: SapObjectReference

    @model_validator(mode="after")
    def validate_capacity(self) -> SapPlantCapacity:
        if self.committed_hours > self.available_hours:
            raise ValueError("Committed plant hours cannot exceed available hours.")
        return self


class SapOperationalStatus(ContractModel):
    resource_kind: Literal[SapResourceKind.OPERATIONAL_STATUS] = SapResourceKind.OPERATIONAL_STATUS
    plant_id: Identifier
    line_id: Identifier
    metric: Identifier
    value: float = Field(ge=0)
    unit: ShortText
    status: ShortText
    reference: SapObjectReference


SapReadRecord = Annotated[
    SapDemandFact
    | SapSalesOrder
    | SapInventoryPosition
    | SapMaterialFact
    | SapProductionOrder
    | SapPlantCapacity
    | SapOperationalStatus,
    Field(discriminator="resource_kind"),
]


class SapReadResult(ContractModel):
    schema_version: SchemaVersion = "1.0"
    source_mode: SapSourceMode
    source_label: ShortText
    resource_kind: SapResourceKind
    records: tuple[SapReadRecord, ...] = Field(min_length=1)
    retrieved_at: Timestamp
    authorization_scope: ShortText

    @model_validator(mode="after")
    def validate_source_and_records(self) -> SapReadResult:
        if any(record.resource_kind is not self.resource_kind for record in self.records):
            raise ValueError("SAP result records must match the requested resource kind.")
        if any(record.reference.source_mode is not self.source_mode for record in self.records):
            raise ValueError("SAP result source mode must match every record.")
        if any(
            record.reference.authorization_scope != self.authorization_scope
            for record in self.records
        ):
            raise ValueError("SAP result authorization scope must match every record.")
        if (
            self.source_mode is SapSourceMode.FIXTURE
            and "not live sap" not in self.source_label.casefold()
        ):
            raise ValueError("Fixture-backed SAP results must be visibly labelled NOT LIVE SAP.")
        return self
