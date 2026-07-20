"""Canonical authored and served Agent Card projections."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from agent_iq.contracts import EVIDENCE_EXTENSION_URI, AgentDescriptor, TransportProtocol


class CardProjectionError(ValueError):
    """A served Agent Card cannot be projected onto canonical authored fields."""


def authored_card_projection(descriptor: AgentDescriptor) -> dict[str, Any]:
    """Build the environment-independent card fields authored by Agent IQ."""

    projection: dict[str, Any] = {
        "description": descriptor.description,
        "version": descriptor.version.version,
        "skills": [
            {
                "id": skill.skill_id,
                "name": skill.name,
                "description": skill.description,
                "tags": list(skill.tags),
                "examples": list(skill.examples),
            }
            for skill in descriptor.skills
        ],
    }
    evidence = next(
        (
            capability.evidence
            for capability in descriptor.capabilities
            if capability.protocol is TransportProtocol.A2A and capability.evidence is not None
        ),
        None,
    )
    if evidence is not None:
        projection["capabilities"] = {
            "extensions": [
                {
                    "uri": EVIDENCE_EXTENSION_URI,
                    "description": (
                        "Machine-verifiable Agent IQ evidence correlated by invocation ID."
                    ),
                    "required": False,
                    "params": evidence.model_dump(mode="json", by_alias=True),
                }
            ]
        }
    return projection


def foundry_card_projection(descriptor: AgentDescriptor) -> dict[str, Any]:
    """Project only Agent Card fields accepted by the Foundry endpoint API."""

    projection = authored_card_projection(descriptor)
    projection.pop("capabilities", None)
    return projection


def served_card_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Remove dynamic transport fields while validating canonical card structure."""

    raw_skills = payload.get("skills")
    if not isinstance(raw_skills, list):
        raise CardProjectionError("Agent Card skills must be an array.")
    skills: list[dict[str, Any]] = []
    for raw_skill in raw_skills:
        if not isinstance(raw_skill, dict):
            raise CardProjectionError("Every Agent Card skill must be an object.")
        skills.append(
            {
                "id": raw_skill.get("id"),
                "name": raw_skill.get("name"),
                "description": raw_skill.get("description"),
                "tags": raw_skill.get("tags", []),
                "examples": raw_skill.get("examples", []),
            }
        )
    projection: dict[str, Any] = {
        "description": payload.get("description"),
        "version": payload.get("version"),
        "skills": skills,
    }
    raw_capabilities = payload.get("capabilities")
    if isinstance(raw_capabilities, Mapping):
        raw_extensions = raw_capabilities.get("extensions")
        if isinstance(raw_extensions, list):
            evidence_extensions = [
                extension
                for extension in raw_extensions
                if isinstance(extension, Mapping) and extension.get("uri") == EVIDENCE_EXTENSION_URI
            ]
            if evidence_extensions:
                projection["capabilities"] = {
                    "extensions": [dict(extension) for extension in evidence_extensions]
                }
    return projection


def card_hash(payload: Mapping[str, Any]) -> str:
    """Return a deterministic SHA-256 digest for safe drift evidence."""

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"sha256:{hashlib.sha256(encoded.encode('ascii')).hexdigest()}"


def drifted_card_fields(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
) -> tuple[str, ...]:
    """List only top-level public fields that differ."""

    return tuple(
        field
        for field in sorted(set(expected) | set(actual))
        if expected.get(field) != actual.get(field)
    )
