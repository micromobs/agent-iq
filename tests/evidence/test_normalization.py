from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_iq.contracts import CallerContext, ErrorCode, EvidenceLocator, EvidenceRecord
from agent_iq.errors import AgentIqOperationError
from agent_iq.evidence import AgentEvidenceInput, EvidenceNormalizer, EvidenceResolver

NOW = datetime(2026, 7, 10, 20, 0, tzinfo=UTC)


def _record(
    *,
    evidence_id: str,
    invocation_id: str,
    agent_id: str,
    source_id: str,
    local_reference_id: str,
    title: str,
    document_key: str | None = None,
    content: str = "a",
) -> EvidenceRecord:
    digest = {
        "a": "sha256:" + ("a" * 64),
        "b": "sha256:" + ("b" * 64),
    }[content]
    return EvidenceRecord(
        evidence_id=evidence_id,
        invocation_id=invocation_id,
        agent_id=agent_id,
        source_id=source_id,
        title=title,
        locator=EvidenceLocator(document_key=document_key or source_id),
        excerpt=f"Excerpt {content}",
        local_reference_id=local_reference_id,
        knowledge_source="kb-1",
        retrieved_at=NOW,
        content_hash=digest,
    )


def _caller(subject: str = "caller-1", tenant: str = "tenant-1") -> CallerContext:
    return CallerContext(subject_id=subject, tenant_id=tenant)


def test_normalizer_deduplicates_stable_sources_and_rewrites_only_backed_markers() -> None:
    resolver = EvidenceResolver("https://agent-iq.example")
    normalizer = EvidenceNormalizer(citation_url_factory=resolver.citation_url)
    shared_a = _record(
        evidence_id="local-a",
        invocation_id="inv-a",
        agent_id="agent-a",
        source_id="shared",
        local_reference_id="0",
        title="Shared title",
    )
    shared_b = _record(
        evidence_id="local-b",
        invocation_id="inv-b",
        agent_id="agent-b",
        source_id="shared",
        local_reference_id="7",
        title="Different display title",
    )
    distinct_same_title = _record(
        evidence_id="local-c",
        invocation_id="inv-b",
        agent_id="agent-b",
        source_id="distinct",
        local_reference_id="8",
        title="Shared title",
    )

    normalized = normalizer.normalize(
        "exec-1",
        (
            AgentEvidenceInput(
                agent_id="agent-a",
                invocation_id="inv-a",
                answer="A [ref_id:0], unknown [ref_id:9], raw ref_id:0 and [1].",
                records=(shared_a,),
            ),
            AgentEvidenceInput(
                agent_id="agent-b",
                invocation_id="inv-b",
                answer="B [ref_id:7] and [ref_id:8].",
                records=(shared_b, distinct_same_title),
            ),
        ),
    )

    assert normalized.answers[0].answer == ("A [1], unknown [ref_id:9], raw ref_id:0 and [1].")
    assert normalized.answers[0].unresolved_reference_ids == ("9",)
    assert normalized.answers[1].answer == "B [1] and [2]."
    assert len(normalized.bundle.records) == 2
    assert [citation.number for citation in normalized.bundle.citations] == [1, 2]
    assert str(normalized.bundle.citations[0].url) == (
        "https://agent-iq.example/v1/executions/exec-1/evidence/"
        f"{normalized.bundle.records[0].evidence_id}"
    )
    assert len(normalized.bundle.claims) == 2
    assert normalized.sources_markdown is not None
    assert "`shared`" in normalized.sources_markdown
    assert "`distinct`" in normalized.sources_markdown


def test_normalizer_rejects_scope_crossing_and_conflicting_content() -> None:
    normalizer = EvidenceNormalizer()
    crossed = _record(
        evidence_id="crossed",
        invocation_id="inv-other",
        agent_id="agent-a",
        source_id="doc-1",
        local_reference_id="0",
        title="Doc",
    )
    with pytest.raises(AgentIqOperationError) as scope_error:
        normalizer.normalize(
            "exec-1",
            (
                AgentEvidenceInput(
                    agent_id="agent-a",
                    invocation_id="inv-a",
                    answer="[ref_id:0]",
                    records=(crossed,),
                ),
            ),
        )
    assert scope_error.value.error.code is ErrorCode.EVIDENCE_UNAVAILABLE

    record_a = crossed.model_copy(update={"evidence_id": "a", "invocation_id": "inv-a"})
    record_b = crossed.model_copy(
        update={
            "evidence_id": "b",
            "invocation_id": "inv-b",
            "agent_id": "agent-b",
            "local_reference_id": "1",
            "content_hash": "sha256:" + ("b" * 64),
        }
    )
    with pytest.raises(AgentIqOperationError) as hash_error:
        normalizer.normalize(
            "exec-1",
            (
                AgentEvidenceInput("agent-a", "inv-a", "[ref_id:0]", (record_a,)),
                AgentEvidenceInput("agent-b", "inv-b", "[ref_id:1]", (record_b,)),
            ),
        )
    assert hash_error.value.error.code is ErrorCode.EVIDENCE_UNAVAILABLE


def test_normalizer_uses_adjacent_structured_title_to_resolve_restarted_markers() -> None:
    brightwash = _record(
        evidence_id="brightwash",
        invocation_id="inv-a",
        agent_id="agent-a",
        source_id="brightwash",
        local_reference_id="1",
        title="BrightWash Cold active system",
    )
    clearwave = _record(
        evidence_id="clearwave",
        invocation_id="inv-a",
        agent_id="agent-a",
        source_id="clearwave",
        local_reference_id="0",
        title="ClearWave Cold Clean active system",
        content="b",
    )

    normalized = EvidenceNormalizer().normalize(
        "exec-1",
        (
            AgentEvidenceInput(
                "agent-a",
                "inv-a",
                (
                    "BrightWash uses four enzymes. Source: **BrightWash Cold active system "
                    "[ref_id:0]**. ClearWave adds pectate lyase. Source: "
                    "**ClearWave Cold Clean active system [ref_id:0]**."
                ),
                (clearwave, brightwash),
            ),
        ),
    )

    assert normalized.answers[0].answer.endswith(
        "Source: **ClearWave Cold Clean active system [2]**."
    )
    assert "BrightWash Cold active system [1]" in normalized.answers[0].answer
    assert [citation.label for citation in normalized.bundle.citations] == [
        "BrightWash Cold active system",
        "ClearWave Cold Clean active system",
    ]


def test_resolver_enforces_caller_tenant_record_and_expiry() -> None:
    current = [NOW]
    resolver = EvidenceResolver(
        "https://agent-iq.example",
        ttl=timedelta(minutes=5),
        clock=lambda: current[0],
    )
    normalized = EvidenceNormalizer(citation_url_factory=resolver.citation_url).normalize(
        "exec-1",
        (
            AgentEvidenceInput(
                "agent-a",
                "inv-a",
                "[ref_id:0]",
                (
                    _record(
                        evidence_id="a",
                        invocation_id="inv-a",
                        agent_id="agent-a",
                        source_id="doc-1",
                        local_reference_id="0",
                        title="Doc",
                    ),
                ),
            ),
        ),
    )
    resolver.publish(normalized.bundle, caller=_caller())
    resolver.publish(normalized.bundle, caller=_caller())
    evidence_id = normalized.bundle.records[0].evidence_id

    assert resolver.resolve("exec-1", evidence_id, caller=_caller()).source_id == "doc-1"
    for caller in (_caller("caller-2"), _caller(tenant="tenant-2")):
        with pytest.raises(AgentIqOperationError) as denied:
            resolver.resolve("exec-1", evidence_id, caller=caller)
        assert denied.value.error.code is ErrorCode.AUTHORIZATION_DENIED
    with pytest.raises(AgentIqOperationError) as missing:
        resolver.resolve("exec-1", "evidence-missing", caller=_caller())
    assert missing.value.error.code is ErrorCode.EVIDENCE_UNAVAILABLE

    current[0] += timedelta(minutes=6)
    with pytest.raises(AgentIqOperationError) as expired:
        resolver.resolve("exec-1", evidence_id, caller=_caller())
    assert expired.value.error.code is ErrorCode.EVIDENCE_UNAVAILABLE
