"""Incoming native A2A adapter for the Agent IQ coordinator."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Message,
    Part,
    Role,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from fastapi import FastAPI
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Value

from agent_iq.contracts import (
    CallerContext,
    MatchFilters,
    MatchRequest,
    OrchestrationCommand,
    TransportProtocol,
)

from .service import AgentIqService

A2A_COMMAND_KEY = "agentIqCommand"
A2A_SUBJECT_KEY = "agentIqSubjectId"
A2A_TENANT_KEY = "agentIqTenantId"
A2A_REQUEST_KEY = "agentIqRequestId"


class AgentIqA2AExecutor(AgentExecutor):
    """Translate one A2A message into the same internal orchestration command."""

    def __init__(
        self,
        service: AgentIqService,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._service = service
        self._clock = clock or (lambda: datetime.now(UTC))
        self._active: dict[str, str] = {}
        self._active_lock = asyncio.Lock()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        command = self.command_from_context(context)
        snapshot = await self._service.start(command)
        keys = _context_keys(context)
        async with self._active_lock:
            for key in keys:
                self._active[key] = snapshot.execution_id
        try:
            result = await self._service.wait(snapshot.execution_id)
            result_data = Value()
            ParseDict(result.model_dump(mode="json", by_alias=True), result_data)
            response = Message(
                message_id=f"msg-{uuid.uuid4().hex}",
                context_id=context.context_id or "",
                task_id=context.task_id or "",
                role=Role.ROLE_AGENT,
                parts=(
                    Part(text=result.summary.synthesis.answer),
                    Part(data=result_data),
                ),
                metadata={"agentIqExecutionId": result.summary.execution_id},
            )
            await event_queue.enqueue_event(response)
        finally:
            async with self._active_lock:
                for key in keys:
                    self._active.pop(key, None)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        keys = _context_keys(context)
        async with self._active_lock:
            execution_id = next(
                (self._active[key] for key in keys if key in self._active),
                None,
            )
        if execution_id is None:
            raise RuntimeError("No active Agent IQ execution matches the A2A task.")
        await self._service.cancel_execution(execution_id)
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id or execution_id,
                context_id=context.context_id or "",
                status=TaskStatus(state=TaskState.TASK_STATE_CANCELED),
            )
        )

    def command_from_context(self, context: RequestContext) -> OrchestrationCommand:
        prompt = context.get_user_input().strip()
        if not prompt:
            raise ValueError("Agent IQ A2A requests require a text intent.")
        metadata = _message_metadata(context)
        provided = metadata.get(A2A_COMMAND_KEY)
        if provided is not None:
            command = OrchestrationCommand.model_validate(provided)
            if command.request.intent != prompt:
                raise ValueError("A2A text intent and Agent IQ command intent must match.")
            return command

        subject_id = _required_metadata(metadata, A2A_SUBJECT_KEY)
        tenant_id = _required_metadata(metadata, A2A_TENANT_KEY)
        message_id = context.message.message_id if context.message is not None else None
        request_id = str(metadata.get(A2A_REQUEST_KEY) or message_id or f"req-{uuid.uuid4().hex}")
        return OrchestrationCommand(
            request=MatchRequest(
                request_id=request_id,
                intent=prompt,
                caller=CallerContext(subject_id=subject_id, tenant_id=tenant_id),
                filters=MatchFilters(required_protocols=(TransportProtocol.A2A,)),
                requested_at=self._clock(),
            )
        )


def add_a2a_ingress(
    app: FastAPI,
    service: AgentIqService,
    *,
    public_base_url: str,
    rpc_path: str = "/a2a",
) -> AgentCard:
    """Mount Agent Card and JSON-RPC v1.0 routes on an existing FastAPI app."""

    card = build_coordinator_card(public_base_url=public_base_url, rpc_path=rpc_path)
    handler = DefaultRequestHandler(
        agent_executor=AgentIqA2AExecutor(service),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(
            handler,
            rpc_url=rpc_path,
            enable_v0_3_compat=False,
        ),
    )
    return card


def build_coordinator_card(*, public_base_url: str, rpc_path: str = "/a2a") -> AgentCard:
    base = public_base_url.rstrip("/")
    path = rpc_path if rpc_path.startswith("/") else f"/{rpc_path}"
    return AgentCard(
        name="Agent IQ Coordinator",
        description=(
            "Discovers and orchestrates the minimum eligible squad of grounded A2A agents."
        ),
        supported_interfaces=(
            AgentInterface(
                url=f"{base}{path}",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            ),
        ),
        version="0.1.0",
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
        ),
        default_input_modes=("text/plain", "application/json"),
        default_output_modes=("text/plain", "application/json"),
        skills=(
            AgentSkill(
                id="dynamic-agent-orchestration",
                name="Dynamic agent orchestration",
                description=(
                    "Match intent, select eligible agents, execute native A2A work, "
                    "and synthesize cited results."
                ),
                tags=("agent discovery", "multi-agent orchestration", "grounded synthesis"),
                examples=(
                    "Compare two brands and validate the regulatory claims.",
                    "Check demand and manufacturing capacity for a launch.",
                ),
            ),
        ),
    )


def _message_metadata(context: RequestContext) -> dict[str, Any]:
    if context.message is None:
        return {}
    return MessageToDict(context.message.metadata, preserving_proto_field_name=True)


def _required_metadata(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"A2A message metadata requires {key}.")
    return value


def _context_keys(context: RequestContext) -> tuple[str, ...]:
    values = (
        context.task_id,
        context.context_id,
        context.message.message_id if context.message is not None else None,
    )
    return tuple(value for value in values if value)
