"""Immutable WP-01 memory-off routing baseline."""

from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from agent_iq.catalog import load_consumer_assets
from agent_iq.config import MemoryMode, Settings
from agent_iq.contracts import CallerContext, MatchFilters, MatchRequest
from agent_iq.match import MatchEngine

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "wp01" / "memory-off-selection.json"


def test_memory_off_selection_matches_wp01_baseline() -> None:
    baseline = json.loads(FIXTURE.read_text(encoding="utf-8"))
    request_data = baseline["request"]
    settings = Settings(_env_file=None)
    assert settings.memory_mode is MemoryMode.DISABLED

    decision = MatchEngine(
        load_consumer_assets().catalog,
        semantic_mode="offline",
        settings=settings,
    ).match(
        MatchRequest(
            request_id=request_data["requestId"],
            intent=request_data["intent"],
            caller=CallerContext(
                subject_id=request_data["subjectId"],
                tenant_id=request_data["tenantId"],
            ),
            filters=MatchFilters(
                max_candidates=request_data["maxCandidates"],
                max_selected=request_data["maxSelected"],
            ),
            requested_at=datetime.fromisoformat(request_data["requestedAt"].replace("Z", "+00:00")),
        )
    )
    wire = decision.model_dump_json(by_alias=True)

    assert f"sha256:{sha256(wire.encode('utf-8')).hexdigest()}" == baseline["decisionSha256"]
    assert [candidate.agent_id for candidate in decision.candidates] == baseline[
        "candidateAgentIds"
    ]
    assert [reason.agent_id for reason in decision.selected] == baseline["selectedAgentIds"]
