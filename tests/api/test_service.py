from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from agent_iq.api import AgentIqService
from agent_iq.contracts import (
    ExecutionLifecycleStatus,
    ExecutionStatus,
    FeedbackRating,
    FeedbackSubmission,
    OrchestrationCommand,
)
from agent_iq.errors import AgentIqOperationError
from agent_iq.presentation import replay_agui_state

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def test_orchestration_persists_complete_ordered_lifecycle(
    service_factory: Callable[..., AgentIqService],
    orchestration_command: OrchestrationCommand,
) -> None:
    async def run() -> None:
        service = service_factory()
        result = await service.orchestrate(orchestration_command)

        assert result.summary.status is ExecutionStatus.COMPLETED
        assert [reason.agent_id for reason in result.summary.selection.selected] == [
            "consumer-formulation-rd"
        ]
        assert [event.payload.type for event in result.events] == [
            "request.accepted",
            "catalog.searched",
            "candidates.ranked",
            "agents.selected",
            "plan.prepared",
            "invocation.started",
            "evidence.received",
            "invocation.completed",
            "synthesis.completed",
            "execution.completed",
        ]
        assert [event.sequence for event in result.events] == list(range(10))
        snapshot = await service.get_execution(result.summary.execution_id)
        assert snapshot.status is ExecutionLifecycleStatus.COMPLETED
        assert snapshot.summary == result.summary
        projection = await service.get_agui_projection(
            result.summary.execution_id,
            thread_id="thread-1",
        )
        assert replay_agui_state(projection).status == "completed"
        assert projection.events[-1].event.type == "RUN_FINISHED"

    asyncio.run(run())


def test_running_execution_can_be_cancelled_and_remains_queryable(
    service_factory: Callable[..., AgentIqService],
    orchestration_command: OrchestrationCommand,
) -> None:
    async def run() -> None:
        service = service_factory(delay_seconds=10.0)
        started = await service.start(orchestration_command)
        async for event in service.watch_events(started.execution_id):
            if event.payload.type == "invocation.started":
                break

        await service.cancel_execution(started.execution_id)
        result = await service.wait(started.execution_id)

        assert result.summary.status is ExecutionStatus.CANCELLED
        assert result.events[-1].payload.type == "execution.completed"
        snapshot = await service.get_execution(started.execution_id)
        assert snapshot.status is ExecutionLifecycleStatus.CANCELLED

    asyncio.run(run())


def test_feedback_is_idempotent_and_bound_to_execution_caller(
    service_factory: Callable[..., AgentIqService],
    orchestration_command: OrchestrationCommand,
) -> None:
    async def run() -> None:
        service = service_factory()
        result = await service.orchestrate(orchestration_command)
        feedback = FeedbackSubmission(
            feedback_id="feedback-1",
            execution_id=result.summary.execution_id,
            subject_id="caller-1",
            tenant_id="tenant-1",
            rating=FeedbackRating.HELPFUL,
            submitted_at=NOW,
        )

        assert await service.submit_feedback(feedback) == await service.submit_feedback(feedback)
        with pytest.raises(AgentIqOperationError) as exc_info:
            await service.submit_feedback(
                feedback.model_copy(
                    update={
                        "feedback_id": "feedback-2",
                        "subject_id": "another-caller",
                    }
                )
            )
        assert exc_info.value.error.code.value == "authorization_denied"

    asyncio.run(run())
