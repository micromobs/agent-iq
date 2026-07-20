"""Strict loaders, cross-file validation, and deterministic resolvers.

This module is the only place that reads the employee showcase JSON data
files from disk. Everything downstream (presentation building, intent
matching) works from the validated, immutable Pydantic assets produced here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .assets import (
    EmployeeAgentCatalogAsset,
    EmployeeKnowledgeSourceAsset,
    EmployeeScenarioCatalogAsset,
    ScenarioDefinition,
)
from .contracts import (
    EmployeeAgentGroup,
    EmployeeAgentPresentation,
    EmployeeCatalogRequest,
    EmployeeExperiencePresentation,
    EmployeeGroupSummary,
    EmployeeIntentMatch,
    EmployeeScenarioCard,
    EmployeeScenarioCatalogPresentation,
    EmployeeScenarioRequest,
    EmployeeSelectedAgentPresentation,
)

DEFAULT_EMPLOYEE_DATA = Path(__file__).resolve().parents[1] / "data" / "employee"

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "to",
        "for",
        "of",
        "on",
        "in",
        "and",
        "or",
        "is",
        "are",
        "my",
        "me",
        "i",
        "with",
        "this",
        "that",
        "it",
        "please",
        "can",
        "you",
        "help",
        "need",
        "about",
        "what",
        "how",
        "do",
        "does",
        "did",
        "was",
        "were",
        "be",
        "has",
        "have",
        "just",
        "get",
        "some",
        "our",
        "your",
        "im",
        "ive",
    }
)
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


class EmployeeDataError(ValueError):
    """An invalid or inconsistent employee showcase asset or request."""


@dataclass(frozen=True, slots=True)
class EmployeeCatalogAssets:
    """The full validated, cross-checked employee showcase asset set."""

    agents: EmployeeAgentCatalogAsset
    scenarios: EmployeeScenarioCatalogAsset
    knowledge_sources: EmployeeKnowledgeSourceAsset


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EmployeeDataError(f"Cannot read employee showcase asset {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise EmployeeDataError(
            f"Invalid JSON in {path} at line {exc.lineno}, column {exc.colno}."
        ) from exc


def _load_jsonl(path: Path) -> list[Any]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EmployeeDataError(f"Cannot read employee showcase asset {path}: {exc}") from exc

    records: list[Any] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise EmployeeDataError(f"Invalid JSON in {path} at line {line_number}: {exc}") from exc
    return records


def load_employee_agent_catalog(path: Path) -> EmployeeAgentCatalogAsset:
    try:
        return EmployeeAgentCatalogAsset.model_validate(_load_json(path))
    except ValidationError as exc:
        raise EmployeeDataError(f"Invalid employee agent catalog {path}: {exc}") from exc


def load_employee_scenario_catalog(path: Path) -> EmployeeScenarioCatalogAsset:
    try:
        payload = {
            "schemaVersion": "1.0",
            "catalogId": "employee-scenarios",
            **_scenario_envelope(path),
        }
        return EmployeeScenarioCatalogAsset.model_validate(payload)
    except ValidationError as exc:
        raise EmployeeDataError(f"Invalid employee scenario catalog {path}: {exc}") from exc


def _scenario_envelope(path: Path) -> dict[str, Any]:
    records = _load_jsonl(path)
    if not records:
        raise EmployeeDataError(f"No scenario records found in {path}.")
    return {"publishedAt": "2025-01-01T00:00:00Z", "scenarios": records}


def load_employee_knowledge_sources(path: Path) -> EmployeeKnowledgeSourceAsset:
    try:
        return EmployeeKnowledgeSourceAsset.model_validate(_load_json(path))
    except ValidationError as exc:
        raise EmployeeDataError(f"Invalid employee knowledge sources {path}: {exc}") from exc


def load_employee_assets(root: Path = DEFAULT_EMPLOYEE_DATA) -> EmployeeCatalogAssets:
    """Load and cross-validate every employee showcase asset from disk."""

    agents = load_employee_agent_catalog(root / "agents.json")
    scenarios = load_employee_scenario_catalog(root / "scenarios.jsonl")
    knowledge_sources = load_employee_knowledge_sources(root / "knowledge_sources.json")
    assets = EmployeeCatalogAssets(
        agents=agents, scenarios=scenarios, knowledge_sources=knowledge_sources
    )
    _validate_cross_references(assets)
    return assets


def _validate_cross_references(assets: EmployeeCatalogAssets) -> None:
    agent_ids = {agent.agent_id for agent in assets.agents.agents}
    source_ids = {source.knowledge_source_id for source in assets.knowledge_sources.sources}

    for agent in assets.agents.agents:
        unknown = set(agent.knowledge_source_ids) - source_ids
        if unknown:
            raise EmployeeDataError(
                f"Agent '{agent.agent_id}' references unknown knowledge source "
                f"{sorted(unknown)[0]}."
            )

    for scenario in assets.scenarios.scenarios:
        unknown_agents = set(scenario.agent_ids) - agent_ids
        if unknown_agents:
            raise EmployeeDataError(
                f"Scenario '{scenario.scenario_id}' references unknown agent "
                f"{sorted(unknown_agents)[0]}."
            )
        unknown_sources = {source.knowledge_source_id for source in scenario.sources} - source_ids
        if unknown_sources:
            raise EmployeeDataError(
                f"Scenario '{scenario.scenario_id}' references unknown knowledge source "
                f"{sorted(unknown_sources)[0]}."
            )


@lru_cache(maxsize=1)
def load_employee_catalog(root: Path | None = None) -> EmployeeCatalogAssets:
    """Load and cache the employee showcase assets (singleton, offline-safe)."""

    return load_employee_assets(root or DEFAULT_EMPLOYEE_DATA)


def _agent_presentation_by_id(
    assets: EmployeeCatalogAssets,
) -> dict[str, EmployeeAgentPresentation]:
    return {agent.agent_id: agent for agent in assets.agents.agents}


def _build_group_summaries(
    assets: EmployeeCatalogAssets,
) -> tuple[EmployeeGroupSummary, ...]:
    by_group: dict[EmployeeAgentGroup, list[str]] = {group: [] for group in EmployeeAgentGroup}
    for agent in assets.agents.agents:
        by_group[agent.group].append(agent.agent_id)
    return tuple(
        EmployeeGroupSummary(
            group=group,
            agent_count=len(sorted(agent_ids)),
            agent_ids=tuple(sorted(agent_ids)),
        )
        for group, agent_ids in by_group.items()
    )


def build_scenario_catalog_presentation(
    assets: EmployeeCatalogAssets,
) -> EmployeeScenarioCatalogPresentation:
    """Build the compact, whole-catalog presentation for the showcase."""

    cards = tuple(
        EmployeeScenarioCard(
            scenario_id=scenario.scenario_id,
            category=scenario.category,
            title=scenario.title,
            subtitle=scenario.subtitle,
            agent_ids=scenario.agent_ids,
            tags=scenario.tags,
        )
        for scenario in sorted(assets.scenarios.scenarios, key=lambda item: item.scenario_id)
    )
    return EmployeeScenarioCatalogPresentation(
        group_summaries=_build_group_summaries(assets),
        scenarios=cards,
        total_agents=len(assets.agents.agents),
        total_knowledge_sources=len(assets.knowledge_sources.sources),
    )


def _selected_agents_for(
    assets: EmployeeCatalogAssets, scenario: ScenarioDefinition
) -> tuple[EmployeeSelectedAgentPresentation, ...]:
    agents_by_id = _agent_presentation_by_id(assets)
    selected: list[EmployeeSelectedAgentPresentation] = []
    for agent_id in scenario.agent_ids:
        agent = agents_by_id[agent_id]
        selected.append(
            EmployeeSelectedAgentPresentation(
                agent_id=agent.agent_id,
                display_name=agent.display_name,
                glyph=agent.glyph,
                group=agent.group,
                reason=f"{agent.role_summary} Relevant to '{scenario.title}'.",
            )
        )
    return tuple(selected)


def build_experience_presentation(
    assets: EmployeeCatalogAssets, scenario: ScenarioDefinition
) -> EmployeeExperiencePresentation:
    """Build the full rich scenario experience presentation."""

    return EmployeeExperiencePresentation(
        scenario_id=scenario.scenario_id,
        category=scenario.category,
        title=scenario.title,
        subtitle=scenario.subtitle,
        status=scenario.status,
        status_label=scenario.status_label,
        selected_agents=_selected_agents_for(assets, scenario),
        phases=scenario.phases,
        metrics=scenario.metrics,
        sections=scenario.sections,
        sources=scenario.sources,
        actions=scenario.actions,
        suggested_prompts=scenario.suggested_prompts,
    )


def list_employee_scenarios(root: Path | None = None) -> EmployeeScenarioCatalogPresentation:
    """List the compact catalog of all 30 scenarios."""

    return build_scenario_catalog_presentation(load_employee_catalog(root))


def resolve_employee_scenario(
    scenario_id: str, root: Path | None = None
) -> EmployeeExperiencePresentation:
    """Resolve one scenario by its exact ID."""

    assets = load_employee_catalog(root)
    for scenario in assets.scenarios.scenarios:
        if scenario.scenario_id == scenario_id:
            return build_experience_presentation(assets, scenario)
    raise EmployeeDataError(f"Unknown employee showcase scenario: '{scenario_id}'.")


def _tokenize(text: str) -> frozenset[str]:
    tokens = {token for token in _TOKEN_PATTERN.findall(text.casefold())}
    return frozenset(tokens - _STOPWORDS)


def _score_scenario(
    scenario: ScenarioDefinition, tokens: frozenset[str]
) -> tuple[float, tuple[str, ...]]:
    """Return a transparent (score, matched_terms) pair for one scenario.

    Tag matches are weighted heaviest, then example-prompt overlap, then
    title/subtitle overlap. The score is normalized to [0, 1] against the
    maximum weight the scenario could have earned, so it is comparable across
    scenarios with different amounts of authored text.
    """

    matched: set[str] = set()
    weighted = 0.0
    max_possible = 0.0

    for tag in scenario.tags:
        max_possible += 3.0
        if _tokenize(tag) & tokens:
            weighted += 3.0
            matched.add(tag)

    example_tokens = _tokenize(" ".join(scenario.examples)) | _tokenize(scenario.prompt)
    max_possible += max(len(example_tokens), 1) * 1.0
    overlap = example_tokens & tokens
    if overlap:
        weighted += 1.0 * len(overlap)
        matched.update(overlap)

    title_tokens = _tokenize(scenario.title) | _tokenize(scenario.subtitle)
    max_possible += max(len(title_tokens), 1) * 0.5
    overlap = title_tokens & tokens
    if overlap:
        weighted += 0.5 * len(overlap)
        matched.update(overlap)

    score = weighted / max_possible if max_possible else 0.0
    return min(score, 1.0), tuple(sorted(matched))


def match_employee_intent(
    intent: str, root: Path | None = None, limit: int = 5
) -> tuple[EmployeeIntentMatch, ...]:
    """Deterministically rank every scenario against a free-text intent.

    Ties are broken by scenario ID (ascending) so results are stable across
    runs and across environments.
    """

    assets = load_employee_catalog(root)
    tokens = _tokenize(intent)
    scored: list[tuple[float, str, tuple[str, ...]]] = []
    for scenario in assets.scenarios.scenarios:
        score, matched_terms = _score_scenario(scenario, tokens)
        scored.append((score, scenario.scenario_id, matched_terms))

    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return tuple(
        EmployeeIntentMatch(scenario_id=scenario_id, rank=rank, score=score, matched_terms=terms)
        for rank, (score, scenario_id, terms) in enumerate(scored[:limit], start=1)
    )


def resolve_employee_request(
    request: EmployeeScenarioRequest | EmployeeCatalogRequest, root: Path | None = None
) -> EmployeeExperiencePresentation | EmployeeScenarioCatalogPresentation:
    """Resolve a request to either a full experience or the compact catalog."""

    if isinstance(request, EmployeeCatalogRequest):
        return list_employee_scenarios(root)

    if request.scenario_id is not None:
        return resolve_employee_scenario(request.scenario_id, root)

    assert request.intent is not None  # enforced by EmployeeScenarioRequest validator
    matches = match_employee_intent(request.intent, root)
    if not matches or matches[0].score <= 0.0:
        raise EmployeeDataError(
            f"No employee showcase scenario matches intent: '{request.intent}'."
        )
    return resolve_employee_scenario(matches[0].scenario_id, root)
