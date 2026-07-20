from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import pytest
from a2a.types import AgentCard
from fastapi.testclient import TestClient

import agent_iq.api.host as host_module
from agent_iq.api import AgentIqService
from agent_iq.api.artifacts import PublisherPrincipal
from agent_iq.api.host import (
    FoundryReadiness,
    ProductReadiness,
    build_live_resources,
    create_product_app,
)
from agent_iq.artifacts import CompanionArtifactStore
from agent_iq.catalog import FoundryAgentRecord, FoundryCardState
from agent_iq.catalog.adapters.foundry import FoundryInventoryRecord
from agent_iq.config import Settings
from agent_iq.contracts import (
    ArtifactKind,
    ArtifactProducerIdentity,
    CallerContext,
    DemandScenario,
    HealthStatus,
)
from agent_iq.evidence import EvidenceResolver


class FakeTokenProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.closed = False

    async def get_token(self, scope: str) -> str:
        assert scope
        self.calls += 1
        return "test-token"

    async def close(self) -> None:
        self.closed = True


class FakeFoundryCatalog:
    project_endpoint = "https://project.example.test"

    def __init__(
        self,
        records: tuple[FoundryAgentRecord, ...],
        *,
        fail: bool = False,
    ) -> None:
        self.records = records
        self.fail = fail
        self.calls = 0
        self.inventory_calls = 0
        self.get_calls = 0
        self.closed = False

    async def list_agents(self, *, refresh: bool = False) -> tuple[FoundryAgentRecord, ...]:
        del refresh
        self.calls += 1
        if self.fail:
            raise RuntimeError("readiness failure")
        return self.records

    async def list_inventory(self, *, refresh: bool = False) -> tuple[FoundryInventoryRecord, ...]:
        del refresh
        self.inventory_calls += 1
        if self.fail:
            raise RuntimeError("readiness failure")
        return tuple(
            FoundryInventoryRecord(agent_id=record.agent_id, status=record.status)
            for record in self.records
        )

    async def get_agent(
        self,
        agent_id: str,
        *,
        refresh: bool = False,
    ) -> FoundryAgentRecord:
        del refresh
        self.get_calls += 1
        record = next((item for item in self.records if item.agent_id == agent_id), None)
        if record is None:
            raise RuntimeError(f"missing agent {agent_id}")
        return record

    def reconcile_card(
        self,
        record: FoundryAgentRecord,
        payload: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> FoundryCardState:
        assert record.agent_id in payload["name"]
        assert correlation_id == "readiness"
        return FoundryCardState(
            agent_id=record.agent_id,
            health=HealthStatus.HEALTHY,
            served_hash=f"sha256:{'a' * 64}",
            expected_hash=f"sha256:{'a' * 64}",
            drifted_fields=(),
        )

    async def close(self) -> None:
        self.closed = True


class FakeCardResolver:
    def __init__(
        self,
        http: object,
        base_url: str,
        agent_card_path: str,
    ) -> None:
        del http
        self.base_url = base_url
        assert agent_card_path == "/agentCard/v1.0"

    async def get_agent_card(self) -> AgentCard:
        agent_id = self.base_url.split("/agents/")[1].split("/")[0]
        return AgentCard(name=f"card-{agent_id}")


def test_foundry_readiness_certifies_identity_inventory_cards_and_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(host_module, "A2ACardResolver", FakeCardResolver)
        records = (
            FoundryAgentRecord(agent_id="agent-1", version="1.0.0", status="active"),
            FoundryAgentRecord(agent_id="agent-2", version="1.0.0", status="active"),
        )
        catalog = FakeFoundryCatalog(records)
        token = FakeTokenProvider()
        readiness = FoundryReadiness(
            catalog,
            token,
            frozenset({"agent-1", "agent-2"}),
        )

        assert await readiness.check(refresh=True)
        assert await readiness.check()
        assert catalog.calls == 1
        assert token.calls == 1

        live_records = (
            FoundryAgentRecord(agent_id="agent-1", version="1.0.0", status="active"),
            FoundryAgentRecord(agent_id="agent-2", version="1.0.0", status="active"),
            *(
                FoundryAgentRecord(
                    agent_id=f"unrelated-{index}",
                    version="legacy",
                    status="active",
                )
                for index in range(18)
            ),
        )
        live_catalog = FakeFoundryCatalog(live_records)
        live_readiness = FoundryReadiness(
            live_catalog,
            FakeTokenProvider(),
            frozenset({"agent-1", "agent-2"}),
            inventory_candidate_ids=frozenset(record.agent_id for record in live_records),
        )
        assert await live_readiness.check(refresh=True)
        assert live_catalog.calls == 0
        assert live_catalog.inventory_calls == 1
        assert live_catalog.get_calls == 2

        inactive = FoundryReadiness(
            FakeFoundryCatalog(
                (
                    FoundryAgentRecord(
                        agent_id="agent-1",
                        version="1.0.0",
                        status="failed",
                    ),
                )
            ),
            FakeTokenProvider(),
            frozenset({"agent-1"}),
        )
        assert not await inactive.check(refresh=True)

        failed = FoundryReadiness(
            FakeFoundryCatalog((), fail=True),
            FakeTokenProvider(),
            frozenset({"agent-1"}),
        )
        assert not await failed.check(refresh=True)

    asyncio.run(run())


def test_live_resource_factory_and_close_use_keyless_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed = {"catalog": False, "runtime": False, "retriever": False}

    class CatalogFactory(FakeFoundryCatalog):
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            super().__init__(())

        async def close(self) -> None:
            closed["catalog"] = True

    class FakeRuntime:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def close(self) -> None:
            closed["runtime"] = True

    class FakeRetriever:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        def close(self) -> None:
            closed["retriever"] = True

    token = FakeTokenProvider()
    monkeypatch.setattr(host_module, "DefaultAzureBearerTokenProvider", lambda: token)
    monkeypatch.setattr(host_module, "FoundryCatalogAdapter", CatalogFactory)
    monkeypatch.setattr(host_module, "NativeA2ARuntime", FakeRuntime)
    monkeypatch.setattr(host_module, "FoundryIQEvidenceRetriever", FakeRetriever)

    settings = Settings(
        _env_file=None,
        foundry_project_endpoint="https://example.services.ai.azure.com/api/projects/test",
        azure_ai_account="example",
        azure_ai_project="test",
        search_service="search-test",
        artifact_publish_endpoint=(
            "https://agent-iq-orchestrator-canary.bluefield-12345678."
            "swedencentral.azurecontainerapps.io/v1/artifacts/publish"
        ),
        artifact_companion_application_id="11111111-1111-4111-8111-abcdefabcdef",
        artifact_companion_tenant_id="22222222-2222-4222-8222-abcdefabcdef",
    )
    resources = build_live_resources(
        settings,
        public_base_url="https://agent-iq.example.test",
    )
    assert resources.service.capabilities().southbound_transport == "native_a2a"
    assert resources.service.capabilities().artifact_publication
    assert resources.artifact_store is not None
    assert resources.artifact_token_validator is not None
    assert resources.readiness._expected_agent_ids == frozenset(
        {"consumer-formulation-rd", "employee-vacation-leave"}
    )
    inventory_candidate_ids = resources.readiness._inventory_candidate_ids
    assert inventory_candidate_ids is not None
    assert inventory_candidate_ids == (
        resources.single_agent_demo_candidate_ids | resources.vacation_demo_candidate_ids
    )
    assert len(inventory_candidate_ids) == 25
    asyncio.run(resources.close())
    assert closed == {"catalog": True, "runtime": True, "retriever": True}
    assert token.closed

    with pytest.raises(ValueError, match="FOUNDRY_PROJECT_ENDPOINT"):
        build_live_resources(
            Settings(_env_file=None),
            public_base_url="https://agent-iq.example.test",
        )


def test_product_app_lifespan_starts_mcp_and_closes_resources(
    service_factory: Callable[..., AgentIqService],
) -> None:
    class Ready:
        def __init__(self) -> None:
            self.calls = 0

        async def check(self, *, refresh: bool = False) -> bool:
            assert refresh
            self.calls += 1
            return True

    class AppResources:
        def __init__(self) -> None:
            self.service = service_factory()
            self.resolver = EvidenceResolver("https://agent-iq.example.test")
            self.ready = Ready()
            self.readiness: ProductReadiness = self.ready
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    async def run() -> None:
        resources = AppResources()
        app = create_product_app(
            resources,
            public_base_url="https://agent-iq.example.test",
        )
        async with app.router.lifespan_context(app):
            assert resources.ready.calls == 1
        assert resources.closed
        paths = {getattr(route, "path", "") for route in app.routes}
        assert "/a2a" in paths
        assert any(
            route.__class__.__name__ == "Mount" and getattr(route, "path", None) == ""
            for route in app.routes
        )

    asyncio.run(run())


def test_product_app_artifact_route_precedes_root_mcp_mount(
    service_factory: Callable[..., AgentIqService],
) -> None:
    now = datetime(2026, 7, 13, tzinfo=UTC)
    caller = CallerContext(subject_id="caller-1", tenant_id="tenant-1")
    producer = ArtifactProducerIdentity(
        agent_id="consumer-demand-forecasting",
        agent_version="7",
        identity_id="live-foundry-principal",
        framework="PydanticAI",
    )
    store = CompanionArtifactStore(
        clock=lambda: now,
        ticket_factory=lambda: f"apt-{'a' * 32}",
    )
    ticket = store.authorize_invocation(
        invocation_id="inv-route",
        correlation_id="corr-route",
        caller=caller,
        producer=producer,
        allowed_kinds=(ArtifactKind.DEMAND_SCENARIO,),
    )

    class Ready:
        async def check(self, *, refresh: bool = False) -> bool:
            del refresh
            return True

    class Validator:
        async def validate(self, token: str) -> PublisherPrincipal:
            assert token == "publisher-token"
            return PublisherPrincipal(
                tenant_id=caller.tenant_id,
                object_id=producer.identity_id,
            )

    class AppResources:
        def __init__(self) -> None:
            self.service = service_factory()
            self.resolver = EvidenceResolver("https://agent-iq.example.test")
            self.readiness = Ready()
            self.artifact_store = store
            self.artifact_token_validator = Validator()

        async def close(self) -> None:
            return None

    app = create_product_app(
        AppResources(),
        public_base_url="https://agent-iq.example.test",
    )
    artifact_index = next(
        index
        for index, route in enumerate(app.routes)
        if getattr(route, "path", None) == "/v1/artifacts/publish"
        or any(
            getattr(nested, "path", None) == "/v1/artifacts/publish"
            for nested in getattr(getattr(route, "original_router", None), "routes", ())
        )
    )
    mount_index = next(
        index
        for index, route in enumerate(app.routes)
        if route.__class__.__name__ == "Mount" and getattr(route, "path", None) == ""
    )
    assert artifact_index < mount_index

    artifact = DemandScenario(
        artifact_id="demand-route",
        product_id="brightwash-cold",
        market="Germany",
        horizon_start=date(2026, 10, 1),
        horizon_end=date(2026, 10, 31),
        unit="cases",
        baseline_quantity=1000,
        forecast_quantity=1250,
        confidence=0.84,
        evidence_ids=("evidence-route",),
        created_at=now,
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/artifacts/publish",
            headers={
                "Authorization": "Bearer publisher-token",
                "X-Agent-IQ-Publication-Ticket": ticket.secret.get_secret_value(),
            },
            json={"artifact": artifact.model_dump(mode="json", by_alias=True)},
        )
    assert response.status_code == 200
