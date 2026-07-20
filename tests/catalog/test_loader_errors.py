"""Catalog loaders reject malformed and inconsistent assets explicitly."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from agent_iq.catalog.loader import (
    DEFAULT_CONSUMER_DATA,
    CatalogDataError,
    load_agent_catalog,
    load_consumer_assets,
    load_corpus_directory,
    load_discovery_cases,
)


def copy_assets(tmp_path: Path) -> Path:
    target = tmp_path / "consumer"
    shutil.copytree(DEFAULT_CONSUMER_DATA, target)
    return target


def test_json_loader_reports_syntax_and_validation_errors(tmp_path: Path) -> None:
    path = tmp_path / "agents.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(CatalogDataError, match="Invalid JSON"):
        load_agent_catalog(path)

    path.write_text("{}", encoding="utf-8")
    with pytest.raises(CatalogDataError, match="Invalid catalog asset"):
        load_agent_catalog(path)


def test_json_loader_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(CatalogDataError, match="Cannot read"):
        load_agent_catalog(tmp_path / "missing.json")


def test_jsonl_loader_reports_line_and_duplicate_cases(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(CatalogDataError, match="line 1"):
        load_discovery_cases(path)

    record = {
        "caseId": "case-1",
        "intent": "Find the right agent.",
        "expectedAgentIds": ["agent-1"],
        "note": "Test.",
    }
    line = json.dumps(record)
    path.write_text(f"{line}\n{line}\n", encoding="utf-8")
    with pytest.raises(CatalogDataError, match="case IDs"):
        load_discovery_cases(path)


def test_corpus_loader_rejects_empty_and_duplicate_documents(tmp_path: Path) -> None:
    corpus = tmp_path / "corpora"
    corpus.mkdir()
    with pytest.raises(CatalogDataError, match="No corpus"):
        load_corpus_directory(corpus)

    record = {
        "documentId": "doc-1",
        "corpusId": "corpus-1",
        "title": "Document",
        "segment": "Segment",
        "category": "Category",
        "content": "Grounded content.",
    }
    line = json.dumps(record)
    (corpus / "one.jsonl").write_text(f"{line}\n{line}\n", encoding="utf-8")
    with pytest.raises(CatalogDataError, match="document IDs"):
        load_corpus_directory(corpus)


def test_relationships_require_matching_runtime_ids(tmp_path: Path) -> None:
    root = copy_assets(tmp_path)
    runtime_path = root / "runtime.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    runtime["agents"].pop()
    runtime_path.write_text(json.dumps(runtime), encoding="utf-8")

    with pytest.raises(CatalogDataError, match="agent ID sets"):
        load_consumer_assets(root)


def test_relationships_require_declared_binding_and_corpus(tmp_path: Path) -> None:
    root = copy_assets(tmp_path)
    runtime_path = root / "runtime.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    runtime["agents"][0]["knowledgeBindingId"] = "missing-binding"
    runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
    agents_path = root / "agents.json"
    agents = json.loads(agents_path.read_text(encoding="utf-8"))
    matching_agent = next(
        agent for agent in agents["agents"] if agent["agentId"] == runtime["agents"][0]["agentId"]
    )
    matching_agent["knowledgeBindingIds"] = ["missing-binding"]
    agents_path.write_text(json.dumps(agents), encoding="utf-8")

    with pytest.raises(CatalogDataError, match="must be declared"):
        load_consumer_assets(root)

    root = copy_assets(tmp_path / "second")
    binding_path = root / "knowledge_bases.json"
    bindings = json.loads(binding_path.read_text(encoding="utf-8"))
    bindings["bindings"][0]["corpusIds"] = ["missing-corpus"]
    binding_path.write_text(json.dumps(bindings), encoding="utf-8")
    with pytest.raises(CatalogDataError, match="must have documents"):
        load_consumer_assets(root)


def test_relationships_require_matching_descriptor_binding(tmp_path: Path) -> None:
    root = copy_assets(tmp_path)
    agents_path = root / "agents.json"
    agents = json.loads(agents_path.read_text(encoding="utf-8"))
    agents["agents"][0]["knowledgeBindingIds"] = ["consumer-product-innovation-kb"]
    agents_path.write_text(json.dumps(agents), encoding="utf-8")

    with pytest.raises(CatalogDataError, match="must match"):
        load_consumer_assets(root)


def test_relationships_reject_unknown_and_uncovered_agents(tmp_path: Path) -> None:
    root = copy_assets(tmp_path)
    cases_path = root / "discovery_cases.jsonl"
    cases = [
        json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line
    ]
    cases[0]["expectedAgentIds"] = ["unknown-agent"]
    cases_path.write_text(
        "\n".join(json.dumps(case) for case in cases) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(CatalogDataError, match="unknown agent"):
        load_consumer_assets(root)

    root = copy_assets(tmp_path / "second")
    cases_path = root / "discovery_cases.jsonl"
    cases = [
        json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line
    ]
    cases = [
        case for case in cases if "consumer-brand-to-brand-compare" not in case["expectedAgentIds"]
    ]
    cases_path.write_text(
        "\n".join(json.dumps(case) for case in cases) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(CatalogDataError, match="lacks a discovery case"):
        load_consumer_assets(root)
