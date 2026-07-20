"""Generate the content-safe five-agent Canvas replay artifact."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_iq.contracts import (
    CallbackMode,
    CandidateScore,
    EligibilityDecision,
    EventEnvelope,
    InteractionMode,
    ScoreComponent,
    SelectionDecision,
    SelectionReason,
    TaskStatus,
    TransportProtocol,
)
from agent_iq.contracts.events import (
    AgentsSelectedPayload,
    ApprovalRequestedPayload,
    ApprovalResolvedPayload,
    CandidatesRankedPayload,
    CatalogSearchedPayload,
    EventPayload,
    EvidenceReceivedPayload,
    ExecutionCompletedPayload,
    InvocationCompletedPayload,
    InvocationStartedPayload,
    PlanPreparedPayload,
    PreparedPlanStep,
    RequestAcceptedPayload,
    SynthesisCompletedPayload,
    TaskCompletedPayload,
    TaskHandedOffPayload,
)
from agent_iq.presentation import AgUiProjector, replay_agui_state

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "apps" / "canvas" / "public" / "demo" / "launch-command-center.agui.json"
NOW = datetime(2026, 7, 12, 14, 0, tzinfo=UTC)
EXECUTION_ID = "demo-eu-launch-command-center"
THREAD_ID = "demo-launch-thread"
TRACE_ID = "5a6ad7d85cd44b2196871d5c7916fa3c"

HEROES = (
    ("consumer-formulation-rd", ("formula", "enzymes", "cold-water")),
    ("consumer-brand-to-brand-compare", ("brand comparison", "ClearWave", "benchmark")),
    ("consumer-demand-forecasting", ("demand", "forecast", "Germany")),
    ("consumer-manufacturing-operations", ("capacity", "plant", "operations")),
    ("consumer-regulatory-affairs", ("Germany", "claims", "regulatory")),
)

DISTRACTORS = (
    ("consumer-consumer-care", ("consumer", "support")),
    ("consumer-packaging-engineering", ("packaging", "materials")),
    ("consumer-trade-promotion", ("promotion", "retail")),
)


def _candidate(agent_id: str, tags: tuple[str, ...], rank: int) -> CandidateScore:
    return CandidateScore(
        agent_id=agent_id,
        rank=rank,
        fused_score=round(1 / (60 + rank), 8),
        components=(
            ScoreComponent(
                name="lexical",
                rank=rank,
                score=round(8 / rank, 3),
                matched_fields=("skills.tags", "skills.examples"),
            ),
            ScoreComponent(
                name="semantic",
                rank=rank,
                score=round(0.96 - rank * 0.035, 3),
                matched_fields=("description", "skills.description"),
            ),
        ),
        eligibility=EligibilityDecision(
            eligible=True,
            policy_facts=("catalog active", "request-time eligible"),
            policy_version="demo-policy-1",
        ),
        matched_fields=("skills.tags", "skills.examples"),
        explanation="Deterministic public-card match for the command-center replay.",
        catalog_version="demo-catalog-20",
    )


def _selection(candidates: tuple[CandidateScore, ...]) -> SelectionDecision:
    selected = tuple(
        SelectionReason(
            agent_id=agent_id,
            work_item_id=f"work-{index}",
            work_item="Private work-item text is intentionally excluded from Canvas.",
            summary="Selected from public capability and policy signals.",
            matched_skills=(tags[0].replace(" ", "-"),),
            matched_tags=tags,
            fused_score=candidates[index - 1].fused_score,
            policy_facts=("catalog active", "request-time eligible"),
        )
        for index, (agent_id, tags) in enumerate(HEROES, start=1)
    )
    return SelectionDecision(
        request_id="request-demo-launch",
        candidates=candidates,
        selected=selected,
        selector="deterministic-demo-selector",
        created_at=NOW,
    )


def _plan() -> PlanPreparedPayload:
    return PlanPreparedPayload(
        plan_id="plan-demo-launch",
        max_concurrency=4,
        steps=(
            PreparedPlanStep(
                step_id="step-formulation",
                agent_id="consumer-formulation-rd",
                context_kinds=("work_iq", "foundry_iq", "fabric_iq"),
                expected_output_count=1,
                invocation_mode=InteractionMode.SYNC,
                approval_required=False,
            ),
            PreparedPlanStep(
                step_id="step-brand",
                agent_id="consumer-brand-to-brand-compare",
                context_kinds=("foundry_iq", "web_iq"),
                expected_output_count=1,
                invocation_mode=InteractionMode.SYNC,
                approval_required=False,
            ),
            PreparedPlanStep(
                step_id="step-demand",
                agent_id="consumer-demand-forecasting",
                context_kinds=("work_iq", "fabric_iq"),
                expected_output_count=1,
                invocation_mode=InteractionMode.SYNC,
                approval_required=False,
            ),
            PreparedPlanStep(
                step_id="step-capacity",
                agent_id="consumer-manufacturing-operations",
                context_kinds=("fabric_iq",),
                expected_output_count=1,
                invocation_mode=InteractionMode.SYNC,
                approval_required=False,
            ),
            PreparedPlanStep(
                step_id="step-regulatory",
                agent_id="consumer-regulatory-affairs",
                depends_on=("step-formulation", "step-brand"),
                context_kinds=("foundry_iq", "web_iq", "prior_step"),
                expected_output_count=1,
                invocation_mode=InteractionMode.SYNC,
                approval_required=False,
            ),
            PreparedPlanStep(
                step_id="step-monitor",
                agent_id="consumer-manufacturing-operations",
                depends_on=("step-demand", "step-capacity", "step-regulatory"),
                context_kinds=("prior_step",),
                expected_output_count=1,
                invocation_mode=InteractionMode.ASYNC,
                approval_required=True,
            ),
        ),
    )


def _events() -> tuple[EventEnvelope, ...]:
    candidates = tuple(
        _candidate(agent_id, tags, rank)
        for rank, (agent_id, tags) in enumerate((*HEROES, *DISTRACTORS), start=1)
    )
    payloads: list[EventPayload] = [
        RequestAcceptedPayload(
            request_id="request-demo-launch",
            intent_summary="Private launch intent intentionally excluded from Canvas.",
        ),
        CatalogSearchedPayload(
            query="Private catalog query intentionally excluded from Canvas.",
            candidate_count=20,
            catalog_version="demo-catalog-20",
        ),
        CandidatesRankedPayload(candidates=candidates),
        AgentsSelectedPayload(decision=_selection(candidates)),
        _plan(),
    ]

    invocations = (
        (
            "invocation-formulation",
            "step-formulation",
            "consumer-formulation-rd",
            "evidence-formula-specification",
            3180,
        ),
        (
            "invocation-brand",
            "step-brand",
            "consumer-brand-to-brand-compare",
            "evidence-competitor-benchmark",
            3540,
        ),
        (
            "invocation-demand",
            "step-demand",
            "consumer-demand-forecasting",
            "evidence-demand-scenario",
            2710,
        ),
        (
            "invocation-capacity",
            "step-capacity",
            "consumer-manufacturing-operations",
            "evidence-plant-capacity",
            2960,
        ),
    )
    for invocation_id, step_id, agent_id, _evidence_id, _duration_ms in invocations:
        payloads.append(
            InvocationStartedPayload(
                invocation_id=invocation_id,
                step_id=step_id,
                agent_id=agent_id,
                transport=TransportProtocol.A2A,
                protocol_version="1.0",
            )
        )
    for invocation_id, step_id, _agent_id, evidence_id, duration_ms in invocations:
        payloads.extend(
            (
                EvidenceReceivedPayload(
                    invocation_id=invocation_id,
                    step_id=step_id,
                    evidence_ids=(evidence_id,),
                ),
                InvocationCompletedPayload(
                    invocation_id=invocation_id,
                    step_id=step_id,
                    duration_ms=duration_ms,
                ),
            )
        )

    payloads.extend(
        (
            InvocationStartedPayload(
                invocation_id="invocation-regulatory",
                step_id="step-regulatory",
                agent_id="consumer-regulatory-affairs",
                transport=TransportProtocol.A2A,
                protocol_version="1.0",
            ),
            EvidenceReceivedPayload(
                invocation_id="invocation-regulatory",
                step_id="step-regulatory",
                evidence_ids=("evidence-germany-claims-guidance",),
            ),
            InvocationCompletedPayload(
                invocation_id="invocation-regulatory",
                step_id="step-regulatory",
                duration_ms=3240,
            ),
            SynthesisCompletedPayload(
                answer=(
                    "Conditional go for the October 1 Germany launch: the formula and "
                    "claims are supportable, demand fits the current scenario, and the "
                    "plant has a bounded capacity path [1][2][3][4][5]. Release remains "
                    "subject to the recorded approval and monitored execution."
                ),
                citation_numbers=(1, 2, 3, 4, 5),
            ),
            ApprovalRequestedPayload(
                approval_id="approval-monitoring-handoff",
                step_ids=("step-monitor",),
                reason=(
                    "Approve the long-running launch-monitoring handoff; no SAP mutation "
                    "is included in this replay."
                ),
            ),
            ApprovalResolvedPayload(
                approval_id="approval-monitoring-handoff",
                status="approved",
            ),
            TaskHandedOffPayload(
                task_id="task-launch-monitoring",
                agent_id="consumer-manufacturing-operations",
                callback_modes=(CallbackMode.TEAMS, CallbackMode.EMAIL),
            ),
            TaskCompletedPayload(
                task_id="task-launch-monitoring",
                status=TaskStatus.COMPLETED,
                summary="Monitoring handoff completed for the deterministic replay.",
            ),
            ExecutionCompletedPayload(status="completed", duration_ms=14_860),
        )
    )

    return tuple(
        EventEnvelope(
            event_id=f"event-demo-{sequence:02d}",
            execution_id=EXECUTION_ID,
            sequence=sequence,
            occurred_at=NOW + timedelta(milliseconds=sequence * 200),
            trace_id=TRACE_ID,
            payload=payload,
        )
        for sequence, payload in enumerate(payloads)
    )


def _render() -> str:
    projection = AgUiProjector().project(_events(), thread_id=THREAD_ID)
    final_state = replay_agui_state(projection)
    if final_state.status != "completed" or len(final_state.selections) != 5:
        raise RuntimeError("The Canvas demo replay did not reach its certified terminal state.")
    return (
        json.dumps(
            projection.model_dump(mode="json", by_alias=True),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    rendered = _render()
    if arguments.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != rendered:
            raise SystemExit("Canvas demo replay is stale. Run scripts/generate_canvas_demo.py.")
        print(f"Canvas demo replay is current: {OUTPUT.relative_to(ROOT)}")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"Wrote Canvas demo replay: {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
