"""Authenticated HTTP boundary for invocation-bound artifact publication."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

import jwt
from fastapi import APIRouter, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from agent_iq.artifacts import CompanionArtifactStore
from agent_iq.contracts import (
    ArtifactChannelReference,
    BusinessArtifact,
    ErrorCode,
)
from agent_iq.contracts.base import ContractModel, Identifier, ShortText
from agent_iq.errors import AgentIqOperationError


@dataclass(frozen=True, slots=True)
class PublisherPrincipal:
    """Validated workload identity resolved from a bearer token."""

    tenant_id: str
    object_id: str


class PublisherTokenValidator(Protocol):
    async def validate(self, token: str) -> PublisherPrincipal: ...


class ArtifactPublishBody(ContractModel):
    artifact: BusinessArtifact


class ArtifactTicketClaimBody(ContractModel):
    invocation_id: Identifier
    correlation_id: Identifier


class EntraPublisherTokenValidator:
    """Validate a tenant-bound Entra token issued for the Agent IQ API."""

    def __init__(
        self,
        *,
        tenant_id: str,
        audience: str,
        jwks_client: Any | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._audience = audience
        self._issuers = {
            f"https://login.microsoftonline.com/{tenant_id}/v2.0",
            f"https://sts.windows.net/{tenant_id}/",
        }
        self._jwks = jwks_client or jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
            cache_jwk_set=True,
            lifespan=3600,
        )

    async def validate(self, token: str) -> PublisherPrincipal:
        try:
            signing_key = await asyncio.to_thread(
                self._jwks.get_signing_key_from_jwt,
                token,
            )
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._audience,
                options={"require": ["exp", "iat", "iss", "aud", "tid", "oid"]},
            )
        except jwt.PyJWTError as exc:
            raise PermissionError("Publisher bearer token validation failed.") from exc
        issuer = payload.get("iss")
        tenant_id = payload.get("tid")
        object_id = payload.get("oid")
        if (
            issuer not in self._issuers
            or tenant_id != self._tenant_id
            or not isinstance(object_id, str)
            or not object_id
        ):
            raise PermissionError("Publisher bearer token claims are not authorized.")
        return PublisherPrincipal(tenant_id=tenant_id, object_id=object_id)


def create_artifact_companion_app(
    store: CompanionArtifactStore,
    token_validator: PublisherTokenValidator,
) -> FastAPI:
    """Create a side-effect-free companion API over one orchestration store."""

    app = FastAPI(
        title="Agent IQ Artifact Companion",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(create_artifact_companion_router(store, token_validator))

    return app


def create_artifact_companion_router(
    store: CompanionArtifactStore,
    token_validator: PublisherTokenValidator,
) -> APIRouter:
    """Create the authenticated publication route for composition into a product app."""

    router = APIRouter()

    @router.post("/v1/artifacts/tickets/claim", response_class=JSONResponse)
    async def claim_artifact_ticket(
        body: ArtifactTicketClaimBody,
        authorization: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        token = _bearer_token(authorization)
        try:
            principal = await token_validator.validate(token)
            ticket = store.claim_publication_ticket(
                invocation_id=body.invocation_id,
                correlation_id=body.correlation_id,
                producer_identity_id=principal.object_id,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="Artifact publication denied.") from exc
        except AgentIqOperationError as exc:
            raise HTTPException(
                status_code=_artifact_error_status(exc.error.code),
                detail=exc.error.message,
            ) from exc
        return JSONResponse(
            content={
                "publicationTicket": ticket.secret.get_secret_value(),
                "expiresAt": ticket.expires_at.isoformat(),
            },
            headers={
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
            },
        )

    @router.post(
        "/v1/artifacts/publish",
        response_model=ArtifactChannelReference,
        response_model_by_alias=True,
    )
    async def publish_artifact(
        body: ArtifactPublishBody,
        authorization: Annotated[str | None, Header()] = None,
        publication_ticket: Annotated[
            ShortText | None,
            Header(alias="X-Agent-IQ-Publication-Ticket"),
        ] = None,
    ) -> ArtifactChannelReference:
        token = _bearer_token(authorization)
        if publication_ticket is None:
            raise HTTPException(status_code=403, detail="Artifact publication denied.")
        try:
            principal = await token_validator.validate(token)
            expected_producer = store.producer_for_ticket(publication_ticket)
            if principal.object_id != expected_producer.identity_id:
                raise PermissionError("Publisher identity does not match the invocation grant.")
            return store.publish_with_ticket(
                publication_ticket,
                body.artifact,
                producer=expected_producer,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="Artifact publication denied.") from exc
        except AgentIqOperationError as exc:
            raise HTTPException(
                status_code=_artifact_error_status(exc.error.code),
                detail=exc.error.message,
            ) from exc

    return router


def _bearer_token(authorization: str | None) -> str:
    if authorization is None or len(authorization) > 8192:
        raise HTTPException(status_code=401, detail="A bearer token is required.")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not token or token.strip() != token:
        raise HTTPException(status_code=401, detail="A bearer token is required.")
    return token


def _artifact_error_status(code: ErrorCode) -> int:
    if code is ErrorCode.ARTIFACT_CONFLICT:
        return 409
    if code is ErrorCode.POLICY_BLOCKED:
        return 422
    if code is ErrorCode.ARTIFACT_UNAVAILABLE:
        return 410
    return 403
