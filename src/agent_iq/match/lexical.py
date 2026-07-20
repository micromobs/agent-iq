"""Deterministic Okapi BM25 matching over canonical agent text."""

from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    _TOKEN_RE.findall(
        """
    a an the of to for in on at by and or but with my me i you your our their his her its
    is are am be been being was were do does did done can could would should will shall may
    might must this that these those it as from into out up down over under again then than
    so if not no yes please help need want have has had get got about we they he she them us
    """
    )
)


def tokenize(text: str) -> list[str]:
    """Tokenize text consistently for lexical and offline semantic matching."""

    return [
        token
        for token in _TOKEN_RE.findall(text.lower())
        if len(token) > 1 and token not in _STOPWORDS
    ]


class BM25Index:
    """Okapi BM25 over a fixed set of documents keyed by canonical agent ID."""

    def __init__(self, documents: dict[str, str], k1: float = 1.5, b: float = 0.75):
        self.ids = tuple(documents)
        self.k1 = k1
        self.b = b
        self._tokens = {agent_id: tokenize(documents[agent_id]) for agent_id in self.ids}
        self._term_frequency = {agent_id: Counter(self._tokens[agent_id]) for agent_id in self.ids}
        self._length = {agent_id: len(self._tokens[agent_id]) for agent_id in self.ids}
        count = len(self.ids)
        self._average_length = sum(self._length.values()) / count if count else 0.0

        document_frequency: dict[str, int] = {}
        for agent_id in self.ids:
            for term in self._term_frequency[agent_id]:
                document_frequency[term] = document_frequency.get(term, 0) + 1
        self._inverse_document_frequency = {
            term: math.log(1 + (count - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }

    def scores(self, query: str) -> dict[str, float]:
        query_terms = tokenize(query)
        scores: dict[str, float] = {}
        for agent_id in self.ids:
            term_frequency = self._term_frequency[agent_id]
            document_length = self._length[agent_id] or 1
            score = 0.0
            for term in query_terms:
                frequency = term_frequency.get(term, 0)
                if not frequency:
                    continue
                inverse_frequency = self._inverse_document_frequency.get(term, 0.0)
                denominator = frequency + self.k1 * (
                    1 - self.b + self.b * document_length / (self._average_length or 1)
                )
                score += inverse_frequency * (frequency * (self.k1 + 1)) / denominator
            scores[agent_id] = score
        return scores
