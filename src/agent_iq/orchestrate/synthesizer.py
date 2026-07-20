"""Deterministic grounded synthesis over normalized agent evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agent_iq.contracts import (
    CallerContext,
    EvidenceRecord,
    GroundingCoverage,
    SynthesisIssue,
    SynthesisIssueStatus,
    SynthesisResult,
    SynthesisStatus,
)
from agent_iq.evidence import AgentEvidenceInput, EvidenceNormalizer, EvidenceResolver


class AgentResultStatus(StrEnum):
    SUCCESS = "success"
    DECLINED = "declined"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class AgentSynthesisInput:
    agent_id: str
    invocation_id: str
    status: AgentResultStatus
    answer: str | None = None
    records: tuple[EvidenceRecord, ...] = ()
    public_message: str | None = None
    display_name: str | None = None
    conflict: bool = False


class GroundedSynthesizer:
    """Render only evidence-backed findings and make every gap visible."""

    def __init__(self, resolver: EvidenceResolver) -> None:
        self._resolver = resolver
        self._normalizer = EvidenceNormalizer(
            citation_url_factory=resolver.citation_url,
        )

    def synthesize(
        self,
        execution_id: str,
        inputs: tuple[AgentSynthesisInput, ...],
        *,
        caller: CallerContext,
    ) -> SynthesisResult:
        successful: list[AgentSynthesisInput] = []
        evidence_inputs: list[AgentEvidenceInput] = []
        for item in inputs:
            if item.status is not AgentResultStatus.SUCCESS:
                continue
            if not item.answer:
                raise ValueError("A successful agent result requires an answer.")
            successful.append(item)
            evidence_inputs.append(
                AgentEvidenceInput(
                    agent_id=item.agent_id,
                    invocation_id=item.invocation_id,
                    answer=item.answer,
                    records=item.records,
                    conflict=item.conflict,
                )
            )

        normalized = self._normalizer.normalize(execution_id, evidence_inputs)
        normalized_by_invocation = {answer.invocation_id: answer for answer in normalized.answers}
        findings: list[str] = []
        issues: list[SynthesisIssue] = []
        conflict_claim_ids: list[str] = []
        grounded_claims = 0

        for item in inputs:
            label = item.display_name or item.agent_id
            if item.status is AgentResultStatus.SUCCESS:
                normalized_answer = normalized_by_invocation[item.invocation_id]
                if not normalized_answer.evidence_ids:
                    issues.append(
                        SynthesisIssue(
                            agent_id=item.agent_id,
                            status=SynthesisIssueStatus.OMITTED_UNGROUNDED,
                            message=(
                                "The agent answer was omitted because no inline marker "
                                "resolved to structured evidence."
                            ),
                        )
                    )
                    continue
                grounded_claims += 1
                findings.append(f"### {label}\n{normalized_answer.answer}")
                if item.conflict and normalized_answer.claim_id is not None:
                    conflict_claim_ids.append(normalized_answer.claim_id)
                continue
            issues.append(_issue(item))

        total_claims = len(successful)
        coverage = GroundingCoverage(
            total_claims=total_claims,
            grounded_claims=grounded_claims,
            ratio=1.0 if total_claims == 0 else grounded_claims / total_claims,
        )
        rendered_answer = _render_answer(findings, issues, conflict_claim_ids)
        status = _result_status(grounded_claims, issues, conflict_claim_ids, coverage)
        result = SynthesisResult(
            execution_id=execution_id,
            status=status,
            answer=rendered_answer,
            evidence=normalized.bundle,
            coverage=coverage,
            issues=tuple(issues),
            conflict_claim_ids=tuple(conflict_claim_ids),
            sources_markdown=normalized.sources_markdown,
        )
        self._resolver.publish(result.evidence, caller=caller)
        return result


def _issue(item: AgentSynthesisInput) -> SynthesisIssue:
    if item.status is AgentResultStatus.DECLINED:
        status = SynthesisIssueStatus.DECLINED
        message = "The agent declined because it lacked sufficient evidence."
    elif item.status is AgentResultStatus.FAILED:
        status = SynthesisIssueStatus.FAILED
        message = "The agent failed to produce a result."
    elif item.status is AgentResultStatus.TIMED_OUT:
        status = SynthesisIssueStatus.TIMED_OUT
        message = "The agent exceeded its execution deadline."
    elif item.status is AgentResultStatus.CANCELLED:
        status = SynthesisIssueStatus.CANCELLED
        message = "The agent invocation was cancelled."
    else:
        raise ValueError("Successful agent results are not synthesis issues.")
    return SynthesisIssue(
        agent_id=item.agent_id,
        status=status,
        message=item.public_message or message,
    )


def _render_answer(
    findings: list[str],
    issues: list[SynthesisIssue],
    conflict_claim_ids: list[str],
) -> str:
    sections: list[str] = []
    if findings:
        sections.extend(["## Findings", "\n\n".join(findings)])
    else:
        sections.append(
            "I could not produce a grounded answer from the available agents and evidence."
        )
    if conflict_claim_ids:
        sections.extend(
            [
                "## Conflicts",
                "One or more grounded agent findings conflict and require human review.",
            ]
        )
    if issues:
        lines = [
            f"- **{issue.agent_id}** ({issue.status.value}): {issue.message}" for issue in issues
        ]
        sections.extend(["## Gaps and partial results", "\n".join(lines)])
    return "\n\n".join(sections)


def _result_status(
    grounded_claims: int,
    issues: list[SynthesisIssue],
    conflict_claim_ids: list[str],
    coverage: GroundingCoverage,
) -> SynthesisStatus:
    if grounded_claims == 0:
        return SynthesisStatus.DECLINED
    if issues or conflict_claim_ids or coverage.ratio != 1:
        return SynthesisStatus.PARTIAL
    return SynthesisStatus.COMPLETE
