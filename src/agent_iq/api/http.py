"""FastAPI and SSE ingress over the protocol-neutral Agent IQ service."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.types import Lifespan

from agent_iq.contracts import (
    AgentSearchResult,
    AgUiProjection,
    CallerContext,
    ErrorCode,
    ErrorDetail,
    EvidenceRecord,
    ExecutionSnapshot,
    FeedbackReceipt,
    FeedbackSubmission,
    OrchestrationCommand,
    OrchestrationResult,
    ProductCapabilities,
)
from agent_iq.errors import AgentIqOperationError, operation_error
from agent_iq.evidence import EvidenceResolver

from .service import AgentIqService

ReadinessProbe = Callable[[], bool | Awaitable[bool]]


def create_http_app(
    service: AgentIqService,
    *,
    readiness_probe: ReadinessProbe | None = None,
    evidence_resolver: EvidenceResolver | None = None,
    lifespan: Lifespan[FastAPI] | None = None,
) -> FastAPI:
    """Create the conventional HTTP/SSE adapter without owning service state."""

    app = FastAPI(
        title="Agent IQ API",
        version="1.0",
        description="Discover and orchestrate trusted A2A agents.",
        lifespan=lifespan,
    )

    @app.exception_handler(AgentIqOperationError)
    async def handle_operation_error(
        request: Request,
        exc: AgentIqOperationError,
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=_http_status(exc.error.code),
            content=exc.error.model_dump(mode="json", by_alias=True),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        correlation_id = request.headers.get("x-request-id")
        details = tuple(
            ErrorDetail(
                field=".".join(str(part) for part in error["loc"])[:512] or None,
                issue=str(error["msg"])[:16_384],
            )
            for error in exc.errors()
        )
        error = operation_error(
            ErrorCode.INVALID_REQUEST,
            "The request does not match the Agent IQ API contract.",
            details=details,
            correlation_id=correlation_id,
        ).error
        return JSONResponse(
            status_code=422,
            content=error.model_dump(mode="json", by_alias=True),
        )

    @app.get("/healthz", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    @app.get("/readyz", include_in_schema=False)
    async def ready() -> JSONResponse:
        available = True
        if readiness_probe is not None:
            result = readiness_probe()
            available = await result if inspect.isawaitable(result) else result
        return JSONResponse(
            status_code=200 if available else 503,
            content={"status": "ready" if available else "not_ready"},
        )

    @app.get("/v1/capabilities", response_model=ProductCapabilities)
    async def capabilities() -> ProductCapabilities:
        return service.capabilities()

    @app.post("/v1/agents/search", response_model=AgentSearchResult)
    async def search_agents(command: OrchestrationCommand) -> AgentSearchResult:
        return service.search_agents(command)

    @app.post("/v1/orchestrate", response_model=OrchestrationResult)
    async def orchestrate(command: OrchestrationCommand) -> OrchestrationResult:
        return await service.orchestrate(command)

    @app.post("/v1/executions", response_model=ExecutionSnapshot, status_code=202)
    async def start_execution(command: OrchestrationCommand) -> ExecutionSnapshot:
        return await service.start(command)

    @app.get("/v1/executions/{execution_id}", response_model=ExecutionSnapshot)
    async def get_execution(execution_id: str) -> ExecutionSnapshot:
        return await service.get_execution(execution_id)

    @app.get(
        "/v1/executions/{execution_id}/ag-ui",
        response_model=AgUiProjection,
    )
    async def get_ag_ui_projection(
        execution_id: str,
        thread_id: str = Query(
            ...,
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ) -> AgUiProjection:
        return await service.get_agui_projection(execution_id, thread_id=thread_id)

    @app.delete("/v1/executions/{execution_id}", response_model=ExecutionSnapshot)
    async def cancel_execution(execution_id: str) -> ExecutionSnapshot:
        return await service.cancel_execution(execution_id)

    @app.post("/v1/feedback", response_model=FeedbackReceipt)
    async def submit_feedback(submission: FeedbackSubmission) -> FeedbackReceipt:
        return await service.submit_feedback(submission)

    @app.get("/v1/executions/{execution_id}/events")
    async def execution_events(
        execution_id: str,
        after: int = Query(default=-1, ge=-1),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        await service.get_execution(execution_id)
        cursor = _event_cursor(after, last_event_id)

        async def stream() -> AsyncIterator[str]:
            async for event in service.watch_events(
                execution_id,
                after_sequence=cursor,
            ):
                payload = event.model_dump_json(by_alias=True)
                yield f"id: {event.sequence}\nevent: {event.payload.type}\ndata: {payload}\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    if evidence_resolver is not None:

        @app.get(
            "/v1/executions/{execution_id}/evidence/{evidence_id}",
            response_model=EvidenceRecord,
        )
        async def resolve_evidence(
            execution_id: str,
            evidence_id: str,
            subject_id: str = Header(alias="X-Agent-IQ-Subject"),
            tenant_id: str = Header(alias="X-Agent-IQ-Tenant"),
        ) -> EvidenceRecord:
            return evidence_resolver.resolve(
                execution_id,
                evidence_id,
                caller=CallerContext(subject_id=subject_id, tenant_id=tenant_id),
            )

    return app


def _event_cursor(after: int, last_event_id: str | None) -> int:
    if last_event_id is None:
        return after
    try:
        parsed = int(last_event_id)
    except ValueError as exc:
        raise operation_error(
            ErrorCode.INVALID_REQUEST,
            "Last-Event-ID must be an integer event sequence.",
        ) from exc
    if parsed < -1:
        raise operation_error(
            ErrorCode.INVALID_REQUEST,
            "Last-Event-ID cannot be less than -1.",
        )
    return max(after, parsed)


def _http_status(code: ErrorCode) -> int:
    return {
        ErrorCode.INVALID_REQUEST: 400,
        ErrorCode.CONTRACT_VERSION_UNSUPPORTED: 400,
        ErrorCode.AUTHENTICATION_REQUIRED: 401,
        ErrorCode.AUTHORIZATION_DENIED: 403,
        ErrorCode.POLICY_BLOCKED: 403,
        ErrorCode.AGENT_INELIGIBLE: 403,
        ErrorCode.AGENT_NOT_FOUND: 404,
        ErrorCode.CANCELLED: 409,
        ErrorCode.ARTIFACT_CONFLICT: 409,
        ErrorCode.INVOCATION_TIMEOUT: 504,
        ErrorCode.CATALOG_UNAVAILABLE: 503,
        ErrorCode.EVIDENCE_UNAVAILABLE: 502,
        ErrorCode.ARTIFACT_UNAVAILABLE: 502,
        ErrorCode.INVOCATION_FAILED: 502,
        ErrorCode.PROTOCOL_UNSUPPORTED: 502,
        ErrorCode.CARD_INVALID: 502,
        ErrorCode.DEPENDENCY_FAILED: 502,
        ErrorCode.SYNTHESIS_FAILED: 502,
        ErrorCode.INTERNAL: 500,
    }[code]
