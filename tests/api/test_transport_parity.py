from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, SendMessageRequest
from google.protobuf.json_format import MessageToDict
from mcp.shared.memory import create_connected_server_and_client_session

from agent_iq.api import (
    AgentIqService,
    add_a2a_ingress,
    create_http_app,
    create_mcp_server,
)
from agent_iq.contracts import (
    ExecutionSnapshot,
    OrchestrationCommand,
    OrchestrationPresentation,
    OrchestrationResult,
)


def test_mcp_http_and_a2a_have_normalized_transport_parity(
    service_factory: Callable[..., AgentIqService],
    orchestration_command: OrchestrationCommand,
) -> None:
    async def run() -> None:
        payload = orchestration_command.model_dump(mode="json", by_alias=True)

        http_app = create_http_app(service_factory())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=http_app),
            base_url="https://http.agent-iq.test",
        ) as client:
            response = await client.post("/v1/orchestrate", json=payload)
            http_result = OrchestrationResult.model_validate(response.json())

        mcp_service = service_factory()
        mcp_server = create_mcp_server(mcp_service)
        async with create_connected_server_and_client_session(mcp_server) as session:
            await session.initialize()
            called = await session.call_tool("orchestrate", {"command": payload})
            mcp_presentation = OrchestrationPresentation.model_validate(called.structuredContent)
            fetched = await session.call_tool(
                "get_execution",
                {"execution_id": mcp_presentation.execution_id},
            )
            snapshot = ExecutionSnapshot.model_validate(fetched.structuredContent)
            assert snapshot.summary is not None
            mcp_result = OrchestrationResult(
                summary=snapshot.summary,
                events=snapshot.events,
            )

        a2a_app = create_http_app(service_factory())
        add_a2a_ingress(
            a2a_app,
            service_factory(),
            public_base_url="https://a2a.agent-iq.test",
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=a2a_app),
            base_url="https://a2a.agent-iq.test",
        ) as http_client:
            card = await A2ACardResolver(
                http_client,
                "https://a2a.agent-iq.test",
            ).get_agent_card()
            a2a_client = ClientFactory(
                ClientConfig(
                    streaming=False,
                    httpx_client=http_client,
                    supported_protocol_bindings=["JSONRPC"],
                )
            ).create(card)
            responses = [
                response
                async for response in a2a_client.send_message(
                    SendMessageRequest(
                        message=Message(
                            message_id="a2a-parity-message",
                            role=Role.ROLE_USER,
                            parts=(Part(text=orchestration_command.request.intent),),
                            metadata={"agentIqCommand": payload},
                        )
                    )
                )
            ]
            await a2a_client.close()
        a2a_message = MessageToDict(responses[0].message)
        a2a_result = OrchestrationResult.model_validate(a2a_message["parts"][1]["data"])

        assert _normalized(http_result) == _normalized(mcp_result) == _normalized(a2a_result)
        assert mcp_presentation.status == http_result.summary.status
        assert [item.agent_id for item in mcp_presentation.selected_agents] == [
            reason.agent_id for reason in http_result.summary.selection.selected
        ]
        assert mcp_presentation.answer == http_result.summary.synthesis.answer
        assert len(mcp_presentation.citations) == len(
            http_result.summary.synthesis.evidence.citations
        )

    asyncio.run(run())


def _normalized(result: OrchestrationResult) -> dict[str, object]:
    return {
        "selected": tuple(reason.agent_id for reason in result.summary.selection.selected),
        "dag": tuple((step.agent_id, step.depends_on) for step in result.summary.plan.steps),
        "steps": tuple(
            (
                step.agent_id,
                step.status.value,
                step.transport.value,
                step.protocol_version,
                len(step.evidence_ids),
            )
            for step in result.summary.steps
        ),
        "answer": result.summary.synthesis.answer,
        "coverage": result.summary.synthesis.coverage.model_dump(),
        "events": tuple(event.payload.type for event in result.events),
    }
