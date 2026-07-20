"""Catalog-driven execution planning over selected Agent IQ descriptors."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from agent_iq.catalog.assets import AgentCatalogAsset
from agent_iq.contracts import (
    ContextInput,
    ContextKind,
    ErrorCode,
    ExecutionPlan,
    ExpectedOutput,
    InteractionMode,
    MatchRequest,
    PlanStep,
    SelectionDecision,
    TransportProtocol,
)
from agent_iq.errors import operation_error

from .context import ContextAssembler, ContextCandidate
from .intent import IntentDecomposer


@dataclass(frozen=True, slots=True)
class PlannerPolicy:
    max_agents: int = 5
    max_concurrency: int = 5
    step_deadline_seconds: int = 120
    overall_deadline_seconds: int = 300

    def __post_init__(self) -> None:
        if not 1 <= self.max_agents <= 20:
            raise ValueError("Planner agent limit must be between one and twenty.")
        if not 1 <= self.max_concurrency <= 20:
            raise ValueError("Planner concurrency must be between one and twenty.")
        if not 1 <= self.step_deadline_seconds <= 600:
            raise ValueError("Planner step deadline must be between one and 600 seconds.")
        if not 1 <= self.overall_deadline_seconds <= 604_800:
            raise ValueError("Planner overall deadline is outside the supported range.")


class ExecutionPlanner:
    """Convert one selection decision into a validated dependency DAG."""

    def __init__(
        self,
        catalog: AgentCatalogAsset,
        *,
        decomposer: IntentDecomposer | None = None,
        context_assembler: ContextAssembler | None = None,
        policy: PlannerPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
        plan_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._agents = {agent.agent_id: agent for agent in catalog.agents}
        self._decomposer = decomposer or IntentDecomposer()
        self._context = context_assembler or ContextAssembler()
        self.policy = policy or PlannerPolicy()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._plan_id_factory = plan_id_factory or (lambda: f"plan-{uuid.uuid4().hex}")

    def plan(
        self,
        request: MatchRequest,
        selection: SelectionDecision,
        *,
        context_candidates: tuple[ContextCandidate, ...] = (),
    ) -> ExecutionPlan:
        if selection.request_id != request.request_id:
            raise ValueError("Selection and match request IDs must match.")
        if not selection.selected:
            raise operation_error(
                ErrorCode.AGENT_INELIGIBLE,
                "No eligible agent met the intent-selection threshold.",
                correlation_id=request.request_id,
            )
        if len(selection.selected) > self.policy.max_agents:
            raise operation_error(
                ErrorCode.POLICY_BLOCKED,
                "The selected squad exceeds the configured planning limit.",
                correlation_id=request.request_id,
            )

        work_items = self._decomposer.decompose(request.intent, selection)
        if len(work_items) != len(selection.selected):
            raise RuntimeError("Intent decomposition did not preserve the selected work items.")
        step_ids = {
            work_item.work_item_id: f"step-{index}"
            for index, work_item in enumerate(work_items, start=1)
        }
        steps: list[PlanStep] = []
        for work_item in work_items:
            descriptor = self._agents.get(work_item.agent_id)
            if descriptor is None:
                raise operation_error(
                    ErrorCode.AGENT_NOT_FOUND,
                    "A selected agent is absent from the current catalog.",
                    correlation_id=request.request_id,
                )
            if not any(
                capability.protocol is TransportProtocol.A2A
                and InteractionMode.SYNC in capability.interaction_modes
                for capability in descriptor.capabilities
            ):
                raise operation_error(
                    ErrorCode.PROTOCOL_UNSUPPORTED,
                    "A selected agent does not support synchronous native A2A.",
                    correlation_id=request.request_id,
                )

            dependency_steps = tuple(step_ids[item] for item in work_item.depends_on)
            approved_context = self._context.select(
                agent_id=work_item.agent_id,
                work_item=work_item.description,
                candidates=context_candidates,
            )
            prior_context = tuple(
                ContextInput(
                    context_id=f"{dependency}-result",
                    kind=ContextKind.PRIOR_STEP,
                    approved=True,
                )
                for dependency in dependency_steps
            )
            step_id = step_ids[work_item.work_item_id]
            steps.append(
                PlanStep(
                    step_id=step_id,
                    agent_id=work_item.agent_id,
                    work_item=work_item.description,
                    depends_on=dependency_steps,
                    context_inputs=approved_context + prior_context,
                    expected_outputs=(
                        ExpectedOutput(
                            output_id=f"{step_id}-answer",
                            description="A grounded answer with structured evidence.",
                        ),
                    ),
                    deadline_seconds=self.policy.step_deadline_seconds,
                )
            )

        dependent_count = sum(bool(step.depends_on) for step in steps)
        return ExecutionPlan(
            plan_id=self._plan_id_factory(),
            request_id=request.request_id,
            objective=request.intent,
            public_summary=(
                f"Execute {len(steps)} selected native-A2A work item(s); "
                f"{dependent_count} depend on earlier results."
            ),
            steps=tuple(steps),
            max_concurrency=min(self.policy.max_concurrency, len(steps)),
            overall_deadline_seconds=self.policy.overall_deadline_seconds,
            created_at=self._clock(),
        )
