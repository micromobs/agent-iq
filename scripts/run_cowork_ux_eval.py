"""Grade saved Cowork UX observations against the versioned release gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_iq.evals import (
    DEFAULT_COWORK_UX_BASELINE_PATH,
    DEFAULT_COWORK_UX_CASES_PATH,
    DEFAULT_COWORK_UX_REPORT_ROOT,
    build_cowork_ux_payload,
    evaluate_cowork_ux_suite,
    load_cowork_ux_cases,
    load_cowork_ux_observations,
    write_cowork_ux_report,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_COWORK_UX_CASES_PATH)
    parser.add_argument("--observations", type=Path, default=DEFAULT_COWORK_UX_BASELINE_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_COWORK_UX_REPORT_ROOT)
    parser.add_argument("--run-label", default="historical-baseline")
    parser.add_argument("--allow-failures", action="store_true")
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()

    results = evaluate_cowork_ux_suite(
        load_cowork_ux_cases(arguments.cases),
        load_cowork_ux_observations(arguments.observations),
    )
    payload = build_cowork_ux_payload(results, run_label=arguments.run_label)
    paths = write_cowork_ux_report(payload, arguments.output_root)
    payload["reportPath"] = str(paths.run_json)
    if arguments.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Cowork UX evaluation: {'PASS' if payload['passes'] else 'FAIL'}")
        print(f"Cases: {payload['passedCases']}/{payload['cases']} passed")
        print(f"Report: {paths.run_json}")
    return 0 if payload["passes"] or arguments.allow_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
