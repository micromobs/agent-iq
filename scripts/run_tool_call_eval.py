"""Run the deterministic Agent IQ hero tool-call gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_iq.catalog import load_consumer_runtime_assets
from agent_iq.catalog.loader import DEFAULT_CONSUMER_DATA
from agent_iq.evals import (
    build_tool_call_payload,
    evaluate_tool_calls,
    load_tool_call_cases,
    write_tool_call_report,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("reports/evals/tool-calls"),
    )
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()

    assets = load_consumer_runtime_assets()
    cases = load_tool_call_cases(DEFAULT_CONSUMER_DATA / "tool_cases.jsonl")
    result = evaluate_tool_calls(assets.runtime, cases)
    tool_count = sum(len(definition.tools) for definition in assets.runtime.agents)
    payload = build_tool_call_payload(result, tool_count=tool_count)
    if not args.no_report:
        write_tool_call_report(payload, args.report_dir)

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Agent IQ hero tools ({payload['cases']} cases, {payload['tools']} tools)")
        for case in result.cases:
            status = (
                "PASS"
                if case.exact_selection
                and case.arguments_valid
                and not case.opaque_catchall_selected
                else "FAIL"
            )
            print(f"{status:<4} {case.case_id:<28} selected={case.selected_tool_id or '<none>'}")
        print(
            f"tool_exact={result.exact_selection_accuracy:.3f} "
            f"arguments_valid={result.argument_validity_accuracy:.3f} "
            f"opaque_catchall={result.opaque_catchall_selections} "
            f"p95_ms={result.p95_latency_ms:.2f}"
        )
        print("RESULT:", "ALL GATES PASSED" if result.passes else "GATES FAILED")
    return 0 if result.passes else 1


if __name__ == "__main__":
    raise SystemExit(main())
