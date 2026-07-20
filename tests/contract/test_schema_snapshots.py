"""Committed JSON Schemas stay synchronized with contract models."""

from __future__ import annotations

import json
from pathlib import Path

from agent_iq.contracts.schemas import generate_contract_schemas

ROOT = Path(__file__).resolve().parents[2]


def test_schema_snapshots_match_models() -> None:
    expected = generate_contract_schemas()

    assert len(expected) == 37
    for filename, schema in expected.items():
        path = ROOT / "schemas" / "v1" / filename
        assert path.exists(), f"Missing schema snapshot: {path}"
        assert json.loads(path.read_text(encoding="utf-8")) == schema
