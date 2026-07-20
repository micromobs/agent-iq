"""Least-context selection from pre-authorized context bindings."""

from __future__ import annotations

from dataclasses import dataclass

from agent_iq.contracts import ContextInput
from agent_iq.match.lexical import tokenize


@dataclass(frozen=True, slots=True)
class ContextCandidate:
    context: ContextInput
    allowed_agent_ids: tuple[str, ...] = ()
    relevance_terms: tuple[str, ...] = ()
    priority: int = 100

    def __post_init__(self) -> None:
        if self.priority < 0:
            raise ValueError("Context priority cannot be negative.")
        if len(self.allowed_agent_ids) != len(set(self.allowed_agent_ids)):
            raise ValueError("Context agent bindings must be unique.")
        normalized = tuple(term.strip().casefold() for term in self.relevance_terms)
        if any(not term for term in normalized):
            raise ValueError("Context relevance terms cannot be empty.")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Context relevance terms must be unique.")


class ContextAssembler:
    """Select only approved context with an explicit agent or relevance binding."""

    def __init__(self, *, max_inputs_per_step: int = 4) -> None:
        if max_inputs_per_step < 0:
            raise ValueError("Maximum context inputs cannot be negative.")
        self.max_inputs_per_step = max_inputs_per_step

    def select(
        self,
        *,
        agent_id: str,
        work_item: str,
        candidates: tuple[ContextCandidate, ...],
    ) -> tuple[ContextInput, ...]:
        if self.max_inputs_per_step == 0:
            return ()
        work_tokens = set(tokenize(work_item))
        selected: list[ContextCandidate] = []
        seen: set[str] = set()
        for candidate in sorted(
            candidates,
            key=lambda item: (item.priority, item.context.context_id),
        ):
            context = candidate.context
            if not context.approved or context.context_id in seen:
                continue
            agent_bound = not candidate.allowed_agent_ids or agent_id in candidate.allowed_agent_ids
            relevance_bound = not candidate.relevance_terms or any(
                work_tokens & set(tokenize(term)) for term in candidate.relevance_terms
            )
            has_binding = bool(candidate.allowed_agent_ids or candidate.relevance_terms)
            if not has_binding or not agent_bound or not relevance_bound:
                continue
            selected.append(candidate)
            seen.add(context.context_id)
            if len(selected) >= self.max_inputs_per_step:
                break
        return tuple(candidate.context for candidate in selected)
