"""Run and persist the Agent IQ live Foundry native-A2A conformance gate."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from agent_iq.runtime.conformance import (
    evaluate_foundry_a2a,
    write_foundry_a2a_report,
)

DEFAULT_PROJECT = os.environ.get("AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT", "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-endpoint",
        default=DEFAULT_PROJECT,
        required=not bool(DEFAULT_PROJECT),
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("reports/evals/foundry-a2a"),
    )
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()

    payload = asyncio.run(evaluate_foundry_a2a(arguments.project_endpoint))
    report = write_foundry_a2a_report(payload, arguments.report_dir)
    if arguments.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        checks = payload["checks"]
        print(
            "Agent IQ Foundry A2A conformance: "
            f"{payload['status'].upper()} "
            f"inventory={checks['consumerAgentCount']}/20 "
            f"cards={len(payload['cards'])}/20 "
            f"hero={'pass' if checks['heroInvoked'] else 'fail'}"
        )
        print(f"REPORT: {report.resolve()}")
        for failure in payload["failures"]:
            print(f"FAIL: {failure}")
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
