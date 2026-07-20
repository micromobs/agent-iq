from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from agent_iq.artifacts import CompanionArtifactStore
from agent_iq.contracts import (
    ArtifactFetchRequest,
    ArtifactInvocationFetchRequest,
    ArtifactKind,
    ArtifactProducerIdentity,
    ArtifactPublishRequest,
    CallerContext,
    DemandScenario,
    ErrorCode,
    SapChangeProposal,
    SapFieldChange,
)
from agent_iq.errors import AgentIqOperationError

NOW = datetime(2026, 7, 11, 0, 0, tzinfo=UTC)


def _caller(subject: str = "caller-1", tenant: str = "tenant-1") -> CallerContext:
    return CallerContext(subject_id=subject, tenant_id=tenant)


def _producer(agent_id: str = "consumer-demand-forecasting") -> ArtifactProducerIdentity:
    return ArtifactProducerIdentity(
        agent_id=agent_id,
        agent_version="2",
        identity_id=f"identity-{agent_id}",
        framework="PydanticAI",
    )


def _scenario(forecast: float = 1250) -> DemandScenario:
    return DemandScenario(
        artifact_id="demand-germany-october",
        product_id="brightwash-cold",
        market="Germany",
        horizon_start=date(2026, 10, 1),
        horizon_end=date(2026, 10, 31),
        unit="cases",
        baseline_quantity=1000,
        forecast_quantity=forecast,
        confidence=0.84,
        assumptions=("October launch",),
        evidence_ids=("evidence-demand-1",),
        created_at=NOW,
    )


def _authorized_store(
    current: list[datetime],
) -> tuple[CompanionArtifactStore, ArtifactProducerIdentity]:
    producer = _producer()
    store = CompanionArtifactStore(
        ttl=timedelta(minutes=5),
        clock=lambda: current[0],
        handle_factory=lambda: "ath-fixed",
    )
    store.authorize_invocation(
        invocation_id="inv-1",
        correlation_id="corr-1",
        caller=_caller(),
        producer=producer,
        allowed_kinds=(ArtifactKind.DEMAND_SCENARIO,),
    )
    return store, producer


def test_publish_is_idempotent_and_fetch_is_identity_bound() -> None:
    current = [NOW]
    store, producer = _authorized_store(current)
    request = ArtifactPublishRequest(
        invocation_id="inv-1",
        correlation_id="corr-1",
        artifact=_scenario(),
    )

    reference = store.publish(request, producer=producer)
    assert store.publish(request, producer=producer) == reference
    envelope = store.fetch(ArtifactFetchRequest.from_reference(reference), caller=_caller())
    invocation_artifacts = store.fetch_invocation(
        ArtifactInvocationFetchRequest(
            invocation_id="inv-1",
            correlation_id="corr-1",
        ),
        caller=_caller(),
    )

    assert envelope.artifact == _scenario()
    assert invocation_artifacts == (envelope,)
    assert envelope.producer == producer
    assert envelope.tenant_id == "tenant-1"
    assert envelope.caller_subject_id == "caller-1"
    assert envelope.content_hash.startswith("sha256:")

    fetch = ArtifactFetchRequest.from_reference(reference)
    for caller in (_caller("caller-2"), _caller(tenant="tenant-2")):
        with pytest.raises(AgentIqOperationError) as denied:
            store.fetch(fetch, caller=caller)
        assert denied.value.error.code is ErrorCode.AUTHORIZATION_DENIED
        with pytest.raises(AgentIqOperationError) as invocation_denied:
            store.fetch_invocation(
                ArtifactInvocationFetchRequest(
                    invocation_id="inv-1",
                    correlation_id="corr-1",
                ),
                caller=caller,
            )
        assert invocation_denied.value.error.code is ErrorCode.AUTHORIZATION_DENIED

    for tampered in (
        fetch.model_copy(update={"artifact_id": "other"}),
        fetch.model_copy(update={"invocation_id": "inv-2"}),
        fetch.model_copy(update={"correlation_id": "corr-2"}),
        fetch.model_copy(update={"handle": "ath-missing"}),
    ):
        with pytest.raises(AgentIqOperationError) as denied:
            store.fetch(tampered, caller=_caller())
        assert denied.value.error.code is ErrorCode.AUTHORIZATION_DENIED

    current[0] += timedelta(minutes=6)
    with pytest.raises(AgentIqOperationError) as expired:
        store.fetch(fetch, caller=_caller())
    assert expired.value.error.code is ErrorCode.ARTIFACT_UNAVAILABLE


def test_publication_rejects_wrong_producer_kind_and_conflicting_content() -> None:
    current = [NOW]
    store, producer = _authorized_store(current)
    request = ArtifactPublishRequest(
        invocation_id="inv-1",
        correlation_id="corr-1",
        artifact=_scenario(),
    )

    with pytest.raises(AgentIqOperationError) as denied:
        store.publish(request, producer=_producer("other-agent"))
    assert denied.value.error.code is ErrorCode.AUTHORIZATION_DENIED

    proposal = SapChangeProposal(
        artifact_id="sap-change-1",
        operation="update",
        object_type="production_order",
        object_id="order-1",
        changes=(SapFieldChange(field_path="quantity", before=100, after=125),),
        reason="Align supply with the approved launch forecast.",
        risk="medium",
        proposed_at=NOW,
        evidence_ids=("evidence-supply-1",),
    )
    with pytest.raises(AgentIqOperationError) as blocked:
        store.publish(
            request.model_copy(update={"artifact": proposal}),
            producer=producer,
        )
    assert blocked.value.error.code is ErrorCode.POLICY_BLOCKED

    store.publish(request, producer=producer)
    with pytest.raises(AgentIqOperationError) as conflict:
        store.publish(
            request.model_copy(update={"artifact": _scenario(1300)}),
            producer=producer,
        )
    assert conflict.value.error.code is ErrorCode.ARTIFACT_CONFLICT


def test_identical_authorization_is_idempotent_without_extending_expiry() -> None:
    current = [NOW]
    store, producer = _authorized_store(current)
    current[0] += timedelta(minutes=1)

    store.authorize_invocation(
        invocation_id="inv-1",
        correlation_id="corr-1",
        caller=_caller(),
        producer=producer,
        allowed_kinds=(ArtifactKind.DEMAND_SCENARIO,),
    )
    with pytest.raises(AgentIqOperationError) as changed:
        store.authorize_invocation(
            invocation_id="inv-1",
            correlation_id="corr-1",
            caller=_caller(),
            producer=producer,
            allowed_kinds=(ArtifactKind.SAP_CHANGE_PROPOSAL,),
        )
    assert changed.value.error.code is ErrorCode.ARTIFACT_CONFLICT

    current[0] += timedelta(minutes=4, seconds=1)
    request = ArtifactPublishRequest(
        invocation_id="inv-1",
        correlation_id="corr-1",
        artifact=_scenario(),
    )
    with pytest.raises(AgentIqOperationError) as expired:
        store.publish(request, producer=producer)
    assert expired.value.error.code is ErrorCode.AUTHORIZATION_DENIED


def test_opaque_ticket_publishes_only_for_authorized_producer_and_lifetime() -> None:
    current = [NOW]
    producer = _producer()
    store = CompanionArtifactStore(
        ttl=timedelta(minutes=5),
        clock=lambda: current[0],
        handle_factory=lambda: "ath-ticket",
        ticket_factory=lambda: f"apt-{'a' * 32}",
    )
    ticket = store.authorize_invocation(
        invocation_id="inv-ticket",
        correlation_id="corr-ticket",
        caller=_caller(),
        producer=producer,
        allowed_kinds=(ArtifactKind.DEMAND_SCENARIO,),
    )
    repeated = store.authorize_invocation(
        invocation_id="inv-ticket",
        correlation_id="corr-ticket",
        caller=_caller(),
        producer=producer,
        allowed_kinds=(ArtifactKind.DEMAND_SCENARIO,),
    )

    assert ticket == repeated
    assert ticket.secret.get_secret_value() not in repr(ticket)
    claimed = store.claim_publication_ticket(
        invocation_id="inv-ticket",
        correlation_id="corr-ticket",
        producer_identity_id=producer.identity_id,
    )
    assert claimed == ticket
    reference = store.publish_with_ticket(claimed.secret, _scenario(), producer=producer)
    assert store.publish_with_ticket(ticket.secret, _scenario(), producer=producer) == reference
    assert reference.invocation_id == "inv-ticket"
    assert reference.correlation_id == "corr-ticket"

    for invocation_id, correlation_id, producer_identity_id in (
        ("inv-other", "corr-ticket", producer.identity_id),
        ("inv-ticket", "corr-other", producer.identity_id),
        ("inv-ticket", "corr-ticket", "identity-other"),
    ):
        with pytest.raises(AgentIqOperationError) as denied_claim:
            store.claim_publication_ticket(
                invocation_id=invocation_id,
                correlation_id=correlation_id,
                producer_identity_id=producer_identity_id,
            )
        assert denied_claim.value.error.code is ErrorCode.AUTHORIZATION_DENIED

    with pytest.raises(AgentIqOperationError) as wrong_ticket:
        store.publish_with_ticket(f"apt-{'b' * 32}", _scenario(), producer=producer)
    assert wrong_ticket.value.error.code is ErrorCode.AUTHORIZATION_DENIED
    with pytest.raises(AgentIqOperationError) as wrong_producer:
        store.publish_with_ticket(
            ticket.secret,
            _scenario(),
            producer=_producer("other-agent"),
        )
    assert wrong_producer.value.error.code is ErrorCode.AUTHORIZATION_DENIED

    current[0] += timedelta(minutes=6)
    with pytest.raises(AgentIqOperationError) as expired_claim:
        store.claim_publication_ticket(
            invocation_id="inv-ticket",
            correlation_id="corr-ticket",
            producer_identity_id=producer.identity_id,
        )
    assert expired_claim.value.error.code is ErrorCode.AUTHORIZATION_DENIED
    with pytest.raises(AgentIqOperationError) as expired:
        store.publish_with_ticket(ticket.secret, _scenario(), producer=producer)
    assert expired.value.error.code is ErrorCode.AUTHORIZATION_DENIED


def test_revocation_removes_grant_and_published_artifacts() -> None:
    current = [NOW]
    store, producer = _authorized_store(current)
    request = ArtifactPublishRequest(
        invocation_id="inv-1",
        correlation_id="corr-1",
        artifact=_scenario(),
    )
    reference = store.publish(request, producer=producer)

    store.revoke_invocation("inv-1")

    with pytest.raises(AgentIqOperationError) as fetch_denied:
        store.fetch(ArtifactFetchRequest.from_reference(reference), caller=_caller())
    assert fetch_denied.value.error.code is ErrorCode.AUTHORIZATION_DENIED
    with pytest.raises(AgentIqOperationError) as publish_denied:
        store.publish(request, producer=producer)
    assert publish_denied.value.error.code is ErrorCode.AUTHORIZATION_DENIED
    with pytest.raises(AgentIqOperationError) as claim_denied:
        store.claim_publication_ticket(
            invocation_id="inv-1",
            correlation_id="corr-1",
            producer_identity_id=producer.identity_id,
        )
    assert claim_denied.value.error.code is ErrorCode.AUTHORIZATION_DENIED


def test_sap_change_proposal_requires_approval_and_real_field_changes() -> None:
    common = {
        "artifact_id": "sap-change-1",
        "operation": "update",
        "object_type": "production_order",
        "object_id": "order-1",
        "reason": "Align supply.",
        "risk": "medium",
        "proposed_at": NOW,
        "evidence_ids": ("evidence-1",),
    }
    with pytest.raises(ValidationError, match="True"):
        SapChangeProposal(
            **common,
            approval_required=False,
            changes=(SapFieldChange(field_path="quantity", before=100, after=125),),
        )
    with pytest.raises(ValidationError, match="modify"):
        SapChangeProposal(
            **common,
            changes=(SapFieldChange(field_path="quantity", before=100, after=100),),
        )
