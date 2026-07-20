"""Run the deterministic or Azure-backed Agent IQ discovery quality gate."""

from __future__ import annotations

import argparse
import json
import webbrowser
from pathlib import Path

from agent_iq.catalog import load_consumer_assets
from agent_iq.config import get_settings
from agent_iq.evals import build_discovery_payload, write_eval_report
from agent_iq.match import MatchEngine, evaluate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--semantic",
        choices=["auto", "offline", "azure"],
        default="offline",
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("reports/evals/discovery"),
        help="Directory for persisted JSON and HTML reports.",
    )
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--open-report", action="store_true")
    args = parser.parse_args()

    assets = load_consumer_assets()
    engine = MatchEngine(
        assets.catalog,
        semantic_mode=args.semantic,
        settings=get_settings(),
    )
    result = evaluate(engine, assets.discovery_cases, top_k=args.top_k)
    payload = build_discovery_payload(
        result,
        agent_count=len(assets.catalog.agents),
        top_k=args.top_k,
        requested_semantic=args.semantic,
    )
    report_paths = None
    if not args.no_report:
        report_paths = write_eval_report(payload, args.report_dir)
        if args.open_report:
            webbrowser.open(report_paths.latest_html.resolve().as_uri())

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            "Agent IQ discovery "
            f"({payload['cases']} cases, {payload['agents']} agents, "
            f"semantic={payload['semanticBackend']})"
        )
        for case in result.cases:
            status = "PASS" if case.exact and not case.false_positives else "FAIL"
            print(
                f"{status:<4} {case.case_id:<30} "
                f"expected={','.join(case.expected)} "
                f"selected={','.join(case.selected)}"
            )
        print(
            f"recall@k={result.recall_at_k:.3f} "
            f"MRR={result.mean_reciprocal_rank:.3f} "
            f"exact={result.exact_accuracy:.3f} "
            f"false_positives={result.false_positives} "
            f"p95_ms={result.p95_latency_ms:.2f}"
        )
        print("RESULT:", "ALL GATES PASSED" if result.passes else "GATES FAILED")
        if report_paths is not None:
            print(f"REPORT: {report_paths.latest_html.resolve()}")
    return 0 if result.passes else 1


if __name__ == "__main__":
    raise SystemExit(main())
