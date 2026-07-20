"""Strict loaders and cross-file validation for canonical catalog assets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from .assets import (
    AgentCatalogAsset,
    DiscoveryCase,
    KnowledgeBindingsAsset,
    KnowledgeDocument,
    RuntimeCatalogAsset,
    SapFixtureAsset,
)

DEFAULT_CONSUMER_DATA = Path(__file__).resolve().parents[1] / "data" / "consumer"


class CatalogDataError(ValueError):
    """An invalid or inconsistent local catalog asset."""


@dataclass(frozen=True, slots=True)
class ConsumerAssets:
    catalog: AgentCatalogAsset
    runtime: RuntimeCatalogAsset
    knowledge_bindings: KnowledgeBindingsAsset
    discovery_cases: tuple[DiscoveryCase, ...]
    documents: tuple[KnowledgeDocument, ...]
    sap_fixtures: SapFixtureAsset


@dataclass(frozen=True, slots=True)
class ConsumerRuntimeAssets:
    """Minimum canonical assets required inside one hosted consumer agent."""

    catalog: AgentCatalogAsset
    runtime: RuntimeCatalogAsset
    knowledge_bindings: KnowledgeBindingsAsset
    sap_fixtures: SapFixtureAsset


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CatalogDataError(f"Cannot read catalog asset {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogDataError(
            f"Invalid JSON in {path} at line {exc.lineno}, column {exc.colno}."
        ) from exc


def _validate_model(path: Path, model: type[BaseModel]) -> BaseModel:
    try:
        return model.model_validate(_load_json(path))
    except ValidationError as exc:
        raise CatalogDataError(f"Invalid catalog asset {path}: {exc}") from exc


def load_agent_catalog(path: Path) -> AgentCatalogAsset:
    return AgentCatalogAsset.model_validate(_validate_model(path, AgentCatalogAsset))


def load_runtime_catalog(path: Path) -> RuntimeCatalogAsset:
    return RuntimeCatalogAsset.model_validate(_validate_model(path, RuntimeCatalogAsset))


def load_knowledge_bindings(path: Path) -> KnowledgeBindingsAsset:
    return KnowledgeBindingsAsset.model_validate(_validate_model(path, KnowledgeBindingsAsset))


def load_sap_fixtures(path: Path) -> SapFixtureAsset:
    return SapFixtureAsset.model_validate(_validate_model(path, SapFixtureAsset))


def _load_jsonl(path: Path, model: type[BaseModel]) -> tuple[BaseModel, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CatalogDataError(f"Cannot read catalog asset {path}: {exc}") from exc

    records: list[BaseModel] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            records.append(model.model_validate_json(line))
        except ValidationError as exc:
            raise CatalogDataError(
                f"Invalid record in {path} at line {line_number}: {exc}"
            ) from exc
    return tuple(records)


def load_discovery_cases(path: Path) -> tuple[DiscoveryCase, ...]:
    records = _load_jsonl(path, DiscoveryCase)
    cases = tuple(DiscoveryCase.model_validate(record) for record in records)
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise CatalogDataError("Discovery case IDs must be unique.")
    return cases


def load_corpus_directory(path: Path) -> tuple[KnowledgeDocument, ...]:
    documents: list[KnowledgeDocument] = []
    for corpus_path in sorted(path.glob("*.jsonl")):
        records = _load_jsonl(corpus_path, KnowledgeDocument)
        documents.extend(KnowledgeDocument.model_validate(record) for record in records)
    if not documents:
        raise CatalogDataError(f"No corpus documents found in {path}.")
    document_ids = [document.document_id for document in documents]
    if len(document_ids) != len(set(document_ids)):
        raise CatalogDataError("Knowledge document IDs must be unique.")
    return tuple(documents)


def load_consumer_assets(root: Path = DEFAULT_CONSUMER_DATA) -> ConsumerAssets:
    runtime_assets = load_consumer_runtime_assets(root)
    assets = ConsumerAssets(
        catalog=runtime_assets.catalog,
        runtime=runtime_assets.runtime,
        knowledge_bindings=runtime_assets.knowledge_bindings,
        discovery_cases=load_discovery_cases(root / "discovery_cases.jsonl"),
        documents=load_corpus_directory(root / "corpora"),
        sap_fixtures=runtime_assets.sap_fixtures,
    )
    _validate_consumer_relationships(assets)
    return assets


def load_consumer_runtime_assets(
    root: Path = DEFAULT_CONSUMER_DATA,
) -> ConsumerRuntimeAssets:
    """Load only assets needed to construct a hosted runtime."""

    assets = ConsumerRuntimeAssets(
        catalog=load_agent_catalog(root / "agents.json"),
        runtime=load_runtime_catalog(root / "runtime.json"),
        knowledge_bindings=load_knowledge_bindings(root / "knowledge_bases.json"),
        sap_fixtures=load_sap_fixtures(root / "sap_fixtures.json"),
    )
    _validate_runtime_relationships(assets)
    return assets


def _validate_runtime_relationships(assets: ConsumerRuntimeAssets) -> None:
    agent_ids = {agent.agent_id for agent in assets.catalog.agents}
    runtime_by_agent = {agent.agent_id: agent for agent in assets.runtime.agents}
    if set(runtime_by_agent) != agent_ids:
        raise CatalogDataError("Catalog and runtime agent ID sets must match exactly.")

    binding_by_id = {
        binding.knowledge_binding_id: binding for binding in assets.knowledge_bindings.bindings
    }
    for agent in assets.catalog.agents:
        runtime = runtime_by_agent[agent.agent_id]
        if runtime.knowledge_binding_id not in binding_by_id:
            raise CatalogDataError("Every runtime knowledge binding must be declared.")
        if agent.knowledge_binding_ids != (runtime.knowledge_binding_id,):
            raise CatalogDataError("Catalog and runtime knowledge bindings must match.")


def _validate_consumer_relationships(assets: ConsumerAssets) -> None:
    _validate_runtime_relationships(
        ConsumerRuntimeAssets(
            catalog=assets.catalog,
            runtime=assets.runtime,
            knowledge_bindings=assets.knowledge_bindings,
            sap_fixtures=assets.sap_fixtures,
        )
    )
    agent_ids = {agent.agent_id for agent in assets.catalog.agents}
    corpus_ids = {document.corpus_id for document in assets.documents}
    for binding in assets.knowledge_bindings.bindings:
        if set(binding.corpus_ids) - corpus_ids:
            raise CatalogDataError("Every knowledge binding corpus must have documents.")

    expected_ids = {
        agent_id for case in assets.discovery_cases for agent_id in case.expected_agent_ids
    }
    unknown_expected = expected_ids - agent_ids
    if unknown_expected:
        raise CatalogDataError(
            f"Discovery case references unknown agent {sorted(unknown_expected)[0]}."
        )
    uncovered = agent_ids - expected_ids
    if uncovered:
        raise CatalogDataError(f"Agent lacks a discovery case: {sorted(uncovered)[0]}.")
