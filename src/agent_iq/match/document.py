"""Build and explain the public catalog text used by Match."""

from __future__ import annotations

from dataclasses import dataclass

from agent_iq.contracts import AgentDescriptor

from .lexical import tokenize


def search_document(agent: AgentDescriptor) -> str:
    """Flatten only public discovery fields from a canonical descriptor."""

    parts: list[str] = [agent.name, agent.description]
    for skill in agent.skills:
        parts.extend(
            [
                skill.name,
                skill.description,
                *skill.tags,
                *skill.examples,
            ]
        )
    return "\n".join(part for part in parts if part)


@dataclass(frozen=True, slots=True)
class MatchEvidence:
    fields: tuple[str, ...]
    skill_ids: tuple[str, ...]
    tags: tuple[str, ...]


def explain_public_matches(query: str, agent: AgentDescriptor) -> MatchEvidence:
    """Identify public fields with lexical overlap for an auditable explanation."""

    query_tokens = set(tokenize(query))
    fields: list[str] = []
    skill_ids: list[str] = []
    tags: list[str] = []

    if query_tokens & set(tokenize(agent.name)):
        fields.append("name")
    if query_tokens & set(tokenize(agent.description)):
        fields.append("description")
    if query_tokens & set(tokenize(" ".join(agent.tags))):
        fields.append("tags")
        tags.extend(tag for tag in agent.tags if query_tokens & set(tokenize(tag)))

    for skill in agent.skills:
        skill_text = " ".join([skill.name, skill.description, *skill.tags, *skill.examples])
        if not query_tokens & set(tokenize(skill_text)):
            continue
        skill_ids.append(skill.skill_id)
        fields.append(f"skills.{skill.skill_id}")
        tags.extend(tag for tag in skill.tags if query_tokens & set(tokenize(tag)))

    return MatchEvidence(
        fields=tuple(dict.fromkeys(fields)),
        skill_ids=tuple(dict.fromkeys(skill_ids)),
        tags=tuple(dict.fromkeys(tags)),
    )
