from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
import pytest
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import Event, EventQueue
from a2a.types import (
    Message,
    Part,
    Role,
    SendMessageRequest,
    TaskState,
    TaskStatusUpdateEvent,
)
from google.protobuf.json_format import MessageToDict

from agent_iq.api import (
    AgentIqA2AExecutor,
    AgentIqService,
    add_a2a_ingress,
    create_http_app,
)
from agent_iq.contracts import OrchestrationCommand, OrchestrationResult


def test_native_a2a_returns_text_and_the_normalized_structured_result(
    service_factory: Callable[..., AgentIqService],
    orchestration_command: OrchestrationCommand,
) -> None:
    async def run() -> None:
        app = create_http_app(service_factory())
        add_a2a_ingress(
            app,
            service_factory(),
            public_base_url="https://agent-iq.example.test",
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://agent-iq.example.test",
        ) as http_client:
            card = await A2ACardResolver(
                http_client,
                "https://agent-iq.example.test",
            ).get_agent_card()
            client = ClientFactory(
                ClientConfig(
                    streaming=False,
                    httpx_client=http_client,
                    supported_protocol_bindings=["JSONRPC"],
                )
            ).create(card)
            request = SendMessageRequest(
                message=Message(
                    message_id="a2a-message-1",
                    role=Role.ROLE_USER,
                    parts=(Part(text=orchestration_command.request.intent),),
                    metadata={
                        "agentIqCommand": orchestration_command.model_dump(
                            mode="json",
                            by_alias=True,
                        )
                    },
                )
            )
            responses = [response async for response in client.send_message(request)]
            await client.close()

        assert len(responses) == 1
        assert responses[0].HasField("message")
        message = MessageToDict(responses[0].message)
        assert message["parts"][0]["text"]
        result = OrchestrationResult.model_validate(message["parts"][1]["data"])
        assert [reason.agent_id for reason in result.summary.selection.selected] == [
            "consumer-formulation-rd"
        ]

    asyncio.run(run())


class CaptureQueue(EventQueue):
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def enqueue_event(self, event: Event) -> None:
        self.events.append(event)


def test_a2a_command_metadata_validation_and_cancellation(
    service_factory: Callable[..., AgentIqService],
    orchestration_command: OrchestrationCommand,
) -> None:
    def context(
        *,
        text: str,
        metadata: dict[str, object],
        message_id: str,
        task_id: str | None = None,
    ) -> RequestContext:
        return RequestContext(
            ServerCallContext(),
            request=SendMessageRequest(
                message=Message(
                    message_id=message_id,
                    role=Role.ROLE_USER,
                    parts=(Part(text=text),),
                    metadata=metadata,
                )
            ),
            task_id=task_id,
            context_id="context-a2a-1",
        )

    async def run() -> None:
        executor = AgentIqA2AExecutor(service_factory())
        fallback = context(
            text=orchestration_command.request.intent,
            metadata={
                "agentIqSubjectId": "caller-1",
                "agentIqTenantId": "tenant-1",
                "agentIqRequestId": "request-a2a-fallback",
            },
            message_id="message-fallback",
        )
        command = executor.command_from_context(fallback)
        assert command.request.request_id == "request-a2a-fallback"

        with pytest.raises(ValueError, match="text intent"):
            executor.command_from_context(
                context(text=" ", metadata={}, message_id="message-empty")
            )
        with pytest.raises(ValueError, match="agentIqSubjectId"):
            executor.command_from_context(
                context(
                    text=orchestration_command.request.intent,
                    metadata={},
                    message_id="message-no-caller",
                )
            )
        with pytest.raises(ValueError, match="must match"):
            executor.command_from_context(
                context(
                    text="A different intent",
                    metadata={
                        "agentIqCommand": orchestration_command.model_dump(
                            mode="json",
                            by_alias=True,
                        )
                    },
                    message_id="message-mismatch",
                )
            )

        slow_executor = AgentIqA2AExecutor(service_factory(delay_seconds=10.0))
        active_context = context(
            text=orchestration_command.request.intent,
            metadata={
                "agentIqCommand": orchestration_command.model_dump(
                    mode="json",
                    by_alias=True,
                )
            },
            message_id="message-cancel",
            task_id="task-cancel",
        )
        response_queue = CaptureQueue()
        running = asyncio.create_task(slow_executor.execute(active_context, response_queue))
        await asyncio.sleep(0.05)
        cancel_queue = CaptureQueue()
        await slow_executor.cancel(active_context, cancel_queue)
        await running

        assert isinstance(cancel_queue.events[0], TaskStatusUpdateEvent)
        assert cancel_queue.events[0].status.state == TaskState.TASK_STATE_CANCELED
        with pytest.raises(RuntimeError, match="No active"):
            await slow_executor.cancel(active_context, CaptureQueue())

    asyncio.run(run())
