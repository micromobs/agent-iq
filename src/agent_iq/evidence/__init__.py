"""Structured evidence retrieval, transport, and citation handling."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .a2a_shape import A2AEventShape, A2AEventShapeRecorder, A2AShapeSummary
    from .companion import (
        CompanionEvidenceStore,
        EvidenceRecordMapper,
        StructuredEvidenceDispatcher,
    )
    from .foundry_iq import FoundryIQEvidenceRetriever, RetrievedEvidenceSource
    from .normalize import (
        AgentEvidenceInput,
        EvidenceNormalizer,
        NormalizedAgentAnswer,
        NormalizedEvidence,
    )
    from .resolver import EvidenceResolver

__all__ = [
    "A2AEventShape",
    "A2AEventShapeRecorder",
    "A2AShapeSummary",
    "AgentEvidenceInput",
    "CompanionEvidenceStore",
    "EvidenceNormalizer",
    "EvidenceRecordMapper",
    "EvidenceResolver",
    "FoundryIQEvidenceRetriever",
    "NormalizedAgentAnswer",
    "NormalizedEvidence",
    "RetrievedEvidenceSource",
    "StructuredEvidenceDispatcher",
]


def __getattr__(name: str) -> Any:
    """Avoid importing live Azure adapters until the evidence path is used."""

    if name in {"A2AEventShape", "A2AEventShapeRecorder", "A2AShapeSummary"}:
        from .a2a_shape import A2AEventShape, A2AEventShapeRecorder, A2AShapeSummary

        return {
            "A2AEventShape": A2AEventShape,
            "A2AEventShapeRecorder": A2AEventShapeRecorder,
            "A2AShapeSummary": A2AShapeSummary,
        }[name]
    if name in {
        "CompanionEvidenceStore",
        "EvidenceRecordMapper",
        "StructuredEvidenceDispatcher",
    }:
        from .companion import (
            CompanionEvidenceStore,
            EvidenceRecordMapper,
            StructuredEvidenceDispatcher,
        )

        return {
            "CompanionEvidenceStore": CompanionEvidenceStore,
            "EvidenceRecordMapper": EvidenceRecordMapper,
            "StructuredEvidenceDispatcher": StructuredEvidenceDispatcher,
        }[name]
    if name in {"FoundryIQEvidenceRetriever", "RetrievedEvidenceSource"}:
        from .foundry_iq import FoundryIQEvidenceRetriever, RetrievedEvidenceSource

        return {
            "FoundryIQEvidenceRetriever": FoundryIQEvidenceRetriever,
            "RetrievedEvidenceSource": RetrievedEvidenceSource,
        }[name]
    if name in {
        "AgentEvidenceInput",
        "EvidenceNormalizer",
        "NormalizedAgentAnswer",
        "NormalizedEvidence",
    }:
        from .normalize import (
            AgentEvidenceInput,
            EvidenceNormalizer,
            NormalizedAgentAnswer,
            NormalizedEvidence,
        )

        return {
            "AgentEvidenceInput": AgentEvidenceInput,
            "EvidenceNormalizer": EvidenceNormalizer,
            "NormalizedAgentAnswer": NormalizedAgentAnswer,
            "NormalizedEvidence": NormalizedEvidence,
        }[name]
    if name == "EvidenceResolver":
        from .resolver import EvidenceResolver

        return EvidenceResolver
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
