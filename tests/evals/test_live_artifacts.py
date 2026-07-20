from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_iq.evals.live_artifacts import (
    VerifiedLiveArtifactProbe,
    build_live_artifact_payload,
    correlation_id_for_run,
)

NOW = datetime(2026, 7, 11, 12, 5, tzinfo=UTC)
RUN_ID = "artifact-canary-run-20260711T1204000000000Z-" + "d" * 32
CORRELATION_ID = correlation_id_for_run(RUN_ID)
REVISION = "agent-iq-orchestrator-canary--d12abc"
IMAGE_DIGEST = f"sha256:{'c' * 64}"


def _publication(*, issued_at: datetime = NOW - timedelta(minutes=1)) -> dict[str, Any]:
    issued_at_text = issued_at.isoformat().replace("+00:00", "Z")
    artifact = {
        "schemaVersion": "1.0",
        "kind": "demand_scenario",
        "artifactId": "demand-live",
        "productId": "brightwash-cold",
        "market": "SE",
        "horizonStart": "2026-07-12",
        "horizonEnd": "2026-07-19",
        "unit": "cases",
        "baselineQuantity": 100.0,
        "forecastQuantity": 110.0,
        "confidence": 0.9,
        "assumptions": ["certification"],
        "evidenceIds": ["evidence-1"],
        "createdAt": issued_at_text,
    }
    content_hash = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                artifact,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        ).hexdigest()
    )
    expires_at = NOW + timedelta(minutes=5)
    reference = {
        "schemaVersion": "1.0",
        "delivery": "companion",
        "mediaType": "application/vnd.agent-iq.business-artifact+json;version=1.0",
        "artifactId": "demand-live",
        "artifactKind": "demand_scenario",
        "invocationId": "inv-live",
        "correlationId": CORRELATION_ID,
        "handle": "ath-live-certification-handle",
        "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
    }
    envelope = {
        "schemaVersion": "1.0",
        "artifactId": "demand-live",
        "invocationId": "inv-live",
        "correlationId": CORRELATION_ID,
        "tenantId": "tenant-live",
        "callerSubjectId": "caller-live",
        "producer": {
            "agentId": "consumer-demand-forecasting",
            "agentVersion": "6",
            "identityId": "identity-live",
            "framework": "PydanticAI",
        },
        "artifact": artifact,
        "contentHash": content_hash,
        "issuedAt": issued_at_text,
        "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
    }
    return {
        "schemaVersion": "1.0",
        "status": "pass",
        "reference": reference,
        "envelope": envelope,
    }


def _context(
    publication: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    common = {
        "schemaVersion": "1.0",
        "generatedAt": NOW.isoformat(),
        "runId": RUN_ID,
        "status": "pass",
    }
    invocation = {
        **common,
        "agentId": "consumer-demand-forecasting",
        "agentVersion": "6",
        "invocationId": "inv-live",
        "correlationId": CORRELATION_ID,
        "protocolVersion": "1.0",
        "durationMs": 100,
        "answerHash": f"sha256:{'a' * 64}",
        "answerNonempty": True,
        "publicationTicketPersisted": False,
    }
    replay_publication = {**publication, **common, "checks": {}}
    security = {
        **common,
        "invocationId": "inv-live",
        "correlationId": CORRELATION_ID,
        "wrongIdentityStatus": 403,
        "wrongTicketStatus": 403,
        "missingTicketStatus": 403,
        "wrongClaimIdentityStatus": 403,
        "wrongClaimInvocationStatus": 403,
        "wrongClaimCorrelationStatus": 403,
        "missingClaimCredentialsStatus": 401,
        "emptyGrantStatus": 404,
    }
    return invocation, replay_publication, security


def _probe(publication: dict[str, Any]) -> VerifiedLiveArtifactProbe:
    return VerifiedLiveArtifactProbe(
        run_id=RUN_ID,
        correlation_id=CORRELATION_ID,
        observed_at=NOW,
        base_url="https://example.invalid",
        retrieval_endpoint="https://example.invalid/v1/certification",
        claim_endpoint="https://example.invalid/v1/artifacts/tickets/claim",
        publication_endpoint="https://example.invalid/v1/artifacts/publish",
        revision=REVISION,
        image_digest=IMAGE_DIGEST,
        publication=publication,
        publish_reference=publication["reference"],
        retrieval_sha256=f"sha256:{'d' * 64}",
        publication_sha256=f"sha256:{'e' * 64}",
        wrong_identity_status=403,
        wrong_ticket_status=403,
        missing_ticket_status=403,
        wrong_claim_identity_status=403,
        wrong_claim_invocation_status=403,
        wrong_claim_correlation_status=403,
        missing_claim_credentials_status=401,
    )


def _deployment() -> dict[str, Any]:
    return {
        "fqdn": "https://example.invalid",
        "revision": REVISION,
        "imageDigest": IMAGE_DIGEST,
    }


def test_fabricated_fresh_files_cannot_self_attest() -> None:
    invocation, publication, security = _context(_publication())

    result = build_live_artifact_payload(
        invocation,
        publication,
        security,
        expected_kind="demand_scenario",
        companion_image_digest=IMAGE_DIGEST,
        generated_at=NOW,
        deployment_metadata=_deployment(),
    )

    assert result["status"] == "fail"
    assert "liveProbe.performed" in result["failures"]
    assert result["runIdSha256"] is None


def test_verified_fresh_bound_probe_passes_content_safely() -> None:
    live_publication = _publication()
    invocation, publication, security = _context(live_publication)

    result = build_live_artifact_payload(
        invocation,
        publication,
        security,
        expected_kind="demand_scenario",
        companion_image_digest=IMAGE_DIGEST,
        generated_at=NOW,
        deployment_metadata=_deployment(),
        live_probe=_probe(live_publication),
    )

    assert result["status"] == "pass"
    assert result["failures"] == []
    assert result["producer"]["identitySha256"] != "identity-live"
    assert result["artifact"]["evidenceCount"] == 1
    serialized = json.dumps(result, sort_keys=True)
    assert "inv-live" not in serialized
    assert CORRELATION_ID not in serialized
    assert "identity-live" not in serialized


def test_verified_probe_fails_wrong_deployment_and_security_binding() -> None:
    live_publication = _publication()
    invocation, publication, security = _context(live_publication)
    probe = _probe(live_publication)
    wrong_probe = VerifiedLiveArtifactProbe(
        **{
            **probe.__dict__,
            "revision": "agent-iq-orchestrator-canary--other",
            "wrong_identity_status": 200,
        }
    )

    result = build_live_artifact_payload(
        invocation,
        publication,
        security,
        expected_kind="demand_scenario",
        companion_image_digest=IMAGE_DIGEST,
        generated_at=NOW,
        deployment_metadata=_deployment(),
        live_probe=wrong_probe,
    )

    assert result["status"] == "fail"
    assert "liveProbe.deploymentBound" in result["failures"]
    assert "liveProbe.securityBoundary" in result["failures"]
