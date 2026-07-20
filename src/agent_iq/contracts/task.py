"""Durable agent-task state and callback contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from .agent import CallbackMode
from .base import (
    ContractModel,
    Identifier,
    NonEmptyText,
    SchemaVersion,
    ShortText,
    Timestamp,
)


class TaskMode(StrEnum):
    ATTENDED = "attended"
    UNATTENDED = "unattended"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CallbackTarget(ContractModel):
    mode: CallbackMode
    destination: ShortText


class TaskCheckpoint(ContractModel):
    sequence: int = Field(ge=0)
    status: TaskStatus
    summary: NonEmptyText
    recorded_at: Timestamp


class AgentTask(ContractModel):
    schema_version: SchemaVersion = "1.0"
    task_id: Identifier
    execution_id: Identifier
    step_id: Identifier
    agent_id: Identifier
    mode: TaskMode
    status: TaskStatus
    created_at: Timestamp
    updated_at: Timestamp
    deadline_at: Timestamp | None = None
    callback_targets: tuple[CallbackTarget, ...] = ()
    approval_id: Identifier | None = None
    checkpoints: tuple[TaskCheckpoint, ...] = ()

    @model_validator(mode="after")
    def validate_timeline(self) -> AgentTask:
        if self.updated_at < self.created_at:
            raise ValueError("Task update time cannot precede creation time.")
        if self.deadline_at is not None and self.deadline_at <= self.created_at:
            raise ValueError("Task deadline must be after creation time.")
        sequences = [checkpoint.sequence for checkpoint in self.checkpoints]
        if sequences != sorted(set(sequences)):
            raise ValueError("Task checkpoint sequences must be unique and ordered.")
        return self
