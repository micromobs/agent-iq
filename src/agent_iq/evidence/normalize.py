"""Global citation normalization backed only by structured evidence."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from agent_iq.contracts import Citation, Claim, ErrorCode, EvidenceBundle, EvidenceRecord
from agent_iq.errors import operation_error

_REFERENCE_MARKER = re.compile(r"\[ref_id:([A-Za-z0-9._:-]+)\]", re.IGNORECASE)
_TITLE_MARKER_SEPARATORS = frozenset(" \t\r\n*`_'\"\u2018\u2019\u201c\u201d:;,.()-")
CitationUrlFactory = Callable[[str, str], str]


@dataclass(frozen=True, slots=True)
class AgentEvidenceInput:
    agent_id: str
    invocation_id: str
    answer: str
    records: tuple[EvidenceRecord, ...]
    conflict: bool = False


@dataclass(frozen=True, slots=True)
class NormalizedAgentAnswer:
    agent_id: str
    invocation_id: str
    answer: str
    evidence_ids: tuple[str, ...]
    unresolved_reference_ids: tuple[str, ...]
    claim_id: str | None


@dataclass(frozen=True, slots=True)
class NormalizedEvidence:
    answers: tuple[NormalizedAgentAnswer, ...]
    bundle: EvidenceBundle
    sources_markdown: str | None


class EvidenceNormalizer:
    """Merge per-agent references without trusting model-generated source metadata."""

    def __init__(self, *, citation_url_factory: CitationUrlFactory | None = None) -> None:
        self._citation_url_factory = citation_url_factory

    def normalize(
        self,
        execution_id: str,
        inputs: Sequence[AgentEvidenceInput],
    ) -> NormalizedEvidence:
        canonical_records: dict[str, EvidenceRecord] = {}
        local_keys: list[dict[str, str]] = []
        title_keys: list[tuple[tuple[str, str], ...]] = []

        for item in inputs:
            item_keys: dict[str, str] = {}
            record_title_keys: list[tuple[str, str]] = []
            for record in item.records:
                _validate_scope(item, record)
                key = _stable_source_key(record)
                existing_key = item_keys.get(record.local_reference_id or "")
                if record.local_reference_id is not None:
                    if existing_key is not None and existing_key != key:
                        raise operation_error(
                            ErrorCode.EVIDENCE_UNAVAILABLE,
                            "A local reference ID mapped to multiple structured sources.",
                            correlation_id=item.invocation_id,
                        )
                    item_keys[record.local_reference_id] = key
                record_title_keys.append((record.title, key))
                existing = canonical_records.get(key)
                if existing is None:
                    canonical_records[key] = _global_record(execution_id, key, record)
                else:
                    _validate_same_content(existing, record, item.invocation_id)
            local_keys.append(item_keys)
            title_keys.append(tuple(record_title_keys))

        citation_by_key: dict[str, Citation] = {}
        citation_order: list[str] = []
        normalized_answers: list[NormalizedAgentAnswer] = []
        claims: list[Claim] = []

        for item, item_keys, answer_title_keys in zip(inputs, local_keys, title_keys, strict=True):
            answer, evidence_ids, unresolved = _rewrite_answer(
                item.answer,
                execution_id=execution_id,
                item_keys=item_keys,
                item_title_keys=answer_title_keys,
                canonical_records=canonical_records,
                citation_by_key=citation_by_key,
                citation_order=citation_order,
                citation_url=self._citation_url,
            )
            claim_id: str | None = None
            if evidence_ids:
                claim_id = _claim_id(execution_id, item)
                claims.append(
                    Claim(
                        claim_id=claim_id,
                        text=answer,
                        evidence_ids=tuple(evidence_ids),
                        agent_ids=(item.agent_id,),
                        conflict=item.conflict,
                    )
                )
            normalized_answers.append(
                NormalizedAgentAnswer(
                    agent_id=item.agent_id,
                    invocation_id=item.invocation_id,
                    answer=answer,
                    evidence_ids=tuple(evidence_ids),
                    unresolved_reference_ids=tuple(unresolved),
                    claim_id=claim_id,
                )
            )

        records = tuple(canonical_records[key] for key in citation_order)
        citations = tuple(citation_by_key[key] for key in citation_order)
        bundle = EvidenceBundle(
            execution_id=execution_id,
            records=records,
            claims=tuple(claims),
            citations=citations,
        )
        return NormalizedEvidence(
            answers=tuple(normalized_answers),
            bundle=bundle,
            sources_markdown=_sources_markdown(bundle),
        )

    def _citation_url(self, execution_id: str, record: EvidenceRecord) -> str | None:
        if self._citation_url_factory is not None:
            return self._citation_url_factory(execution_id, record.evidence_id)
        return str(record.locator.uri) if record.locator.uri is not None else None


def _rewrite_answer(
    answer: str,
    *,
    execution_id: str,
    item_keys: dict[str, str],
    item_title_keys: tuple[tuple[str, str], ...],
    canonical_records: dict[str, EvidenceRecord],
    citation_by_key: dict[str, Citation],
    citation_order: list[str],
    citation_url: Callable[[str, EvidenceRecord], str | None],
) -> tuple[str, list[str], list[str]]:
    evidence_ids: list[str] = []
    unresolved: list[str] = []

    def rewrite(match: re.Match[str]) -> str:
        local_id = match.group(1)
        key = _adjacent_title_key(answer[: match.start()], item_title_keys)
        if key is None:
            key = item_keys.get(local_id)
        if key is None:
            if local_id not in unresolved:
                unresolved.append(local_id)
            return match.group(0)
        citation = citation_by_key.get(key)
        if citation is None:
            record = canonical_records[key]
            citation = Citation(
                number=len(citation_order) + 1,
                evidence_id=record.evidence_id,
                label=record.title,
                url=citation_url(execution_id, record),
            )
            citation_by_key[key] = citation
            citation_order.append(key)
        if citation.evidence_id not in evidence_ids:
            evidence_ids.append(citation.evidence_id)
        return f"[{citation.number}]"

    return _REFERENCE_MARKER.sub(rewrite, answer), evidence_ids, unresolved


def _adjacent_title_key(
    prefix: str,
    item_title_keys: tuple[tuple[str, str], ...],
) -> str | None:
    candidates: list[tuple[int, str]] = []
    for title, key in item_title_keys:
        matches = tuple(re.finditer(re.escape(title), prefix, re.IGNORECASE))
        if not matches:
            continue
        suffix = prefix[matches[-1].end() :]
        if len(suffix) <= 32 and all(char in _TITLE_MARKER_SEPARATORS for char in suffix):
            candidates.append((len(title), key))
    if not candidates:
        return None
    longest = max(length for length, _key in candidates)
    keys = {key for length, key in candidates if length == longest}
    return next(iter(keys)) if len(keys) == 1 else None


def _validate_scope(item: AgentEvidenceInput, record: EvidenceRecord) -> None:
    if record.agent_id != item.agent_id or record.invocation_id != item.invocation_id:
        raise operation_error(
            ErrorCode.EVIDENCE_UNAVAILABLE,
            "Structured evidence crossed its agent or invocation boundary.",
            correlation_id=item.invocation_id,
        )


def _stable_source_key(record: EvidenceRecord) -> str:
    knowledge_source = record.knowledge_source or ""
    if record.locator.document_key:
        return f"document:{knowledge_source}:{record.locator.document_key}"
    if record.locator.uri is not None:
        return f"uri:{str(record.locator.uri).casefold()}"
    return f"source:{knowledge_source}:{record.source_id}"


def _global_record(execution_id: str, key: str, record: EvidenceRecord) -> EvidenceRecord:
    digest = hashlib.sha256(f"{execution_id}\0{key}".encode()).hexdigest()
    return record.model_copy(
        update={
            "evidence_id": f"evidence-{digest[:24]}",
            "local_reference_id": None,
        }
    )


def _validate_same_content(
    existing: EvidenceRecord,
    candidate: EvidenceRecord,
    correlation_id: str,
) -> None:
    if (
        existing.content_hash is not None
        and candidate.content_hash is not None
        and existing.content_hash != candidate.content_hash
    ):
        raise operation_error(
            ErrorCode.EVIDENCE_UNAVAILABLE,
            "A stable source identity returned conflicting content hashes.",
            correlation_id=correlation_id,
        )


def _claim_id(execution_id: str, item: AgentEvidenceInput) -> str:
    digest = hashlib.sha256(
        f"{execution_id}\0{item.agent_id}\0{item.invocation_id}".encode()
    ).hexdigest()
    return f"claim-{digest[:24]}"


def _sources_markdown(bundle: EvidenceBundle) -> str | None:
    if not bundle.citations:
        return None
    records = {record.evidence_id: record for record in bundle.records}
    lines = ["**Sources**"]
    for citation in bundle.citations:
        record = records[citation.evidence_id]
        label = _escape_markdown(citation.label)
        if citation.url is not None:
            label = f"[{label}]({citation.url})"
        lines.append(f"{citation.number}. {label} - `{record.source_id}`")
    return "\n".join(lines)


def _escape_markdown(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
