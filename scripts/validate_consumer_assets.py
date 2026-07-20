"""Validate the complete consumer product asset set and print its stable fingerprint."""

from __future__ import annotations

import hashlib
import json

from agent_iq.catalog import load_consumer_assets, render_registry_projection
from agent_iq.catalog.loader import DEFAULT_CONSUMER_DATA

EXPECTED_AGENTS = 24
EXPECTED_SKILLS = 48
EXPECTED_DOCUMENTS = 54
EXPECTED_CASES = 42
EXPECTED_BINDINGS = 9
EXPECTED_LIVE_SMOKES = 24
EXPECTED_HERO_TOOLS = 20
EXPECTED_TOOL_CASES = 20


def main() -> int:
    assets = load_consumer_assets()
    counts = {
        "agents": len(assets.catalog.agents),
        "skills": sum(len(agent.skills) for agent in assets.catalog.agents),
        "documents": len(assets.documents),
        "cases": len(assets.discovery_cases),
        "bindings": len(assets.knowledge_bindings.bindings),
        "liveSmokes": len(
            json.loads((DEFAULT_CONSUMER_DATA / "live_smoke.json").read_text(encoding="ascii"))[
                "agents"
            ]
        ),
        "heroTools": sum(len(definition.tools) for definition in assets.runtime.agents),
        "toolCases": len(
            [
                line
                for line in (DEFAULT_CONSUMER_DATA / "tool_cases.jsonl")
                .read_text(encoding="ascii")
                .splitlines()
                if line.strip()
            ]
        ),
    }
    expected = {
        "agents": EXPECTED_AGENTS,
        "skills": EXPECTED_SKILLS,
        "documents": EXPECTED_DOCUMENTS,
        "cases": EXPECTED_CASES,
        "bindings": EXPECTED_BINDINGS,
        "liveSmokes": EXPECTED_LIVE_SMOKES,
        "heroTools": EXPECTED_HERO_TOOLS,
        "toolCases": EXPECTED_TOOL_CASES,
    }
    if counts != expected:
        print(
            "Fabrikam Consumer Products (synthetic demo) asset validation failed: "
            f"expected {expected}, got {counts}"
        )
        return 1

    projection = render_registry_projection(assets.catalog).encode("ascii")
    fingerprint = hashlib.sha256(projection).hexdigest()
    print(
        "Fabrikam Consumer Products (synthetic demo) assets: "
        f"PASS {counts} projection_sha256={fingerprint}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
