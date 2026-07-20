"""Live Agent IQ canary composition and readiness certification."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from a2a.client import A2ACardResolver
from fastapi import FastAPI
from google.protobuf.json_format import MessageToDict

from agent_iq.artifacts import CompanionArtifactStore
from agent_iq.catalog import (
    FoundryAgentRecord,
    FoundryCardState,
    FoundryCatalogAdapter,
    LiveDemoInventory,
    build_canonical_single_agent_demo_catalog,
    build_live_vacation_demo_catalog,
    ensure_live_demo_inventory,
    load_consumer_assets,
)
from agent_iq.catalog.adapters.foundry import AZURE_AI_SCOPE, FoundryInventoryRecord
from agent_iq.catalog.live_demo import VACATION_AGENT_ID
from agent_iq.config import Settings
from agent_iq.connectors import DefaultAzureBearerTokenProvider
from agent_iq.contracts import AgentDescriptor, ArtifactProducerIdentity, ErrorCode, HealthStatus
from agent_iq.errors import operation_error
from agent_iq.evidence import (
    CompanionEvidenceStore,
    EvidenceRecordMapper,
    EvidenceResolver,
    FoundryIQEvidenceRetriever,
    StructuredEvidenceDispatcher,
)
from agent_iq.match import MatchEngine
from agent_iq.orchestrate import (
    DagExecutor,
    ExecutionPlanner,
    GroundedSynthesizer,
    OrchestrationEngine,
)
from agent_iq.runtime import NativeA2ARuntime, NativeOnlyDispatcher

from .a2a import add_a2a_ingress
from .artifacts import (
    EntraPublisherTokenValidator,
    PublisherTokenValidator,
    create_artifact_companion_router,
)
from .http import create_http_app
from .mcp import create_mcp_server
from .service import AgentIqService, ExecutionLedger

LOGGER = logging.getLogger(__name__)


class ReadinessCatalog(Protocol):
    project_endpoint: str

    async def list_agents(self, *, refresh: bool = False) -> tuple[FoundryAgentRecord, ...]: ...

    async def list_inventory(
        self,
        *,
        refresh: bool = False,
    ) -> tuple[FoundryInventoryRecord, ...]: ...

    async def get_agent(
        self,
        agent_id: str,
        *,
        refresh: bool = False,
    ) -> FoundryAgentRecord: ...

    def reconcile_card(
        self,
        agent: FoundryAgentRecord,
        payload: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> FoundryCardState: ...


class ReadinessTokenProvider(Protocol):
    async def get_token(self, scope: str) -> str: ...


class ProductReadiness(Protocol):
    async def check(self, *, refresh: bool = False) -> bool: ...


class FoundryArtifactProducerResolver:
    """Bind publication grants to the selected agent's current Foundry identity."""

    def __init__(self, catalog: FoundryCatalogAdapter) -> None:
        self._catalog = catalog

    async def resolve(self, descriptor: AgentDescriptor) -> ArtifactProducerIdentity:
        record = await self._catalog.get_agent(descriptor.agent_id, refresh=True)
        if record.instance_identity is None:
            raise operation_error(
                ErrorCode.AGENT_INELIGIBLE,
                "The live artifact-producing agent has no managed runtime identity.",
            )
        return ArtifactProducerIdentity(
            agent_id=descriptor.agent_id,
            agent_version=record.version,
            identity_id=record.instance_identity,
            framework=descriptor.implementation.framework,
        )


class ProductAppResources(Protocol):
    @property
    def service(self) -> AgentIqService: ...

    @property
    def resolver(self) -> EvidenceResolver: ...

    @property
    def readiness(self) -> ProductReadiness: ...

    async def close(self) -> None: ...


class FoundryReadiness:
    """Cache a full identity, inventory, and canonical-card readiness probe."""

    def __init__(
        self,
        catalog: ReadinessCatalog,
        token_provider: ReadinessTokenProvider,
        expected_agent_ids: frozenset[str],
        *,
        inventory_candidate_ids: frozenset[str] | None = None,
        cache_seconds: float = 60.0,
    ) -> None:
        self._catalog = catalog
        self._token_provider = token_provider
        self._expected_agent_ids = expected_agent_ids
        self._inventory_candidate_ids = inventory_candidate_ids
        self._cache_seconds = cache_seconds
        self._last_check = 0.0
        self._last_result = False
        self._lock = asyncio.Lock()

    async def check(self, *, refresh: bool = False) -> bool:
        async with self._lock:
            now = time.monotonic()
            if not refresh and now - self._last_check <= self._cache_seconds:
                return self._last_result
            try:
                if self._inventory_candidate_ids is None:
                    records = await self._catalog.list_agents(refresh=refresh)
                else:
                    await ensure_live_demo_inventory(
                        self._catalog,
                        self._inventory_candidate_ids,
                    )
                    records = tuple(
                        await asyncio.gather(
                            *(
                                self._catalog.get_agent(agent_id, refresh=refresh)
                                for agent_id in sorted(self._expected_agent_ids)
                            )
                        )
                    )
                by_id = {record.agent_id: record for record in records}
                if any(
                    agent_id not in by_id or by_id[agent_id].status.casefold() != "active"
                    for agent_id in self._expected_agent_ids
                ):
                    result = False
                else:
                    token = await self._token_provider.get_token(AZURE_AI_SCOPE)
                    headers = {"Authorization": f"Bearer {token}"}
                    semaphore = asyncio.Semaphore(5)

                    async def card_healthy(agent_id: str) -> bool:
                        async with semaphore:
                            record = by_id[agent_id]
                            async with httpx.AsyncClient(
                                headers=headers,
                                timeout=httpx.Timeout(30.0),
                                follow_redirects=False,
                                trust_env=False,
                            ) as http:
                                card = await A2ACardResolver(
                                    http,
                                    record.a2a_base_url(self._catalog.project_endpoint),
                                    agent_card_path="/agentCard/v1.0",
                                ).get_agent_card()
                            state = self._catalog.reconcile_card(
                                record,
                                MessageToDict(card),
                                correlation_id="readiness",
                            )
                            return state.health is HealthStatus.HEALTHY

                    checks = await asyncio.gather(
                        *(card_healthy(agent_id) for agent_id in self._expected_agent_ids)
                    )
                    result = all(checks)
            except Exception:
                LOGGER.exception("Agent IQ Foundry readiness certification failed")
                result = False
            self._last_result = result
            self._last_check = now
            return result


@dataclass(slots=True)
class LiveAgentIqResources:
    service: AgentIqService
    single_agent_demo_service: AgentIqService
    single_agent_demo_candidate_ids: frozenset[str]
    vacation_demo_service: AgentIqService
    vacation_demo_candidate_ids: frozenset[str]
    resolver: EvidenceResolver
    readiness: FoundryReadiness
    token_provider: DefaultAzureBearerTokenProvider
    catalog: FoundryCatalogAdapter
    runtime: NativeA2ARuntime
    retriever: FoundryIQEvidenceRetriever
    artifact_store: CompanionArtifactStore | None = None
    artifact_token_validator: PublisherTokenValidator | None = None

    async def preflight_single_agent_demo(self) -> LiveDemoInventory:
        return await ensure_live_demo_inventory(
            self.catalog,
            self.single_agent_demo_candidate_ids,
        )

    async def preflight_vacation_demo(self) -> LiveDemoInventory:
        return await ensure_live_demo_inventory(
            self.catalog,
            self.vacation_demo_candidate_ids,
        )

    async def close(self) -> None:
        self.retriever.close()
        await self.runtime.close()
        await self.catalog.close()
        await self.token_provider.close()


def build_live_resources(
    settings: Settings,
    *,
    public_base_url: str,
) -> LiveAgentIqResources:
    if settings.foundry_project_endpoint is None:
        raise ValueError("AGENT_IQ_FOUNDRY_PROJECT_ENDPOINT is required.")
    assets = load_consumer_assets()
    single_agent_demo_catalog = build_canonical_single_agent_demo_catalog(assets.catalog)
    vacation_demo_catalog = build_live_vacation_demo_catalog(assets.catalog)
    single_agent_demo_candidate_ids = frozenset(
        agent.agent_id for agent in single_agent_demo_catalog.agents
    )
    vacation_demo_candidate_ids = frozenset(
        agent.agent_id for agent in vacation_demo_catalog.agents
    )
    token_provider = DefaultAzureBearerTokenProvider()
    project_endpoint = str(settings.foundry_project_endpoint)
    catalog = FoundryCatalogAdapter(
        project_endpoint,
        token_provider=token_provider,
        expected_descriptors=assets.catalog.agents,
        cache_ttl_seconds=60,
    )
    runtime = NativeA2ARuntime(
        project_endpoint,
        token_provider=token_provider,
        catalog=catalog,
    )
    retriever = FoundryIQEvidenceRetriever(
        settings.search_endpoint(),
        assets=assets,
    )
    evidence_dispatcher = StructuredEvidenceDispatcher(
        NativeOnlyDispatcher(runtime),
        retriever,
        mapper=EvidenceRecordMapper(assets),
        store=CompanionEvidenceStore(),
    )
    resolver = EvidenceResolver(public_base_url)
    matcher = MatchEngine(assets.catalog, semantic_mode="auto", settings=settings)
    planner = ExecutionPlanner(assets.catalog)
    artifact_channel = settings.artifact_channel()
    artifact_store = CompanionArtifactStore() if artifact_channel is not None else None
    synthesizer = GroundedSynthesizer(resolver)
    producer_resolver = (
        FoundryArtifactProducerResolver(catalog) if artifact_store is not None else None
    )
    executor = DagExecutor(
        assets.catalog,
        evidence_dispatcher,
        synthesizer,
        artifact_broker=artifact_store,
        artifact_producer_resolver=producer_resolver,
    )
    ledger = ExecutionLedger()
    service = AgentIqService(
        OrchestrationEngine(matcher, planner, executor),
        ledger=ledger,
        anonymous_canary=settings.allow_anonymous_connector,
        artifact_publication=artifact_store is not None,
    )
    live_demo_executor = DagExecutor(
        single_agent_demo_catalog,
        evidence_dispatcher,
        synthesizer,
        artifact_broker=artifact_store,
        artifact_producer_resolver=producer_resolver,
    )
    single_agent_demo_service = AgentIqService(
        OrchestrationEngine(
            MatchEngine(single_agent_demo_catalog, semantic_mode="auto", settings=settings),
            ExecutionPlanner(single_agent_demo_catalog),
            live_demo_executor,
        ),
        ledger=ledger,
        anonymous_canary=settings.allow_anonymous_connector,
        artifact_publication=artifact_store is not None,
    )
    vacation_demo_executor = DagExecutor(
        vacation_demo_catalog,
        evidence_dispatcher,
        synthesizer,
        artifact_broker=artifact_store,
        artifact_producer_resolver=producer_resolver,
    )
    vacation_demo_service = AgentIqService(
        OrchestrationEngine(
            MatchEngine(vacation_demo_catalog, semantic_mode="auto", settings=settings),
            ExecutionPlanner(vacation_demo_catalog),
            vacation_demo_executor,
        ),
        ledger=ledger,
        anonymous_canary=settings.allow_anonymous_connector,
        artifact_publication=artifact_store is not None,
    )
    readiness = FoundryReadiness(
        catalog,
        token_provider,
        single_agent_demo_candidate_ids | {VACATION_AGENT_ID},
        inventory_candidate_ids=(single_agent_demo_candidate_ids | vacation_demo_candidate_ids),
    )
    artifact_token_validator = (
        EntraPublisherTokenValidator(
            tenant_id=artifact_channel.tenant_id,
            audience=artifact_channel.token_audience,
        )
        if artifact_channel is not None
        else None
    )
    return LiveAgentIqResources(
        service=service,
        single_agent_demo_service=single_agent_demo_service,
        single_agent_demo_candidate_ids=single_agent_demo_candidate_ids,
        vacation_demo_service=vacation_demo_service,
        vacation_demo_candidate_ids=vacation_demo_candidate_ids,
        resolver=resolver,
        readiness=readiness,
        token_provider=token_provider,
        catalog=catalog,
        runtime=runtime,
        retriever=retriever,
        artifact_store=artifact_store,
        artifact_token_validator=artifact_token_validator,
    )


def create_product_app(
    resources: ProductAppResources,
    *,
    public_base_url: str,
    deployment_probe_base_url: str | None = None,
) -> FastAPI:
    mcp = create_mcp_server(
        resources.service,
        public_base_url=public_base_url,
        deployment_probe_base_url=deployment_probe_base_url,
        single_agent_demo_service=getattr(resources, "single_agent_demo_service", None),
        single_agent_demo_preflight=getattr(
            resources,
            "preflight_single_agent_demo",
            None,
        ),
        vacation_demo_service=getattr(resources, "vacation_demo_service", None),
        vacation_demo_preflight=getattr(
            resources,
            "preflight_vacation_demo",
            None,
        ),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        del app
        async with mcp.session_manager.run():
            await resources.readiness.check(refresh=True)
            try:
                yield
            finally:
                await resources.close()

    app = create_http_app(
        resources.service,
        readiness_probe=resources.readiness.check,
        evidence_resolver=resources.resolver,
        lifespan=lifespan,
    )
    add_a2a_ingress(
        app,
        resources.service,
        public_base_url=public_base_url,
    )
    artifact_store = getattr(resources, "artifact_store", None)
    artifact_token_validator = getattr(resources, "artifact_token_validator", None)
    if artifact_store is not None and artifact_token_validator is not None:
        app.include_router(
            create_artifact_companion_router(
                artifact_store,
                artifact_token_validator,
            )
        )
    app.mount("/", mcp.streamable_http_app())
    return app
