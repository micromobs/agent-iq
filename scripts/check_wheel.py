"""Verify the wheel contains all Fabrikam Consumer Products synthetic demo assets."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

REQUIRED_FILES = {
    "agent_iq/api/widgets/employee-operations.html",
    "agent_iq/api/widgets/formation.html",
    "agent_iq/api/widgets/historical-result.html",
    "agent_iq/api/widgets/vacation-ring.html",
    "agent_iq/data/consumer/agents.json",
    "agent_iq/data/consumer/discovery_cases.jsonl",
    "agent_iq/data/consumer/knowledge_bases.json",
    "agent_iq/data/consumer/live_smoke.json",
    "agent_iq/data/consumer/orchestration_cases.jsonl",
    "agent_iq/data/consumer/runtime.json",
    "agent_iq/data/consumer/sap_fixtures.json",
    "agent_iq/data/consumer/tool_cases.jsonl",
    "agent_iq/data/consumer/corpora/consumer-market-brand-consumer.jsonl",
    "agent_iq/data/consumer/corpora/consumer-product-innovation.jsonl",
    "agent_iq/data/consumer/corpora/consumer-safety-regulatory-quality.jsonl",
    "agent_iq/data/consumer/corpora/consumer-supply-manufacturing.jsonl",
    "agent_iq/data/consumer/corpora/employee-expense.jsonl",
    "agent_iq/data/consumer/corpora/employee-identity-security.jsonl",
    "agent_iq/data/consumer/corpora/employee-it-helpdesk.jsonl",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()

    with zipfile.ZipFile(args.wheel) as archive:
        names = set(archive.namelist())
        missing = REQUIRED_FILES - names
        if missing:
            print(f"Wheel asset check failed; missing {sorted(missing)[0]}")
            return 1
        catalog = json.loads(archive.read("agent_iq/data/consumer/agents.json"))

    if len(catalog.get("agents", [])) != 24:
        print("Wheel asset check failed; expected 24 agents")
        return 1
    print(f"Wheel assets: PASS ({len(REQUIRED_FILES)} files, 24 agents)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
