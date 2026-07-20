from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from scripts import build_live_artifact_certificate as artifact_builder
from scripts import certify_orchestrator_canary as orchestrator_certifier

from agent_iq.contracts import (
    ArtifactEnvelope,
    ExecutionStatus,
    StepExecutionStatus,
)
from agent_iq.evals.live_artifacts import correlation_id_for_run

NOW = datetime(2026, 7, 12, 12, 5, tzinfo=UTC)
OBSERVED_AT = NOW - timedelta(minutes=1)
RUN_ID = "artifact-canary-run-20260712T1204000000000Z-" + "d" * 32
CORRELATION_ID = correlation_id_for_run(RUN_ID)
ORCHESTRATION_CORRELATION_ID = "exec-live"
APP_NAME = "agent-iq-orchestrator-canary"
RESOURCE_GROUP = "rg-agent-iq"
REVISION = f"{APP_NAME}--d12abc"
IMAGE_DIGEST = f"sha256:{'a' * 64}"
IMAGE = f"registry.example.invalid/agent-iq-orchestrator@{IMAGE_DIGEST}"
TICKET = "apt-" + "t" * 40
TEST_BEARER = "test-" + "access-token"
COMPANION_APPLICATION_ID = "11111111-1111-4111-8111-abcdefabcdef"
EXPECTED_AUDIENCE = COMPANION_APPLICATION_ID
EXPECTED_SCOPE = f"{EXPECTED_AUDIENCE}/.default"


def _live_publication(
    *,
    correlation_id: str = CORRELATION_ID,
    issued_at: datetime = OBSERVED_AT,
) -> dict[str, Any]:
    timestamp = issued_at.isoformat().replace("+00:00", "Z")
    expires_at = (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
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
        "assumptions": ["live certification"],
        "evidenceIds": ["evidence-1"],
        "createdAt": timestamp,
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
    reference = {
        "schemaVersion": "1.0",
        "delivery": "companion",
        "mediaType": "application/vnd.agent-iq.business-artifact+json;version=1.0",
        "artifactId": "demand-live",
        "artifactKind": "demand_scenario",
        "invocationId": "inv-live",
        "correlationId": correlation_id,
        "handle": "ath-live-certification-handle",
        "expiresAt": expires_at,
    }
    envelope = {
        "schemaVersion": "1.0",
        "artifactId": "demand-live",
        "invocationId": "inv-live",
        "correlationId": correlation_id,
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
        "issuedAt": timestamp,
        "expiresAt": expires_at,
    }
    return {
        "schemaVersion": "1.0",
        "status": "pass",
        "reference": reference,
        "envelope": envelope,
    }


def _payloads(publication: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    live_publication = publication or _live_publication()
    common = {
        "schemaVersion": "1.0",
        "generatedAt": OBSERVED_AT.isoformat(),
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
        "answerHash": f"sha256:{'b' * 64}",
        "answerNonempty": True,
        "publicationTicketPersisted": False,
        "prompt": "private prompt that must not be copied",
    }
    replay_publication = {
        **live_publication,
        **common,
        "checks": {
            "typedArtifactValidated": True,
            "invocationBound": True,
            "correlationBound": True,
            "producerIdentityBound": True,
            "opaqueTicketNotPersisted": True,
        },
        "sources": ["private source"],
        "toolArguments": {"query": "private query"},
        "contextId": "private-context",
    }
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
    return {
        "invocation": invocation,
        "publication": replay_publication,
        "security": security,
    }


def _write_evidence(
    tmp_path: Path,
    publication: dict[str, Any] | None = None,
) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for kind, payload in _payloads(publication).items():
        path = tmp_path / f"{RUN_ID}-{kind}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[kind] = path
    return paths


def _deployment_report(
    tmp_path: Path,
    *,
    image: str = IMAGE,
    image_digest: str = IMAGE_DIGEST,
    revision: str = REVISION,
) -> Path:
    payload = {
        "schemaVersion": "1.0",
        "generatedAt": OBSERVED_AT.isoformat(),
        "status": "pass",
        "appName": APP_NAME,
        "resourceGroup": RESOURCE_GROUP,
        "fqdn": "https://example.invalid",
        "revision": revision,
        "image": image,
        "imageDigest": image_digest,
        "ownershipTags": dict(orchestrator_certifier.EXPECTED_DEPLOYMENT_OWNERSHIP_TAGS),
        "managedIdentityObjectId": "identity-object-id",
        "ingress": ["mcp", "http_sse", "a2a"],
        "containerAppIngress": {
            "external": True,
            "targetPort": 8080,
            "transport": "auto",
        },
        "authentication": "anonymous_canary",
        "containerAppAuthConfig": "none",
        "applicationInsights": "agent-iq-orchestrator-ai",
        "minReplicas": 1,
        "maxReplicas": 1,
    }
    path = tmp_path / ("orchestrator-canary-run-20260712T1204000000000Z-" + "e" * 32 + ".json")
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _current_app(
    *,
    traffic: list[dict[str, object]] | None = None,
    image: str = IMAGE,
    revision: str = REVISION,
    public_base_url: str = "https://example.invalid",
) -> dict[str, object]:
    return {
        "name": APP_NAME,
        "tags": dict(orchestrator_certifier.EXPECTED_DEPLOYMENT_OWNERSHIP_TAGS),
        "properties": {
            "latestReadyRevisionName": revision,
            "configuration": {
                "activeRevisionsMode": "Multiple",
                "ingress": {
                    "external": True,
                    "fqdn": "example.invalid",
                    "targetPort": 8080,
                    "transport": "Auto",
                    "traffic": (
                        [{"revisionName": revision, "weight": 100}] if traffic is None else traffic
                    ),
                },
            },
            "template": {
                "containers": [
                    {
                        "image": image,
                        "env": [
                            {
                                "name": "AGENT_IQ_ALLOW_ANONYMOUS_CONNECTOR",
                                "value": "true",
                            },
                            {
                                "name": "AGENT_IQ_PUBLIC_BASE_URL",
                                "value": public_base_url,
                            },
                        ],
                    }
                ],
                "scale": {"minReplicas": 1, "maxReplicas": 1},
            },
        },
    }


def _current_revision(
    *,
    image: str = IMAGE,
    revision: str = REVISION,
    public_base_url: str = "https://example.invalid",
) -> dict[str, object]:
    return {
        "name": revision,
        "properties": {
            "active": True,
            "healthState": "Healthy",
            "provisioningState": "Provisioned",
            "runningState": "Running",
            "template": {
                "containers": [
                    {
                        "image": image,
                        "env": [
                            {
                                "name": "AGENT_IQ_PUBLIC_BASE_URL",
                                "value": public_base_url,
                            }
                        ],
                    }
                ]
            },
        },
    }


def _mock_azure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    current: dict[str, object] | None = None,
    revision: dict[str, object] | None = None,
) -> None:
    monkeypatch.setattr(
        orchestrator_certifier,
        "_query_current_container_app",
        lambda resource_group, app_name: _current_app() if current is None else current,
    )
    monkeypatch.setattr(
        orchestrator_certifier,
        "_query_current_container_app_auth",
        lambda resource_group, app_name: {},
    )
    monkeypatch.setattr(
        orchestrator_certifier,
        "_query_current_container_app_revision",
        lambda resource_group, app_name, revision_name: (
            _current_revision() if revision is None else revision
        ),
    )


def _args(
    report: Path,
    paths: dict[str, Path] | None = None,
    *,
    base_url: str = "https://example.invalid",
    probe_mode: str = artifact_builder.CERTIFICATION_PROBE,
) -> argparse.Namespace:
    return argparse.Namespace(
        invocation=None if paths is None else paths["invocation"],
        publication=None if paths is None else paths["publication"],
        security=None if paths is None else paths["security"],
        run_id=RUN_ID,
        correlation_id=(
            None if probe_mode == artifact_builder.ORCHESTRATION_PROBE else CORRELATION_ID
        ),
        probe_mode=probe_mode,
        expected_kind="demand_scenario",
        deployment_report=report,
        base_url=base_url,
        resource_group=RESOURCE_GROUP,
        app_name=APP_NAME,
        app_insights=None,
        companion_application_id=COMPANION_APPLICATION_ID,
        output=report.parent / "certificate.json",
    )


def _transport(
    publication: dict[str, Any] | None = None,
    *,
    redirect: bool = False,
    missing: bool = False,
    calls: list[str] | None = None,
) -> httpx.MockTransport:
    live_publication = publication or _live_publication()
    security_probe = 0
    claim_probe = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal claim_probe, security_probe
        if calls is not None:
            calls.append(f"{request.method} {request.url}")
        if request.url.path == "/v1/certification":
            if redirect:
                return httpx.Response(307, headers={"Location": "/v1/certification"})
            if missing:
                return httpx.Response(404, json={"detail": "No artifact has been published."})
            if request.headers.get("X-Agent-IQ-Publication-Ticket") != TICKET:
                return httpx.Response(403, json={"detail": "denied"})
            return httpx.Response(200, json=live_publication)
        if request.url.path == "/v1/artifacts/tickets/claim":
            body = json.loads(request.content)
            if claim_probe < 3:
                assert request.headers.get("Authorization") == " ".join(("Bearer", TEST_BEARER))
                assert body["invocationId"]
                assert body["correlationId"]
                status = 403
            else:
                assert request.headers.get("Authorization") is None
                assert body == {
                    "invocationId": "inv-live",
                    "correlationId": CORRELATION_ID,
                }
                status = 401
            claim_probe += 1
            return httpx.Response(status, json={"detail": "denied"})
        if request.url.path == "/v1/artifacts/publish":
            assert request.headers.get("Authorization") == " ".join(("Bearer", TEST_BEARER))
            supplied_ticket = request.headers.get("X-Agent-IQ-Publication-Ticket")
            if security_probe == 0:
                assert supplied_ticket == TICKET
            elif security_probe == 1:
                assert supplied_ticket is not None and supplied_ticket != TICKET
            else:
                assert supplied_ticket is None
            security_probe += 1
            return httpx.Response(403, json={"detail": "denied"})
        return httpx.Response(404, json={"detail": "not found"})

    return httpx.MockTransport(handler)


def _orchestration_result(*, artifact_count: int = 1) -> SimpleNamespace:
    envelope = ArtifactEnvelope.model_validate(
        _live_publication(correlation_id=ORCHESTRATION_CORRELATION_ID)["envelope"]
    )
    step = SimpleNamespace(
        agent_id=artifact_builder.DEMAND_AGENT_ID,
        status=StepExecutionStatus.SUCCEEDED,
        protocol_version="1.0",
        invocation_id=envelope.invocation_id,
        answer="Grounded live demand forecast [1].",
        duration_ms=125,
        artifacts=tuple(envelope for _ in range(artifact_count)),
    )
    summary = SimpleNamespace(
        execution_id=ORCHESTRATION_CORRELATION_ID,
        status=ExecutionStatus.COMPLETED,
        selection=SimpleNamespace(
            selected=(SimpleNamespace(agent_id=artifact_builder.DEMAND_AGENT_ID),)
        ),
        steps=(step,),
    )
    return SimpleNamespace(summary=summary)


def _orchestration_transport(*, calls: list[str] | None = None) -> httpx.MockTransport:
    claim_probe = 0
    publication_probe = 0
    seen_tickets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal claim_probe, publication_probe
        if calls is not None:
            calls.append(f"{request.method} {request.url}")
        if request.url.path == "/v1/orchestrate":
            body = json.loads(request.content)
            assert request.method == "POST"
            assert body["request"]["requestId"] == RUN_ID
            assert body["request"]["intent"] == artifact_builder.DEMAND_CERTIFICATION_INTENT
            assert body["request"]["filters"] == {
                "maxCandidates": 20,
                "maxSelected": 1,
                "requiredProtocols": ["a2a"],
                "requiredInteractionModes": [],
                "allowedAgentIds": [artifact_builder.DEMAND_AGENT_ID],
                "excludedAgentIds": [],
            }
            return httpx.Response(200, json={"mock": "orchestration-result"})
        if request.url.path == "/v1/artifacts/tickets/claim":
            body = json.loads(request.content)
            if claim_probe < 3:
                assert request.headers.get("Authorization") == f"Bearer {TEST_BEARER}"
                assert body["invocationId"]
                assert body["correlationId"]
                status = 403
            else:
                assert request.headers.get("Authorization") is None
                assert body == {
                    "invocationId": "inv-live",
                    "correlationId": ORCHESTRATION_CORRELATION_ID,
                }
                status = 401
            claim_probe += 1
            return httpx.Response(status, json={"detail": "denied"})
        if request.url.path == "/v1/artifacts/publish":
            assert request.headers.get("Authorization") == f"Bearer {TEST_BEARER}"
            ticket = request.headers.get("X-Agent-IQ-Publication-Ticket")
            if publication_probe < 2:
                assert ticket is not None
                seen_tickets.append(ticket)
                assert len(seen_tickets) < 2 or seen_tickets[0] != seen_tickets[1]
            else:
                assert ticket is None
            publication_probe += 1
            return httpx.Response(403, json={"detail": "denied"})
        return httpx.Response(404, json={"detail": "not found"})

    return httpx.MockTransport(handler)


def _build(
    args: argparse.Namespace,
    monkeypatch: pytest.MonkeyPatch,
    transport: httpx.BaseTransport,
) -> dict[str, Any]:
    monkeypatch.setenv("AGENT_IQ_LIVE_PUBLICATION_TICKET", TICKET)
    return artifact_builder._build_certificate(
        args,
        now=NOW,
        http_transport=transport,
        access_token=TEST_BEARER,
    )


def test_live_artifact_builder_rejects_latest_input_pointer(tmp_path: Path) -> None:
    paths = _write_evidence(tmp_path)
    latest = tmp_path / "latest.json"
    latest.write_bytes(paths["invocation"].read_bytes())
    paths["invocation"] = latest

    with pytest.raises(ValueError, match="run-specific"):
        artifact_builder._build_certificate(
            _args(_deployment_report(tmp_path), paths),
            now=NOW,
            access_token=TEST_BEARER,
        )


def test_noncanonical_companion_id_fails_before_deployment_or_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args(_deployment_report(tmp_path))
    args.companion_application_id = COMPANION_APPLICATION_ID.upper()
    side_effects: list[str] = []
    monkeypatch.setattr(
        artifact_builder,
        "certify_deployment_evidence",
        lambda *args, **kwargs: side_effects.append("deployment"),
    )
    monkeypatch.setattr(
        artifact_builder,
        "_get_access_token",
        lambda scope: side_effects.append(f"token:{scope}"),
    )

    with pytest.raises(ValueError, match="canonical UUID"):
        artifact_builder._build_certificate(
            args,
            now=NOW,
            http_transport=_transport(calls=side_effects),
        )

    assert side_effects == []


def test_cli_rejects_removed_free_form_token_scope(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = _deployment_report(tmp_path)

    with pytest.raises(SystemExit):
        artifact_builder._parse_args(
            [
                "--run-id",
                RUN_ID,
                "--correlation-id",
                CORRELATION_ID,
                "--expected-kind",
                "demand_scenario",
                "--deployment-report",
                str(report),
                "--base-url",
                "https://example.invalid",
                "--resource-group",
                RESOURCE_GROUP,
                "--app-name",
                APP_NAME,
                "--companion-application-id",
                COMPANION_APPLICATION_ID,
                "--output",
                str(tmp_path / "certificate.json"),
                "--token-scope",
                "api://attacker.invalid/.default",
            ]
        )

    assert "unrecognized arguments: --token-scope" in capsys.readouterr().err


def test_fabricated_fresh_files_and_missing_live_probe_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_evidence(tmp_path)
    _mock_azure(monkeypatch)

    with pytest.raises(RuntimeError, match="HTTP 404"):
        _build(
            _args(_deployment_report(tmp_path), paths),
            monkeypatch,
            _transport(missing=True),
        )


def test_replayed_live_response_fails_freshness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay = _live_publication(issued_at=NOW - timedelta(minutes=11))
    _mock_azure(monkeypatch)

    with pytest.raises(ValueError, match="stale"):
        _build(
            _args(_deployment_report(tmp_path)),
            monkeypatch,
            _transport(replay),
        )


def test_wrong_live_correlation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_azure(monkeypatch)

    with pytest.raises(ValueError, match="not bound"):
        _build(
            _args(_deployment_report(tmp_path)),
            monkeypatch,
            _transport(_live_publication(correlation_id="different-correlation")),
        )


def test_wrong_current_revision_fails_before_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong_revision = f"{APP_NAME}--other"
    current = _current_app(revision=wrong_revision)
    revision = _current_revision(revision=wrong_revision)
    _mock_azure(monkeypatch, current=current, revision=revision)
    calls: list[str] = []

    with pytest.raises(RuntimeError, match=r"current.*Revision"):
        _build(
            _args(_deployment_report(tmp_path)),
            monkeypatch,
            _transport(calls=calls),
        )
    assert calls == []


def test_wrong_current_digest_fails_before_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong_digest = f"sha256:{'f' * 64}"
    wrong_image = f"registry.example.invalid/agent-iq-orchestrator@{wrong_digest}"
    current = _current_app(image=wrong_image)
    revision = _current_revision(image=wrong_image)
    _mock_azure(monkeypatch, current=current, revision=revision)
    calls: list[str] = []

    with pytest.raises(RuntimeError, match=r"current.*ImageDigest"):
        _build(
            _args(_deployment_report(tmp_path)),
            monkeypatch,
            _transport(calls=calls),
        )
    assert calls == []


def test_mismatched_live_traffic_fails_before_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _current_app(
        traffic=[
            {"revisionName": REVISION, "weight": 90},
            {"revisionName": f"{APP_NAME}--old", "weight": 10},
        ]
    )
    _mock_azure(monkeypatch, current=current)
    calls: list[str] = []

    with pytest.raises(RuntimeError, match="currentTraffic"):
        _build(
            _args(_deployment_report(tmp_path)),
            monkeypatch,
            _transport(calls=calls),
        )
    assert calls == []


def test_redirect_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_azure(monkeypatch)

    with pytest.raises(RuntimeError, match="redirects"):
        _build(
            _args(_deployment_report(tmp_path)),
            monkeypatch,
            _transport(redirect=True),
        )


def test_arbitrary_host_fails_before_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_azure(monkeypatch)
    calls: list[str] = []

    with pytest.raises(RuntimeError, match="fqdn"):
        _build(
            _args(_deployment_report(tmp_path), base_url="https://attacker.invalid"),
            monkeypatch,
            _transport(calls=calls),
        )
    assert calls == []


def test_valid_fresh_bound_probe_passes_without_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_azure(monkeypatch)

    certificate = _build(
        _args(_deployment_report(tmp_path)),
        monkeypatch,
        _transport(),
    )

    assert certificate["status"] == "pass"
    assert "evidence" not in certificate
    assert certificate["liveProbe"]["revision"] == REVISION
    assert certificate["liveProbe"]["imageDigest"] == IMAGE_DIGEST
    assert certificate["liveProbe"]["baseUrl"] == "https://example.invalid"
    assert certificate["liveProbe"]["claimEndpoint"].endswith("/v1/artifacts/tickets/claim")
    assert certificate["security"] == {
        "wrongIdentityStatus": 403,
        "wrongTicketStatus": 403,
        "missingTicketStatus": 403,
        "wrongClaimIdentityStatus": 403,
        "wrongClaimInvocationStatus": 403,
        "wrongClaimCorrelationStatus": 403,
        "missingClaimCredentialsStatus": 401,
    }
    assert certificate["publisherAuthentication"] == {
        "expectedAudience": EXPECTED_AUDIENCE,
        "expectedAudienceSha256": (
            "sha256:" + hashlib.sha256(EXPECTED_AUDIENCE.encode("ascii")).hexdigest()
        ),
    }


def test_orchestration_response_probe_certifies_exact_live_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_azure(monkeypatch)
    monkeypatch.setattr(
        artifact_builder,
        "OrchestrationResult",
        SimpleNamespace(model_validate=lambda payload: _orchestration_result()),
    )
    calls: list[str] = []

    certificate = _build(
        _args(
            _deployment_report(tmp_path),
            probe_mode=artifact_builder.ORCHESTRATION_PROBE,
        ),
        monkeypatch,
        _orchestration_transport(calls=calls),
    )

    assert certificate["status"] == "pass"
    assert certificate["liveProbe"]["retrievalMode"] == "orchestration_response"
    assert certificate["liveProbe"]["retrievalEndpoint"].endswith("/v1/orchestrate")
    assert calls[0] == "POST https://example.invalid/v1/orchestrate"
    serialized = json.dumps(certificate, sort_keys=True)
    assert "Grounded live demand forecast" not in serialized
    assert ORCHESTRATION_CORRELATION_ID not in serialized


def test_orchestration_response_probe_rejects_multiple_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_azure(monkeypatch)
    monkeypatch.setattr(
        artifact_builder,
        "OrchestrationResult",
        SimpleNamespace(model_validate=lambda payload: _orchestration_result(artifact_count=2)),
    )

    with pytest.raises(RuntimeError, match="one successful artifact"):
        _build(
            _args(
                _deployment_report(tmp_path),
                probe_mode=artifact_builder.ORCHESTRATION_PROBE,
            ),
            monkeypatch,
            _orchestration_transport(),
        )


def test_access_token_uses_only_uuid_derived_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeCredential:
        def __init__(self, **kwargs: Any) -> None:
            captured["transportIsolated"] = kwargs["transport"]._use_env_settings is False
            captured["redirectsDisabled"] = kwargs["redirect_policy"].allow is False

        def get_token(self, scope: str) -> Any:
            captured["scope"] = scope
            return argparse.Namespace(token=TEST_BEARER)

        def close(self) -> None:
            captured["closed"] = True

    _mock_azure(monkeypatch)
    monkeypatch.setenv("AGENT_IQ_LIVE_PUBLICATION_TICKET", TICKET)
    monkeypatch.setenv("AGENT_IQ_CANARY_TOKEN_SCOPE", "api://attacker.invalid/.default")
    monkeypatch.setattr(artifact_builder, "DefaultAzureCredential", FakeCredential)

    certificate = artifact_builder._build_certificate(
        _args(_deployment_report(tmp_path)),
        now=NOW,
        http_transport=_transport(),
    )

    assert certificate["status"] == "pass"
    assert captured == {
        "transportIsolated": True,
        "redirectsDisabled": True,
        "scope": EXPECTED_SCOPE,
        "closed": True,
    }


def test_matching_files_are_non_authoritative_safe_expectations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = _live_publication()
    paths = _write_evidence(tmp_path, publication)
    report = _deployment_report(tmp_path)
    _mock_azure(monkeypatch)

    certificate = _build(
        _args(report, paths),
        monkeypatch,
        _transport(publication),
    )

    assert certificate["status"] == "pass"
    assert certificate["deployment"]["reportPath"] == str(report.resolve())
    assert certificate["evidence"]["invocation"]["path"] == str(paths["invocation"].resolve())
    serialized = json.dumps(certificate, sort_keys=True)
    for unsafe in (
        "private prompt",
        "private source",
        "private query",
        "private-context",
        "inv-live",
        CORRELATION_ID,
        "identity-live",
        "identity-object-id",
        TICKET,
        TEST_BEARER,
    ):
        assert unsafe not in serialized
