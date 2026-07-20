"""Generate the versioned language-neutral JSON Schema publication set."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .agent import AgentDescriptor
from .agui import AgUiProjection
from .api import OrchestrationCommand, OrchestrationResult
from .artifacts import (
    ArtifactEnvelope,
    DemandScenario,
    RegulatoryDecision,
    SapChangeProposal,
    SupplyCommitment,
)
from .capabilities import CapabilityLedger
from .errors import AgentIqError
from .evaluation import (
    FoundryEvaluationSuite,
    FoundryNativeEvaluationCertificate,
    RubricDefinition,
    RubricEvaluationReport,
    RubricReplayBundle,
)
from .events import EventEnvelope
from .evidence import EvidenceBundle, EvidenceEnvelope
from .execution import ExecutionSummary
from .match import MatchRequest, SelectionDecision
from .memory import (
    EpisodeReceipt,
    ExecutionMemoryRecord,
    FeedbackMemoryRecord,
    MemoryPreparationRequest,
    MemorySnapshot,
    SquadDecisionEnvelope,
)
from .plan import ExecutionPlan
from .presentation import AgentSearchPresentation, OrchestrationPresentation
from .sap import SapReadResult
from .synthesis import SynthesisResult
from .task import AgentTask
from .tools import ToolDescriptor
from .vacation import VacationBalance
from .vpn import VpnRecoveryGuidance

SCHEMA_BASE_URI = "https://schemas.agent-iq.dev/v1"
SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "ag-ui-projection.schema.json": AgUiProjection,
    "agent.schema.json": AgentDescriptor,
    "agent-task.schema.json": AgentTask,
    "artifact-envelope.schema.json": ArtifactEnvelope,
    "foundry-capability-ledger.schema.json": CapabilityLedger,
    "foundry-evaluation-certificate.schema.json": FoundryNativeEvaluationCertificate,
    "foundry-evaluation-suite.schema.json": FoundryEvaluationSuite,
    "demand-scenario.schema.json": DemandScenario,
    "error.schema.json": AgentIqError,
    "evaluation-replay.schema.json": RubricReplayBundle,
    "evaluation-rubric.schema.json": RubricDefinition,
    "evaluation-scorecard.schema.json": RubricEvaluationReport,
    "event-envelope.schema.json": EventEnvelope,
    "execution-summary.schema.json": ExecutionSummary,
    "evidence-bundle.schema.json": EvidenceBundle,
    "evidence-envelope.schema.json": EvidenceEnvelope,
    "execution-plan.schema.json": ExecutionPlan,
    "match-request.schema.json": MatchRequest,
    "memory-episode-receipt.schema.json": EpisodeReceipt,
    "memory-execution-record.schema.json": ExecutionMemoryRecord,
    "memory-feedback-record.schema.json": FeedbackMemoryRecord,
    "memory-preparation-request.schema.json": MemoryPreparationRequest,
    "memory-snapshot.schema.json": MemorySnapshot,
    "orchestration-command.schema.json": OrchestrationCommand,
    "orchestration-presentation.schema.json": OrchestrationPresentation,
    "orchestration-result.schema.json": OrchestrationResult,
    "regulatory-decision.schema.json": RegulatoryDecision,
    "sap-change-proposal.schema.json": SapChangeProposal,
    "sap-read-result.schema.json": SapReadResult,
    "selection-decision.schema.json": SelectionDecision,
    "squad-decision-envelope.schema.json": SquadDecisionEnvelope,
    "search-presentation.schema.json": AgentSearchPresentation,
    "supply-commitment.schema.json": SupplyCommitment,
    "synthesis-result.schema.json": SynthesisResult,
    "tool-descriptor.schema.json": ToolDescriptor,
    "vacation-balance.schema.json": VacationBalance,
    "vpn-recovery-guidance.schema.json": VpnRecoveryGuidance,
}


def generate_contract_schemas() -> dict[str, dict[str, Any]]:
    """Return deterministic Draft 2020-12 schemas keyed by publication filename."""

    generated: dict[str, dict[str, Any]] = {}
    for filename, model in SCHEMA_MODELS.items():
        model_schema = model.model_json_schema(by_alias=True, mode="serialization")
        generated[filename] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"{SCHEMA_BASE_URI}/{filename}",
            **model_schema,
        }
    return generated
