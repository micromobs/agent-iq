"""Strict vacation-balance projection preserved across the text-only A2A boundary."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from enum import StrEnum
from json import JSONDecodeError
from typing import Annotated, Literal

from pydantic import Field, ValidationError, model_validator

from .base import ContractModel, Identifier, NonEmptyText, SchemaVersion, ShortText, Timestamp

VACATION_BALANCE_JSON_START = "VACATION_BALANCE_JSON_START"
VACATION_BALANCE_JSON_END = "VACATION_BALANCE_JSON_END"

VacationDays = Annotated[Decimal, Field(ge=0, max_digits=6, decimal_places=2)]
PositiveVacationDays = Annotated[Decimal, Field(gt=0, max_digits=6, decimal_places=2)]


class VacationEntryType(StrEnum):
    """Current-year leave states included in the balance calculation."""

    TAKEN = "taken"
    BOOKED = "booked"


class VacationLeaveEntry(ContractModel):
    """One dated leave entry from the synthetic source record."""

    entry_id: Identifier
    entry_type: VacationEntryType
    label: ShortText
    start_date: date
    end_date: date
    days: PositiveVacationDays

    @model_validator(mode="after")
    def validate_dates(self) -> VacationLeaveEntry:
        if self.end_date < self.start_date:
            raise ValueError("A vacation entry cannot end before it starts.")
        return self


class VacationRecommendation(ContractModel):
    """One evidence-backed opportunity to use part of the remaining balance."""

    recommendation_id: Identifier
    title: ShortText
    start_date: date
    end_date: date
    return_date: date
    days_to_book: PositiveVacationDays
    total_days_away: int = Field(ge=1, le=31)
    rationale: NonEmptyText

    @model_validator(mode="after")
    def validate_dates(self) -> VacationRecommendation:
        if self.end_date < self.start_date:
            raise ValueError("A vacation recommendation cannot end before it starts.")
        if self.return_date <= self.end_date:
            raise ValueError("A vacation recommendation return date must follow its end date.")
        return self


class VacationBalance(ContractModel):
    """Validated vacation balance returned by the selected Foundry specialist."""

    schema_version: SchemaVersion = "1.0"
    employee_id: Identifier
    employee_display_name: ShortText
    balance_year: int = Field(ge=2000, le=2100)
    as_of: Timestamp
    entitlement_days: VacationDays
    taken_days: VacationDays
    booked_days: VacationDays
    remaining_days: VacationDays
    current_year_entries: tuple[VacationLeaveEntry, ...] = Field(min_length=1, max_length=20)
    prior_year_entries: tuple[VacationLeaveEntry, ...] = Field(min_length=1, max_length=20)
    recommendations: tuple[VacationRecommendation, ...] = Field(min_length=1, max_length=5)
    synthetic_data: Literal[True] = True
    source_system: ShortText
    source_record_id: Identifier
    source_title: ShortText

    @model_validator(mode="after")
    def validate_balance(self) -> VacationBalance:
        if self.as_of.year != self.balance_year:
            raise ValueError("Vacation balance year must match the as-of timestamp.")
        if self.taken_days + self.booked_days + self.remaining_days != self.entitlement_days:
            raise ValueError(
                "Vacation entitlement must equal taken plus booked plus remaining days."
            )
        if any(
            entry.start_date.year != self.balance_year or entry.end_date.year != self.balance_year
            for entry in self.current_year_entries
        ):
            raise ValueError("Current-year vacation entries must stay within the balance year.")
        if any(entry.end_date.year >= self.balance_year for entry in self.prior_year_entries):
            raise ValueError("Prior-year vacation entries must precede the balance year.")

        taken = sum(
            (
                entry.days
                for entry in self.current_year_entries
                if entry.entry_type is VacationEntryType.TAKEN
            ),
            Decimal(0),
        )
        booked = sum(
            (
                entry.days
                for entry in self.current_year_entries
                if entry.entry_type is VacationEntryType.BOOKED
            ),
            Decimal(0),
        )
        if taken != self.taken_days or booked != self.booked_days:
            raise ValueError("Dated leave entries must reconcile with taken and booked totals.")
        if any(
            recommendation.start_date.year != self.balance_year
            or recommendation.end_date.year != self.balance_year
            or recommendation.days_to_book > self.remaining_days
            for recommendation in self.recommendations
        ):
            raise ValueError(
                "Vacation recommendations must fit the balance year and remaining allowance."
            )
        return self


def parse_vacation_balance(answer: str) -> VacationBalance:
    """Extract and validate the one marked vacation JSON object from an A2A answer."""

    if (
        answer.count(VACATION_BALANCE_JSON_START) != 1
        or answer.count(VACATION_BALANCE_JSON_END) != 1
    ):
        raise ValueError("The A2A answer must contain exactly one marked vacation balance.")
    start = answer.index(VACATION_BALANCE_JSON_START) + len(VACATION_BALANCE_JSON_START)
    end = answer.index(VACATION_BALANCE_JSON_END, start)
    payload = answer[start:end].strip()
    if not payload:
        raise ValueError("The marked vacation balance is empty.")
    try:
        decoded = json.loads(payload)
    except JSONDecodeError as exc:
        raise ValueError("The marked vacation balance is not valid JSON.") from exc
    try:
        return VacationBalance.model_validate(decoded)
    except ValidationError as exc:
        raise ValueError("The marked vacation balance does not satisfy its contract.") from exc
