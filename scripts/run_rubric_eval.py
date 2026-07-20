"""Replay sanitized transcripts and traces through the versioned release rubric."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_iq.contracts import RubricEvaluationMode
from agent_iq.evals import (
    DEFAULT_RUBRIC_BUNDLE_PATH,
    DEFAULT_RUBRIC_REPORT_ROOT,
    evaluate_rubric,
    load_rubric_bundle,
    write_rubric_report,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, default=DEFAULT_RUBRIC_BUNDLE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_RUBRIC_REPORT_ROOT)
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in RubricEvaluationMode],
        default=RubricEvaluationMode.DETERMINISTIC.value,
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    bundle = load_rubric_bundle(args.bundle.resolve())
    mode = RubricEvaluationMode(args.mode)
    report = evaluate_rubric(bundle, mode=mode)
    paths = write_rubric_report(report, args.output.resolve())
    skipped = sum(len(result.skipped_criterion_ids) for result in report.results)
    summary = {
        "rubricId": report.rubric_id,
        "rubricVersion": report.rubric_version,
        "mode": report.evaluation_mode.value,
        "observations": report.observations,
        "passed": report.passed,
        "failed": report.failed,
        "meanScore": report.mean_score,
        "modelJudgments": report.model_judgments,
        "skippedCriteria": skipped,
        "passes": report.passes,
        "report": str(paths.latest_json_path),
    }
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        status = "PASS" if report.passes else "FAIL"
        print(
            f"Rubric evaluation: {status} | mode={report.evaluation_mode.value} | "
            f"{report.passed}/{report.observations} | mean={report.mean_score:.3f} | "
            f"model={report.model_judgments} | skipped={skipped}"
        )
        print(f"Scorecard: {paths.latest_html_path}")
    return 0 if report.passes else 1


if __name__ == "__main__":
    raise SystemExit(main())
