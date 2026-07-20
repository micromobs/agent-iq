from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_iq.contracts import (
    CallerContext,
    ErrorCode,
    EvidenceLocator,
    EvidenceRecord,
    SynthesisIssueStatus,
    SynthesisStatus,
)
from agent_iq.errors import AgentIqOperationError
from agent_iq.evidence import EvidenceResolver
from agent_iq.orchestrate import AgentResultStatus, AgentSynthesisInput, GroundedSynthesizer

NOW = datetime(2026, 7, 10, 20, 0, tzinfo=UTC)


def _record(
    invocation_id: str,
    agent_id: str,
    source_id: str,
    local_reference_id: str,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=f"local-{agent_id}",
        invocation_id=invocation_id,
        agent_id=agent_id,
        source_id=source_id,
        title=f"Source {source_id}",
        locator=EvidenceLocator(document_key=source_id),
        excerpt="Grounding passage.",
        local_reference_id=local_reference_id,
        knowledge_source="kb-1",
        retrieved_at=NOW,
        content_hash="sha256:" + ("a" * 64),
    )


def _caller() -> CallerContext:
    return CallerContext(subject_id="caller-1", tenant_id="tenant-1")


def test_synthesizer_returns_grounded_partial_result_and_omits_ungrounded_prose() -> None:
    resolver = EvidenceResolver("https://agent-iq.example")
    synthesizer = GroundedSynthesizer(resolver)
    result = synthesizer.synthesize(
        "exec-1",
        (
            AgentSynthesisInput(
                agent_id="agent-grounded",
                invocation_id="inv-1",
                status=AgentResultStatus.SUCCESS,
                answer="Grounded finding [ref_id:0].",
                records=(_record("inv-1", "agent-grounded", "doc-1", "0"),),
                display_name="Grounded Agent",
            ),
            AgentSynthesisInput(
                agent_id="agent-failed",
                invocation_id="inv-2",
                status=AgentResultStatus.FAILED,
                public_message="The approved backend was unavailable.",
            ),
            AgentSynthesisInput(
                agent_id="agent-ungrounded",
                invocation_id="inv-3",
                status=AgentResultStatus.SUCCESS,
                answer="This unsupported answer must not be rendered.",
            ),
        ),
        caller=_caller(),
    )

    assert result.status is SynthesisStatus.PARTIAL
    assert "Grounded finding [1]." in result.answer
    assert "unsupported answer" not in result.answer
    assert "The approved backend was unavailable." in result.answer
    assert result.coverage.total_claims == 2
    assert result.coverage.grounded_claims == 1
    assert result.coverage.ratio == 0.5
    assert [issue.status for issue in result.issues] == [
        SynthesisIssueStatus.FAILED,
        SynthesisIssueStatus.OMITTED_UNGROUNDED,
    ]
    assert result.sources_markdown is not None

    record = result.evidence.records[0]
    assert (
        resolver.resolve(
            "exec-1",
            record.evidence_id,
            caller=_caller(),
        )
        == record
    )
    with pytest.raises(AgentIqOperationError) as denied:
        resolver.resolve(
            "exec-1",
            record.evidence_id,
            caller=CallerContext(subject_id="other", tenant_id="tenant-1"),
        )
    assert denied.value.error.code is ErrorCode.AUTHORIZATION_DENIED


def test_synthesizer_marks_grounded_conflicts_as_partial() -> None:
    result = GroundedSynthesizer(EvidenceResolver("https://agent-iq.example")).synthesize(
        "exec-conflict",
        (
            AgentSynthesisInput(
                agent_id="agent-a",
                invocation_id="inv-a",
                status=AgentResultStatus.SUCCESS,
                answer="Conflicting grounded finding [ref_id:0].",
                records=(_record("inv-a", "agent-a", "doc-a", "0"),),
                conflict=True,
            ),
        ),
        caller=_caller(),
    )

    assert result.status is SynthesisStatus.PARTIAL
    assert "## Conflicts" in result.answer
    assert len(result.conflict_claim_ids) == 1
    assert result.evidence.claims[0].conflict is True


def test_synthesizer_declines_when_no_grounded_result_exists() -> None:
    result = GroundedSynthesizer(EvidenceResolver("https://agent-iq.example")).synthesize(
        "exec-decline",
        (
            AgentSynthesisInput(
                agent_id="agent-a",
                invocation_id="inv-a",
                status=AgentResultStatus.DECLINED,
            ),
        ),
        caller=_caller(),
    )

    assert result.status is SynthesisStatus.DECLINED
    assert result.coverage.total_claims == 0
    assert result.coverage.ratio == 1
    assert result.sources_markdown is None
    assert result.evidence.records == ()
    assert "could not produce a grounded answer" in result.answer
