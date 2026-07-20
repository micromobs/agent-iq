"""Private artifact publication state carried across trusted runtime boundaries."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import SecretStr, TypeAdapter, ValidationError

from agent_iq.contracts.base import Identifier

_TICKET_PATTERN = re.compile(r"apt-[A-Za-z0-9_-]{32,128}")
_PUBLICATION_TICKET_PREFIX = "AGENT_IQ_ARTIFACT_TICKET_V1="
_LEGACY_PUBLICATION_TICKET_PREFIX = "[[agent-iq-artifact-ticket:"
_PUBLICATION_CONTEXT_PREFIX = "AGENT_IQ_ARTIFACT_CONTEXT_V1="
_PUBLICATION_CONTEXT_ID_PREFIX = "aiq-pub-v1|"
_PREFIX_PATTERN = re.compile(r"(?m)^AGENT_IQ_ARTIFACT_TICKET_V1=(apt-[A-Za-z0-9_-]{32,128})\r?\n")
_LEGACY_PREFIX_PATTERN = re.compile(
    r"(?m)^\[\[agent-iq-artifact-ticket:v1:(apt-[A-Za-z0-9_-]{32,128})\]\]\r?\n"
)
_CONTEXT_PATTERN = re.compile(
    r"(?m)^AGENT_IQ_ARTIFACT_CONTEXT_V1="
    r"invocationId:([A-Za-z0-9][A-Za-z0-9._:-]{0,127});"
    r"correlationId:([A-Za-z0-9][A-Za-z0-9._:-]{0,127})\r?\n"
)
_CONTEXT_ID_PATTERN = re.compile(
    r"^aiq-pub-v1\|"
    r"([A-Za-z0-9][A-Za-z0-9._:-]{0,127})\|"
    r"([A-Za-z0-9][A-Za-z0-9._:-]{0,127})$"
)
_IDENTIFIER_ADAPTER = TypeAdapter(Identifier)
PUBLICATION_TICKET_MESSAGE_KEY = "agent_iq_publication_ticket"
PUBLICATION_CONTEXT_MESSAGE_KEY = "agent_iq_publication_context"


@dataclass(frozen=True, slots=True)
class ArtifactPublicationContext:
    """Non-secret coordinates for claiming one pre-authorized publication ticket."""

    invocation_id: str
    correlation_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "invocation_id",
            _validate_identifier(self.invocation_id, "invocation ID"),
        )
        object.__setattr__(
            self,
            "correlation_id",
            _validate_identifier(self.correlation_id, "correlation ID"),
        )


def validate_publication_ticket(ticket: SecretStr | str) -> SecretStr:
    """Validate and wrap one short-lived opaque publication credential."""

    value = ticket.get_secret_value() if isinstance(ticket, SecretStr) else ticket
    if not _TICKET_PATTERN.fullmatch(value):
        raise ValueError("Artifact publication ticket has an invalid format.")
    return SecretStr(value)


def attach_publication_ticket(prompt: str, ticket: SecretStr) -> str:
    """Add a machine prefix that the hosted adapter must strip before model use."""

    validated = validate_publication_ticket(ticket).get_secret_value()
    return f"{_PUBLICATION_TICKET_PREFIX}{validated}\n{prompt}"


def attach_publication_context(
    prompt: str,
    *,
    invocation_id: str,
    correlation_id: str,
) -> str:
    """Add non-secret claim coordinates that the hosted adapter strips before model use."""

    context = ArtifactPublicationContext(
        invocation_id=invocation_id,
        correlation_id=correlation_id,
    )
    return (
        f"{_PUBLICATION_CONTEXT_PREFIX}"
        f"invocationId:{context.invocation_id};"
        f"correlationId:{context.correlation_id}\n"
        f"{prompt}"
    )


def encode_publication_context_id(
    *,
    invocation_id: str,
    correlation_id: str,
) -> str:
    """Encode non-secret claim coordinates into the A2A context identifier."""

    context = ArtifactPublicationContext(
        invocation_id=invocation_id,
        correlation_id=correlation_id,
    )
    return f"{_PUBLICATION_CONTEXT_ID_PREFIX}{context.invocation_id}|{context.correlation_id}"


def decode_publication_context_id(value: object) -> ArtifactPublicationContext | None:
    """Decode Agent IQ coordinates from an A2A context identifier."""

    if not isinstance(value, str) or not value.startswith(_PUBLICATION_CONTEXT_ID_PREFIX):
        return None
    match = _CONTEXT_ID_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("Malformed artifact publication A2A context ID.")
    return ArtifactPublicationContext(
        invocation_id=match.group(1),
        correlation_id=match.group(2),
    )


def publication_ticket_marker_present(text: str) -> bool:
    """Return whether text contains either supported ticket sentinel."""

    return _PUBLICATION_TICKET_PREFIX in text or _LEGACY_PUBLICATION_TICKET_PREFIX in text


def publication_context_marker_present(text: str) -> bool:
    """Return whether text contains the non-secret publication-context sentinel."""

    return _PUBLICATION_CONTEXT_PREFIX in text


def extract_publication_ticket(text: str) -> tuple[str, SecretStr | None]:
    """Strip and return a publication ticket without exposing it to the model."""

    matches = sorted(
        (*_PREFIX_PATTERN.finditer(text), *_LEGACY_PREFIX_PATTERN.finditer(text)),
        key=lambda candidate: candidate.start(),
    )
    if not matches:
        if publication_ticket_marker_present(text):
            raise ValueError("Malformed artifact publication ticket prefix.")
        return text, None
    if len(matches) != 1:
        raise ValueError("Multiple artifact publication ticket prefixes are forbidden.")
    match = matches[0]
    clean = f"{text[: match.start()]}{text[match.end() :]}"
    if not clean.strip():
        raise ValueError("Artifact publication ticket prefix requires a user prompt.")
    if publication_ticket_marker_present(clean):
        raise ValueError("Multiple artifact publication ticket prefixes are forbidden.")
    return clean, validate_publication_ticket(match.group(1))


def extract_publication_context(
    text: str,
) -> tuple[str, ArtifactPublicationContext | None]:
    """Strip and return non-secret coordinates without exposing them to the model."""

    matches = tuple(_CONTEXT_PATTERN.finditer(text))
    if not matches:
        if publication_context_marker_present(text):
            raise ValueError("Malformed artifact publication context prefix.")
        return text, None
    if len(matches) != 1:
        raise ValueError("Multiple artifact publication context prefixes are forbidden.")
    match = matches[0]
    clean = f"{text[: match.start()]}{text[match.end() :]}"
    if not clean.strip():
        raise ValueError("Artifact publication context prefix requires a user prompt.")
    if publication_context_marker_present(clean):
        raise ValueError("Multiple artifact publication context prefixes are forbidden.")
    return clean, ArtifactPublicationContext(
        invocation_id=match.group(1),
        correlation_id=match.group(2),
    )


def _validate_identifier(value: str, label: str) -> str:
    try:
        validated = _IDENTIFIER_ADAPTER.validate_python(value, strict=True)
    except ValidationError as exc:
        raise ValueError(f"Artifact publication {label} has an invalid format.") from exc
    if validated != value:
        raise ValueError(f"Artifact publication {label} has an invalid format.")
    return validated
