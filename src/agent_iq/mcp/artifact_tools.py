"""MCP publication tool for pre-authorized validated business artifacts."""

from __future__ import annotations

from collections.abc import Callable

from mcp.server.fastmcp import FastMCP

from agent_iq.artifacts import CompanionArtifactStore
from agent_iq.contracts import (
    ArtifactChannelReference,
    ArtifactProducerIdentity,
    ArtifactPublishRequest,
    BusinessArtifact,
)

ProducerProvider = Callable[[], ArtifactProducerIdentity]
ArtifactPublishTool = Callable[[str, str, BusinessArtifact], ArtifactChannelReference]


def register_artifact_publish_tool(
    server: FastMCP,
    store: CompanionArtifactStore,
    *,
    producer_provider: ProducerProvider,
) -> ArtifactPublishTool:
    """Register the tool; authentication middleware supplies the producer identity."""

    @server.tool(
        name="publish_typed_artifact",
        description=(
            "Publish a validated Agent IQ business artifact for an invocation that "
            "the coordinator pre-authorized. Never put artifact JSON in A2A prose."
        ),
        structured_output=True,
    )
    def publish_typed_artifact(
        invocation_id: str,
        correlation_id: str,
        artifact: BusinessArtifact,
    ) -> ArtifactChannelReference:
        request = ArtifactPublishRequest(
            invocation_id=invocation_id,
            correlation_id=correlation_id,
            artifact=artifact,
        )
        return store.publish(request, producer=producer_provider())

    return publish_typed_artifact
