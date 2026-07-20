from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from agent_iq.api import AgentIqService, create_http_app
from agent_iq.contracts import (
    FeedbackRating,
    FeedbackSubmission,
    OrchestrationCommand,
    OrchestrationResult,
)
from agent_iq.evidence import EvidenceResolver


def test_http_and_sse_return_the_normalized_result_contract(
    service_factory: Callable[..., AgentIqService],
    orchestration_command: OrchestrationCommand,
) -> None:
    async def run() -> None:
        service = service_factory()
        app = create_http_app(service)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://agent-iq.example.test",
        ) as client:
            response = await client.post(
                "/v1/orchestrate",
                json=orchestration_command.model_dump(mode="json", by_alias=True),
            )
            assert response.status_code == 200
            result = OrchestrationResult.model_validate(response.json())

            events = await client.get(f"/v1/executions/{result.summary.execution_id}/events")
            assert events.status_code == 200
            assert events.headers["content-type"].startswith("text/event-stream")
            assert "event: agents.selected" in events.text
            assert "event: execution.completed" in events.text

            snapshot = await client.get(f"/v1/executions/{result.summary.execution_id}")
            assert snapshot.status_code == 200
            assert snapshot.json()["summary"] == response.json()["summary"]

            projection = await client.get(
                f"/v1/executions/{result.summary.execution_id}/ag-ui",
                params={"thread_id": "thread-http-1"},
            )
            assert projection.status_code == 200
            projection_payload = projection.json()
            assert projection_payload["executionId"] == result.summary.execution_id
            assert projection_payload["threadId"] == "thread-http-1"
            serialized_projection = projection.text
            assert orchestration_command.request.intent not in serialized_projection
            assert orchestration_command.request.caller.subject_id not in serialized_projection

            invalid_thread = await client.get(
                f"/v1/executions/{result.summary.execution_id}/ag-ui",
                params={"thread_id": "../not-allowed"},
            )
            assert invalid_thread.status_code == 422

        schema = app.openapi()
        response_schema = schema["paths"]["/v1/orchestrate"]["post"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        assert response_schema == {"$ref": "#/components/schemas/OrchestrationResult"}

    asyncio.run(run())


def test_http_readiness_and_contract_errors_fail_closed(
    service_factory: Callable[..., AgentIqService],
    orchestration_command: OrchestrationCommand,
) -> None:
    async def run() -> None:
        app = create_http_app(service_factory(), readiness_probe=lambda: False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://agent-iq.example.test",
        ) as client:
            assert (await client.get("/readyz")).status_code == 503
            payload = orchestration_command.model_dump(mode="json", by_alias=True)
            payload["request"]["caller"]["subjectId"] = ""
            response = await client.post("/v1/orchestrate", json=payload)
            assert response.status_code == 422
            assert response.json()["code"] == "invalid_request"

    asyncio.run(run())


def test_http_exposes_search_capabilities_feedback_and_identity_bound_evidence(
    service_factory: Callable[..., AgentIqService],
    orchestration_command: OrchestrationCommand,
) -> None:
    async def ready() -> bool:
        return True

    async def run() -> None:
        service = service_factory()
        resolver = EvidenceResolver("https://agent-iq.example.test")
        app = create_http_app(
            service,
            readiness_probe=ready,
            evidence_resolver=resolver,
        )
        payload = orchestration_command.model_dump(mode="json", by_alias=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://agent-iq.example.test",
        ) as client:
            assert (await client.get("/healthz")).json() == {"status": "healthy"}
            assert (await client.get("/readyz")).status_code == 200
            capabilities = await client.get("/v1/capabilities")
            assert capabilities.json()["ingressTransports"] == ["mcp", "http_sse", "a2a"]

            search = await client.post("/v1/agents/search", json=payload)
            assert search.status_code == 200
            assert search.json()["candidates"][0]["agentId"] == "consumer-formulation-rd"

            response = await client.post("/v1/orchestrate", json=payload)
            result = OrchestrationResult.model_validate(response.json())
            resolver.publish(
                result.summary.synthesis.evidence,
                caller=orchestration_command.request.caller,
            )
            evidence_id = result.summary.synthesis.evidence.records[0].evidence_id
            evidence_url = f"/v1/executions/{result.summary.execution_id}/evidence/{evidence_id}"
            evidence = await client.get(
                evidence_url,
                headers={
                    "X-Agent-IQ-Subject": "caller-1",
                    "X-Agent-IQ-Tenant": "tenant-1",
                },
            )
            assert evidence.status_code == 200
            denied = await client.get(
                evidence_url,
                headers={
                    "X-Agent-IQ-Subject": "another-caller",
                    "X-Agent-IQ-Tenant": "tenant-1",
                },
            )
            assert denied.status_code == 403

            feedback = FeedbackSubmission(
                feedback_id="feedback-http-1",
                execution_id=result.summary.execution_id,
                subject_id="caller-1",
                tenant_id="tenant-1",
                rating=FeedbackRating.HELPFUL,
                submitted_at=datetime.now(UTC),
            )
            receipt = await client.post(
                "/v1/feedback",
                json=feedback.model_dump(mode="json", by_alias=True),
            )
            assert receipt.status_code == 200
            assert receipt.json()["accepted"]

            invalid_cursor = await client.get(
                f"/v1/executions/{result.summary.execution_id}/events",
                headers={"Last-Event-ID": "not-an-integer"},
            )
            assert invalid_cursor.status_code == 400
            missing = await client.get("/v1/executions/exec-missing")
            assert missing.status_code == 400
            cannot_cancel = await client.delete(f"/v1/executions/{result.summary.execution_id}")
            assert cannot_cancel.status_code == 400

    asyncio.run(run())
