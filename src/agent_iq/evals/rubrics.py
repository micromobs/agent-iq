"""Fail-closed rubric evaluation over sanitized transcripts and execution traces."""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from agent_iq.contracts import (
    CriterionScore,
    ExecutionStatus,
    ExecutionSummary,
    ModelJudgment,
    RubricCase,
    RubricCaseScore,
    RubricCriterion,
    RubricCriterionMode,
    RubricDefinition,
    RubricEvaluationMode,
    RubricEvaluationReport,
    RubricObservation,
    RubricObservationSource,
    RubricPlanStep,
    RubricReplayBundle,
    SynthesisStatus,
)

DEFAULT_BUNDLE_PATH = Path(__file__).resolve().parents[3] / "evals" / "release_rubric.json"
DEFAULT_REPORT_ROOT = Path(__file__).resolve().parents[3] / "reports" / "evals" / "rubrics"
DEFAULT_HISTORY_LIMIT = 25


class ModelJudge(Protocol):
    """Provider-neutral judge that returns only a score and public rationale."""

    def judge(
        self,
        *,
        rubric: RubricDefinition,
        criterion: RubricCriterion,
        case: RubricCase,
        observation: RubricObservation,
    ) -> ModelJudgment: ...


@dataclass(frozen=True, slots=True)
class RubricReportPaths:
    json_path: Path
    html_path: Path
    latest_json_path: Path
    latest_html_path: Path


@dataclass(frozen=True, slots=True)
class _DeterministicResult:
    score: float
    public_reason: str
    provenance: tuple[str, ...]


_Checker = Callable[[RubricCase, RubricObservation], _DeterministicResult]


def load_rubric_bundle(path: Path = DEFAULT_BUNDLE_PATH) -> RubricReplayBundle:
    """Load one strict, version-bound replay bundle."""

    try:
        payload = path.read_text(encoding="utf-8")
        return RubricReplayBundle.model_validate_json(payload)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot load rubric replay bundle at {path}: {exc}") from exc


def attach_model_judgments(
    bundle: RubricReplayBundle,
    judge: ModelJudge,
) -> RubricReplayBundle:
    """Evaluate every model criterion without weakening deterministic criteria."""

    if bundle.judgments:
        raise ValueError("The replay bundle already contains model judgments.")
    model_criteria = tuple(
        criterion
        for criterion in bundle.rubric.criteria
        if criterion.mode is RubricCriterionMode.MODEL_JUDGE
    )
    if not model_criteria:
        raise ValueError("The rubric does not define model-judge criteria.")

    cases = {case.case_id: case for case in bundle.cases}
    judgments: list[ModelJudgment] = []
    for observation in bundle.observations:
        case = cases[observation.case_id]
        for criterion in model_criteria:
            judgment = judge.judge(
                rubric=bundle.rubric,
                criterion=criterion,
                case=case,
                observation=observation,
            )
            expected = (
                observation.observation_id,
                bundle.rubric.rubric_id,
                bundle.rubric.version,
                criterion.criterion_id,
            )
            actual = (
                judgment.observation_id,
                judgment.rubric_id,
                judgment.rubric_version,
                judgment.criterion_id,
            )
            if actual != expected:
                raise ValueError("A model judge returned mismatched rubric provenance.")
            judgments.append(judgment)
    return bundle.model_copy(update={"judgments": tuple(judgments)})


def evaluate_rubric(
    bundle: RubricReplayBundle,
    *,
    mode: RubricEvaluationMode = RubricEvaluationMode.DETERMINISTIC,
    generated_at: datetime | None = None,
) -> RubricEvaluationReport:
    """Score every replay observation with deterministic gates always enabled."""

    cases = {case.case_id: case for case in bundle.cases}
    judgment_map = {
        (judgment.observation_id, judgment.criterion_id): judgment for judgment in bundle.judgments
    }
    model_criteria = tuple(
        criterion
        for criterion in bundle.rubric.criteria
        if criterion.mode is RubricCriterionMode.MODEL_JUDGE
    )
    if mode is RubricEvaluationMode.HYBRID and not model_criteria:
        raise ValueError("Hybrid evaluation requires model-judge criteria.")

    results: list[RubricCaseScore] = []
    used_judgments = 0
    for observation in bundle.observations:
        case = cases[observation.case_id]
        criterion_scores: list[CriterionScore] = []
        skipped: list[str] = []
        for criterion in bundle.rubric.criteria:
            if criterion.mode is RubricCriterionMode.DETERMINISTIC:
                checker = _DETERMINISTIC_CHECKERS.get(criterion.evaluator)
                if checker is None:
                    raise ValueError(
                        f"Unknown deterministic rubric evaluator: {criterion.evaluator}"
                    )
                checked = checker(case, observation)
                score = checked.score
                public_reason = checked.public_reason
                provenance = checked.provenance
            elif mode is RubricEvaluationMode.DETERMINISTIC:
                skipped.append(criterion.criterion_id)
                continue
            else:
                judgment = judgment_map.get((observation.observation_id, criterion.criterion_id))
                if judgment is None:
                    raise ValueError(
                        "Hybrid evaluation requires one judgment per model criterion "
                        f"and observation: {observation.observation_id}/"
                        f"{criterion.criterion_id}"
                    )
                score = judgment.score
                public_reason = judgment.public_reason
                provenance = (
                    f"provider={judgment.provider}",
                    f"model={judgment.model}",
                    f"promptVersion={judgment.prompt_version}",
                )
                used_judgments += 1

            criterion_scores.append(
                CriterionScore(
                    criterion_id=criterion.criterion_id,
                    dimension=criterion.dimension,
                    mode=criterion.mode,
                    score=score,
                    threshold=criterion.threshold,
                    weight=criterion.weight,
                    weighted_score=score * criterion.weight,
                    required=criterion.required,
                    passes=score >= criterion.threshold,
                    public_reason=public_reason,
                    provenance=provenance,
                )
            )

        if not criterion_scores:
            raise ValueError("Rubric evaluation produced no criterion scores.")
        total_weight = sum(result.weight for result in criterion_scores)
        score = sum(result.weighted_score for result in criterion_scores) / total_weight
        passes = score >= bundle.rubric.pass_threshold and all(
            result.passes for result in criterion_scores if result.required
        )
        results.append(
            RubricCaseScore(
                case_id=case.case_id,
                observation_id=observation.observation_id,
                source=observation.source,
                score=score,
                pass_threshold=bundle.rubric.pass_threshold,
                passes=passes,
                criteria=tuple(criterion_scores),
                skipped_criterion_ids=tuple(skipped),
            )
        )

    passed = sum(1 for result in results if result.passes)
    failed = len(results) - passed
    timestamp = (generated_at or datetime.now(UTC)).astimezone(UTC)
    return RubricEvaluationReport(
        suite_id=bundle.suite_id,
        rubric_id=bundle.rubric.rubric_id,
        rubric_version=bundle.rubric.version,
        rubric_sha256=_rubric_hash(bundle.rubric),
        evaluation_mode=mode,
        generated_at=timestamp,
        observations=len(results),
        passed=passed,
        failed=failed,
        mean_score=sum(result.score for result in results) / len(results),
        model_judgments=used_judgments,
        passes=failed == 0,
        results=tuple(results),
    )


def observation_from_execution(
    *,
    case: RubricCase,
    observation_id: str,
    execution: ExecutionSummary,
) -> RubricObservation:
    """Project a canonical execution summary into a content-bounded trace replay."""

    agent_by_step = {step.step_id: step.agent_id for step in execution.plan.steps}
    actual_plan = tuple(
        RubricPlanStep(
            agent_id=step.agent_id,
            depends_on_agent_ids=tuple(
                sorted(agent_by_step[dependency] for dependency in step.depends_on)
            ),
        )
        for step in execution.plan.steps
    )
    evidence = execution.synthesis.evidence
    delivered_outputs = tuple(
        artifact.artifact_id for step in execution.steps for artifact in step.artifacts
    )
    return RubricObservation(
        observation_id=observation_id,
        case_id=case.case_id,
        rubric_id=case.rubric_id,
        rubric_version=case.rubric_version,
        source=RubricObservationSource.EXECUTION_TRACE,
        selected_agent_ids=tuple(reason.agent_id for reason in execution.selection.selected),
        actual_plan=actual_plan,
        synthesis_status=execution.synthesis.status,
        execution_status=execution.status,
        answer=execution.synthesis.answer,
        evidence_ids=tuple(record.evidence_id for record in evidence.records),
        cited_evidence_ids=tuple(citation.evidence_id for citation in evidence.citations),
        total_claims=execution.synthesis.coverage.total_claims,
        grounded_claims=execution.synthesis.coverage.grounded_claims,
        conflict_claim_ids=execution.synthesis.conflict_claim_ids,
        visible_failure_ids=tuple(
            dict.fromkeys(issue.agent_id for issue in execution.synthesis.issues)
        ),
        delivered_output_ids=delivered_outputs,
    )


def write_rubric_report(
    report: RubricEvaluationReport,
    output_dir: Path = DEFAULT_REPORT_ROOT,
    *,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
) -> RubricReportPaths:
    """Persist JSON, escaped local HTML, latest aliases, and bounded history."""

    if history_limit < 1:
        raise ValueError("Rubric report history limit must be at least one.")
    output_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = output_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    stamp = report.generated_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    stem = _safe_stem(
        f"{stamp}-{report.rubric_id}-{report.rubric_version}-{report.evaluation_mode.value}"
    )
    json_text = (
        json.dumps(
            report.model_dump(mode="json", by_alias=True),
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
        )
        + "\n"
    )
    html_text = _render_html(report)
    json_path = runs_dir / f"{stem}.json"
    html_path = runs_dir / f"{stem}.html"
    latest_json_path = output_dir / "latest.json"
    latest_html_path = output_dir / "latest.html"

    _atomic_write(json_path, json_text)
    _atomic_write(html_path, html_text)
    _atomic_write(latest_json_path, json_text)
    _atomic_write(latest_html_path, html_text)
    _prune_history(runs_dir, history_limit)
    return RubricReportPaths(
        json_path=json_path,
        html_path=html_path,
        latest_json_path=latest_json_path,
        latest_html_path=latest_html_path,
    )


def _routing_exact(case: RubricCase, observation: RubricObservation) -> _DeterministicResult:
    passes = set(observation.selected_agent_ids) == set(case.expected_agent_ids)
    return _binary_result(
        passes,
        "Selected-agent set matches the labelled expectation.",
        "Selected-agent set differs from the labelled expectation.",
        (
            f"expected={_join_ids(case.expected_agent_ids)}",
            f"actual={_join_ids(observation.selected_agent_ids)}",
        ),
    )


def _orchestration_exact(case: RubricCase, observation: RubricObservation) -> _DeterministicResult:
    expected = _normalized_plan(case.expected_plan)
    actual = _normalized_plan(observation.actual_plan)
    passes = actual == expected
    return _binary_result(
        passes,
        "Execution DAG matches the labelled dependency graph.",
        "Execution DAG differs from the labelled dependency graph.",
        (f"expected={expected or '<none>'}", f"actual={actual or '<none>'}"),
    )


def _grounded_claims(case: RubricCase, observation: RubricObservation) -> _DeterministicResult:
    if case.expected_synthesis_status is SynthesisStatus.DECLINED:
        passes = observation.total_claims == 0 and observation.grounded_claims == 0
    else:
        passes = (
            observation.total_claims > 0 and observation.grounded_claims == observation.total_claims
        )
    return _binary_result(
        passes,
        "Every applicable factual claim is grounded.",
        "One or more applicable factual claims are not grounded.",
        (
            f"totalClaims={observation.total_claims}",
            f"groundedClaims={observation.grounded_claims}",
        ),
    )


def _evidence_coverage(case: RubricCase, observation: RubricObservation) -> _DeterministicResult:
    expected = set(case.expected_evidence_ids)
    actual = set(observation.evidence_ids)
    cited = set(observation.cited_evidence_ids)
    passes = actual == expected and cited == expected
    return _binary_result(
        passes,
        "Expected structured evidence is present and cited exactly once globally.",
        "Structured evidence or citation coverage differs from the expectation.",
        (
            f"expected={_join_ids(case.expected_evidence_ids)}",
            f"evidence={_join_ids(observation.evidence_ids)}",
            f"cited={_join_ids(observation.cited_evidence_ids)}",
        ),
    )


def _honest_decline(case: RubricCase, observation: RubricObservation) -> _DeterministicResult:
    expected_decline = case.expected_synthesis_status is SynthesisStatus.DECLINED
    if expected_decline:
        passes = (
            observation.synthesis_status is SynthesisStatus.DECLINED
            and observation.execution_status is ExecutionStatus.DECLINED
            and not observation.evidence_ids
            and observation.grounded_claims == 0
        )
    else:
        passes = (
            observation.synthesis_status is not SynthesisStatus.DECLINED
            and observation.execution_status is not ExecutionStatus.DECLINED
        )
    return _binary_result(
        passes,
        "The response honestly follows the labelled answer-or-decline boundary.",
        "The response violates the labelled answer-or-decline boundary.",
        (
            f"expectedSynthesis={case.expected_synthesis_status.value}",
            f"actualSynthesis={observation.synthesis_status.value}",
            f"actualExecution={observation.execution_status.value}",
        ),
    )


def _conflict_visible(case: RubricCase, observation: RubricObservation) -> _DeterministicResult:
    if case.expected_conflict:
        passes = (
            bool(observation.conflict_claim_ids)
            and observation.synthesis_status is SynthesisStatus.PARTIAL
            and observation.execution_status is ExecutionStatus.PARTIAL
        )
    else:
        passes = not observation.conflict_claim_ids
    return _binary_result(
        passes,
        "Conflict handling matches the labelled expectation.",
        "A required conflict is hidden or an unexpected conflict is reported.",
        (
            f"expectedConflict={str(case.expected_conflict).lower()}",
            f"conflictClaims={_join_ids(observation.conflict_claim_ids)}",
        ),
    )


def _response_completeness(
    case: RubricCase, observation: RubricObservation
) -> _DeterministicResult:
    answer = observation.answer.casefold()
    missing_terms = tuple(
        term for term in case.required_answer_terms if term.casefold() not in answer
    )
    forbidden_terms = tuple(
        term for term in case.forbidden_answer_terms if term.casefold() in answer
    )
    missing_outputs = tuple(
        output_id
        for output_id in case.required_output_ids
        if output_id not in observation.delivered_output_ids
    )
    passes = not missing_terms and not forbidden_terms and not missing_outputs
    return _binary_result(
        passes,
        "Required answer elements and outputs are complete and in scope.",
        "Required answer elements are missing, forbidden content appears, or outputs are absent.",
        (
            f"missingTerms={_join_ids(missing_terms)}",
            f"forbiddenTerms={_join_ids(forbidden_terms)}",
            f"missingOutputs={_join_ids(missing_outputs)}",
        ),
    )


def _task_success(case: RubricCase, observation: RubricObservation) -> _DeterministicResult:
    passes = observation.execution_status is case.expected_execution_status and set(
        observation.visible_failure_ids
    ) == set(case.expected_visible_failure_ids)
    return _binary_result(
        passes,
        "Terminal task status and visible failures match the labelled outcome.",
        "Terminal task status or visible failures differ from the labelled outcome.",
        (
            f"expectedStatus={case.expected_execution_status.value}",
            f"actualStatus={observation.execution_status.value}",
            f"expectedFailures={_join_ids(case.expected_visible_failure_ids)}",
            f"actualFailures={_join_ids(observation.visible_failure_ids)}",
        ),
    )


_DETERMINISTIC_CHECKERS: Mapping[str, _Checker] = {
    "routing_exact": _routing_exact,
    "orchestration_exact": _orchestration_exact,
    "grounded_claims": _grounded_claims,
    "evidence_coverage": _evidence_coverage,
    "honest_decline": _honest_decline,
    "conflict_visible": _conflict_visible,
    "response_completeness": _response_completeness,
    "task_success": _task_success,
}


def _binary_result(
    passes: bool,
    pass_reason: str,
    fail_reason: str,
    provenance: tuple[str, ...],
) -> _DeterministicResult:
    return _DeterministicResult(
        score=1.0 if passes else 0.0,
        public_reason=pass_reason if passes else fail_reason,
        provenance=provenance,
    )


def _normalized_plan(
    plan: tuple[RubricPlanStep, ...],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(sorted((step.agent_id, tuple(sorted(step.depends_on_agent_ids))) for step in plan))


def _join_ids(values: tuple[str, ...]) -> str:
    return ",".join(values) if values else "<none>"


def _rubric_hash(rubric: RubricDefinition) -> str:
    payload = json.dumps(
        rubric.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _render_html(report: RubricEvaluationReport) -> str:
    status = "PASS" if report.passes else "FAIL"
    case_sections: list[str] = []
    for result in report.results:
        criterion_rows = "".join(
            "<tr>"
            f"<td>{html.escape(criterion.dimension.value)}</td>"
            f"<td>{html.escape(criterion.criterion_id)}</td>"
            f"<td>{html.escape(criterion.mode.value)}</td>"
            f"<td>{criterion.score:.3f}</td>"
            f"<td>{'PASS' if criterion.passes else 'FAIL'}</td>"
            f"<td>{html.escape(criterion.public_reason)}</td>"
            f"<td>{html.escape('; '.join(criterion.provenance))}</td>"
            "</tr>"
            for criterion in result.criteria
        )
        skipped = (
            html.escape(", ".join(result.skipped_criterion_ids))
            if result.skipped_criterion_ids
            else "None"
        )
        case_sections.append(
            "<section>"
            f"<h2>{html.escape(result.observation_id)} - "
            f"{'PASS' if result.passes else 'FAIL'} ({result.score:.3f})</h2>"
            f"<p>Case: {html.escape(result.case_id)} | "
            f"Source: {html.escape(result.source.value)} | "
            f"Skipped: {skipped}</p>"
            "<table><thead><tr><th>Dimension</th><th>Criterion</th>"
            "<th>Mode</th><th>Score</th><th>Status</th><th>Reason</th>"
            f"<th>Provenance</th></tr></thead><tbody>{criterion_rows}</tbody></table>"
            "</section>"
        )
    generated = report.generated_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(report.rubric_id)} scorecard</title>"
        "<style>"
        "body{font:15px system-ui;margin:2rem;background:#10141d;color:#eef2ff}"
        "main{max-width:1200px;margin:auto}section{margin:2rem 0;padding:1rem;"
        "background:#192131;border-radius:10px;overflow:auto}"
        "table{border-collapse:collapse;width:100%}th,td{padding:.55rem;"
        "border:1px solid #52617a;text-align:left;vertical-align:top}"
        "th{background:#243149}code{word-break:break-all}</style></head><body><main>"
        f"<h1>{html.escape(report.rubric_id)} {html.escape(report.rubric_version)}"
        f" - {status}</h1>"
        f"<p>Mode: {html.escape(report.evaluation_mode.value)} | "
        f"Mean: {report.mean_score:.3f} | Passed: {report.passed}/"
        f"{report.observations} | Model judgments: {report.model_judgments} | "
        f"Generated: {html.escape(generated)}</p>"
        f"<p><code>{html.escape(report.rubric_sha256)}</code></p>"
        f"{''.join(case_sections)}</main></body></html>\n"
    )


def _safe_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    if not stem:
        raise ValueError("Rubric report filename cannot be empty.")
    return stem


def _prune_history(runs_dir: Path, history_limit: int) -> None:
    stems = sorted({path.stem for path in runs_dir.glob("*.json")}, reverse=True)
    for stem in stems[history_limit:]:
        for suffix in (".json", ".html"):
            path = runs_dir / f"{stem}{suffix}"
            if path.exists():
                path.unlink()


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)
