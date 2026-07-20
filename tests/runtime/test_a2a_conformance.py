"""Tests for live-conformance orchestration and safe report persistence."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

import agent_iq.runtime.conformance as conformance
from agent_iq.catalog import FoundryAgentRecord, FoundryCardState
from agent_iq.contracts import ErrorCode, HealthStatus
from agent_iq.errors import operation_error
from agent_iq.runtime.adapters import NegotiatedAgentCard
from agent_iq.runtime.conformance import (
    MAX_RUN_REPORTS,
    RUN_REPORT_PREFIX,
    evaluate_foundry_a2a,
    write_foundry_a2a_report,
)
from agent_iq.runtime.models import NativeA2AResult
from agent_iq.runtime.telemetry import RuntimeTelemetryEvent


class FakeTokenProvider:
    async def get_token(self, scope: str) -> str:
        del scope
        return "test-token"

    async def close(self) -> None:
        return None


def _record(agent_id: str) -> FoundryAgentRecord:
    return FoundryAgentRecord(
        agent_id=agent_id,
        version="2",
        status="active",
        kind="hosted",
    )


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    inventory_error: bool = False,
    card_error_id: str | None = None,
    invocation_error: bool = False,
    answer: str = "Grounded answer. [ref_id:0]",
) -> None:
    class FakeCatalog:
        def __init__(
            self,
            project_endpoint: str,
            *,
            token_provider: object,
            expected_descriptors: tuple[Any, ...],
            cache_ttl_seconds: float,
        ) -> None:
            del project_endpoint, token_provider, cache_ttl_seconds
            self.records = {
                descriptor.agent_id: _record(descriptor.agent_id)
                for descriptor in expected_descriptors
            }

        async def list_agents(self, *, refresh: bool = False) -> tuple[FoundryAgentRecord, ...]:
            assert refresh is True
            if inventory_error:
                raise operation_error(
                    ErrorCode.CATALOG_UNAVAILABLE,
                    "Inventory unavailable.",
                    retryable=True,
                )
            return tuple(self.records.values())

        async def close(self) -> None:
            return None

    class FakeRuntime:
        def __init__(
            self,
            project_endpoint: str,
            *,
            token_provider: object,
            catalog: FakeCatalog,
            telemetry: Any,
        ) -> None:
            del project_endpoint, token_provider
            self.catalog = catalog
            self.telemetry = telemetry

        async def negotiate_card(self, agent_id: str) -> NegotiatedAgentCard:
            if agent_id == card_error_id:
                raise operation_error(ErrorCode.CARD_INVALID, "Card invalid.")
            record = self.catalog.records[agent_id]
            state = FoundryCardState(
                agent_id=agent_id,
                health=HealthStatus.HEALTHY,
                served_hash=f"sha256:{'a' * 64}",
                expected_hash=f"sha256:{'a' * 64}",
                drifted_fields=(),
            )
            return NegotiatedAgentCard(
                agent=record,
                protocol_version="1.0",
                card=object(),
                payload={"name": agent_id},
                state=state,
            )

        async def invoke(self, request: Any) -> NativeA2AResult:
            if invocation_error:
                raise operation_error(ErrorCode.INVOCATION_FAILED, "Invocation failed.")
            await self.telemetry.emit(
                RuntimeTelemetryEvent.create(
                    "invocation.completed",
                    invocation_id="inv-test",
                    agent_id=request.agent_id,
                    attempt=1,
                    duration_ms=10,
                )
            )
            return NativeA2AResult(
                invocation_id="inv-test",
                agent_id=request.agent_id,
                agent_version="2",
                protocol_version="1.0",
                message_id="msg-test",
                answer=answer,
                duration_ms=10,
                attempt_count=1,
            )

        async def close(self) -> None:
            return None

    monkeypatch.setattr(
        conformance,
        "DefaultAzureBearerTokenProvider",
        FakeTokenProvider,
    )
    monkeypatch.setattr(conformance, "FoundryCatalogAdapter", FakeCatalog)
    monkeypatch.setattr(conformance, "NativeA2ARuntime", FakeRuntime)


def test_live_conformance_success_is_content_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fakes(monkeypatch)
    payload = asyncio.run(evaluate_foundry_a2a("https://example.test/project"))
    output = write_foundry_a2a_report(payload, tmp_path)

    assert payload["status"] == "pass"
    assert payload["checks"]["allCardsCanonical"] is True
    assert payload["checks"]["heroSourceReferencePresent"] is True
    assert len(payload["cards"]) == 20
    serialized = output.read_text(encoding="utf-8")
    assert "Grounded answer" not in serialized
    assert conformance.HERO_PROMPT not in serialized
    assert "test-token" not in serialized
    assert payload["telemetry"][0]["name"] == "invocation.completed"


def test_live_conformance_persists_typed_inventory_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fakes(monkeypatch, inventory_error=True)
    payload = asyncio.run(evaluate_foundry_a2a("https://example.test/project"))

    assert payload["status"] == "fail"
    assert payload["checks"]["consumerAgentCount"] == 0
    assert payload["cards"] == []
    assert "catalog_unavailable" in payload["failures"][0]


def test_live_conformance_records_card_and_invocation_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fakes(monkeypatch, card_error_id="consumer-formulation-rd")
    card_payload = asyncio.run(evaluate_foundry_a2a("https://example.test/project"))
    assert card_payload["status"] == "fail"
    assert len(card_payload["cards"]) == 19
    assert card_payload["heroInvocation"] is None

    _install_fakes(monkeypatch, invocation_error=True)
    invocation_payload = asyncio.run(evaluate_foundry_a2a("https://example.test/project"))
    assert invocation_payload["status"] == "fail"
    assert "invocation_failed" in invocation_payload["failures"][0]


def test_live_conformance_rejects_uncited_hero_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fakes(monkeypatch, answer="Uncited answer.")
    payload = asyncio.run(evaluate_foundry_a2a("https://example.test/project"))

    assert payload["status"] == "fail"
    assert payload["heroInvocation"]["sourceReferencePresent"] is False
    assert "source-reference marker" in payload["failures"][0]


def test_conformance_report_writer_is_collision_safe_bounded_and_isolated(
    tmp_path: Path,
) -> None:
    unrelated = tmp_path / "unrelated.json"
    unrelated.write_text("keep", encoding="utf-8")
    unrelated_directory = tmp_path / f"{RUN_REPORT_PREFIX}not-a-run"
    unrelated_directory.mkdir()
    (unrelated_directory / "marker.txt").write_text("keep", encoding="utf-8")
    near_match = tmp_path / f"{RUN_REPORT_PREFIX}not-a-run.json"
    near_match.write_text("keep", encoding="utf-8")

    paths = [
        write_foundry_a2a_report({"schemaVersion": "1.0", "run": index}, tmp_path)
        for index in range(MAX_RUN_REPORTS + 3)
    ]

    managed = [
        path
        for path in tmp_path.iterdir()
        if path.is_file() and conformance._RUN_REPORT_NAME.fullmatch(path.name)
    ]
    assert len(paths) == len({path.name for path in paths})
    assert len(managed) == MAX_RUN_REPORTS
    assert (tmp_path / "latest.json").read_text(encoding="utf-8") in {
        path.read_text(encoding="utf-8") for path in managed
    }
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert near_match.read_text(encoding="utf-8") == "keep"
    assert (unrelated_directory / "marker.txt").read_text(encoding="utf-8") == "keep"
    assert not list(tmp_path.rglob("*.tmp"))


def test_conformance_report_writer_cleans_temp_on_atomic_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_replace(source: object, destination: object) -> None:
        del source, destination
        raise OSError("simulated replace failure")

    monkeypatch.setattr("agent_iq.runtime.conformance.os.replace", fail_replace)

    with pytest.raises(OSError, match="simulated"):
        write_foundry_a2a_report({"schemaVersion": "1.0"}, tmp_path)

    assert not list(tmp_path.rglob("*.tmp"))
