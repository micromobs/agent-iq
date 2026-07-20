from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from agent_iq.api import (
    EntraPublisherTokenValidator,
    PublisherPrincipal,
    create_artifact_companion_app,
)
from agent_iq.artifacts import CompanionArtifactStore
from agent_iq.contracts import (
    ArtifactFetchRequest,
    ArtifactKind,
    ArtifactProducerIdentity,
    CallerContext,
    DemandScenario,
    SupplyCommitment,
    SupplyCommitmentStatus,
)

NOW = datetime(2026, 7, 12, 0, 0, tzinfo=UTC)
TICKET = f"apt-{'a' * 32}"


class FakeTokenValidator:
    def __init__(self, object_id: str = "identity-demand") -> None:
        self.object_id = object_id
        self.tokens: list[str] = []

    async def validate(self, token: str) -> PublisherPrincipal:
        self.tokens.append(token)
        return PublisherPrincipal(tenant_id="tenant-1", object_id=self.object_id)


def _scenario() -> DemandScenario:
    return DemandScenario(
        artifact_id="demand-scenario-api",
        product_id="brightwash-cold",
        market="Germany",
        horizon_start=date(2026, 10, 1),
        horizon_end=date(2026, 10, 31),
        unit="cases",
        baseline_quantity=1000,
        forecast_quantity=1250,
        confidence=0.84,
        evidence_ids=("evidence-demand-1",),
        created_at=NOW,
    )


def _authorized_app(
    validator: FakeTokenValidator,
) -> tuple[TestClient, CompanionArtifactStore, CallerContext]:
    caller = CallerContext(subject_id="caller-1", tenant_id="tenant-1")
    producer = ArtifactProducerIdentity(
        agent_id="consumer-demand-forecasting",
        agent_version="5",
        identity_id="identity-demand",
        framework="PydanticAI",
    )
    store = CompanionArtifactStore(
        clock=lambda: NOW,
        handle_factory=lambda: "ath-api",
        ticket_factory=lambda: TICKET,
    )
    store.authorize_invocation(
        invocation_id="inv-api",
        correlation_id="corr-api",
        caller=caller,
        producer=producer,
        allowed_kinds=(ArtifactKind.DEMAND_SCENARIO,),
    )
    return TestClient(create_artifact_companion_app(store, validator)), store, caller


def test_http_companion_claims_ticket_then_publishes_with_managed_identity() -> None:
    validator = FakeTokenValidator()
    client, store, caller = _authorized_app(validator)
    claimed = client.post(
        "/v1/artifacts/tickets/claim",
        headers={"Authorization": "Bearer hosted-agent-token"},
        json={"invocationId": "inv-api", "correlationId": "corr-api"},
    )

    assert claimed.status_code == 200
    assert claimed.headers["cache-control"] == "no-store"
    assert claimed.json()["publicationTicket"] == TICKET
    response = client.post(
        "/v1/artifacts/publish",
        headers={
            "Authorization": "Bearer hosted-agent-token",
            "X-Agent-IQ-Publication-Ticket": claimed.json()["publicationTicket"],
        },
        json={"artifact": _scenario().model_dump(mode="json", by_alias=True)},
    )

    assert response.status_code == 200
    assert validator.tokens == ["hosted-agent-token", "hosted-agent-token"]
    reference = response.json()
    envelope = store.fetch(
        ArtifactFetchRequest(
            artifact_id=reference["artifactId"],
            invocation_id=reference["invocationId"],
            correlation_id=reference["correlationId"],
            handle=reference["handle"],
        ),
        caller=caller,
    )
    assert envelope.artifact == _scenario()


def test_http_companion_rejects_wrong_claim_identity_and_coordinates() -> None:
    client, _, _ = _authorized_app(FakeTokenValidator())
    for body in (
        {"invocationId": "inv-other", "correlationId": "corr-api"},
        {"invocationId": "inv-api", "correlationId": "corr-other"},
    ):
        denied = client.post(
            "/v1/artifacts/tickets/claim",
            headers={"Authorization": "Bearer hosted-agent-token"},
            json=body,
        )
        assert denied.status_code == 403

    wrong_identity, _, _ = _authorized_app(FakeTokenValidator("identity-other"))
    denied = wrong_identity.post(
        "/v1/artifacts/tickets/claim",
        headers={"Authorization": "Bearer hosted-agent-token"},
        json={"invocationId": "inv-api", "correlationId": "corr-api"},
    )
    assert denied.status_code == 403


def test_http_companion_claim_fails_after_expiry_or_revocation() -> None:
    current = [NOW]
    caller = CallerContext(subject_id="caller-1", tenant_id="tenant-1")
    producer = ArtifactProducerIdentity(
        agent_id="consumer-demand-forecasting",
        agent_version="5",
        identity_id="identity-demand",
        framework="PydanticAI",
    )
    store = CompanionArtifactStore(
        ttl=timedelta(minutes=5),
        clock=lambda: current[0],
        ticket_factory=lambda: TICKET,
    )
    store.authorize_invocation(
        invocation_id="inv-expiring",
        correlation_id="corr-expiring",
        caller=caller,
        producer=producer,
        allowed_kinds=(ArtifactKind.DEMAND_SCENARIO,),
    )
    client = TestClient(create_artifact_companion_app(store, FakeTokenValidator()))
    current[0] += timedelta(minutes=6)

    expired = client.post(
        "/v1/artifacts/tickets/claim",
        headers={"Authorization": "Bearer hosted-agent-token"},
        json={"invocationId": "inv-expiring", "correlationId": "corr-expiring"},
    )
    assert expired.status_code == 403

    current[0] = NOW
    store.authorize_invocation(
        invocation_id="inv-revoked",
        correlation_id="corr-revoked",
        caller=caller,
        producer=producer,
        allowed_kinds=(ArtifactKind.DEMAND_SCENARIO,),
    )
    store.revoke_invocation("inv-revoked")
    revoked = client.post(
        "/v1/artifacts/tickets/claim",
        headers={"Authorization": "Bearer hosted-agent-token"},
        json={"invocationId": "inv-revoked", "correlationId": "corr-revoked"},
    )
    assert revoked.status_code == 403


def test_http_companion_requires_ticket_and_matching_managed_identity() -> None:
    validator = FakeTokenValidator()
    client, store, caller = _authorized_app(validator)
    response = client.post(
        "/v1/artifacts/publish",
        headers={
            "Authorization": "Bearer hosted-agent-token",
            "X-Agent-IQ-Publication-Ticket": TICKET,
        },
        json={"artifact": _scenario().model_dump(mode="json", by_alias=True)},
    )

    assert response.status_code == 200
    assert validator.tokens == ["hosted-agent-token"]
    reference = response.json()
    assert reference["invocationId"] == "inv-api"
    assert reference["correlationId"] == "corr-api"
    envelope = store.fetch(
        ArtifactFetchRequest(
            artifact_id=reference["artifactId"],
            invocation_id=reference["invocationId"],
            correlation_id=reference["correlationId"],
            handle=reference["handle"],
        ),
        caller=caller,
    )
    assert envelope.artifact == _scenario()

    wrong_identity, _, _ = _authorized_app(FakeTokenValidator("identity-other"))
    denied = wrong_identity.post(
        "/v1/artifacts/publish",
        headers={
            "Authorization": "Bearer other-token",
            "X-Agent-IQ-Publication-Ticket": TICKET,
        },
        json={"artifact": _scenario().model_dump(mode="json", by_alias=True)},
    )
    assert denied.status_code == 403


def test_http_companion_accepts_normal_entra_token_length() -> None:
    validator = FakeTokenValidator()
    client, _, _ = _authorized_app(validator)
    token = "a" * 1200

    response = client.post(
        "/v1/artifacts/publish",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Agent-IQ-Publication-Ticket": TICKET,
        },
        json={"artifact": _scenario().model_dump(mode="json", by_alias=True)},
    )

    assert response.status_code == 200
    assert validator.tokens == [token]


def test_http_companion_health_and_header_size_limit() -> None:
    client, _, _ = _authorized_app(FakeTokenValidator())

    assert client.get("/healthz").json() == {"status": "ok"}
    denied = client.post(
        "/v1/artifacts/publish",
        headers={
            "Authorization": f"Bearer {'a' * 8192}",
            "X-Agent-IQ-Publication-Ticket": TICKET,
        },
        json={"artifact": _scenario().model_dump(mode="json", by_alias=True)},
    )
    assert denied.status_code == 401


def test_http_companion_maps_conflict_and_policy_failures() -> None:
    client, _, _ = _authorized_app(FakeTokenValidator())
    headers = {
        "Authorization": "Bearer hosted-agent-token",
        "X-Agent-IQ-Publication-Ticket": TICKET,
    }
    scenario = _scenario()

    assert (
        client.post(
            "/v1/artifacts/publish",
            headers=headers,
            json={"artifact": scenario.model_dump(mode="json", by_alias=True)},
        ).status_code
        == 200
    )
    changed = scenario.model_copy(update={"forecast_quantity": 1300})
    assert (
        client.post(
            "/v1/artifacts/publish",
            headers=headers,
            json={"artifact": changed.model_dump(mode="json", by_alias=True)},
        ).status_code
        == 409
    )

    disallowed = SupplyCommitment(
        artifact_id="supply-api",
        material_id="material-1",
        plant_id="plant-1",
        market="Germany",
        quantity=100,
        unit="cases",
        available_from=date(2026, 10, 1),
        status=SupplyCommitmentStatus.PROPOSED,
        source_system="sap-fixture",
        source_object_id="supply-1",
        as_of=NOW,
        evidence_ids=("evidence-supply-1",),
    )
    assert (
        client.post(
            "/v1/artifacts/publish",
            headers=headers,
            json={"artifact": disallowed.model_dump(mode="json", by_alias=True)},
        ).status_code
        == 422
    )


def test_http_companion_fails_closed_on_missing_or_malformed_credentials_and_body() -> None:
    client, _, _ = _authorized_app(FakeTokenValidator())
    body = {"artifact": _scenario().model_dump(mode="json", by_alias=True)}

    assert client.post("/v1/artifacts/publish", json=body).status_code == 401
    assert (
        client.post(
            "/v1/artifacts/publish",
            headers={"Authorization": "Basic value"},
            json=body,
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/v1/artifacts/publish",
            headers={"Authorization": "Bearer value"},
            json=body,
        ).status_code
        == 403
    )
    malformed = _scenario().model_dump(mode="json", by_alias=True)
    malformed["confidence"] = 2
    assert (
        client.post(
            "/v1/artifacts/publish",
            headers={
                "Authorization": "Bearer value",
                "X-Agent-IQ-Publication-Ticket": TICKET,
            },
            json={"artifact": malformed},
        ).status_code
        == 422
    )


def test_entra_validator_checks_signature_audience_tenant_issuer_and_object_id() -> None:
    tenant_id = "tenant-test"
    audience = "api://agent-iq-test"
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    now = datetime.now(UTC)

    class FakeJwks:
        def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
            assert token
            return SimpleNamespace(key=public_key)

    def encoded(**updates: str) -> str:
        claims = {
            "iss": f"https://login.microsoftonline.com/{tenant_id}/v2.0",
            "aud": audience,
            "tid": tenant_id,
            "oid": "identity-demand",
            "iat": int((now - timedelta(seconds=1)).timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        }
        claims.update(updates)
        return jwt.encode(claims, private_key, algorithm="RS256")

    validator = EntraPublisherTokenValidator(
        tenant_id=tenant_id,
        audience=audience,
        jwks_client=FakeJwks(),
    )

    principal = asyncio.run(validator.validate(encoded()))
    assert principal == PublisherPrincipal(
        tenant_id=tenant_id,
        object_id="identity-demand",
    )
    for invalid in (
        encoded(aud="api://wrong"),
        encoded(tid="other-tenant"),
        encoded(iss="https://issuer.invalid/"),
        encoded(oid=""),
    ):
        with pytest.raises(PermissionError):
            asyncio.run(validator.validate(invalid))
