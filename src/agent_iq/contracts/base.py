"""Shared primitives for versioned Agent IQ wire contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints

CONTRACT_VERSION = "1.0"
SchemaVersion = Literal["1.0"]
Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=16_384),
]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]
VersionText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]
Sha256Digest = Annotated[
    str,
    StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$"),
]
Timestamp = AwareDatetime


def to_camel(value: str) -> str:
    """Convert internal snake_case fields to stable JSON camelCase."""

    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ContractModel(BaseModel):
    """Strict immutable base for all externally serialized contracts."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


def require_unique_text(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    """Reject duplicate text values while preserving caller-provided order."""

    normalized = [value.casefold() for value in values]
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} must not contain duplicates.")
    return values
