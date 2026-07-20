"""Deterministic public intent decomposition for execution planning."""

from __future__ import annotations

import re
from dataclasses import dataclass

from agent_iq.contracts import SelectionDecision
from agent_iq.match.lexical import tokenize

_INDEPENDENT_SPLIT = re.compile(r"\band\b|\bplus\b|[;,]", re.IGNORECASE)
_SEQUENCE_SPLIT = re.compile(
    r"\bthen\b|\bnext\b|\bafter\s+that\b|\bfollowed\s+by\b",
    re.IGNORECASE,
)
_BEFORE_SPLIT = re.compile(r"\bbefore\b", re.IGNORECASE)
_AFTER_SPLIT = re.compile(r"\bafter\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class IntentWorkItem:
    work_item_id: str
    agent_id: str
    description: str
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _StagedClause:
    description: str
    stage: int


class IntentDecomposer:
    """Map selected public work items onto explicit independent or ordered stages."""

    def decompose(
        self,
        intent: str,
        selection: SelectionDecision,
    ) -> tuple[IntentWorkItem, ...]:
        if not selection.selected:
            return ()

        clauses = _staged_clauses(intent)
        staged: list[tuple[int, int, str, str, str]] = []
        for selection_index, reason in enumerate(selection.selected):
            stage = _best_stage(reason.work_item, clauses)
            staged.append(
                (
                    stage,
                    selection_index,
                    reason.work_item_id,
                    reason.agent_id,
                    reason.work_item,
                )
            )
        staged.sort(key=lambda item: (item[0], item[1]))

        work_items: list[IntentWorkItem] = []
        previous_stage_ids: tuple[str, ...] = ()
        for stage in sorted({item[0] for item in staged}):
            current = [item for item in staged if item[0] == stage]
            current_ids = tuple(item[2] for item in current)
            work_items.extend(
                IntentWorkItem(
                    work_item_id=work_item_id,
                    agent_id=agent_id,
                    description=description,
                    depends_on=previous_stage_ids,
                )
                for _, _, work_item_id, agent_id, description in current
            )
            previous_stage_ids = current_ids
        return tuple(work_items)


def _staged_clauses(intent: str) -> tuple[_StagedClause, ...]:
    text = intent.strip()
    stages = tuple(part.strip() for part in _SEQUENCE_SPLIT.split(text) if part.strip())
    if len(stages) >= 2:
        return _stage_parts(stages)

    before = _binary_sequence(text, _BEFORE_SPLIT, reverse=False)
    if before is not None:
        return _stage_parts(before)
    after = _binary_sequence(text, _AFTER_SPLIT, reverse=True)
    if after is not None:
        return _stage_parts(after)
    return _stage_parts((text,))


def _binary_sequence(
    text: str,
    pattern: re.Pattern[str],
    *,
    reverse: bool,
) -> tuple[str, str] | None:
    parts = tuple(part.strip() for part in pattern.split(text, maxsplit=1))
    if len(parts) != 2 or any(len(part.split()) < 2 for part in parts):
        return None
    return (parts[1], parts[0]) if reverse else (parts[0], parts[1])


def _stage_parts(stages: tuple[str, ...]) -> tuple[_StagedClause, ...]:
    clauses: list[_StagedClause] = []
    for stage, text in enumerate(stages):
        independent = tuple(
            part.strip()
            for part in _INDEPENDENT_SPLIT.split(text)
            if len(part.strip().split()) >= 2
        )
        if len(independent) < 2:
            independent = (text.strip(),)
        clauses.extend(_StagedClause(description=part, stage=stage) for part in independent)
    return tuple(clauses)


def _best_stage(work_item: str, clauses: tuple[_StagedClause, ...]) -> int:
    normalized = " ".join(tokenize(work_item))
    exact = [clause for clause in clauses if " ".join(tokenize(clause.description)) == normalized]
    if exact:
        return exact[0].stage

    work_tokens = set(tokenize(work_item))
    ranked = sorted(
        clauses,
        key=lambda clause: (
            -len(work_tokens & set(tokenize(clause.description))),
            clause.stage,
        ),
    )
    return ranked[0].stage if ranked else 0
