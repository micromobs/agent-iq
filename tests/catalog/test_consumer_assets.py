"""Completeness and determinism gates for the canonical consumer assets."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from agent_iq.catalog import (
    build_canonical_single_agent_demo_catalog,
    load_consumer_assets,
    load_consumer_runtime_assets,
    render_registry_projection,
)
from agent_iq.catalog.loader import DEFAULT_CONSUMER_DATA
from agent_iq.contracts import (
    ArtifactKind,
    ToolBackend,
    ToolEffect,
    ToolIdempotency,
    TransportProtocol,
)

ROOT = Path(__file__).resolve().parents[2]
DATA = DEFAULT_CONSUMER_DATA
EXPECTED_AGENT_IDS = {
    "consumer-brand-marketing",
    "consumer-brand-to-brand-compare",
    "consumer-connect-develop",
    "consumer-consumer-care",
    "consumer-consumer-insights",
    "consumer-demand-forecasting",
    "consumer-ecommerce-retail-media",
    "consumer-formulation-rd",
    "consumer-fragrance-sensory",
    "consumer-logistics-distribution",
    "consumer-manufacturing-operations",
    "consumer-manufacturing-sustainability",
    "consumer-microbiological-safety",
    "consumer-packaging-sustainability",
    "consumer-pricing-revenue-growth",
    "consumer-procurement-sourcing",
    "consumer-product-safety-toxicology",
    "consumer-quality-capa",
    "consumer-regulatory-affairs",
    "consumer-trade-promotion",
    "employee-expense",
    "employee-identity-security",
    "employee-it-helpdesk",
    "employee-vacation-leave",
}
EXPECTED_PROJECTION_SHA256 = (
    "85ad8ac7dfd9aa2ea9990c16335cfd44"  # pragma: allowlist secret
    "7e5a05e070746a8382a64e113cfe3293"  # pragma: allowlist secret
)


def test_consumer_asset_counts_and_ids_are_locked() -> None:
    assets = load_consumer_assets()

    assert {agent.agent_id for agent in assets.catalog.agents} == EXPECTED_AGENT_IDS
    assert len(assets.catalog.agents) == 24
    assert sum(len(agent.skills) for agent in assets.catalog.agents) == 48
    assert len(assets.documents) == 54
    assert len(assets.discovery_cases) == 42
    assert len(assets.knowledge_bindings.bindings) == 9


def test_every_agent_has_rich_framework_neutral_discovery_data() -> None:
    assets = load_consumer_assets()
    pydantic_agents = {"consumer-demand-forecasting", "consumer-regulatory-affairs"}

    for descriptor in assets.catalog.agents:
        expected_framework = "PydanticAI" if descriptor.agent_id in pydantic_agents else "LangGraph"
        assert descriptor.implementation.framework == expected_framework
        assert all(expected_framework.casefold() not in tag.casefold() for tag in descriptor.tags)
        assert descriptor.skills
        assert all(skill.tags for skill in descriptor.skills)
        assert all(skill.examples for skill in descriptor.skills)
        assert descriptor.knowledge_binding_ids
        assert len(descriptor.capabilities) == 1
        capability = descriptor.capabilities[0]
        assert capability.protocol is TransportProtocol.A2A
        assert capability.protocol_version == "1.0.0"
        assert capability.endpoint is None
        assert capability.streaming is False
        assert capability.push_notifications is False

    evidence_agents = {
        descriptor.agent_id
        for descriptor in assets.catalog.agents
        if descriptor.capabilities[0].evidence is not None
    }
    assert evidence_agents == EXPECTED_AGENT_IDS
    # Keep publication private until live identity reconciliation and a broker are active.
    assert all(
        not descriptor.capabilities[0].artifact_kinds for descriptor in assets.catalog.agents
    )


def test_discovery_cases_cover_all_agents() -> None:
    assets = load_consumer_assets()
    covered = {agent_id for case in assets.discovery_cases for agent_id in case.expected_agent_ids}

    assert covered == EXPECTED_AGENT_IDS
    assert sum(len(case.expected_agent_ids) > 1 for case in assets.discovery_cases) == 5


def test_live_smoke_contracts_cover_every_agent_and_grounded_source() -> None:
    assets = load_consumer_assets()
    payload = json.loads((DATA / "live_smoke.json").read_text(encoding="ascii"))
    contracts = payload["agents"]
    source_titles = {document.title for document in assets.documents}

    assert payload["schemaVersion"] == "1.0"
    assert {contract["agentId"] for contract in contracts} == EXPECTED_AGENT_IDS
    assert len(contracts) == 24
    assert all(contract["prompt"] for contract in contracts)
    assert all(contract["requiredTerms"] for contract in contracts)
    assert all(
        contract.get("sourceTitleMode", "advisory") in {"required", "advisory"}
        for contract in contracts
    )
    assert (
        sum(contract.get("sourceTitleMode", "advisory") == "advisory" for contract in contracts)
        == 21
    )
    assert (
        next(
            contract for contract in contracts if contract["agentId"] == "consumer-formulation-rd"
        )["sourceTitleMode"]
        == "required"
    )
    assert all(
        source in source_titles for contract in contracts for source in contract["requiredSources"]
    )


def test_composite_consumer_care_binding_and_compare_prompt_are_preserved() -> None:
    assets = load_consumer_assets()
    bindings = {
        binding.knowledge_binding_id: binding.corpus_ids
        for binding in assets.knowledge_bindings.bindings
    }
    runtime = {definition.agent_id: definition for definition in assets.runtime.agents}

    assert bindings["consumer-consumer-care-kb"] == (
        "consumer-market-brand-consumer-kb",
        "consumer-safety-regulatory-quality-kb",
    )
    assert runtime["consumer-consumer-care"].knowledge_binding_id == "consumer-consumer-care-kb"
    assert "side-by-side" in runtime["consumer-brand-to-brand-compare"].prompt_addendum
    assert "retrieve_forecast_drivers" in runtime["consumer-demand-forecasting"].prompt_addendum
    assert "[ref_id:n]" in runtime["consumer-demand-forecasting"].prompt_addendum
    assert (
        "retrieve_manufacturing_guidance"
        in runtime["consumer-manufacturing-operations"].prompt_addendum
    )
    assert "[ref_id:n]" in runtime["consumer-manufacturing-operations"].prompt_addendum
    assert {agent_id for agent_id, definition in runtime.items() if definition.prompt_addendum} == {
        "consumer-brand-to-brand-compare",
        "consumer-demand-forecasting",
        "consumer-formulation-rd",
        "consumer-manufacturing-operations",
        "employee-identity-security",
        "employee-it-helpdesk",
        "employee-vacation-leave",
    }


def test_exactly_five_heroes_expose_three_to_five_narrow_read_tools() -> None:
    assets = load_consumer_runtime_assets()
    runtime_by_id = {runtime.agent_id: runtime for runtime in assets.runtime.agents}
    expected_names = {
        "consumer-brand-to-brand-compare": {
            "retrieve_brand_profile",
            "compare_brand_dimension",
            "retrieve_comparison_method",
        },
        "consumer-demand-forecasting": {
            "retrieve_demand_history",
            "retrieve_open_sales_orders",
            "retrieve_inventory_position",
            "retrieve_forecast_drivers",
        },
        "consumer-formulation-rd": {
            "retrieve_formula_components",
            "assess_enzyme_stability",
            "compare_active_systems",
            "retrieve_claim_support",
        },
        "consumer-manufacturing-operations": {
            "retrieve_material_facts",
            "retrieve_production_orders",
            "retrieve_plant_capacity",
            "retrieve_operational_status",
            "retrieve_manufacturing_guidance",
        },
        "consumer-regulatory-affairs": {
            "retrieve_market_requirements",
            "review_claim_requirements",
            "retrieve_ingredient_restrictions",
            "retrieve_registration_evidence",
        },
    }

    assert {agent_id for agent_id, runtime in runtime_by_id.items() if runtime.tools} == set(
        expected_names
    )
    for agent_id, expected in expected_names.items():
        tools = runtime_by_id[agent_id].tools
        assert 3 <= len(tools) <= 5
        assert {tool.descriptor.tool_id for tool in tools} == expected
        assert all(tool.descriptor.effect is ToolEffect.READ for tool in tools)
        assert all(not tool.descriptor.approval_required for tool in tools)
        assert all(tool.descriptor.idempotency is ToolIdempotency.SAFE for tool in tools)
        assert all(tool.descriptor.produces_evidence for tool in tools)
        assert all(tool.descriptor.authorization_scopes for tool in tools)
        assert all(tool.descriptor.telemetry.capture_latency for tool in tools)
        assert all(tool.descriptor.telemetry.capture_cost for tool in tools)
        assert all(not tool.descriptor.telemetry.capture_arguments for tool in tools)
        assert all("knowledge_base_retrieve" not in tool.descriptor.tool_id for tool in tools)

    sap_agents = {
        agent_id
        for agent_id, runtime in runtime_by_id.items()
        if any(tool.descriptor.backend is ToolBackend.SAP for tool in runtime.tools)
    }
    assert sap_agents == {
        "consumer-demand-forecasting",
        "consumer-manufacturing-operations",
    }
    artifact_agents = {
        agent_id: runtime.artifact_output_kinds
        for agent_id, runtime in runtime_by_id.items()
        if runtime.artifact_output_kinds
    }
    assert artifact_agents == {
        "consumer-demand-forecasting": (ArtifactKind.DEMAND_SCENARIO,),
        "consumer-regulatory-affairs": (ArtifactKind.REGULATORY_DECISION,),
    }


def test_hosted_runtime_assets_do_not_require_eval_or_corpus_files(tmp_path: Path) -> None:
    runtime_root = tmp_path / "consumer"
    runtime_root.mkdir()
    for name in (
        "agents.json",
        "runtime.json",
        "knowledge_bases.json",
        "sap_fixtures.json",
    ):
        shutil.copy2(DATA / name, runtime_root / name)

    assets = load_consumer_runtime_assets(runtime_root)

    assert len(assets.catalog.agents) == 24
    assert len(assets.runtime.agents) == 24
    assert len(assets.knowledge_bindings.bindings) == 9


def test_single_agent_demo_keeps_canonical_formulation_binding() -> None:
    catalog = build_canonical_single_agent_demo_catalog(load_consumer_assets().catalog)

    assert catalog.catalog_id == "canonical-single-agent-demo"
    assert [agent.agent_id for agent in catalog.agents] == ["consumer-formulation-rd"]
    assert catalog.agents[0].source.source_agent_id == "consumer-formulation-rd"


def test_registry_projection_is_deterministic() -> None:
    catalog = load_consumer_assets().catalog
    first = render_registry_projection(catalog)
    second = render_registry_projection(catalog)

    assert first == second
    assert hashlib.sha256(first.encode("ascii")).hexdigest() == EXPECTED_PROJECTION_SHA256
    projection = json.loads(first)
    assert [agent["agentId"] for agent in projection["agents"]] == sorted(EXPECTED_AGENT_IDS)


def test_data_files_are_ascii_and_contain_no_replacement_characters() -> None:
    files = [path for path in DATA.rglob("*") if path.is_file()]

    assert len(files) == 16
    for path in files:
        content = path.read_bytes()
        assert content.decode("ascii")
        assert b"\xef\xbf\xbd" not in content


def test_source_provenance_is_complete() -> None:
    provenance_path = ROOT / "docs" / "provenance" / "sources.json"
    provenance = json.loads(provenance_path.read_text(encoding="ascii"))
    sources = provenance["sources"]

    assert len(sources) >= 3
    assert {
        "consumer/fleet.py",
        "consumer/corpora.py",
        "datasets/discovery_testset_consumer.jsonl",
    }.issubset({source["path"] for source in sources})
    assert {source["license"] for source in sources} == {
        "MIT",
        "Apache-2.0",
        "CC BY-SA 4.0",
    }
    assert all(len(source["sha256"]) == 64 for source in sources)
    assert all(source["destinations"] for source in sources)
