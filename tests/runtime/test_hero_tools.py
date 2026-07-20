"""Data-driven narrow hero-tool composition tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import ValidationError

from agent_iq.catalog import load_consumer_runtime_assets
from agent_iq.connectors.sap import FixtureSapReader
from agent_iq.contracts import ToolBackend
from agent_iq.errors import AgentIqOperationError
from agent_iq.runtime.hero_tools import build_agent_tools
from agent_iq.runtime.telemetry import MemoryToolTelemetry


def _runtime(agent_id: str) -> Any:
    assets = load_consumer_runtime_assets()
    return next(item for item in assets.runtime.agents if item.agent_id == agent_id)


def _knowledge_tool(
    queries: list[str],
    *,
    name: str = "knowledge_base_retrieve",
) -> BaseTool:
    async def retrieve(query: str) -> str:
        queries.append(query)
        return "Grounded result [ref_id:0]"

    return StructuredTool.from_function(
        coroutine=retrieve,
        name=name,
        description="Retrieve grounded knowledge.",
    )


def _knowledge_batch_tool(calls: list[list[str]]) -> BaseTool:
    async def retrieve(queries: list[str]) -> str:
        calls.append(queries)
        return "Grounded batch result [ref_id:0]"

    return StructuredTool.from_function(
        coroutine=retrieve,
        name="knowledge_base",
        description="Retrieve grounded knowledge.",
        args_schema={
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 1,
                }
            },
            "required": ["queries"],
        },
    )


def _knowledge_content_block_tool(calls: list[list[str]]) -> BaseTool:
    async def retrieve(queries: list[str]) -> list[dict[str, str]]:
        calls.append(queries)
        return [
            {
                "type": "text",
                "text": "Grounded MCP result [ref_id:0]",
                "id": "lc-result",
            }
        ]

    return StructuredTool.from_function(
        coroutine=retrieve,
        name="knowledge_base",
        description="Retrieve grounded knowledge.",
        args_schema={
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 1,
                }
            },
            "required": ["queries"],
        },
    )


def _tool_by_name(tools: list[BaseTool], name: str) -> BaseTool:
    return next(tool for tool in tools if tool.name == name)


def test_formulation_tools_are_narrow_and_scope_foundry_iq_queries() -> None:
    queries: list[str] = []
    telemetry = MemoryToolTelemetry()
    tools = build_agent_tools(
        _runtime("consumer-formulation-rd"),
        [_knowledge_tool(queries)],
        telemetry=telemetry,
    )

    assert [tool.name for tool in tools] == [
        "retrieve_formula_components",
        "assess_enzyme_stability",
        "compare_active_systems",
        "retrieve_claim_support",
    ]
    assert "knowledge_base_retrieve" not in {tool.name for tool in tools}

    result = asyncio.run(
        _tool_by_name(tools, "assess_enzyme_stability").ainvoke(
            {"product": "BrightWash", "conditions": "20 C wash"}
        )
    )

    assert result == "Grounded result [ref_id:0]"
    assert queries == ["Enzyme stability for BrightWash under 20 C wash"]
    assert len(telemetry.events) == 1
    event = telemetry.events[0]
    assert event.agent_id == "consumer-formulation-rd"
    assert event.tool_id == "assess_enzyme_stability"
    assert event.backend == "foundry_iq"
    assert event.status == "completed"
    assert event.duration_ms is not None
    assert event.cost_capture_status == "unavailable"
    assert event.arguments_captured is False
    assert "BrightWash" not in repr(event)


def test_narrow_tool_arguments_reject_missing_and_extra_values() -> None:
    tools = build_agent_tools(
        _runtime("consumer-formulation-rd"),
        [_knowledge_tool([])],
    )
    tool = _tool_by_name(tools, "assess_enzyme_stability")

    with pytest.raises(ValidationError):
        asyncio.run(tool.ainvoke({"product": "BrightWash"}))
    with pytest.raises(ValidationError):
        asyncio.run(
            tool.ainvoke(
                {
                    "product": "BrightWash",
                    "conditions": "20 C wash",
                    "unexpected": "blocked",
                }
            )
        )


def test_foundry_iq_backend_accepts_platform_tool_name_variation() -> None:
    queries: list[list[str]] = []
    tools = build_agent_tools(
        _runtime("consumer-formulation-rd"),
        [_knowledge_batch_tool(queries)],
    )

    result = asyncio.run(
        _tool_by_name(tools, "retrieve_formula_components").ainvoke(
            {
                "product": "BrightWash Cold",
                "performance_goal": "cold-water cleaning",
            }
        )
    )

    assert result == "Grounded batch result [ref_id:0]"
    assert queries == [
        [
            "For BrightWash Cold only, what formula components directly support "
            "cold-water cleaning? Retrieve the smallest sufficient source set directly "
            "about BrightWash Cold. Exclude competitor products, brand comparisons, and "
            "comparison-method documents. "
            "Include exact source titles and [ref_id:n] citation markers."
        ]
    ]
    assert "knowledge_base" not in {tool.name for tool in tools}


def test_foundry_iq_backend_normalizes_mcp_text_content_blocks() -> None:
    calls: list[list[str]] = []
    tools = build_agent_tools(
        _runtime("consumer-demand-forecasting"),
        [_knowledge_content_block_tool(calls)],
        sap_reader=FixtureSapReader(load_consumer_runtime_assets().sap_fixtures),
    )

    result = asyncio.run(
        _tool_by_name(tools, "retrieve_forecast_drivers").ainvoke(
            {
                "product": "BrightWash Cold",
                "market": "Germany",
                "horizon": "October 2026",
            }
        )
    )

    assert result == "Grounded MCP result [ref_id:0]"
    assert calls


def test_formulation_comparison_query_remains_available_only_when_explicit() -> None:
    queries: list[str] = []
    tools = build_agent_tools(
        _runtime("consumer-formulation-rd"),
        [_knowledge_tool(queries)],
    )

    result = asyncio.run(
        _tool_by_name(tools, "compare_active_systems").ainvoke(
            {
                "product_a": "BrightWash Cold",
                "product_b": "ClearWave Cold Clean",
                "dimension": "surfactant performance",
            }
        )
    )

    assert result == "Grounded result [ref_id:0]"
    assert queries == [
        (
            "Compare active systems in BrightWash Cold versus ClearWave Cold Clean "
            "for surfactant performance"
        )
    ]


def test_hero_rejects_ambiguous_foundry_iq_backends() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        build_agent_tools(
            _runtime("consumer-formulation-rd"),
            [_knowledge_tool([]), _knowledge_tool([], name="other_retrieval")],
        )


def test_sap_tools_fail_closed_without_an_explicit_reader() -> None:
    with pytest.raises(ValueError, match="explicitly configured reader"):
        build_agent_tools(
            _runtime("consumer-demand-forecasting"),
            [_knowledge_tool([])],
        )


def test_fixture_sap_tool_filters_records_and_keeps_non_live_label() -> None:
    assets = load_consumer_runtime_assets()
    telemetry = MemoryToolTelemetry()
    tools = build_agent_tools(
        _runtime("consumer-demand-forecasting"),
        [_knowledge_tool([])],
        sap_reader=FixtureSapReader(assets.sap_fixtures),
        telemetry=telemetry,
    )

    payload = asyncio.run(
        _tool_by_name(tools, "retrieve_demand_history").ainvoke(
            {
                "product_id": "brightwash-cold",
                "market": "Germany",
                "start_date": "2026-10-01",
                "end_date": "2026-10-31",
            }
        )
    )
    heading, serialized = payload.split("\n", maxsplit=1)
    wire = json.loads(serialized)

    assert "DETERMINISTIC FIXTURE - NOT LIVE SAP" in heading
    assert wire["sourceMode"] == "fixture"
    assert wire["resourceKind"] == "demand_fact"
    assert len(wire["records"]) == 1
    assert wire["records"][0]["productId"] == "brightwash-cold"
    assert wire["records"][0]["reference"]["deepLink"].startswith(
        "https://fixture.sap.example.invalid/"
    )
    assert telemetry.events[0].source_mode == "fixture"
    assert telemetry.events[0].status == "completed"


def test_fixture_sap_tool_denies_undeclared_scope_and_missing_records() -> None:
    assets = load_consumer_runtime_assets()
    reader = FixtureSapReader(assets.sap_fixtures)

    with pytest.raises(AgentIqOperationError) as denied:
        reader.read(
            assets.sap_fixtures.records[0].resource_kind,
            {"market": "Germany"},
            authorization_scope="sap:s4:production-orders:read",
        )
    assert denied.value.error.code.value == "authorization_denied"

    with pytest.raises(AgentIqOperationError) as missing:
        reader.read(
            assets.sap_fixtures.records[0].resource_kind,
            {
                "product_id": "missing-product",
                "market": "United States",
                "start_date": "2026-10-01",
                "end_date": "2026-10-31",
            },
            authorization_scope="sap:s4:demand:read",
        )
    assert missing.value.error.code.value == "evidence_unavailable"

    with pytest.raises(AgentIqOperationError) as invalid:
        reader.read(
            assets.sap_fixtures.records[0].resource_kind,
            {
                "product_id": "brightwash-cold",
                "market": "Germany",
                "start_date": "2026-11-01",
                "end_date": "2026-10-01",
            },
            authorization_scope="sap:s4:demand:read",
        )
    assert invalid.value.error.code.value == "invalid_request"


def test_sap_tool_surfaces_missing_fixture_as_explicit_failed_tool_result() -> None:
    assets = load_consumer_runtime_assets()
    telemetry = MemoryToolTelemetry()
    tools = build_agent_tools(
        _runtime("consumer-demand-forecasting"),
        [_knowledge_tool([])],
        sap_reader=FixtureSapReader(assets.sap_fixtures),
        telemetry=telemetry,
    )

    result = asyncio.run(
        _tool_by_name(tools, "retrieve_demand_history").ainvoke(
            {
                "product_id": "missing-product",
                "market": "Germany",
                "start_date": "2026-10-01",
                "end_date": "2026-10-31",
            }
        )
    )

    assert "Governed tool request failed (evidence_unavailable)" in result
    assert "do not invent a record" in result
    assert telemetry.events[0].status == "failed"
    assert telemetry.events[0].source_mode is None


def test_non_hero_runtime_keeps_existing_tools_unchanged() -> None:
    raw = _knowledge_tool([])
    tools = build_agent_tools(_runtime("consumer-consumer-care"), [raw])

    assert tools == [raw]
    assert not _runtime("consumer-consumer-care").tools


def test_each_sap_tool_has_a_declared_read_scope() -> None:
    for agent_id in ("consumer-demand-forecasting", "consumer-manufacturing-operations"):
        for tool in _runtime(agent_id).tools:
            if tool.descriptor.backend is ToolBackend.SAP:
                assert tool.descriptor.authorization_scopes
                assert all(
                    scope.endswith(":read") for scope in tool.descriptor.authorization_scopes
                )
