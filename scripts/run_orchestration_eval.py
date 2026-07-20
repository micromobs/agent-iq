"""Run the deterministic Agent IQ selection and dependency-DAG gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_iq.catalog import load_consumer_assets
from agent_iq.catalog.loader import DEFAULT_CONSUMER_DATA
from agent_iq.config import get_settings
from agent_iq.evals import (
    build_orchestration_payload,
    evaluate_orchestration,
    load_orchestration_cases,
    write_orchestration_report,
)
from agent_iq.match import MatchEngine
from agent_iq.orchestrate import ExecutionPlanner


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--semantic",
        choices=["auto", "offline", "azure"],
        default="offline",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("reports/evals/orchestration"),
    )
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()

    assets = load_consumer_assets()
    matcher = MatchEngine(
        assets.catalog,
        semantic_mode=args.semantic,
        settings=get_settings(),
    )
    planner = ExecutionPlanner(assets.catalog)
    result = evaluate_orchestration(
        matcher,
        planner,
        load_orchestration_cases(DEFAULT_CONSUMER_DATA / "orchestration_cases.jsonl"),
    )
    payload = build_orchestration_payload(result, agent_count=len(assets.catalog.agents))
    if not args.no_report:
        write_orchestration_report(payload, args.report_dir)

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"Agent IQ orchestration ({payload['cases']} cases, "
            f"{payload['agents']} agents, semantic={payload['semanticBackend']})"
        )
        for case in result.cases:
            status = (
                "PASS"
                if case.exact_selection and case.exact_dag and not case.false_positives
                else "FAIL"
            )
            print(f"{status:<4} {case.case_id:<36} selected={','.join(case.selected_agents)}")
        print(
            f"selected_exact={result.exact_selection_accuracy:.3f} "
            f"dag_exact={result.exact_dag_accuracy:.3f} "
            f"false_positives={result.false_positives} "
            f"p95_ms={result.p95_latency_ms:.2f}"
        )
        print("RESULT:", "ALL GATES PASSED" if result.passes else "GATES FAILED")
    return 0 if result.passes else 1


if __name__ == "__main__":
    raise SystemExit(main())
