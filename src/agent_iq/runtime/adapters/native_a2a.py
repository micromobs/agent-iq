"""Strict Microsoft Foundry native-A2A runtime using the official A2A SDK."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.client.errors import (
    A2AClientError,
    A2AClientTimeoutError,
    AgentCardResolutionError,
)
from a2a.helpers import get_artifact_text, get_message_text
from a2a.types import Message, Part, Role, SendMessageRequest
from a2a.utils.errors import A2AError
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import CredentialUnavailableError
from google.protobuf.json_format import MessageToDict, ParseError
from pydantic import ValidationError

from agent_iq.catalog import FoundryAgentRecord, FoundryCardState, FoundryCatalogAdapter
from agent_iq.config import canonicalize_foundry_project_endpoint
from agent_iq.connectors import BearerTokenProvider, DefaultAzureBearerTokenProvider
from agent_iq.contracts import ErrorCode, ErrorDetail, HealthStatus
from agent_iq.errors import AgentIqOperationError, operation_error
from agent_iq.publication import encode_publication_context_id
from agent_iq.runtime.models import IdempotencyPolicy, NativeA2ARequest, NativeA2AResult
from agent_iq.runtime.telemetry import (
    NullRuntimeTelemetry,
    RuntimeTelemetryEvent,
    RuntimeTelemetrySink,
)

AZURE_AI_SCOPE = "https://ai.azure.com/.default"
_CARD_PATHS = {"1.0": "agentCard/v1.0", "0.3": "agentCard/v0.3"}
_EVIDENCE_MARKER_CONTRACT = (
    "\n\nAgent IQ response contract: Preserve every inline source marker from approved "
    "tool output verbatim next to the claim it supports. Do not create a source marker "
    "that was not present in tool output."
)
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class NegotiatedAgentCard:
    agent: FoundryAgentRecord
    protocol_version: str
    card: Any
    payload: Mapping[str, Any]
    state: FoundryCardState


class NativeA2ARuntime:
    """Invoke only authenticated native A2A endpoints; no local fallback exists.

    Injected transports and factories are trusted dependency-injection boundaries. Factories must
    use the supplied proxy-isolated HTTP client rather than create an alternative network path.
    """

    def __init__(
        self,
        project_endpoint: str,
        *,
        token_provider: BearerTokenProvider | None = None,
        catalog: FoundryCatalogAdapter | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        telemetry: RuntimeTelemetrySink | None = None,
        resolver_factory: Callable[..., Any] = A2ACardResolver,
        client_factory: Callable[..., Awaitable[Any]] = create_client,
        card_mapper: Callable[[Any], Mapping[str, Any]] = MessageToDict,
        event_text: Callable[[Any], str] | None = None,
    ) -> None:
        self.project_endpoint = canonicalize_foundry_project_endpoint(project_endpoint)
        self._token_provider = token_provider or DefaultAzureBearerTokenProvider()
        self._owns_token_provider = token_provider is None
        self.catalog = catalog or FoundryCatalogAdapter(
            self.project_endpoint,
            token_provider=self._token_provider,
            transport=transport,
        )
        self._owns_catalog = catalog is None
        self._transport = transport
        self._telemetry = telemetry or NullRuntimeTelemetry()
        self._resolver_factory = resolver_factory
        self._client_factory = client_factory
        self._card_mapper = card_mapper
        self._event_text = event_text or event_text_from_sdk

    async def __aenter__(self) -> NativeA2ARuntime:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: Any,
    ) -> None:
        del exc_type, exc, traceback
        await self.close()

    async def close(self) -> None:
        if self._owns_catalog:
            await self.catalog.close()
        if self._owns_token_provider:
            await self._token_provider.close()

    async def negotiate_card(
        self,
        agent_id: str,
        *,
        preferred_versions: tuple[str, ...] = ("1.0", "0.3"),
        deadline_seconds: float = 30.0,
        cancel_event: asyncio.Event | None = None,
    ) -> NegotiatedAgentCard:
        """Resolve the best supported authenticated Agent Card."""

        operation_id = f"card-{uuid.uuid4().hex}"
        if deadline_seconds <= 0:
            raise operation_error(
                ErrorCode.INVALID_REQUEST,
                "Agent Card deadline must be positive.",
                correlation_id=operation_id,
            )
        if cancel_event is not None and cancel_event.is_set():
            raise operation_error(
                ErrorCode.CANCELLED,
                "Native A2A card negotiation was cancelled.",
                correlation_id=operation_id,
            )

        async def operation() -> NegotiatedAgentCard:
            agent = await self.catalog.get_agent(agent_id)
            _require_eligible(agent, operation_id)
            token = await self._access_token(operation_id)
            async with httpx.AsyncClient(
                headers={"Authorization": f"Bearer {token}"},
                timeout=httpx.Timeout(deadline_seconds),
                transport=self._transport,
                follow_redirects=False,
                trust_env=False,
            ) as http:
                return await self._negotiate_with_http(
                    http,
                    agent,
                    preferred_versions,
                    operation_id,
                    attempt=1,
                )

        return await _run_with_controls(
            operation(),
            deadline_seconds=deadline_seconds,
            cancel_event=cancel_event,
            correlation_id=operation_id,
        )

    async def invoke(
        self,
        request: NativeA2ARequest,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> NativeA2AResult:
        """Invoke one agent with deadline, cancellation, and guarded retry semantics."""

        invocation_id = request.invocation_id or f"inv-{uuid.uuid4().hex}"
        if cancel_event is not None and cancel_event.is_set():
            raise operation_error(
                ErrorCode.CANCELLED,
                "Native A2A invocation was cancelled.",
                correlation_id=request.correlation_id or invocation_id,
            )
        message_id = _message_id(request)
        started = time.monotonic()
        deadline_at = started + request.deadline_seconds
        last_error: AgentIqOperationError | None = None

        for attempt in range(1, request.max_attempts + 1):
            remaining = deadline_at - time.monotonic()
            if remaining <= 0:
                raise operation_error(
                    ErrorCode.INVOCATION_TIMEOUT,
                    "Native A2A invocation exceeded its deadline.",
                    retryable=True,
                    correlation_id=request.correlation_id or invocation_id,
                )
            await self._emit(
                "invocation.started",
                invocation_id,
                request.agent_id,
                attempt,
                deadline_seconds=round(remaining, 3),
            )
            try:
                result = await _run_with_controls(
                    self._invoke_attempt(
                        request,
                        invocation_id=invocation_id,
                        message_id=message_id,
                        attempt=attempt,
                        timeout_seconds=remaining,
                    ),
                    deadline_seconds=remaining,
                    cancel_event=cancel_event,
                    correlation_id=request.correlation_id or invocation_id,
                )
            except AgentIqOperationError as exc:
                last_error = exc
                await self._emit(
                    "invocation.failed",
                    invocation_id,
                    request.agent_id,
                    attempt,
                    code=exc.error.code.value,
                    retryable=exc.error.retryable,
                )
                if not _can_retry(request, exc, attempt):
                    raise
                await self._emit(
                    "invocation.retrying",
                    invocation_id,
                    request.agent_id,
                    attempt,
                    next_attempt=attempt + 1,
                )
                continue

            duration_ms = round((time.monotonic() - started) * 1000)
            completed = result.model_copy(
                update={
                    "duration_ms": duration_ms,
                    "attempt_count": attempt,
                }
            )
            await self._emit(
                "invocation.completed",
                invocation_id,
                request.agent_id,
                attempt,
                duration_ms=duration_ms,
                protocol_version=completed.protocol_version,
            )
            return completed

        if last_error is not None:
            raise last_error
        raise operation_error(
            ErrorCode.INTERNAL,
            "Native A2A invocation ended without a result.",
            correlation_id=request.correlation_id or invocation_id,
        )

    async def _invoke_attempt(
        self,
        request: NativeA2ARequest,
        *,
        invocation_id: str,
        message_id: str,
        attempt: int,
        timeout_seconds: float,
    ) -> NativeA2AResult:
        correlation_id = request.correlation_id or invocation_id
        try:
            agent = await self.catalog.get_agent(request.agent_id, refresh=attempt > 1)
            _require_eligible(agent, correlation_id)
            token = await self._access_token(correlation_id)
            async with httpx.AsyncClient(
                headers={"Authorization": f"Bearer {token}"},
                timeout=httpx.Timeout(timeout_seconds),
                transport=self._transport,
                follow_redirects=False,
                trust_env=False,
            ) as http:
                negotiated = await self._negotiate_with_http(
                    http,
                    agent,
                    request.preferred_card_versions,
                    invocation_id,
                    attempt,
                )
                await self.catalog.assert_exact_traffic(
                    agent.agent_id,
                    agent.version,
                    http=http,
                )
                client = await self._client_factory(
                    negotiated.card,
                    client_config=ClientConfig(
                        httpx_client=http,
                        streaming=False,
                        supported_protocol_bindings=["JSONRPC"],
                    ),
                )
                pieces: list[str] = []
                try:
                    outbound_prompt = (
                        f"{request.prompt}{_EVIDENCE_MARKER_CONTRACT}"
                        if request.preserve_evidence_markers
                        else request.prompt
                    )
                    publication_context_id = (
                        encode_publication_context_id(
                            invocation_id=invocation_id,
                            correlation_id=correlation_id,
                        )
                        if request.enable_artifact_publication
                        else ""
                    )
                    message = SendMessageRequest(
                        message=Message(
                            role=Role.ROLE_USER,
                            message_id=message_id,
                            context_id=publication_context_id,
                            parts=[Part(text=outbound_prompt)],
                            metadata={
                                "agentIq": {
                                    "invocationId": invocation_id,
                                    "correlationId": request.correlation_id or invocation_id,
                                }
                            },
                        )
                    )
                    await self._emit(
                        "invocation.request_prepared",
                        invocation_id,
                        request.agent_id,
                        attempt,
                        artifact_context_transport=(
                            "a2a_context_id" if publication_context_id else "none"
                        ),
                        artifact_context_present=bool(publication_context_id),
                        artifact_context_length=len(publication_context_id),
                        prompt_length=len(outbound_prompt),
                        publication_marker_in_prompt=False,
                    )
                    async for event in client.send_message(message):
                        try:
                            text = self._event_text(event).strip()
                        except (AttributeError, TypeError, ValueError) as exc:
                            raise operation_error(
                                ErrorCode.INVOCATION_FAILED,
                                "The native A2A endpoint returned a malformed response.",
                                correlation_id=correlation_id,
                            ) from exc
                        if text:
                            pieces.append(text)
                finally:
                    await client.close()
            await self.catalog.assert_exact_traffic(
                agent.agent_id,
                agent.version,
            )
        except AgentIqOperationError:
            raise
        except A2AClientTimeoutError as exc:
            raise operation_error(
                ErrorCode.INVOCATION_TIMEOUT,
                "Native A2A client timed out.",
                retryable=True,
                correlation_id=correlation_id,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise _invocation_http_error(exc.response, correlation_id) from exc
        except httpx.TimeoutException as exc:
            raise operation_error(
                ErrorCode.INVOCATION_TIMEOUT,
                "Native A2A transport timed out.",
                retryable=True,
                correlation_id=correlation_id,
            ) from exc
        except httpx.TransportError as exc:
            raise operation_error(
                ErrorCode.INVOCATION_FAILED,
                "Native A2A transport failed.",
                retryable=True,
                correlation_id=correlation_id,
            ) from exc
        except A2AClientError as exc:
            raise _a2a_client_error(exc, correlation_id) from exc
        except A2AError as exc:
            raise operation_error(
                ErrorCode.INVOCATION_FAILED,
                "The native A2A endpoint returned a protocol error.",
                correlation_id=correlation_id,
            ) from exc
        except (ParseError, ValidationError) as exc:
            raise operation_error(
                ErrorCode.INVOCATION_FAILED,
                "The native A2A endpoint returned a malformed response.",
                correlation_id=correlation_id,
            ) from exc

        answer = " ".join(dict.fromkeys(pieces)).strip()
        if not answer:
            raise operation_error(
                ErrorCode.INVOCATION_FAILED,
                "Native A2A returned no answer.",
                correlation_id=correlation_id,
            )
        return NativeA2AResult(
            invocation_id=invocation_id,
            agent_id=agent.agent_id,
            agent_version=agent.version,
            protocol_version=negotiated.protocol_version,
            message_id=message_id,
            answer=answer,
            duration_ms=0,
            attempt_count=attempt,
            correlation_id=request.correlation_id,
        )

    async def _negotiate_with_http(
        self,
        http: httpx.AsyncClient,
        agent: FoundryAgentRecord,
        preferred_versions: tuple[str, ...],
        invocation_id: str,
        attempt: int,
    ) -> NegotiatedAgentCard:
        base_url = agent.a2a_base_url(self.project_endpoint)
        for version in preferred_versions:
            card_path = _CARD_PATHS.get(version)
            if card_path is None:
                continue
            resolver = self._resolver_factory(
                httpx_client=http,
                base_url=base_url,
                agent_card_path=card_path,
            )
            try:
                card = await resolver.get_agent_card()
            except AgentCardResolutionError as exc:
                if exc.status_code == 404:
                    continue
                raise _card_resolution_error(
                    exc.status_code,
                    invocation_id,
                    cause=exc.__cause__,
                ) from exc
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    continue
                raise _card_http_error(exc.response, invocation_id) from exc
            try:
                payload = self._card_mapper(card)
            except (TypeError, ValueError) as exc:
                raise operation_error(
                    ErrorCode.CARD_INVALID,
                    "The native A2A Agent Card is invalid.",
                    correlation_id=invocation_id,
                ) from exc
            state = self.catalog.reconcile_card(
                agent,
                payload,
                correlation_id=invocation_id,
            )
            if state.health is HealthStatus.UNHEALTHY:
                raise operation_error(
                    ErrorCode.AGENT_INELIGIBLE,
                    "The native A2A Agent Card drifted from its canonical descriptor.",
                    details=(
                        ErrorDetail(
                            field="agentCard",
                            issue=f"Drifted fields: {', '.join(state.drifted_fields)}.",
                        ),
                    ),
                    correlation_id=invocation_id,
                )
            await self._emit(
                "card.negotiated",
                invocation_id,
                agent.agent_id,
                attempt,
                protocol_version=version,
            )
            return NegotiatedAgentCard(
                agent=agent,
                protocol_version=version,
                card=card,
                payload=payload,
                state=state,
            )
        raise operation_error(
            ErrorCode.PROTOCOL_UNSUPPORTED,
            "The agent exposes none of the requested native A2A Agent Card versions.",
            correlation_id=invocation_id,
        )

    async def _emit(
        self,
        name: str,
        invocation_id: str,
        agent_id: str,
        attempt: int,
        **attributes: str | int | float | bool | None,
    ) -> None:
        await self._telemetry.emit(
            RuntimeTelemetryEvent.create(
                name,
                invocation_id=invocation_id,
                agent_id=agent_id,
                attempt=attempt,
                **attributes,
            )
        )

    async def _access_token(self, correlation_id: str) -> str:
        try:
            return await self._token_provider.get_token(AZURE_AI_SCOPE)
        except (ClientAuthenticationError, CredentialUnavailableError) as exc:
            raise operation_error(
                ErrorCode.AUTHENTICATION_REQUIRED,
                "Microsoft Entra authentication for native A2A failed.",
                correlation_id=correlation_id,
            ) from exc


def event_text_from_sdk(event: Any) -> str:
    """Extract public text from all official A2A SDK response event shapes."""

    which = event.WhichOneof("payload")
    if which == "message":
        return get_message_text(event.message, delimiter=" ")
    if which == "task":
        artifacts = [
            get_artifact_text(artifact, delimiter=" ") for artifact in event.task.artifacts
        ]
        text = " ".join(part for part in artifacts if part).strip()
        if text:
            return text
        if event.task.status.HasField("message"):
            return get_message_text(event.task.status.message, delimiter=" ")
        return ""
    if which == "artifact_update":
        return get_artifact_text(event.artifact_update.artifact, delimiter=" ")
    if which == "status_update" and event.status_update.status.HasField("message"):
        return get_message_text(event.status_update.status.message, delimiter=" ")
    return ""


async def _run_with_controls(
    operation: Coroutine[Any, Any, T],
    *,
    deadline_seconds: float,
    cancel_event: asyncio.Event | None,
    correlation_id: str,
) -> T:
    operation_task: asyncio.Task[T] = asyncio.create_task(operation)
    cancellation_task = (
        asyncio.create_task(cancel_event.wait()) if cancel_event is not None else None
    )
    wait_for: set[asyncio.Task[Any]] = {operation_task}
    if cancellation_task is not None:
        wait_for.add(cancellation_task)
    try:
        done, _pending = await asyncio.wait(
            wait_for,
            timeout=deadline_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if operation_task in done:
            return await operation_task
        operation_task.cancel()
        await _drain_cancelled(operation_task)
        if cancellation_task is not None and cancellation_task in done:
            raise operation_error(
                ErrorCode.CANCELLED,
                "Native A2A invocation was cancelled.",
                correlation_id=correlation_id,
            )
        raise operation_error(
            ErrorCode.INVOCATION_TIMEOUT,
            "Native A2A invocation exceeded its deadline.",
            retryable=True,
            correlation_id=correlation_id,
        )
    finally:
        if cancellation_task is not None and not cancellation_task.done():
            cancellation_task.cancel()
            await _drain_cancelled(cancellation_task)
        if not operation_task.done():
            operation_task.cancel()
            await _drain_cancelled(operation_task)


async def _drain_cancelled(task: asyncio.Task[Any]) -> None:
    with contextlib.suppress(asyncio.CancelledError):
        await task


def _message_id(request: NativeA2ARequest) -> str:
    if request.idempotency_key is None:
        return f"msg-{uuid.uuid4().hex}"
    value = f"{request.agent_id}:{request.idempotency_key}"
    return f"msg-{uuid.uuid5(uuid.NAMESPACE_URL, value).hex}"


def _can_retry(
    request: NativeA2ARequest,
    error: AgentIqOperationError,
    attempt: int,
) -> bool:
    return (
        request.idempotency_policy is IdempotencyPolicy.RETRY_WITH_KEY
        and request.idempotency_key is not None
        and attempt < request.max_attempts
        and error.error.retryable
    )


def _require_eligible(agent: FoundryAgentRecord, correlation_id: str) -> None:
    if agent.status != "active":
        raise operation_error(
            ErrorCode.AGENT_INELIGIBLE,
            f"Foundry agent {agent.agent_id!r} is not active.",
            correlation_id=correlation_id,
        )
    if agent.kind != "hosted":
        raise operation_error(
            ErrorCode.AGENT_INELIGIBLE,
            f"Foundry agent {agent.agent_id!r} is not a hosted agent.",
            correlation_id=correlation_id,
        )


def _card_resolution_error(
    status_code: int | None,
    correlation_id: str,
    *,
    cause: BaseException | None = None,
) -> AgentIqOperationError:
    if status_code == 401:
        return operation_error(
            ErrorCode.AUTHENTICATION_REQUIRED,
            "The Agent Card endpoint rejected the Microsoft Entra token.",
            correlation_id=correlation_id,
        )
    if status_code == 403:
        return operation_error(
            ErrorCode.AUTHORIZATION_DENIED,
            "The caller is not authorized to read the Agent Card.",
            correlation_id=correlation_id,
        )
    retryable = (
        status_code == 408
        or status_code == 429
        or (status_code is not None and status_code >= 500)
        or isinstance(cause, httpx.TransportError)
    )
    return operation_error(
        ErrorCode.CARD_INVALID,
        "The native A2A Agent Card could not be resolved.",
        retryable=retryable,
        correlation_id=correlation_id,
    )


def _card_http_error(
    response: httpx.Response,
    correlation_id: str,
) -> AgentIqOperationError:
    status = response.status_code
    if status in {401, 403}:
        return _card_resolution_error(status, correlation_id)
    retryable = status == 408 or status == 429 or status >= 500
    return operation_error(
        ErrorCode.CARD_INVALID,
        f"Agent Card request failed with HTTP {status}.",
        retryable=retryable,
        retry_after_seconds=_retry_after(response) if retryable else None,
        correlation_id=correlation_id,
    )


def _invocation_http_error(
    response: httpx.Response,
    correlation_id: str,
) -> AgentIqOperationError:
    status = response.status_code
    if status == 401:
        return operation_error(
            ErrorCode.AUTHENTICATION_REQUIRED,
            "The native A2A endpoint rejected the Microsoft Entra token.",
            correlation_id=correlation_id,
        )
    if status == 403:
        return operation_error(
            ErrorCode.AUTHORIZATION_DENIED,
            "The caller is not authorized to invoke the native A2A endpoint.",
            correlation_id=correlation_id,
        )
    if status == 408:
        return operation_error(
            ErrorCode.INVOCATION_TIMEOUT,
            "The native A2A endpoint timed out.",
            retryable=True,
            retry_after_seconds=_retry_after(response),
            correlation_id=correlation_id,
        )
    retryable = status == 429 or status >= 500
    return operation_error(
        ErrorCode.INVOCATION_FAILED,
        f"Native A2A invocation failed with HTTP {status}.",
        retryable=retryable,
        retry_after_seconds=_retry_after(response) if retryable else None,
        correlation_id=correlation_id,
    )


def _a2a_client_error(
    error: A2AClientError,
    correlation_id: str,
) -> AgentIqOperationError:
    cause = error.__cause__
    if isinstance(cause, httpx.HTTPStatusError):
        return _invocation_http_error(cause.response, correlation_id)
    if isinstance(cause, httpx.TimeoutException):
        return operation_error(
            ErrorCode.INVOCATION_TIMEOUT,
            "Native A2A client timed out.",
            retryable=True,
            correlation_id=correlation_id,
        )
    if isinstance(cause, httpx.TransportError):
        return operation_error(
            ErrorCode.INVOCATION_FAILED,
            "Native A2A transport failed.",
            retryable=True,
            correlation_id=correlation_id,
        )
    if isinstance(cause, json.JSONDecodeError):
        return operation_error(
            ErrorCode.INVOCATION_FAILED,
            "The native A2A endpoint returned malformed JSON.",
            correlation_id=correlation_id,
        )
    return operation_error(
        ErrorCode.INVOCATION_FAILED,
        "The native A2A client rejected the invocation.",
        correlation_id=correlation_id,
    )


def _retry_after(response: httpx.Response) -> int | None:
    value = response.headers.get("retry-after")
    if value is None or not value.isdigit():
        return None
    return min(max(int(value), 1), 86_400)
