"""Labelled evaluation of dynamic agent selection and dependency planning."""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_iq.contracts import CallerContext, MatchFilters, MatchRequest
from agent_iq.contracts.base import ContractModel, Identifier, NonEmptyText
from agent_iq.match import MatchEngine
from agent_iq.orchestrate import ExecutionPlanner

DEFAULT_CASES_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "consumer" / "orchestration_cases.jsonl"
)


class DependencyExpectation(ContractModel):
    agent_id: Identifier
    depends_on_agent_ids: tuple[Identifier, ...] = Field(min_length=1)

    @field_validator("depends_on_agent_ids")
    @classmethod
    def validate_unique_dependencies(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("Expected dependency agents must be unique.")
        return values


class OrchestrationCase(ContractModel):
    case_id: Identifier
    intent: NonEmptyText
    expected_agent_ids: tuple[Identifier, ...] = ()
    expected_dependencies: tuple[DependencyExpectation, ...] = ()
    allowed_agent_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_case(self) -> OrchestrationCase:
        if len(self.expected_agent_ids) != len(set(self.expected_agent_ids)):
            raise ValueError("Expected orchestration agents must be unique.")
        if len(self.allowed_agent_ids) != len(set(self.allowed_agent_ids)):
            raise ValueError("Allowed orchestration agents must be unique.")
        dependency_agents = [item.agent_id for item in self.expected_dependencies]
        if len(dependency_agents) != len(set(dependency_agents)):
            raise ValueError("Expected dependency targets must be unique.")
        expected = set(self.expected_agent_ids)
        for dependency in self.expected_dependencies:
            if dependency.agent_id not in expected:
                raise ValueError("A dependency target must be an expected agent.")
            if set(dependency.depends_on_agent_ids) - expected:
                raise ValueError("A dependency source must be an expected agent.")
            if dependency.agent_id in dependency.depends_on_agent_ids:
                raise ValueError("An agent cannot depend on itself.")
        return self


@dataclass(frozen=True, slots=True)
class OrchestrationCaseResult:
    case_id: str
    expected_agents: tuple[str, ...]
    selected_agents: tuple[str, ...]
    expected_dependencies: tuple[tuple[str, tuple[str, ...]], ...]
    actual_dependencies: tuple[tuple[str, tuple[str, ...]], ...]
    false_positives: tuple[str, ...]
    exact_selection: bool
    exact_dag: bool
    duration_ms: float


@dataclass(frozen=True, slots=True)
class OrchestrationEvaluationResult:
    cases: tuple[OrchestrationCaseResult, ...]
    exact_selection_accuracy: float
    exact_dag_accuracy: float
    false_positives: int
    p95_latency_ms: float
    semantic_backend: str

    @property
    def passes(self) -> bool:
        return (
            self.exact_selection_accuracy == 1.0
            and self.exact_dag_accuracy == 1.0
            and self.false_positives == 0
        )


def load_orchestration_cases(
    path: Path = DEFAULT_CASES_PATH,
) -> tuple[OrchestrationCase, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"Cannot read orchestration cases at {path}: {exc}") from exc
    cases = tuple(OrchestrationCase.model_validate_json(line) for line in lines if line.strip())
    case_ids = [case.case_id for case in cases]
    if not cases:
        raise ValueError("At least one orchestration case is required.")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Orchestration case IDs must be unique.")
    return cases


def evaluate_orchestration(
    matcher: MatchEngine,
    planner: ExecutionPlanner,
    cases: tuple[OrchestrationCase, ...],
) -> OrchestrationEvaluationResult:
    results: list[OrchestrationCaseResult] = []
    for case in cases:
        request = MatchRequest(
            request_id=case.case_id,
            intent=case.intent,
            caller=CallerContext(subject_id="eval-user", tenant_id="eval-tenant"),
            filters=MatchFilters(
                max_candidates=len(matcher.catalog.agents),
                max_selected=5,
                allowed_agent_ids=case.allowed_agent_ids,
            ),
            requested_at=datetime(2026, 7, 11, tzinfo=UTC),
        )
        started = time.perf_counter()
        selection = matcher.match(request)
        selected = tuple(reason.agent_id for reason in selection.selected)
        actual_dependencies: tuple[tuple[str, tuple[str, ...]], ...] = ()
        if selected:
            plan = planner.plan(request, selection)
            agent_by_step = {step.step_id: step.agent_id for step in plan.steps}
            actual_dependencies = tuple(
                sorted(
                    (
                        step.agent_id,
                        tuple(sorted(agent_by_step[item] for item in step.depends_on)),
                    )
                    for step in plan.steps
                    if step.depends_on
                )
            )
        expected_dependencies = tuple(
            sorted(
                (
                    dependency.agent_id,
                    tuple(sorted(dependency.depends_on_agent_ids)),
                )
                for dependency in case.expected_dependencies
            )
        )
        duration_ms = (time.perf_counter() - started) * 1_000
        false_positives = tuple(
            agent_id for agent_id in selected if agent_id not in case.expected_agent_ids
        )
        results.append(
            OrchestrationCaseResult(
                case_id=case.case_id,
                expected_agents=case.expected_agent_ids,
                selected_agents=selected,
                expected_dependencies=expected_dependencies,
                actual_dependencies=actual_dependencies,
                false_positives=false_positives,
                exact_selection=set(selected) == set(case.expected_agent_ids),
                exact_dag=actual_dependencies == expected_dependencies,
                duration_ms=duration_ms,
            )
        )
    return OrchestrationEvaluationResult(
        cases=tuple(results),
        exact_selection_accuracy=_mean(
            1.0 if result.exact_selection else 0.0 for result in results
        ),
        exact_dag_accuracy=_mean(1.0 if result.exact_dag else 0.0 for result in results),
        false_positives=sum(len(result.false_positives) for result in results),
        p95_latency_ms=_percentile([result.duration_ms for result in results], 0.95),
        semantic_backend=matcher.semantic_backend,
    )


def build_orchestration_payload(
    result: OrchestrationEvaluationResult,
    *,
    agent_count: int,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    timestamp = (generated_at or datetime.now(UTC)).astimezone(UTC)
    case_results = [
        {
            "caseId": case.case_id,
            "status": (
                "pass"
                if case.exact_selection and case.exact_dag and not case.false_positives
                else "fail"
            ),
            "expectedAgents": list(case.expected_agents),
            "selectedAgents": list(case.selected_agents),
            "expectedDependencies": _dependency_payload(case.expected_dependencies),
            "actualDependencies": _dependency_payload(case.actual_dependencies),
            "falsePositives": list(case.false_positives),
            "exactSelection": case.exact_selection,
            "exactDag": case.exact_dag,
            "durationMs": round(case.duration_ms, 3),
        }
        for case in result.cases
    ]
    return {
        "schemaVersion": "1.0",
        "suiteId": "consumer-orchestration",
        "generatedAt": timestamp.isoformat().replace("+00:00", "Z"),
        "semanticBackend": result.semantic_backend,
        "agents": agent_count,
        "cases": len(result.cases),
        "exactSelectionAccuracy": result.exact_selection_accuracy,
        "exactDagAccuracy": result.exact_dag_accuracy,
        "falsePositives": result.false_positives,
        "p95LatencyMs": round(result.p95_latency_ms, 3),
        "passes": result.passes,
        "gates": {
            "exactSelectionAccuracy": {
                "actual": result.exact_selection_accuracy,
                "required": 1.0,
                "passes": result.exact_selection_accuracy == 1.0,
            },
            "exactDagAccuracy": {
                "actual": result.exact_dag_accuracy,
                "required": 1.0,
                "passes": result.exact_dag_accuracy == 1.0,
            },
            "falsePositives": {
                "actual": result.false_positives,
                "required": 0,
                "passes": result.false_positives == 0,
            },
        },
        "caseResults": case_results,
        "failures": [case for case in case_results if case["status"] == "fail"],
    }


def write_orchestration_report(payload: dict[str, Any], output_dir: Path) -> Path:
    if payload.get("schemaVersion") != "1.0" or payload.get("suiteId") != "consumer-orchestration":
        raise ValueError("Unsupported orchestration evaluation payload.")
    output_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = output_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_id = re.sub(r"[^A-Za-z0-9._-]+", "-", str(payload["generatedAt"])).strip("-")
    if not run_id:
        raise ValueError("Orchestration evaluation run ID cannot be empty.")
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    run_path = runs_dir / f"{run_id}.json"
    _atomic_write(run_path, text)
    _atomic_write(output_dir / "latest.json", text)
    return run_path


def _dependency_payload(
    dependencies: tuple[tuple[str, tuple[str, ...]], ...],
) -> list[dict[str, Any]]:
    return [
        {"agentId": agent_id, "dependsOnAgentIds": list(depends_on)}
        for agent_id, depends_on in dependencies
    ]


def _mean(values: Iterable[float]) -> float:
    numbers = list(values)
    return sum(numbers) / len(numbers) if numbers else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)
