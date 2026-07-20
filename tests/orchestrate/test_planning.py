from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_iq.catalog import load_consumer_assets
from agent_iq.catalog.assets import AgentCatalogAsset
from agent_iq.config import Settings
from agent_iq.contracts import (
    CallerContext,
    ContextInput,
    ContextKind,
    ErrorCode,
    MatchFilters,
    MatchRequest,
)
from agent_iq.errors import AgentIqOperationError
from agent_iq.match import MatchEngine
from agent_iq.orchestrate import (
    ContextAssembler,
    ContextCandidate,
    ExecutionPlanner,
    PlannerPolicy,
)

NOW = datetime(2026, 7, 11, 1, 0, tzinfo=UTC)


def _request(
    intent: str,
    *,
    filters: MatchFilters | None = None,
    request_id: str = "request-1",
) -> MatchRequest:
    return MatchRequest(
        request_id=request_id,
        intent=intent,
        caller=CallerContext(subject_id="caller-1", tenant_id="tenant-1"),
        filters=filters or MatchFilters(),
        requested_at=NOW,
    )


def _matcher(catalog: AgentCatalogAsset | None = None) -> MatchEngine:
    assets = load_consumer_assets()
    return MatchEngine(
        catalog or assets.catalog,
        semantic_mode="offline",
        settings=Settings(_env_file=None),
    )


def _planner(
    catalog: AgentCatalogAsset | None = None,
    *,
    context_assembler: ContextAssembler | None = None,
) -> ExecutionPlanner:
    assets = load_consumer_assets()
    return ExecutionPlanner(
        catalog or assets.catalog,
        context_assembler=context_assembler,
        clock=lambda: NOW,
        plan_id_factory=lambda: "plan-fixed",
    )


def test_single_intent_builds_one_native_a2a_step() -> None:
    request = _request("Which enzymes drive BrightWash's cold-water cleaning performance?")
    selection = _matcher().match(request)

    plan = _planner().plan(request, selection)

    assert [reason.agent_id for reason in selection.selected] == ["consumer-formulation-rd"]
    assert [step.agent_id for step in plan.steps] == ["consumer-formulation-rd"]
    assert plan.steps[0].work_item == request.intent
    assert plan.steps[0].depends_on == ()
    assert plan.max_concurrency == 1


def test_independent_intents_have_no_dependency_and_receive_only_bound_context() -> None:
    request = _request(
        "Improve forecast accuracy with demand sensing and raise OEE on the bottling line"
    )
    selection = _matcher().match(request)
    context = (
        ContextCandidate(
            context=ContextInput(
                context_id="meeting-launch",
                kind=ContextKind.WORK_IQ,
                source_uri="https://example.test/meetings/launch",
                approved=True,
            ),
            allowed_agent_ids=("consumer-demand-forecasting",),
            relevance_terms=("forecast demand",),
            priority=1,
        ),
        ContextCandidate(
            context=ContextInput(
                context_id="plant-line",
                kind=ContextKind.FABRIC_IQ,
                source_uri="https://example.test/plants/line-1",
                approved=True,
            ),
            allowed_agent_ids=("consumer-manufacturing-operations",),
            relevance_terms=("OEE bottling",),
            priority=2,
        ),
        ContextCandidate(
            context=ContextInput(
                context_id="unapproved-email",
                kind=ContextKind.WORK_IQ,
                approved=False,
            ),
            allowed_agent_ids=("consumer-demand-forecasting",),
        ),
        ContextCandidate(
            context=ContextInput(
                context_id="unbound-file",
                kind=ContextKind.ATTACHMENT,
                approved=True,
            )
        ),
    )

    plan = _planner().plan(request, selection, context_candidates=context)

    assert [step.agent_id for step in plan.steps] == [
        "consumer-demand-forecasting",
        "consumer-manufacturing-operations",
    ]
    assert all(not step.depends_on for step in plan.steps)
    assert [item.context_id for item in plan.steps[0].context_inputs] == ["meeting-launch"]
    assert [item.context_id for item in plan.steps[1].context_inputs] == ["plant-line"]


def test_explicit_sequence_and_after_syntax_build_dependency_dags() -> None:
    cases = (
        (
            "Improve forecast accuracy with demand sensing, then raise OEE on the bottling line",
            ("consumer-demand-forecasting", "consumer-manufacturing-operations"),
        ),
        (
            "Raise OEE on the bottling line after improving the demand forecast accuracy",
            ("consumer-demand-forecasting", "consumer-manufacturing-operations"),
        ),
    )

    for index, (intent, expected) in enumerate(cases):
        request = _request(intent, request_id=f"sequence-{index}")
        selection = _matcher().match(request)
        plan = _planner().plan(request, selection)

        assert tuple(step.agent_id for step in plan.steps) == expected
        assert plan.steps[0].depends_on == ()
        assert plan.steps[1].depends_on == ("step-1",)
        assert plan.steps[1].context_inputs[-1].kind is ContextKind.PRIOR_STEP
        assert plan.steps[1].context_inputs[-1].context_id == "step-1-result"


def test_ambiguous_or_fully_filtered_intent_produces_no_plan() -> None:
    ambiguous = _request("Schedule a haircut appointment near me", request_id="ambiguous")
    ambiguous_selection = _matcher().match(ambiguous)
    assert ambiguous_selection.selected == ()

    with pytest.raises(AgentIqOperationError) as no_match:
        _planner().plan(ambiguous, ambiguous_selection)
    assert no_match.value.error.code is ErrorCode.AGENT_INELIGIBLE

    filtered = _request(
        "Which enzymes drive BrightWash's cold-water cleaning performance?",
        request_id="filtered",
        filters=MatchFilters(allowed_agent_ids=("agent-not-in-catalog",)),
    )
    filtered_selection = _matcher().match(filtered)
    assert filtered_selection.selected == ()


def test_planner_rejects_stale_catalog_and_invalid_limits() -> None:
    assets = load_consumer_assets()
    request = _request("Which enzymes drive BrightWash's cold-water cleaning performance?")
    selection = _matcher().match(request)
    stale = assets.catalog.model_copy(
        update={
            "agents": tuple(
                agent
                for agent in assets.catalog.agents
                if agent.agent_id != "consumer-formulation-rd"
            )
        }
    )

    with pytest.raises(AgentIqOperationError) as missing:
        _planner(stale).plan(request, selection)
    assert missing.value.error.code is ErrorCode.AGENT_NOT_FOUND

    with pytest.raises(ValueError, match="agent limit"):
        PlannerPolicy(max_agents=0)
    with pytest.raises(ValueError, match="concurrency"):
        PlannerPolicy(max_concurrency=21)
    with pytest.raises(ValueError, match="step deadline"):
        PlannerPolicy(step_deadline_seconds=601)
    with pytest.raises(ValueError, match="overall deadline"):
        PlannerPolicy(overall_deadline_seconds=0)


def test_context_candidates_fail_closed_without_valid_bindings() -> None:
    context = ContextInput(
        context_id="context-1",
        kind=ContextKind.WEB_IQ,
        approved=True,
    )
    assembler = ContextAssembler(max_inputs_per_step=0)
    assert (
        assembler.select(
            agent_id="agent-1",
            work_item="fresh competitor signal",
            candidates=(ContextCandidate(context=context, relevance_terms=("competitor",)),),
        )
        == ()
    )

    with pytest.raises(ValueError, match="negative"):
        ContextAssembler(max_inputs_per_step=-1)
    with pytest.raises(ValueError, match="priority"):
        ContextCandidate(context=context, priority=-1)
    with pytest.raises(ValueError, match="unique"):
        ContextCandidate(context=context, allowed_agent_ids=("agent-1", "agent-1"))
    with pytest.raises(ValueError, match="empty"):
        ContextCandidate(context=context, relevance_terms=(" ",))
    with pytest.raises(ValueError, match="unique"):
        ContextCandidate(context=context, relevance_terms=("OEE", "oee"))
