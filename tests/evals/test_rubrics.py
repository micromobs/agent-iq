from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent_iq.contracts import (
    CriterionScore,
    ExecutionStatus,
    ExecutionSummary,
    GroundingCoverage,
    ModelJudgment,
    RubricCase,
    RubricCaseScore,
    RubricCriterion,
    RubricCriterionMode,
    RubricDefinition,
    RubricEvaluationMode,
    RubricEvaluationReport,
    RubricObservation,
    RubricPlanStep,
    RubricReplayBundle,
    StepExecutionResult,
    StepExecutionStatus,
    SynthesisResult,
    SynthesisStatus,
)
from agent_iq.evals.rubrics import (
    DEFAULT_BUNDLE_PATH,
    attach_model_judgments,
    evaluate_rubric,
    load_rubric_bundle,
    observation_from_execution,
    write_rubric_report,
)
from contract.samples import NOW, evidence, plan, selection


class PassingJudge:
    def __init__(self, *, score: float = 0.9, reason: str = "The answer meets the criterion."):
        self.score = score
        self.reason = reason

    def judge(
        self,
        *,
        rubric: RubricDefinition,
        criterion: RubricCriterion,
        case: RubricCase,
        observation: RubricObservation,
    ) -> ModelJudgment:
        del rubric, case
        return ModelJudgment(
            observation_id=observation.observation_id,
            rubric_id=observation.rubric_id,
            rubric_version=observation.rubric_version,
            criterion_id=criterion.criterion_id,
            provider="test-provider",
            model="test-model",
            prompt_version="1.0.0",
            score=self.score,
            public_reason=self.reason,
        )


class MismatchedJudge(PassingJudge):
    def judge(
        self,
        *,
        rubric: RubricDefinition,
        criterion: RubricCriterion,
        case: RubricCase,
        observation: RubricObservation,
    ) -> ModelJudgment:
        judgment = super().judge(
            rubric=rubric,
            criterion=criterion,
            case=case,
            observation=observation,
        )
        return judgment.model_copy(update={"criterion_id": "wrong-criterion"})


def test_release_bundle_passes_all_deterministic_gates() -> None:
    bundle = load_rubric_bundle()

    report = evaluate_rubric(
        bundle,
        generated_at=datetime(2026, 7, 12, 12, 0, tzinfo=UTC),
    )

    assert report.passes
    assert report.observations == 4
    assert report.passed == 4
    assert report.mean_score == 1
    assert report.model_judgments == 0
    assert {result.source.value for result in report.results} == {
        "cowork_transcript",
        "execution_trace",
    }
    assert all(len(result.criteria) == 8 for result in report.results)
    assert all(len(result.skipped_criterion_ids) == 2 for result in report.results)
    assert report.rubric_sha256.startswith("sha256:")


@pytest.mark.parametrize(
    ("observation_id", "update", "failed_criterion"),
    (
        ("cowork-brightwash-certified", {"selected_agent_ids": ()}, "routing-exact"),
        ("cowork-brightwash-certified", {"actual_plan": ()}, "orchestration-exact"),
        ("cowork-brightwash-certified", {"grounded_claims": 0}, "grounded-claims"),
        ("cowork-brightwash-certified", {"cited_evidence_ids": ()}, "evidence-coverage"),
        (
            "cowork-brightwash-certified",
            {
                "synthesis_status": SynthesisStatus.DECLINED,
                "execution_status": ExecutionStatus.DECLINED,
            },
            "honest-decline",
        ),
        ("trace-conflict-certified", {"conflict_claim_ids": ()}, "conflict-visible"),
        (
            "cowork-brightwash-certified",
            {"answer": "Protease, amylase, and mannanase [1]."},
            "response-completeness",
        ),
        (
            "cowork-brightwash-certified",
            {"execution_status": ExecutionStatus.PARTIAL},
            "task-success",
        ),
    ),
)
def test_each_deterministic_dimension_fails_closed(
    observation_id: str,
    update: dict[str, object],
    failed_criterion: str,
) -> None:
    bundle = load_rubric_bundle()
    observations = tuple(
        observation.model_copy(update=update)
        if observation.observation_id == observation_id
        else observation
        for observation in bundle.observations
    )

    report = evaluate_rubric(bundle.model_copy(update={"observations": observations}))
    result = next(item for item in report.results if item.observation_id == observation_id)
    criterion = next(item for item in result.criteria if item.criterion_id == failed_criterion)

    assert not criterion.passes
    assert not result.passes
    assert not report.passes


def test_hybrid_mode_requires_and_audits_every_model_judgment() -> None:
    bundle = load_rubric_bundle()

    with pytest.raises(ValueError, match="requires one judgment"):
        evaluate_rubric(bundle, mode=RubricEvaluationMode.HYBRID)

    judged = attach_model_judgments(bundle, PassingJudge())
    report = evaluate_rubric(judged, mode=RubricEvaluationMode.HYBRID)

    assert len(judged.judgments) == 8
    assert report.passes
    assert report.model_judgments == 8
    assert all(len(result.criteria) == 10 for result in report.results)
    assert all(not result.skipped_criterion_ids for result in report.results)
    model_scores = [
        criterion
        for result in report.results
        for criterion in result.criteria
        if criterion.mode is RubricCriterionMode.MODEL_JUDGE
    ]
    assert {item.provenance for item in model_scores} == {
        (
            "provider=test-provider",
            "model=test-model",
            "promptVersion=1.0.0",
        )
    }

    with pytest.raises(ValueError, match="already contains"):
        attach_model_judgments(judged, PassingJudge())
    with pytest.raises(ValueError, match="mismatched rubric provenance"):
        attach_model_judgments(bundle, MismatchedJudge())


def test_model_judgments_supplement_but_cannot_mask_deterministic_failure() -> None:
    bundle = load_rubric_bundle()
    first = bundle.observations[0].model_copy(update={"selected_agent_ids": ()})
    failing = bundle.model_copy(update={"observations": (first, *bundle.observations[1:])})
    judged = attach_model_judgments(failing, PassingJudge(score=1.0))

    report = evaluate_rubric(judged, mode=RubricEvaluationMode.HYBRID)

    assert not report.passes
    routing = next(
        criterion
        for criterion in report.results[0].criteria
        if criterion.criterion_id == "routing-exact"
    )
    assert not routing.passes


def test_hybrid_model_threshold_is_enforced() -> None:
    judged = attach_model_judgments(load_rubric_bundle(), PassingJudge(score=0.5))

    report = evaluate_rubric(judged, mode=RubricEvaluationMode.HYBRID)

    assert not report.passes
    assert report.failed == report.observations


def test_unknown_deterministic_evaluator_is_rejected() -> None:
    bundle = load_rubric_bundle()
    criterion = bundle.rubric.criteria[0].model_copy(update={"evaluator": "unknown-checker"})
    rubric = bundle.rubric.model_copy(update={"criteria": (criterion, *bundle.rubric.criteria[1:])})

    with pytest.raises(ValueError, match="Unknown deterministic"):
        evaluate_rubric(bundle.model_copy(update={"rubric": rubric}))


def test_replay_contract_rejects_version_drift_and_invalid_judgments() -> None:
    payload = json.loads(DEFAULT_BUNDLE_PATH.read_text(encoding="utf-8"))
    payload["observations"][0]["rubricVersion"] = "2.0.0"
    with pytest.raises(ValidationError, match="version"):
        RubricReplayBundle.model_validate(payload)

    payload = json.loads(DEFAULT_BUNDLE_PATH.read_text(encoding="utf-8"))
    payload["judgments"] = [
        {
            "observationId": "cowork-brightwash-certified",
            "rubricId": "agent-iq-release",
            "rubricVersion": "1.0.0",
            "criterionId": "routing-exact",
            "provider": "test",
            "model": "test",
            "promptVersion": "1.0.0",
            "score": 1.0,
            "publicReason": "Invalid deterministic judgment.",
        }
    ]
    with pytest.raises(ValidationError, match="model-judge criterion"):
        RubricReplayBundle.model_validate(payload)


def test_rubric_and_plan_contracts_reject_ambiguous_definitions() -> None:
    payload = json.loads(DEFAULT_BUNDLE_PATH.read_text(encoding="utf-8"))
    rubric = payload["rubric"]
    duplicate = deepcopy(rubric)
    duplicate["criteria"] = [rubric["criteria"][0], rubric["criteria"][0]]
    with pytest.raises(ValidationError, match="criterion IDs"):
        RubricDefinition.model_validate(duplicate)

    model_only = deepcopy(rubric)
    model_only["criteria"] = [item for item in rubric["criteria"] if item["mode"] == "model_judge"]
    with pytest.raises(ValidationError, match="deterministic criterion"):
        RubricDefinition.model_validate(model_only)

    with pytest.raises(ValidationError, match="dependencies must be unique"):
        RubricPlanStep(
            agent_id="agent-1",
            depends_on_agent_ids=("agent-2", "agent-2"),
        )
    with pytest.raises(ValidationError, match="depend on itself"):
        RubricPlanStep(agent_id="agent-1", depends_on_agent_ids=("agent-1",))


def test_case_contract_rejects_inconsistent_expectations() -> None:
    payload = json.loads(DEFAULT_BUNDLE_PATH.read_text(encoding="utf-8"))
    base = payload["cases"][0]
    invalid_cases: tuple[tuple[dict[str, Any], str], ...] = (
        (
            {
                "expectedAgentIds": [
                    "consumer-formulation-rd",
                    "consumer-formulation-rd",
                ]
            },
            "identifiers must be unique",
        ),
        (
            {"expectedPlan": [base["expectedPlan"][0], base["expectedPlan"][0]]},
            "plan agents must be unique",
        ),
        ({"expectedPlan": []}, "must match expected selection"),
        (
            {"expectedVisibleFailureIds": ["unknown-agent"]},
            "failures must reference selected agents",
        ),
        (
            {
                "requiredAnswerTerms": ["Protease"],
                "forbiddenAnswerTerms": ["protease"],
            },
            "must not overlap",
        ),
        (
            {
                "expectedSynthesisStatus": "declined",
                "expectedExecutionStatus": "declined",
            },
            "decline cannot require evidence",
        ),
    )
    for update, message in invalid_cases:
        case_payload = deepcopy(base)
        case_payload.update(update)
        with pytest.raises(ValidationError, match=message):
            RubricCase.model_validate(case_payload)

    conflict = deepcopy(payload["cases"][2])
    conflict["expectedSynthesisStatus"] = "complete"
    with pytest.raises(ValidationError, match="conflict requires a partial"):
        RubricCase.model_validate(conflict)

    unknown_dependency = deepcopy(base)
    unknown_dependency["expectedPlan"][0]["dependsOnAgentIds"] = ["unknown-agent"]
    with pytest.raises(ValidationError, match="reference plan agents"):
        RubricCase.model_validate(unknown_dependency)

    cycle = deepcopy(payload["cases"][2])
    cycle["expectedPlan"][0]["dependsOnAgentIds"] = ["consumer-regulatory-affairs"]
    with pytest.raises(ValidationError, match="acyclic"):
        RubricCase.model_validate(cycle)


def test_observation_contract_rejects_unverifiable_facts() -> None:
    payload = json.loads(DEFAULT_BUNDLE_PATH.read_text(encoding="utf-8"))
    base = payload["observations"][0]
    invalid_observations: tuple[tuple[dict[str, Any], str], ...] = (
        (
            {
                "selectedAgentIds": [
                    "consumer-formulation-rd",
                    "consumer-formulation-rd",
                ]
            },
            "identifiers must be unique",
        ),
        (
            {"actualPlan": [base["actualPlan"][0], base["actualPlan"][0]]},
            "plan agents must be unique",
        ),
        ({"totalClaims": 1, "groundedClaims": 2}, "cannot exceed"),
        ({"citedEvidenceIds": ["unknown-evidence"]}, "must reference observed evidence"),
        ({"visibleFailureIds": ["unknown-agent"]}, "must reference selected agents"),
    )
    for update, message in invalid_observations:
        observation_payload = deepcopy(base)
        observation_payload.update(update)
        with pytest.raises(ValidationError, match=message):
            RubricObservation.model_validate(observation_payload)


def test_replay_bundle_rejects_ambiguous_references() -> None:
    payload = json.loads(DEFAULT_BUNDLE_PATH.read_text(encoding="utf-8"))
    invalid_bundles: list[tuple[dict[str, Any], str]] = []

    duplicate_case = deepcopy(payload)
    duplicate_case["cases"].append(deepcopy(duplicate_case["cases"][0]))
    invalid_bundles.append((duplicate_case, "case IDs must be unique"))

    duplicate_observation = deepcopy(payload)
    duplicate_observation["observations"].append(deepcopy(duplicate_observation["observations"][0]))
    invalid_bundles.append((duplicate_observation, "observation IDs must be unique"))

    unknown_case = deepcopy(payload)
    unknown_case["observations"][0]["caseId"] = "unknown-case"
    invalid_bundles.append((unknown_case, "reference a known case"))

    unobserved_case = deepcopy(payload)
    added_case = deepcopy(unobserved_case["cases"][0])
    added_case["caseId"] = "unobserved-case"
    unobserved_case["cases"].append(added_case)
    invalid_bundles.append((unobserved_case, "requires at least one observation"))

    for bundle_payload, message in invalid_bundles:
        with pytest.raises(ValidationError, match=message):
            RubricReplayBundle.model_validate(bundle_payload)

    judged = attach_model_judgments(load_rubric_bundle(), PassingJudge())
    judged_payload = judged.model_dump(mode="json", by_alias=True)
    judged_payload["judgments"].append(deepcopy(judged_payload["judgments"][0]))
    with pytest.raises(ValidationError, match="unique per observation"):
        RubricReplayBundle.model_validate(judged_payload)

    judged_payload = judged.model_dump(mode="json", by_alias=True)
    judged_payload["judgments"][0]["observationId"] = "unknown-observation"
    with pytest.raises(ValidationError, match="known observation"):
        RubricReplayBundle.model_validate(judged_payload)


def test_scorecard_contracts_reject_internally_inconsistent_scores() -> None:
    report = evaluate_rubric(load_rubric_bundle())
    report_payload = report.model_dump(mode="json", by_alias=True)
    criterion_payload = report_payload["results"][0]["criteria"][0]

    bad_criterion = deepcopy(criterion_payload)
    bad_criterion["weightedScore"] = 0.5
    with pytest.raises(ValidationError, match="weighted score"):
        CriterionScore.model_validate(bad_criterion)
    bad_criterion = deepcopy(criterion_payload)
    bad_criterion["passes"] = False
    with pytest.raises(ValidationError, match="pass state"):
        CriterionScore.model_validate(bad_criterion)

    case_payload = report_payload["results"][0]
    invalid_cases: tuple[tuple[dict[str, Any], str], ...] = (
        (
            {"criteria": [*case_payload["criteria"], case_payload["criteria"][0]]},
            "criterion IDs must be unique",
        ),
        (
            {"skippedCriterionIds": ["skip-1", "skip-1"]},
            "Skipped criterion IDs must be unique",
        ),
        (
            {"skippedCriterionIds": [case_payload["criteria"][0]["criterionId"]]},
            "both evaluated and skipped",
        ),
        ({"score": 0.5}, "score does not match"),
        ({"passes": False}, "pass state does not match"),
    )
    for update, message in invalid_cases:
        invalid = deepcopy(case_payload)
        invalid.update(update)
        with pytest.raises(ValidationError, match=message):
            RubricCaseScore.model_validate(invalid)

    invalid_reports: tuple[tuple[dict[str, Any], str], ...] = (
        ({"observations": 5}, "observation count"),
        ({"failed": 1}, "pass and failure counts"),
        ({"passed": 3, "failed": 1}, "pass count"),
        ({"meanScore": 0.5}, "mean score"),
        ({"passes": False}, "pass state"),
        (
            {
                "observations": 5,
                "passed": 5,
                "results": [*report_payload["results"], report_payload["results"][0]],
            },
            "observation IDs must be unique",
        ),
    )
    for update, message in invalid_reports:
        invalid = deepcopy(report_payload)
        invalid.update(update)
        with pytest.raises(ValidationError, match=message):
            RubricEvaluationReport.model_validate(invalid)


def test_model_modes_fail_closed_without_applicable_criteria() -> None:
    bundle = load_rubric_bundle()
    deterministic = tuple(
        criterion
        for criterion in bundle.rubric.criteria
        if criterion.mode is RubricCriterionMode.DETERMINISTIC
    )
    deterministic_only = bundle.model_copy(
        update={"rubric": bundle.rubric.model_copy(update={"criteria": deterministic})}
    )
    with pytest.raises(ValueError, match="does not define model-judge"):
        attach_model_judgments(deterministic_only, PassingJudge())
    with pytest.raises(ValueError, match="requires model-judge"):
        evaluate_rubric(deterministic_only, mode=RubricEvaluationMode.HYBRID)

    model_only = tuple(
        criterion
        for criterion in bundle.rubric.criteria
        if criterion.mode is RubricCriterionMode.MODEL_JUDGE
    )
    invalid_model_only = bundle.model_copy(
        update={"rubric": bundle.rubric.model_copy(update={"criteria": model_only})}
    )
    with pytest.raises(ValueError, match="produced no criterion scores"):
        evaluate_rubric(invalid_model_only)


def test_loader_wraps_malformed_input(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(ValueError, match="Cannot load"):
        load_rubric_bundle(path)


def test_report_writer_escapes_html_and_bounds_history(tmp_path: Path) -> None:
    judged = attach_model_judgments(
        load_rubric_bundle(),
        PassingJudge(reason="<script>alert('unsafe')</script>"),
    )
    report = evaluate_rubric(
        judged,
        mode=RubricEvaluationMode.HYBRID,
        generated_at=datetime(2026, 7, 12, 12, 0, tzinfo=UTC),
    )

    for offset in range(3):
        write_rubric_report(
            report.model_copy(
                update={"generated_at": report.generated_at + timedelta(seconds=offset)}
            ),
            tmp_path,
            history_limit=2,
        )

    assert len(tuple((tmp_path / "runs").glob("*.json"))) == 2
    assert len(tuple((tmp_path / "runs").glob("*.html"))) == 2
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert latest["passes"] is True
    rendered = (tmp_path / "latest.html").read_text(encoding="utf-8")
    assert "<script>alert" not in rendered
    assert "&lt;script&gt;" in rendered

    with pytest.raises(ValueError, match="at least one"):
        write_rubric_report(report, tmp_path, history_limit=0)


def test_execution_summary_projects_to_trace_observation() -> None:
    synthesis = SynthesisResult(
        execution_id="execution-1",
        status=SynthesisStatus.COMPLETE,
        answer="Grounded answer [1].",
        evidence=evidence(),
        coverage=GroundingCoverage(total_claims=1, grounded_claims=1, ratio=1),
        sources_markdown="## Sources\n1. BrightWash Cold active system",
    )
    execution = ExecutionSummary(
        execution_id="execution-1",
        request_id="request-1",
        status=ExecutionStatus.COMPLETED,
        plan=plan(),
        selection=selection(),
        steps=(
            StepExecutionResult(
                step_id="step-1",
                agent_id="consumer-formulation-rd",
                status=StepExecutionStatus.SUCCEEDED,
                invocation_id="invocation-1",
                protocol_version="1.0",
                duration_ms=10,
                answer="Grounded answer [1].",
                evidence_ids=("evidence-1",),
            ),
        ),
        synthesis=synthesis,
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
        duration_ms=1000,
    )
    case = (
        load_rubric_bundle()
        .cases[0]
        .model_copy(
            update={
                "expected_evidence_ids": ("evidence-1",),
                "required_output_ids": (),
            }
        )
    )

    observation = observation_from_execution(
        case=case,
        observation_id="execution-summary-replay",
        execution=execution,
    )

    assert observation.source.value == "execution_trace"
    assert observation.selected_agent_ids == ("consumer-formulation-rd",)
    assert observation.evidence_ids == ("evidence-1",)
    assert observation.cited_evidence_ids == ("evidence-1",)
    assert observation.total_claims == observation.grounded_claims == 1
    assert observation.actual_plan[0].depends_on_agent_ids == ()
