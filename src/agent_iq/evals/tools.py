"""Deterministic hero tool-selection and argument-schema evaluation."""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError, field_validator

from agent_iq.catalog.assets import (
    RuntimeAgentDefinition,
    RuntimeCatalogAsset,
    RuntimeToolDefinition,
)
from agent_iq.contracts.base import ContractModel, Identifier, NonEmptyText
from agent_iq.match.lexical import BM25Index
from agent_iq.runtime.hero_tools import validate_tool_arguments

DEFAULT_TOOL_CASES_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "consumer" / "tool_cases.jsonl"
)
_OPAQUE_TOOL_ID = "knowledge_base_retrieve"


class ToolCallCase(ContractModel):
    case_id: Identifier
    agent_id: Identifier
    intent: NonEmptyText
    expected_tool_id: Identifier
    arguments: dict[Identifier, NonEmptyText] = Field(min_length=1)

    @field_validator("arguments")
    @classmethod
    def validate_argument_ids(cls, values: dict[str, str]) -> dict[str, str]:
        normalized = [key.casefold() for key in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Tool-call argument IDs must be unique.")
        return values


@dataclass(frozen=True, slots=True)
class ToolCallCaseResult:
    case_id: str
    agent_id: str
    expected_tool_id: str
    selected_tool_id: str | None
    ranked_tool_ids: tuple[str, ...]
    exact_selection: bool
    arguments_valid: bool
    opaque_catchall_selected: bool
    duration_ms: float


@dataclass(frozen=True, slots=True)
class ToolCallEvaluationResult:
    cases: tuple[ToolCallCaseResult, ...]
    exact_selection_accuracy: float
    argument_validity_accuracy: float
    opaque_catchall_selections: int
    p95_latency_ms: float

    @property
    def passes(self) -> bool:
        return (
            self.exact_selection_accuracy == 1.0
            and self.argument_validity_accuracy == 1.0
            and self.opaque_catchall_selections == 0
        )


def load_tool_call_cases(
    path: Path = DEFAULT_TOOL_CASES_PATH,
) -> tuple[ToolCallCase, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"Cannot read tool-call cases at {path}: {exc}") from exc
    cases = tuple(ToolCallCase.model_validate_json(line) for line in lines if line.strip())
    if not cases:
        raise ValueError("At least one tool-call case is required.")
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Tool-call case IDs must be unique.")
    return cases


def evaluate_tool_calls(
    runtime: RuntimeCatalogAsset,
    cases: tuple[ToolCallCase, ...],
) -> ToolCallEvaluationResult:
    definitions = {definition.agent_id: definition for definition in runtime.agents}
    results: list[ToolCallCaseResult] = []
    for case in cases:
        definition = definitions.get(case.agent_id)
        if definition is None:
            raise ValueError(f"Unknown tool-call agent {case.agent_id!r}.")
        specifications = {item.descriptor.tool_id: item for item in definition.tools}
        if case.expected_tool_id not in specifications:
            raise ValueError(
                f"Unknown expected tool {case.expected_tool_id!r} for {case.agent_id!r}."
            )
        started = time.perf_counter()
        ranked = _rank_tools(definition, case.intent)
        selected = ranked[0] if ranked else None
        exact = selected == case.expected_tool_id
        arguments_valid = False
        if selected is not None:
            try:
                validate_tool_arguments(specifications[selected], dict(case.arguments))
            except ValidationError:
                arguments_valid = False
            else:
                arguments_valid = exact
        results.append(
            ToolCallCaseResult(
                case_id=case.case_id,
                agent_id=case.agent_id,
                expected_tool_id=case.expected_tool_id,
                selected_tool_id=selected,
                ranked_tool_ids=ranked,
                exact_selection=exact,
                arguments_valid=arguments_valid,
                opaque_catchall_selected=selected == _OPAQUE_TOOL_ID,
                duration_ms=(time.perf_counter() - started) * 1_000,
            )
        )
    return ToolCallEvaluationResult(
        cases=tuple(results),
        exact_selection_accuracy=_mean(
            1.0 if result.exact_selection else 0.0 for result in results
        ),
        argument_validity_accuracy=_mean(
            1.0 if result.arguments_valid else 0.0 for result in results
        ),
        opaque_catchall_selections=sum(result.opaque_catchall_selected for result in results),
        p95_latency_ms=_percentile([result.duration_ms for result in results], 0.95),
    )


def build_tool_call_payload(
    result: ToolCallEvaluationResult,
    *,
    tool_count: int,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    timestamp = (generated_at or datetime.now(UTC)).astimezone(UTC)
    case_results = [
        {
            "caseId": case.case_id,
            "agentId": case.agent_id,
            "status": (
                "pass"
                if case.exact_selection
                and case.arguments_valid
                and not case.opaque_catchall_selected
                else "fail"
            ),
            "expectedToolId": case.expected_tool_id,
            "selectedToolId": case.selected_tool_id,
            "rankedToolIds": list(case.ranked_tool_ids),
            "exactSelection": case.exact_selection,
            "argumentsValid": case.arguments_valid,
            "opaqueCatchallSelected": case.opaque_catchall_selected,
            "durationMs": round(case.duration_ms, 3),
        }
        for case in result.cases
    ]
    return {
        "schemaVersion": "1.0",
        "suiteId": "consumer-hero-tool-calls",
        "generatedAt": timestamp.isoformat().replace("+00:00", "Z"),
        "selectorBackend": "deterministic-bm25",
        "argumentSource": "labelled-evaluation-fixture",
        "tools": tool_count,
        "cases": len(result.cases),
        "exactSelectionAccuracy": result.exact_selection_accuracy,
        "argumentValidityAccuracy": result.argument_validity_accuracy,
        "opaqueCatchallSelections": result.opaque_catchall_selections,
        "p95LatencyMs": round(result.p95_latency_ms, 3),
        "passes": result.passes,
        "gates": {
            "exactSelectionAccuracy": {
                "actual": result.exact_selection_accuracy,
                "required": 1.0,
                "passes": result.exact_selection_accuracy == 1.0,
            },
            "argumentValidityAccuracy": {
                "actual": result.argument_validity_accuracy,
                "required": 1.0,
                "passes": result.argument_validity_accuracy == 1.0,
            },
            "opaqueCatchallSelections": {
                "actual": result.opaque_catchall_selections,
                "required": 0,
                "passes": result.opaque_catchall_selections == 0,
            },
        },
        "caseResults": case_results,
        "failures": [case for case in case_results if case["status"] == "fail"],
    }


def write_tool_call_report(payload: dict[str, Any], output_dir: Path) -> Path:
    if (
        payload.get("schemaVersion") != "1.0"
        or payload.get("suiteId") != "consumer-hero-tool-calls"
    ):
        raise ValueError("Unsupported tool-call evaluation payload.")
    output_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = output_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_id = re.sub(r"[^A-Za-z0-9._-]+", "-", str(payload["generatedAt"])).strip("-")
    if not run_id:
        raise ValueError("Tool-call evaluation run ID cannot be empty.")
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    run_path = runs_dir / f"{run_id}.json"
    _atomic_write(run_path, text)
    _atomic_write(output_dir / "latest.json", text)
    return run_path


def _rank_tools(
    definition: RuntimeAgentDefinition,
    intent: str,
) -> tuple[str, ...]:
    if not definition.tools:
        return ()
    documents = {item.descriptor.tool_id: _tool_document(item) for item in definition.tools}
    scores = BM25Index(documents).scores(intent)
    ranked = sorted(documents, key=lambda tool_id: (-scores[tool_id], tool_id))
    if not ranked or scores[ranked[0]] <= 0:
        return ()
    return tuple(ranked)


def _tool_document(specification: RuntimeToolDefinition) -> str:
    descriptor = specification.descriptor
    fields = [
        descriptor.tool_id.replace("_", " "),
        descriptor.name,
        descriptor.description,
        " ".join(
            f"{parameter.parameter_id.replace('_', ' ')} {parameter.description}"
            for parameter in descriptor.parameters
        ),
        specification.query_template,
        specification.sap_resource.value if specification.sap_resource is not None else "",
    ]
    return " ".join(fields)


def _mean(values: Iterable[float]) -> float:
    numbers = list(values)
    return sum(numbers) / len(numbers) if numbers else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)
