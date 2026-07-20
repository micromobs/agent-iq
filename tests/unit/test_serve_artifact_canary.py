from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import pytest
import uvicorn
from scripts import serve_artifact_canary as artifact_canary

from agent_iq.contracts import ArtifactProducerIdentity, DemandScenario

COMPANION_APPLICATION_ID = "11111111-1111-4111-8111-abcdefabcdef"
EXPECTED_AUDIENCE = COMPANION_APPLICATION_ID
TICKET = "apt-" + "t" * 40


def _argv(tmp_path: Path, application_id: str = COMPANION_APPLICATION_ID) -> list[str]:
    return [
        "--tenant-id",
        "tenant-live",
        "--companion-application-id",
        application_id,
        "--expected-identity",
        "identity-live",
        "--agent-id",
        "consumer-demand-forecasting",
        "--agent-version",
        "6",
        "--invocation-id",
        "inv-live",
        "--correlation-id",
        "corr-live",
        "--artifact-kind",
        "demand_scenario",
        "--output",
        str(tmp_path / "publication.json"),
    ]


def test_canary_derives_validator_audience_and_safe_claim_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeValidator:
        def __init__(self, *, tenant_id: str, audience: str) -> None:
            captured["tenant_id"] = tenant_id
            captured["audience"] = audience

    class FakeApp:
        def get(
            self,
            path: str,
        ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
            def decorator(handler: Callable[..., Any]) -> Callable[..., Any]:
                captured["route"] = path
                return handler

            return decorator

    def create_app(store: object, validator: object) -> FakeApp:
        captured["store"] = store
        captured["validator"] = validator
        return FakeApp()

    def run_server(app: object, **kwargs: object) -> None:
        captured["app"] = app
        captured["server"] = kwargs

    monkeypatch.setenv("AGENT_IQ_LIVE_PUBLICATION_TICKET", TICKET)
    monkeypatch.setenv("AGENT_IQ_CANARY_AUDIENCE", "api://attacker.invalid")
    monkeypatch.setattr(artifact_canary, "EntraPublisherTokenValidator", FakeValidator)
    monkeypatch.setattr(artifact_canary, "create_artifact_companion_app", create_app)
    monkeypatch.setattr(uvicorn, "run", run_server)

    artifact_canary.main(_argv(tmp_path))

    assert captured["tenant_id"] == "tenant-live"
    assert captured["audience"] == EXPECTED_AUDIENCE
    assert captured["route"] == "/v1/certification"
    store = cast(artifact_canary.RecordingArtifactStore, captured["store"])
    store.publish_with_ticket(
        TICKET,
        DemandScenario(
            artifact_id="demand-live",
            product_id="brightwash-cold",
            market="SE",
            horizon_start=date(2026, 7, 12),
            horizon_end=date(2026, 7, 19),
            unit="cases",
            baseline_quantity=100,
            forecast_quantity=110,
            confidence=0.9,
            evidence_ids=("evidence-1",),
            created_at=datetime(2026, 7, 12, tzinfo=UTC),
        ),
        producer=ArtifactProducerIdentity(
            agent_id="consumer-demand-forecasting",
            agent_version="6",
            identity_id="identity-live",
            framework="PydanticAI",
        ),
    )

    assert store.certification is not None
    assert store.certification["publisherAuthentication"] == {
        "expectedAudience": EXPECTED_AUDIENCE,
        "expectedAudienceSha256": (
            "sha256:" + hashlib.sha256(EXPECTED_AUDIENCE.encode("ascii")).hexdigest()
        ),
    }
    serialized = json.dumps(store.certification, sort_keys=True)
    assert TICKET not in serialized
    assert "attacker.invalid" not in serialized


def test_noncanonical_companion_id_fails_before_server_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    side_effects: list[str] = []
    monkeypatch.setenv("AGENT_IQ_LIVE_PUBLICATION_TICKET", TICKET)
    monkeypatch.setattr(
        artifact_canary,
        "create_artifact_companion_app",
        lambda *args, **kwargs: side_effects.append("app"),
    )
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: side_effects.append("server"))

    with pytest.raises(SystemExit):
        artifact_canary.main(_argv(tmp_path, COMPANION_APPLICATION_ID.upper()))

    assert side_effects == []


def test_cli_rejects_removed_free_form_audience(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        artifact_canary.main(
            [
                *_argv(tmp_path),
                "--audience",
                "api://attacker.invalid",
            ]
        )

    assert "unrecognized arguments: --audience" in capsys.readouterr().err
