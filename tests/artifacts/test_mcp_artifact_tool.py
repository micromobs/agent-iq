from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult, ListToolsResult

from agent_iq.artifacts import CompanionArtifactStore
from agent_iq.contracts import (
    ArtifactFetchRequest,
    ArtifactKind,
    ArtifactProducerIdentity,
    CallerContext,
    DemandScenario,
)
from agent_iq.mcp import register_artifact_publish_tool

NOW = datetime(2026, 7, 11, 0, 0, tzinfo=UTC)


def test_mcp_tool_publishes_validated_artifact_through_real_protocol() -> None:
    caller = CallerContext(subject_id="caller-1", tenant_id="tenant-1")
    producer = ArtifactProducerIdentity(
        agent_id="consumer-demand-forecasting",
        agent_version="2",
        identity_id="identity-demand",
        framework="PydanticAI",
    )
    store = CompanionArtifactStore(
        clock=lambda: NOW,
        handle_factory=lambda: "ath-mcp",
    )
    store.authorize_invocation(
        invocation_id="inv-mcp",
        correlation_id="corr-mcp",
        caller=caller,
        producer=producer,
        allowed_kinds=(ArtifactKind.DEMAND_SCENARIO,),
    )
    server = FastMCP("agent-iq-artifact-test")
    register_artifact_publish_tool(
        server,
        store,
        producer_provider=lambda: producer,
    )
    artifact = DemandScenario(
        artifact_id="demand-mcp",
        product_id="brightwash-cold",
        market="Germany",
        horizon_start=date(2026, 10, 1),
        horizon_end=date(2026, 10, 31),
        unit="cases",
        baseline_quantity=1000,
        forecast_quantity=1250,
        confidence=0.84,
        evidence_ids=("evidence-demand-1",),
        created_at=NOW,
    )

    async def invoke() -> tuple[ListToolsResult, CallToolResult]:
        async with create_connected_server_and_client_session(server) as client:
            await client.initialize()
            tools = await client.list_tools()
            result = await client.call_tool(
                "publish_typed_artifact",
                {
                    "invocation_id": "inv-mcp",
                    "correlation_id": "corr-mcp",
                    "artifact": artifact.model_dump(mode="json", by_alias=True),
                },
            )
            return tools, result

    tools, result = asyncio.run(invoke())
    assert [tool.name for tool in tools.tools] == ["publish_typed_artifact"]
    assert result.isError is False
    assert result.structuredContent is not None
    reference = result.structuredContent
    assert reference["artifactId"] == "demand-mcp"
    envelope = store.fetch(
        ArtifactFetchRequest(
            artifact_id=reference["artifactId"],
            invocation_id=reference["invocationId"],
            correlation_id=reference["correlationId"],
            handle=reference["handle"],
        ),
        caller=caller,
    )
    assert envelope.artifact == artifact
