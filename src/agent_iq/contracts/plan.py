"""Transparent execution-plan and dependency-DAG contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AnyUrl, Field, field_validator, model_validator

from .agent import InteractionMode
from .base import (
    ContractModel,
    Identifier,
    NonEmptyText,
    SchemaVersion,
    ShortText,
    Timestamp,
    require_unique_text,
)


class ContextKind(StrEnum):
    WORK_IQ = "work_iq"
    FOUNDRY_IQ = "foundry_iq"
    FABRIC_IQ = "fabric_iq"
    WEB_IQ = "web_iq"
    ATTACHMENT = "attachment"
    PRIOR_STEP = "prior_step"


class ContextInput(ContractModel):
    context_id: Identifier
    kind: ContextKind
    source_uri: AnyUrl | None = None
    sensitivity: ShortText | None = None
    approved: bool = False


class ExpectedOutput(ContractModel):
    output_id: Identifier
    description: NonEmptyText
    media_type: ShortText = "text/plain"
    schema_ref: AnyUrl | None = None


class PlanStep(ContractModel):
    step_id: Identifier
    agent_id: Identifier
    work_item: NonEmptyText
    depends_on: tuple[Identifier, ...] = ()
    context_inputs: tuple[ContextInput, ...] = ()
    expected_outputs: tuple[ExpectedOutput, ...] = Field(min_length=1)
    invocation_mode: InteractionMode = InteractionMode.SYNC
    deadline_seconds: int = Field(default=120, ge=1, le=86_400)
    approval_required: bool = False

    @field_validator("depends_on")
    @classmethod
    def validate_dependencies(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Step dependencies")


class ExecutionPlan(ContractModel):
    schema_version: SchemaVersion = "1.0"
    plan_id: Identifier
    request_id: Identifier
    objective: NonEmptyText
    public_summary: NonEmptyText
    steps: tuple[PlanStep, ...] = Field(min_length=1)
    max_concurrency: int = Field(default=5, ge=1, le=20)
    overall_deadline_seconds: int = Field(default=300, ge=1, le=604_800)
    created_at: Timestamp

    @model_validator(mode="after")
    def validate_dag(self) -> ExecutionPlan:
        steps_by_id = {step.step_id: step for step in self.steps}
        if len(steps_by_id) != len(self.steps):
            raise ValueError("Plan step IDs must be unique.")

        for step in self.steps:
            if step.step_id in step.depends_on:
                raise ValueError("A plan step cannot depend on itself.")
            missing = set(step.depends_on) - steps_by_id.keys()
            if missing:
                raise ValueError(f"Plan dependency does not exist: {sorted(missing)[0]}.")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("Plan dependencies must be acyclic.")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in steps_by_id[step_id].depends_on:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in steps_by_id:
            visit(step_id)
        return self
