"""Build a content-safe certificate from fresh, live-bound artifact evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from azure.identity import DefaultAzureCredential

from agent_iq.config import canonicalize_artifact_companion_application_id
from agent_iq.connectors.entra import isolated_default_azure_credential
from agent_iq.contracts import (
    ARTIFACT_MEDIA_TYPE,
    ArtifactChannelReference,
    ArtifactKind,
    CallerContext,
    DemandScenario,
    ExecutionStatus,
    MatchFilters,
    MatchRequest,
    OrchestrationCommand,
    OrchestrationResult,
    StepExecutionStatus,
    TransportProtocol,
)
from agent_iq.evals import (
    MAX_LIVE_EVIDENCE_AGE,
    LiveArtifactEvidence,
    build_live_artifact_payload,
    load_live_artifact_evidence,
    validate_live_artifact_evidence,
)
from agent_iq.evals.live_artifacts import (
    VerifiedLiveArtifactProbe,
    validate_live_publication_response,
    validate_live_run_binding,
    validate_live_run_freshness,
)
from agent_iq.publication import validate_publication_ticket

try:
    from scripts.certify_orchestrator_canary import (
        _base_url_argument,
        _deployment_report_path_argument,
        certify_deployment_evidence,
    )
except ModuleNotFoundError:
    from certify_orchestrator_canary import (  # type: ignore[import-not-found, no-redef]
        _base_url_argument,
        _deployment_report_path_argument,
        certify_deployment_evidence,
    )

ORCHESTRATION_PROBE: Literal["orchestration_response"] = "orchestration_response"
CERTIFICATION_PROBE: Literal["certification"] = "certification"
DEMAND_AGENT_ID = "consumer-demand-forecasting"
DEMAND_CERTIFICATION_INTENT = (
    "Use retrieve_forecast_drivers for product Adaptive Supply Network, market Germany, "
    "and the short-term horizon. Return a grounded forecast and publish exactly one "
    "DemandScenario typed artifact."
)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    companion_application_id = os.environ.get("AGENT_IQ_ARTIFACT_COMPANION_APPLICATION_ID") or None
    parser.add_argument("--invocation", type=Path)
    parser.add_argument("--publication", type=Path)
    parser.add_argument("--security", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--correlation-id")
    parser.add_argument(
        "--probe-mode",
        choices=(CERTIFICATION_PROBE, ORCHESTRATION_PROBE),
        default=CERTIFICATION_PROBE,
    )
    parser.add_argument(
        "--expected-kind",
        choices=[item.value for item in ArtifactKind],
        required=True,
    )
    parser.add_argument("--deployment-report", type=_deployment_report_path_argument, required=True)
    parser.add_argument("--base-url", type=_base_url_argument, required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--app-insights")
    parser.add_argument(
        "--companion-application-id",
        type=_companion_application_id_argument,
        default=companion_application_id,
        required=companion_application_id is None,
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def _build_certificate(
    args: argparse.Namespace,
    *,
    now: datetime | None = None,
    http_transport: httpx.BaseTransport | None = None,
    access_token: str | None = None,
) -> dict[str, Any]:
    companion_application_id = canonicalize_artifact_companion_application_id(
        args.companion_application_id
    )
    expected_audience = companion_application_id
    token_scope = f"{expected_audience}/.default"
    reference_now = now or datetime.now(UTC)
    probe_mode = getattr(args, "probe_mode", CERTIFICATION_PROBE)
    supplied_correlation_id = getattr(args, "correlation_id", None)
    if probe_mode == ORCHESTRATION_PROBE:
        if supplied_correlation_id is not None:
            raise ValueError(
                "Orchestration-response probes use the server-generated correlation ID."
            )
        validate_live_run_freshness(args.run_id, now=reference_now)
    else:
        if not isinstance(supplied_correlation_id, str):
            raise ValueError("Certification probes require a correlation ID.")
        validate_live_run_binding(
            args.run_id,
            supplied_correlation_id,
            now=reference_now,
        )
    evidence = _load_optional_expectations(args, now=reference_now)

    deployment = certify_deployment_evidence(
        args.deployment_report,
        base_url=args.base_url,
        resource_group=args.resource_group,
        app_name=args.app_name,
        app_insights=args.app_insights,
        now=reference_now,
    )
    if deployment.get("valid") is not True:
        failures = deployment.get("failures")
        safe_failures = (
            sorted(item for item in failures if isinstance(item, str))
            if isinstance(failures, list)
            else ["deploymentEvidence"]
        )
        raise RuntimeError(
            "Current Container App deployment binding failed: " + ", ".join(safe_failures)
        )
    report = deployment.get("report")
    current = deployment.get("current")
    if not isinstance(report, dict) or not isinstance(current, dict):
        raise RuntimeError("Current Container App deployment binding returned no safe projection.")
    report_generated_at = _parse_utc_timestamp(report.get("generatedAt"))
    if (
        report_generated_at is None
        or report_generated_at > reference_now
        or reference_now - report_generated_at > MAX_LIVE_EVIDENCE_AGE
    ):
        raise RuntimeError("The deployment report is not fresh for this live artifact probe.")
    image_digest = report.get("imageDigest")
    revision = report.get("revision")
    if not isinstance(image_digest, str) or not isinstance(revision, str):
        raise RuntimeError("The deployment report has no certified revision and image digest.")

    deployment_metadata = _safe_deployment_metadata(
        Path(args.deployment_report),
        deployment.get("reportSha256"),
        report,
        current,
    )
    token = access_token or _get_access_token(token_scope)
    fixed_observed_at = reference_now if now is not None else None
    if probe_mode == ORCHESTRATION_PROBE:
        live_probe = _perform_live_orchestration_probe(
            base_url=args.base_url,
            run_id=args.run_id,
            expected_kind=args.expected_kind,
            revision=revision,
            image_digest=image_digest,
            access_token=token,
            observed_at=fixed_observed_at,
            transport=http_transport,
        )
    else:
        assert isinstance(supplied_correlation_id, str)
        live_probe = _perform_live_certification_probe(
            base_url=args.base_url,
            run_id=args.run_id,
            correlation_id=supplied_correlation_id,
            expected_kind=args.expected_kind,
            revision=revision,
            image_digest=image_digest,
            ticket=_publication_ticket(),
            access_token=token,
            observed_at=fixed_observed_at,
            transport=http_transport,
        )
    probe_now = live_probe.observed_at
    if evidence is None:
        invocation_payload, publication_payload, security_payload = _probe_context(live_probe)
        evidence_metadata = None
    else:
        invocation, publication, security = evidence
        _validate_expectations(invocation, publication, security, live_probe)
        invocation_payload = dict(invocation.payload)
        publication_payload = dict(publication.payload)
        security_payload = dict(security.payload)
        evidence_metadata = validate_live_artifact_evidence(
            invocation,
            publication,
            security,
        )[1]
    payload = build_live_artifact_payload(
        invocation_payload,
        publication_payload,
        security_payload,
        expected_kind=args.expected_kind,
        companion_image_digest=image_digest,
        generated_at=probe_now,
        evidence_metadata=evidence_metadata,
        deployment_metadata=deployment_metadata,
        live_probe=live_probe,
    )
    payload["publisherAuthentication"] = {
        "expectedAudience": expected_audience,
        "expectedAudienceSha256": _digest_text(expected_audience),
    }
    return payload


def _load_optional_expectations(
    args: argparse.Namespace,
    *,
    now: datetime,
) -> (
    tuple[
        LiveArtifactEvidence,
        LiveArtifactEvidence,
        LiveArtifactEvidence,
    ]
    | None
):
    paths = (args.invocation, args.publication, args.security)
    if not any(path is not None for path in paths):
        return None
    if not all(path is not None for path in paths):
        raise ValueError(
            "Filesystem replay expectations must provide invocation, publication, and security."
        )
    assert all(isinstance(path, Path) for path in paths)
    invocation = load_live_artifact_evidence(paths[0], "invocation", now=now)
    publication = load_live_artifact_evidence(paths[1], "publication", now=now)
    security = load_live_artifact_evidence(paths[2], "security", now=now)
    validate_live_artifact_evidence(invocation, publication, security)
    return invocation, publication, security


def _validate_expectations(
    invocation: LiveArtifactEvidence,
    publication: LiveArtifactEvidence,
    security: LiveArtifactEvidence,
    probe: VerifiedLiveArtifactProbe,
) -> None:
    live_reference = probe.publication.get("reference")
    live_envelope = probe.publication.get("envelope")
    expected_reference = publication.payload.get("reference")
    expected_envelope = publication.payload.get("envelope")
    if not all(
        isinstance(item, dict)
        for item in (live_reference, live_envelope, expected_reference, expected_envelope)
    ):
        raise ValueError("Filesystem replay expectations are malformed.")
    assert isinstance(live_reference, dict)
    assert isinstance(live_envelope, dict)
    assert isinstance(expected_reference, dict)
    assert isinstance(expected_envelope, dict)
    expected_producer = expected_envelope.get("producer")
    live_producer = live_envelope.get("producer")
    if (
        invocation.run_id != probe.run_id
        or publication.run_id != probe.run_id
        or security.run_id != probe.run_id
        or invocation.payload.get("correlationId") != probe.correlation_id
        or security.payload.get("correlationId") != probe.correlation_id
        or expected_reference != live_reference
        or expected_envelope != live_envelope
        or not isinstance(expected_producer, dict)
        or not isinstance(live_producer, dict)
        or invocation.payload.get("agentId") != live_producer.get("agentId")
        or invocation.payload.get("agentVersion") != live_producer.get("agentVersion")
        or security.payload.get("wrongIdentityStatus") != probe.wrong_identity_status
        or security.payload.get("wrongTicketStatus") != probe.wrong_ticket_status
        or security.payload.get("missingTicketStatus") != probe.missing_ticket_status
        or security.payload.get("wrongClaimIdentityStatus") != probe.wrong_claim_identity_status
        or security.payload.get("wrongClaimInvocationStatus") != probe.wrong_claim_invocation_status
        or security.payload.get("wrongClaimCorrelationStatus")
        != probe.wrong_claim_correlation_status
        or security.payload.get("missingClaimCredentialsStatus")
        != probe.missing_claim_credentials_status
    ):
        raise ValueError("Filesystem replay expectations do not match the fresh live probe.")


def _probe_context(
    probe: VerifiedLiveArtifactProbe,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    envelope = probe.publication["envelope"]
    assert isinstance(envelope, dict)
    producer = envelope["producer"]
    assert isinstance(producer, dict)
    generated_at = probe.observed_at.isoformat().replace("+00:00", "Z")
    common = {
        "schemaVersion": "1.0",
        "generatedAt": generated_at,
        "runId": probe.run_id,
        "status": "pass",
    }
    invocation = (
        {**common, **dict(probe.invocation)}
        if probe.invocation is not None
        else {
            **common,
            "agentId": producer["agentId"],
            "agentVersion": producer["agentVersion"],
            "invocationId": envelope["invocationId"],
            "correlationId": probe.correlation_id,
            "protocolVersion": "1.0",
            "durationMs": 0,
            "answerHash": probe.retrieval_sha256,
            "answerNonempty": True,
            "publicationTicketPersisted": False,
        }
    )
    publication = {**probe.publication, **common}
    publication.setdefault("checks", {})
    security = {
        **common,
        "invocationId": envelope["invocationId"],
        "correlationId": probe.correlation_id,
        "wrongIdentityStatus": probe.wrong_identity_status,
        "wrongTicketStatus": probe.wrong_ticket_status,
        "missingTicketStatus": probe.missing_ticket_status,
        "wrongClaimIdentityStatus": probe.wrong_claim_identity_status,
        "wrongClaimInvocationStatus": probe.wrong_claim_invocation_status,
        "wrongClaimCorrelationStatus": probe.wrong_claim_correlation_status,
        "missingClaimCredentialsStatus": probe.missing_claim_credentials_status,
        "emptyGrantStatus": 404,
    }
    return invocation, publication, security


def _perform_live_certification_probe(
    *,
    base_url: str,
    run_id: str,
    correlation_id: str,
    expected_kind: str,
    revision: str,
    image_digest: str,
    ticket: str,
    access_token: str,
    observed_at: datetime | None,
    transport: httpx.BaseTransport | None,
) -> VerifiedLiveArtifactProbe:
    certification_url = f"{base_url}/v1/certification"
    claim_url = f"{base_url}/v1/artifacts/tickets/claim"
    publication_url = f"{base_url}/v1/artifacts/publish"
    context_headers = {
        "Cache-Control": "no-store",
        "X-Agent-IQ-Certification-Run-Id": run_id,
        "X-Agent-IQ-Certification-Correlation-Id": correlation_id,
        "X-Agent-IQ-Certified-Revision": revision,
        "X-Agent-IQ-Certified-Image-Digest": image_digest,
    }
    ticket_headers = {
        **context_headers,
        "X-Agent-IQ-Publication-Ticket": ticket,
    }
    timeout = httpx.Timeout(30.0, connect=10.0)
    with httpx.Client(
        follow_redirects=False,
        timeout=timeout,
        transport=transport,
        trust_env=False,
    ) as client:
        retrieval = _request_json(
            client,
            "GET",
            certification_url,
            headers=ticket_headers,
        )
        verified_at = observed_at or datetime.now(UTC)
        reference, envelope = validate_live_publication_response(
            retrieval,
            expected_kind=expected_kind,
            correlation_id=correlation_id,
            now=verified_at,
        )
        retrieved_again = _request_json(
            client,
            "GET",
            certification_url,
            headers=ticket_headers,
        )
        validate_live_publication_response(
            retrieved_again,
            expected_kind=expected_kind,
            correlation_id=correlation_id,
            now=verified_at,
        )
        if _canonical_json(retrieved_again) != _canonical_json(retrieval):
            raise RuntimeError("Live companion retrieval changed during certification.")

        invocation_id = envelope.get("invocationId")
        if not isinstance(invocation_id, str) or not invocation_id:
            raise RuntimeError("Live companion retrieval omitted the invocation identifier.")
        (
            wrong_identity_status,
            wrong_ticket_status,
            missing_ticket_status,
            wrong_claim_identity_status,
            wrong_claim_invocation_status,
            wrong_claim_correlation_status,
            missing_claim_credentials_status,
        ) = _perform_security_probes(
            client,
            claim_url=claim_url,
            publication_url=publication_url,
            invocation_id=invocation_id,
            correlation_id=correlation_id,
            artifact=envelope["artifact"],
            access_token=access_token,
            context_headers=context_headers,
            identity_ticket=ticket,
        )

    return VerifiedLiveArtifactProbe(
        run_id=run_id,
        correlation_id=correlation_id,
        observed_at=verified_at,
        base_url=base_url,
        retrieval_endpoint=certification_url,
        claim_endpoint=claim_url,
        publication_endpoint=publication_url,
        revision=revision,
        image_digest=image_digest,
        publication=retrieval,
        publish_reference=reference,
        retrieval_sha256=_digest_json(retrieval),
        publication_sha256=_digest_json(reference),
        wrong_identity_status=wrong_identity_status,
        wrong_ticket_status=wrong_ticket_status,
        missing_ticket_status=missing_ticket_status,
        wrong_claim_identity_status=wrong_claim_identity_status,
        wrong_claim_invocation_status=wrong_claim_invocation_status,
        wrong_claim_correlation_status=wrong_claim_correlation_status,
        missing_claim_credentials_status=missing_claim_credentials_status,
    )


def _perform_live_orchestration_probe(
    *,
    base_url: str,
    run_id: str,
    expected_kind: str,
    revision: str,
    image_digest: str,
    access_token: str,
    observed_at: datetime | None,
    transport: httpx.BaseTransport | None,
) -> VerifiedLiveArtifactProbe:
    orchestration_url = f"{base_url}/v1/orchestrate"
    claim_url = f"{base_url}/v1/artifacts/tickets/claim"
    publication_url = f"{base_url}/v1/artifacts/publish"
    requested_at = observed_at or datetime.now(UTC)
    command = OrchestrationCommand(
        request=MatchRequest(
            request_id=run_id,
            intent=DEMAND_CERTIFICATION_INTENT,
            caller=CallerContext(
                subject_id="artifact-certification-caller",
                tenant_id="artifact-certification-tenant",
            ),
            filters=MatchFilters(
                max_candidates=20,
                max_selected=1,
                required_protocols=(TransportProtocol.A2A,),
                allowed_agent_ids=(DEMAND_AGENT_ID,),
            ),
            requested_at=requested_at,
        )
    )
    timeout = httpx.Timeout(360.0, connect=10.0)
    with httpx.Client(
        follow_redirects=False,
        timeout=timeout,
        transport=transport,
        trust_env=False,
    ) as client:
        raw_result = _request_json(
            client,
            "POST",
            orchestration_url,
            headers={"Cache-Control": "no-store"},
            json_body=command.model_dump(mode="json", by_alias=True),
        )
        verified_at = observed_at or datetime.now(UTC)
        try:
            result = OrchestrationResult.model_validate(raw_result)
        except ValueError as exc:
            raise RuntimeError("Live orchestration returned an invalid terminal result.") from exc
        summary = result.summary
        selected = tuple(reason.agent_id for reason in summary.selection.selected)
        if (
            summary.status is not ExecutionStatus.COMPLETED
            or selected != (DEMAND_AGENT_ID,)
            or len(summary.steps) != 1
        ):
            raise RuntimeError(
                "Live orchestration did not complete the exact Demand Forecasting plan."
            )
        step = summary.steps[0]
        if (
            step.agent_id != DEMAND_AGENT_ID
            or step.status is not StepExecutionStatus.SUCCEEDED
            or step.protocol_version != "1.0"
            or step.invocation_id is None
            or step.answer is None
            or len(step.artifacts) != 1
        ):
            raise RuntimeError(
                "Live Demand Forecasting execution did not return one successful artifact."
            )
        envelope = step.artifacts[0]
        if (
            expected_kind != ArtifactKind.DEMAND_SCENARIO.value
            or not isinstance(envelope.artifact, DemandScenario)
            or envelope.artifact.kind.value != expected_kind
            or envelope.invocation_id != step.invocation_id
            or envelope.correlation_id != summary.execution_id
            or envelope.producer.agent_id != DEMAND_AGENT_ID
        ):
            raise RuntimeError(
                "Live orchestration returned an artifact outside the certified binding."
            )
        reference = ArtifactChannelReference(
            media_type=ARTIFACT_MEDIA_TYPE,
            artifact_id=envelope.artifact_id,
            artifact_kind=envelope.artifact.kind,
            invocation_id=envelope.invocation_id,
            correlation_id=envelope.correlation_id,
            handle="ath-orchestration-response",
            expires_at=envelope.expires_at,
        )
        reference_payload = reference.model_dump(mode="json", by_alias=True)
        envelope_payload = envelope.model_dump(mode="json", by_alias=True)
        publication = {
            "schemaVersion": "1.0",
            "status": "pass",
            "reference": reference_payload,
            "envelope": envelope_payload,
            "checks": {
                "exactAgent": True,
                "nativeA2AV1": True,
                "exactlyOneArtifact": True,
                "typedDemandScenario": True,
            },
        }
        validated_reference, _ = validate_live_publication_response(
            publication,
            expected_kind=expected_kind,
            correlation_id=summary.execution_id,
            now=verified_at,
        )
        (
            wrong_identity_status,
            wrong_ticket_status,
            missing_ticket_status,
            wrong_claim_identity_status,
            wrong_claim_invocation_status,
            wrong_claim_correlation_status,
            missing_claim_credentials_status,
        ) = _perform_security_probes(
            client,
            claim_url=claim_url,
            publication_url=publication_url,
            invocation_id=envelope.invocation_id,
            correlation_id=envelope.correlation_id,
            artifact=envelope_payload["artifact"],
            access_token=access_token,
            context_headers={"Cache-Control": "no-store"},
            identity_ticket=f"apt-{secrets.token_urlsafe(32)}",
        )

    invocation = {
        "agentId": envelope.producer.agent_id,
        "agentVersion": envelope.producer.agent_version,
        "invocationId": step.invocation_id,
        "correlationId": summary.execution_id,
        "protocolVersion": step.protocol_version,
        "durationMs": step.duration_ms,
        "answerHash": _digest_text(step.answer),
        "answerNonempty": bool(step.answer.strip()),
        "publicationTicketPersisted": False,
    }
    return VerifiedLiveArtifactProbe(
        run_id=run_id,
        correlation_id=summary.execution_id,
        observed_at=verified_at,
        base_url=base_url,
        retrieval_endpoint=orchestration_url,
        claim_endpoint=claim_url,
        publication_endpoint=publication_url,
        revision=revision,
        image_digest=image_digest,
        publication=publication,
        publish_reference=validated_reference,
        retrieval_sha256=_digest_json(raw_result),
        publication_sha256=_digest_json(envelope_payload),
        wrong_identity_status=wrong_identity_status,
        wrong_ticket_status=wrong_ticket_status,
        missing_ticket_status=missing_ticket_status,
        wrong_claim_identity_status=wrong_claim_identity_status,
        wrong_claim_invocation_status=wrong_claim_invocation_status,
        wrong_claim_correlation_status=wrong_claim_correlation_status,
        missing_claim_credentials_status=missing_claim_credentials_status,
        retrieval_mode=ORCHESTRATION_PROBE,
        invocation=invocation,
    )


def _perform_security_probes(
    client: httpx.Client,
    *,
    claim_url: str,
    publication_url: str,
    invocation_id: str,
    correlation_id: str,
    artifact: object,
    access_token: str,
    context_headers: dict[str, str],
    identity_ticket: str,
) -> tuple[int, int, int, int, int, int, int]:
    claim_headers = {
        **context_headers,
        "Authorization": f"Bearer {access_token}",
    }
    wrong_claim_identity_status = _request_expected_status(
        client,
        "POST",
        claim_url,
        403,
        headers=claim_headers,
        json_body={
            "invocationId": invocation_id,
            "correlationId": correlation_id,
        },
    )
    wrong_claim_invocation_status = _request_expected_status(
        client,
        "POST",
        claim_url,
        403,
        headers=claim_headers,
        json_body={
            "invocationId": f"claim-probe-{secrets.token_hex(16)}",
            "correlationId": correlation_id,
        },
    )
    wrong_claim_correlation_status = _request_expected_status(
        client,
        "POST",
        claim_url,
        403,
        headers=claim_headers,
        json_body={
            "invocationId": invocation_id,
            "correlationId": f"claim-probe-{secrets.token_hex(16)}",
        },
    )
    missing_claim_credentials_status = _request_expected_status(
        client,
        "POST",
        claim_url,
        401,
        headers=context_headers,
        json_body={
            "invocationId": invocation_id,
            "correlationId": correlation_id,
        },
    )
    wrong_identity_status = _request_expected_status(
        client,
        "POST",
        publication_url,
        403,
        headers={
            **context_headers,
            "Authorization": f"Bearer {access_token}",
            "X-Agent-IQ-Publication-Ticket": identity_ticket,
        },
        json_body={"artifact": artifact},
    )
    wrong_ticket_status = _request_expected_status(
        client,
        "POST",
        publication_url,
        403,
        headers={
            **context_headers,
            "Authorization": f"Bearer {access_token}",
            "X-Agent-IQ-Publication-Ticket": f"apt-{secrets.token_urlsafe(32)}",
        },
        json_body={"artifact": artifact},
    )
    missing_ticket_status = _request_expected_status(
        client,
        "POST",
        publication_url,
        403,
        headers={
            **context_headers,
            "Authorization": f"Bearer {access_token}",
        },
        json_body={"artifact": artifact},
    )
    return (
        wrong_identity_status,
        wrong_ticket_status,
        missing_ticket_status,
        wrong_claim_identity_status,
        wrong_claim_invocation_status,
        wrong_claim_correlation_status,
        missing_claim_credentials_status,
    )


def _request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = client.request(method, url, headers=headers, json=json_body)
    _validate_response_route(response, url)
    if not 200 <= response.status_code < 300:
        raise RuntimeError(
            f"Live companion {method} probe failed with HTTP {response.status_code}."
        )
    content_type = response.headers.get("content-type", "").partition(";")[0].strip().casefold()
    if content_type != "application/json" or len(response.content) > 1024 * 1024:
        raise RuntimeError("Live companion returned a non-canonical JSON response.")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Live companion returned malformed JSON.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Live companion returned a non-object JSON response.")
    return payload


def _request_expected_status(
    client: httpx.Client,
    method: str,
    url: str,
    expected_status: int,
    *,
    headers: dict[str, str],
    json_body: dict[str, Any] | None = None,
) -> int:
    response = client.request(method, url, headers=headers, json=json_body)
    _validate_response_route(response, url)
    if response.status_code != expected_status:
        raise RuntimeError(
            f"Live companion security probe returned HTTP {response.status_code}; "
            f"expected {expected_status}."
        )
    return response.status_code


def _validate_response_route(response: httpx.Response, expected_url: str) -> None:
    if response.is_redirect:
        raise RuntimeError("Live companion redirects are forbidden.")
    if str(response.url) != expected_url:
        raise RuntimeError("Live companion response did not come from the canonical endpoint.")


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def _digest_json(payload: dict[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(_canonical_json(payload)).hexdigest()}"


def _digest_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('ascii')).hexdigest()}"


def _publication_ticket() -> str:
    value = os.environ.get("AGENT_IQ_LIVE_PUBLICATION_TICKET", "")
    if not value:
        raise RuntimeError("AGENT_IQ_LIVE_PUBLICATION_TICKET is required.")
    try:
        return validate_publication_ticket(value).get_secret_value()
    except ValueError as exc:
        raise RuntimeError("AGENT_IQ_LIVE_PUBLICATION_TICKET is malformed.") from exc


def _get_access_token(scope: str) -> str:
    credential = isolated_default_azure_credential(
        credential_factory=DefaultAzureCredential,
    )
    try:
        token = credential.get_token(scope).token
    finally:
        credential.close()
    if not token:
        raise RuntimeError("Azure identity returned no companion access token.")
    return token


def _companion_application_id_argument(value: str) -> str:
    try:
        return canonicalize_artifact_companion_application_id(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _safe_deployment_metadata(
    report_path: Path,
    report_sha256: object,
    report: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(report_sha256, str):
        raise RuntimeError("The deployment report snapshot has no content hash.")
    ownership_tags = report.get("ownershipTags")
    tags_hash = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                ownership_tags,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("ascii")
        ).hexdigest()
        if isinstance(ownership_tags, dict)
        else None
    )
    return {
        "reportPath": str(report_path.resolve()),
        "reportSha256": report_sha256,
        "appName": report.get("appName"),
        "resourceGroup": report.get("resourceGroup"),
        "fqdn": report.get("fqdn"),
        "revision": report.get("revision"),
        "imageDigest": report.get("imageDigest"),
        "ownershipTagsSha256": tags_hash,
        "activeRevisionsMode": current.get("activeRevisionsMode"),
        "authentication": report.get("authentication"),
        "containerAppAuthConfig": report.get("containerAppAuthConfig"),
        "minReplicas": report.get("minReplicas"),
        "maxReplicas": report.get("maxReplicas"),
    }


def _parse_utc_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    timestamp = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = _build_certificate(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="ascii",
    )
    temporary.replace(args.output)
    print(f"Foundry live artifact certificate: {payload['status'].upper()} {args.output}")
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
