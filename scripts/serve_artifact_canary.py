"""Serve one live, identity-bound artifact-publication certification grant."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import Header, HTTPException
from pydantic import SecretStr

from agent_iq.api import EntraPublisherTokenValidator, create_artifact_companion_app
from agent_iq.artifacts import CompanionArtifactStore
from agent_iq.config import canonicalize_artifact_companion_application_id
from agent_iq.contracts import (
    ArtifactChannelReference,
    ArtifactFetchRequest,
    ArtifactKind,
    ArtifactProducerIdentity,
    BusinessArtifact,
    CallerContext,
)
from agent_iq.errors import AgentIqOperationError


class RecordingArtifactStore(CompanionArtifactStore):
    """Persist only the content-safe certification envelope after publication."""

    def __init__(
        self,
        *,
        caller: CallerContext,
        output_path: Path,
        ticket: str,
        ttl_seconds: int,
        companion_application_id: str,
    ) -> None:
        super().__init__(
            ttl=timedelta(seconds=ttl_seconds),
            ticket_factory=lambda: ticket,
        )
        self._caller = caller
        self._output_path = output_path
        application_id = canonicalize_artifact_companion_application_id(companion_application_id)
        self._expected_audience = application_id
        self.certification: dict[str, object] | None = None

    def publish_with_ticket(
        self,
        ticket: SecretStr | str,
        artifact: BusinessArtifact,
        *,
        producer: ArtifactProducerIdentity,
    ) -> ArtifactChannelReference:
        reference = super().publish_with_ticket(
            ticket,
            artifact,
            producer=producer,
        )
        envelope = self.fetch(
            ArtifactFetchRequest.from_reference(reference),
            caller=self._caller,
        )
        payload: dict[str, object] = {
            "schemaVersion": "1.0",
            "status": "pass",
            "reference": reference.model_dump(mode="json", by_alias=True),
            "envelope": envelope.model_dump(mode="json", by_alias=True),
            "checks": {
                "typedArtifactValidated": True,
                "invocationBound": True,
                "correlationBound": True,
                "producerIdentityBound": True,
                "publisherAudienceBound": True,
                "opaqueTicketNotPersisted": True,
            },
            "publisherAuthentication": {
                "expectedAudience": self._expected_audience,
                "expectedAudienceSha256": (
                    "sha256:" + hashlib.sha256(self._expected_audience.encode("ascii")).hexdigest()
                ),
            },
        }
        self.certification = payload
        temporary = self._output_path.with_suffix(".tmp")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
            encoding="ascii",
        )
        temporary.replace(self._output_path)
        return reference


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", default=os.environ.get("AGENT_IQ_CANARY_TENANT_ID"))
    companion_application_id = os.environ.get("AGENT_IQ_ARTIFACT_COMPANION_APPLICATION_ID") or None
    parser.add_argument(
        "--companion-application-id",
        type=_companion_application_id_argument,
        default=companion_application_id,
        required=companion_application_id is None,
    )
    parser.add_argument(
        "--expected-identity",
        default=os.environ.get("AGENT_IQ_CANARY_EXPECTED_IDENTITY"),
    )
    parser.add_argument("--agent-id", default=os.environ.get("AGENT_IQ_CANARY_AGENT_ID"))
    parser.add_argument(
        "--agent-version",
        default=os.environ.get("AGENT_IQ_CANARY_AGENT_VERSION"),
    )
    parser.add_argument(
        "--invocation-id",
        default=os.environ.get("AGENT_IQ_CANARY_INVOCATION_ID"),
    )
    parser.add_argument(
        "--correlation-id",
        default=os.environ.get("AGENT_IQ_CANARY_CORRELATION_ID"),
    )
    parser.add_argument(
        "--artifact-kind",
        choices=[item.value for item in ArtifactKind],
        default=os.environ.get("AGENT_IQ_CANARY_ARTIFACT_KIND"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(os.environ.get("AGENT_IQ_CANARY_OUTPUT", "/tmp/publication.json")),
    )
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument(
        "--ttl-seconds",
        type=int,
        default=int(os.environ.get("AGENT_IQ_CANARY_TTL_SECONDS", "600")),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    required = {
        "tenant ID": args.tenant_id,
        "companion application ID": args.companion_application_id,
        "expected identity": args.expected_identity,
        "agent ID": args.agent_id,
        "agent version": args.agent_version,
        "invocation ID": args.invocation_id,
        "correlation ID": args.correlation_id,
        "artifact kind": args.artifact_kind,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing canary configuration: {', '.join(missing)}.")
    companion_application_id = canonicalize_artifact_companion_application_id(
        args.companion_application_id
    )
    expected_audience = companion_application_id
    ticket = os.environ.get("AGENT_IQ_LIVE_PUBLICATION_TICKET", "")
    if not ticket:
        raise RuntimeError("AGENT_IQ_LIVE_PUBLICATION_TICKET is required.")
    caller = CallerContext(
        subject_id="live-certification-caller",
        tenant_id=args.tenant_id,
    )
    producer = ArtifactProducerIdentity(
        agent_id=args.agent_id,
        agent_version=args.agent_version,
        identity_id=args.expected_identity,
        framework="PydanticAI",
    )
    store = RecordingArtifactStore(
        caller=caller,
        output_path=args.output.resolve(),
        ticket=ticket,
        ttl_seconds=args.ttl_seconds,
        companion_application_id=companion_application_id,
    )
    store.authorize_invocation(
        invocation_id=args.invocation_id,
        correlation_id=args.correlation_id,
        caller=caller,
        producer=producer,
        allowed_kinds=(ArtifactKind(args.artifact_kind),),
    )
    app = create_artifact_companion_app(
        store,
        EntraPublisherTokenValidator(
            tenant_id=args.tenant_id,
            audience=expected_audience,
        ),
    )

    @app.get("/v1/certification")
    async def certification(
        publication_ticket: Annotated[
            str | None,
            Header(alias="X-Agent-IQ-Publication-Ticket"),
        ] = None,
    ) -> dict[str, object]:
        if publication_ticket is None or not hmac.compare_digest(publication_ticket, ticket):
            raise HTTPException(status_code=403, detail="Certification access denied.")
        try:
            store.producer_for_ticket(publication_ticket)
        except (AgentIqOperationError, PermissionError) as exc:
            raise HTTPException(
                status_code=403,
                detail="Certification access denied.",
            ) from exc
        if store.certification is None:
            raise HTTPException(status_code=404, detail="No artifact has been published.")
        return store.certification

    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")


def _companion_application_id_argument(value: str) -> str:
    try:
        return canonicalize_artifact_companion_application_id(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


if __name__ == "__main__":
    main()
