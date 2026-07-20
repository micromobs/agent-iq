"""Validate or run the versioned native Microsoft Foundry evaluation suite."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from agent_iq.evals import (
    DEFAULT_FOUNDRY_REPORT_ROOT,
    DEFAULT_FOUNDRY_SUITE_PATH,
    FoundryEvaluationError,
    FoundryTraceSelection,
    OfficialFoundryEvaluationApi,
    evaluate_rubric,
    load_foundry_evaluation_suite,
    load_rubric_bundle,
    probe_azd_evaluation_capability,
    run_native_foundry_evaluation,
    select_foundry_evaluation_mode,
    tooling_from_capability,
    write_foundry_evaluation_certificate,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, default=DEFAULT_FOUNDRY_SUITE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_FOUNDRY_REPORT_ROOT)
    parser.add_argument("--project-endpoint", default=os.environ.get("FOUNDRY_PROJECT_ENDPOINT"))
    parser.add_argument("--account-name", default=os.environ.get("FOUNDRY_ACCOUNT_NAME"))
    parser.add_argument("--project-name", default=os.environ.get("FOUNDRY_PROJECT_NAME"))
    parser.add_argument("--trace-id", action="append", default=[])
    parser.add_argument("--trace-agent-id")
    parser.add_argument("--lookback-hours", type=int, default=1)
    parser.add_argument("--max-traces", type=int, default=50)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    suite = load_foundry_evaluation_suite(args.suite.resolve())
    selected_mode = select_foundry_evaluation_mode(
        suite.protocol,
        suite.interaction_pattern,
    )
    if args.validate_only:
        summary: dict[str, object] = {
            "suiteId": suite.suite_id,
            "suiteVersion": suite.suite_version,
            "targetMode": suite.target_mode.value,
            "selectedMode": selected_mode.value,
            "criteria": len(suite.criteria),
            "cases": len(suite.cases),
            "passes": suite.target_mode is selected_mode,
        }
        print(json.dumps(summary, sort_keys=True))
        return 0 if summary["passes"] else 1

    if not args.project_endpoint:
        parser.error("--project-endpoint or FOUNDRY_PROJECT_ENDPOINT is required")
    if not args.account_name:
        parser.error("--account-name or FOUNDRY_ACCOUNT_NAME is required")
    if not args.project_name:
        parser.error("--project-name or FOUNDRY_PROJECT_NAME is required")

    trace_selection = None
    if args.trace_id or args.trace_agent_id:
        trace_selection = FoundryTraceSelection(
            trace_ids=tuple(args.trace_id),
            agent_id=args.trace_agent_id,
            lookback_hours=args.lookback_hours,
            max_traces=args.max_traces,
        )

    try:
        capability = probe_azd_evaluation_capability()
        tooling = tooling_from_capability(capability)
        deterministic_report = evaluate_rubric(load_rubric_bundle())
        with OfficialFoundryEvaluationApi(
            project_endpoint=args.project_endpoint,
            account_name=args.account_name,
            project_name=args.project_name,
        ) as api:
            certificate = run_native_foundry_evaluation(
                api=api,
                suite=suite,
                deterministic_report=deterministic_report,
                tooling=tooling,
                trace_selection=trace_selection,
            )
        paths = write_foundry_evaluation_certificate(
            certificate,
            args.output.resolve(),
        )
    except FoundryEvaluationError as exc:
        print(f"Native Foundry evaluation failed: {exc}", file=sys.stderr)
        return 2

    summary = {
        "certificateId": certificate.certificate_id,
        "evaluationId": certificate.evaluation_id,
        "runId": certificate.run_id,
        "criteria": len(certificate.criteria),
        "durationMs": certificate.duration_ms,
        "totalTokens": certificate.total_tokens,
        "estimatedPricedModelCostUsd": certificate.estimated_priced_model_cost_usd,
        "passes": certificate.passes,
        "report": str(paths.latest_json_path),
    }
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        status = "PASS" if certificate.passes else "FAIL"
        print(
            f"Native Foundry evaluation: {status} | "
            f"{len(certificate.criteria)} criteria | "
            f"{certificate.duration_ms} ms | {certificate.total_tokens} tokens"
        )
        print(f"Certificate: {paths.latest_html_path}")
    return 0 if certificate.passes else 1


if __name__ == "__main__":
    raise SystemExit(main())
