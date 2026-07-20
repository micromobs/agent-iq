"""Deterministic transcript-level quality gates for the Cowork experience."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_iq.contracts.base import (
    ContractModel,
    Identifier,
    NonEmptyText,
    ShortText,
    require_unique_text,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CASES_PATH = REPOSITORY_ROOT / "evals" / "cowork_ux_cases.jsonl"
DEFAULT_BASELINE_PATH = REPOSITORY_ROOT / "evals" / "cowork_ux_baseline.jsonl"
DEFAULT_REPORT_ROOT = REPOSITORY_ROOT / "reports" / "evals" / "cowork-ux"
_WORD = re.compile(r"\b[\w'-]+\b")
_ORCHESTRATION_TOOLS = frozenset({"orchestrate", "run_single_agent_demo"})
_PROGRESS_JARGON = (
    "request.accepted",
    "catalog.searched",
    "candidates.ranked",
    "agents.selected",
    "execution.completed",
    "spilled",
    "glob",
    ".json",
    "mcp result file",
    "locate spilled",
)
_TRANSPORT_TERMS = (
    "northbound",
    "southbound",
    "native a2a",
    "json-rpc",
    "over mcp",
)
_MEMORY_PROMISES = (
    "will remember",
    "remember your preference",
    "saved your preference",
)


class CoworkUxCase(ContractModel):
    case_id: Identifier
    intent: NonEmptyText
    expected_agent_ids: tuple[Identifier, ...] = Field(min_length=1)
    required_answer_terms: tuple[ShortText, ...] = ()
    forbidden_answer_terms: tuple[ShortText, ...] = ()
    expected_source_ids: tuple[Identifier, ...] = Field(min_length=1)
    required_progress_fragments: tuple[ShortText, ...] = (
        "Matching eligible specialists",
        "Selected ",
        "Retrieving approved evidence",
        "Building the cited answer",
        "Completed",
    )
    max_payload_bytes: int = Field(default=16 * 1024, ge=1)
    max_first_progress_ms: int = Field(default=1_000, ge=1)
    max_completion_ms: int = Field(default=35_000, ge=1)
    max_answer_words: int = Field(default=220, ge=1)

    @field_validator(
        "expected_agent_ids",
        "required_answer_terms",
        "forbidden_answer_terms",
        "expected_source_ids",
        "required_progress_fragments",
    )
    @classmethod
    def validate_unique_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Cowork UX case values")

    @model_validator(mode="after")
    def validate_answer_terms(self) -> CoworkUxCase:
        required = {term.casefold() for term in self.required_answer_terms}
        forbidden = {term.casefold() for term in self.forbidden_answer_terms}
        if required & forbidden:
            raise ValueError("Required and forbidden answer terms cannot overlap.")
        return self


class CoworkUxObservation(ContractModel):
    transcript_id: Identifier
    case_id: Identifier
    tool_calls: tuple[Identifier, ...]
    approval_count: int = Field(ge=0)
    spill_recovery_count: int = Field(ge=0)
    progress_events: tuple[NonEmptyText, ...] = ()
    first_progress_ms: int | None = Field(default=None, ge=0)
    completion_ms: int = Field(ge=0)
    payload_bytes: int = Field(ge=0)
    selected_agent_ids: tuple[Identifier, ...] = ()
    selection_reasons: tuple[NonEmptyText, ...] = ()
    answer: NonEmptyText
    source_ids: tuple[Identifier, ...] = ()
    citation_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_selection_reasons(self) -> CoworkUxObservation:
        if len(self.selection_reasons) != len(self.selected_agent_ids):
            raise ValueError("Every selected agent requires one public selection reason.")
        return self


@dataclass(frozen=True, slots=True)
class CoworkUxGate:
    name: str
    passes: bool
    actual: Any
    required: Any


@dataclass(frozen=True, slots=True)
class CoworkUxResult:
    case_id: str
    transcript_id: str
    gates: tuple[CoworkUxGate, ...]

    @property
    def passes(self) -> bool:
        return all(gate.passes for gate in self.gates)

    @property
    def failures(self) -> tuple[str, ...]:
        return tuple(gate.name for gate in self.gates if not gate.passes)


@dataclass(frozen=True, slots=True)
class CoworkUxReportPaths:
    run_json: Path
    run_html: Path
    latest_json: Path
    latest_html: Path
    history_json: Path


def load_cowork_ux_cases(path: Path = DEFAULT_CASES_PATH) -> tuple[CoworkUxCase, ...]:
    return _load_jsonl(path, CoworkUxCase, "Cowork UX case")


def load_cowork_ux_observations(
    path: Path = DEFAULT_BASELINE_PATH,
) -> tuple[CoworkUxObservation, ...]:
    return _load_jsonl(path, CoworkUxObservation, "Cowork UX observation")


def evaluate_cowork_ux(
    case: CoworkUxCase,
    observation: CoworkUxObservation,
) -> CoworkUxResult:
    if observation.case_id != case.case_id:
        raise ValueError("Cowork UX observation does not match its case.")

    answer = observation.answer.casefold()
    progress = tuple(event.casefold() for event in observation.progress_events)
    required_progress = tuple(fragment.casefold() for fragment in case.required_progress_fragments)
    answer_words = len(_WORD.findall(observation.answer))
    gates = (
        _gate(
            "oneOrchestrationCall",
            len(observation.tool_calls) == 1 and observation.tool_calls[0] in _ORCHESTRATION_TOOLS,
            list(observation.tool_calls),
            sorted(_ORCHESTRATION_TOOLS),
        ),
        _gate(
            "zeroGenericApprovals",
            observation.approval_count == 0,
            observation.approval_count,
            0,
        ),
        _gate(
            "zeroSpillRecovery",
            observation.spill_recovery_count == 0,
            observation.spill_recovery_count,
            0,
        ),
        _gate(
            "payloadBudget",
            observation.payload_bytes <= case.max_payload_bytes,
            observation.payload_bytes,
            f"<= {case.max_payload_bytes}",
        ),
        _gate(
            "orderedReadableProgress",
            _contains_in_order(progress, required_progress),
            list(observation.progress_events),
            list(case.required_progress_fragments),
        ),
        _gate(
            "noProgressJargon",
            not _contains_any(" ".join(progress), _PROGRESS_JARGON),
            list(observation.progress_events),
            f"exclude {list(_PROGRESS_JARGON)}",
        ),
        _gate(
            "firstProgressLatency",
            observation.first_progress_ms is not None
            and observation.first_progress_ms <= case.max_first_progress_ms,
            observation.first_progress_ms,
            f"<= {case.max_first_progress_ms}",
        ),
        _gate(
            "exactSelection",
            observation.selected_agent_ids == case.expected_agent_ids,
            list(observation.selected_agent_ids),
            list(case.expected_agent_ids),
        ),
        _gate(
            "publicSelectionReasons",
            len(observation.selection_reasons) == len(case.expected_agent_ids)
            and all(observation.selection_reasons),
            list(observation.selection_reasons),
            f"{len(case.expected_agent_ids)} non-empty reason(s)",
        ),
        _gate(
            "requiredAnswerTerms",
            all(term.casefold() in answer for term in case.required_answer_terms),
            [term for term in case.required_answer_terms if term.casefold() not in answer],
            "no missing required terms",
        ),
        _gate(
            "intentScopedAnswer",
            not _contains_any(answer, case.forbidden_answer_terms),
            [term for term in case.forbidden_answer_terms if term.casefold() in answer],
            "no forbidden adjacent context",
        ),
        _gate(
            "minimalSources",
            len(observation.source_ids) == len(case.expected_source_ids)
            and set(observation.source_ids) == set(case.expected_source_ids),
            list(observation.source_ids),
            list(case.expected_source_ids),
        ),
        _gate(
            "citationCount",
            observation.citation_count == len(case.expected_source_ids),
            observation.citation_count,
            len(case.expected_source_ids),
        ),
        _gate(
            "transportSilent",
            not _contains_any(answer, _TRANSPORT_TERMS),
            [term for term in _TRANSPORT_TERMS if term in answer],
            "no transport narration unless requested",
        ),
        _gate(
            "noMemoryPromise",
            not _contains_any(answer, _MEMORY_PROMISES),
            [term for term in _MEMORY_PROMISES if term in answer],
            "no unsupported preference-memory promise",
        ),
        _gate(
            "conciseAnswer",
            answer_words <= case.max_answer_words,
            answer_words,
            f"<= {case.max_answer_words}",
        ),
        _gate(
            "completionLatency",
            observation.completion_ms <= case.max_completion_ms,
            observation.completion_ms,
            f"<= {case.max_completion_ms}",
        ),
    )
    return CoworkUxResult(
        case_id=case.case_id,
        transcript_id=observation.transcript_id,
        gates=gates,
    )


def evaluate_cowork_ux_suite(
    cases: tuple[CoworkUxCase, ...],
    observations: tuple[CoworkUxObservation, ...],
) -> tuple[CoworkUxResult, ...]:
    case_by_id = {case.case_id: case for case in cases}
    if len(case_by_id) != len(cases):
        raise ValueError("Cowork UX case IDs must be unique.")
    results = []
    for observation in observations:
        case = case_by_id.get(observation.case_id)
        if case is None:
            raise ValueError(f"Unknown Cowork UX case {observation.case_id!r}.")
        results.append(evaluate_cowork_ux(case, observation))
    if not results:
        raise ValueError("At least one Cowork UX observation is required.")
    return tuple(results)


def build_cowork_ux_payload(
    results: tuple[CoworkUxResult, ...],
    *,
    generated_at: datetime | None = None,
    run_label: str = "cowork-ux",
) -> dict[str, Any]:
    if not results:
        raise ValueError("At least one Cowork UX result is required.")
    timestamp = (generated_at or datetime.now(UTC)).astimezone(UTC)
    case_results = [
        {
            "caseId": result.case_id,
            "transcriptId": result.transcript_id,
            "status": "pass" if result.passes else "fail",
            "failedGates": list(result.failures),
            "gates": [
                {
                    "name": gate.name,
                    "passes": gate.passes,
                    "actual": gate.actual,
                    "required": gate.required,
                }
                for gate in result.gates
            ],
        }
        for result in results
    ]
    failures = [result for result in case_results if result["status"] == "fail"]
    return {
        "schemaVersion": "1.0",
        "suiteId": "cowork-ux",
        "runLabel": run_label,
        "generatedAt": timestamp.isoformat().replace("+00:00", "Z"),
        "cases": len(results),
        "passes": not failures,
        "passedCases": len(results) - len(failures),
        "failedCases": len(failures),
        "caseResults": case_results,
        "failures": failures,
    }


def write_cowork_ux_report(
    payload: dict[str, Any],
    output_root: Path = DEFAULT_REPORT_ROOT,
) -> CoworkUxReportPaths:
    _validate_payload(payload)
    output_root.mkdir(parents=True, exist_ok=True)
    runs = output_root / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    stamp = re.sub(r"[^A-Za-z0-9._-]+", "-", str(payload["generatedAt"])).strip("-")
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", str(payload["runLabel"])).strip("-")
    if not stamp or not label:
        raise ValueError("Cowork UX report identifiers cannot be empty.")
    run_id = f"{stamp}-{label}"
    run_json = runs / f"{run_id}.json"
    run_html = runs / f"{run_id}.html"
    latest_json = output_root / "latest.json"
    latest_html = output_root / "latest.html"
    history_json = output_root / "history.json"
    json_text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    html_text = _render_html(payload)
    for path, text in (
        (run_json, json_text),
        (run_html, html_text),
        (latest_json, json_text),
        (latest_html, html_text),
    ):
        _atomic_write(path, text)
    _write_history(history_json, payload, run_id)
    return CoworkUxReportPaths(
        run_json=run_json,
        run_html=run_html,
        latest_json=latest_json,
        latest_html=latest_html,
        history_json=history_json,
    )


def _load_jsonl(path: Path, model: type[Any], label: str) -> tuple[Any, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"Cannot read {label.lower()}s at {path}: {exc}") from exc
    values = tuple(model.model_validate_json(line) for line in lines if line.strip())
    if not values:
        raise ValueError(f"At least one {label.lower()} is required.")
    return values


def _gate(name: str, passes: bool, actual: Any, required: Any) -> CoworkUxGate:
    return CoworkUxGate(name=name, passes=passes, actual=actual, required=required)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    normalized = text.casefold()
    return any(term.casefold() in normalized for term in terms)


def _contains_in_order(events: tuple[str, ...], fragments: tuple[str, ...]) -> bool:
    index = 0
    for event in events:
        if index < len(fragments) and fragments[index] in event:
            index += 1
    return index == len(fragments)


def _validate_payload(payload: dict[str, Any]) -> None:
    if payload.get("schemaVersion") != "1.0" or payload.get("suiteId") != "cowork-ux":
        raise ValueError("Unsupported Cowork UX evaluation payload.")
    if not isinstance(payload.get("caseResults"), list):
        raise TypeError("Cowork UX caseResults must be a list.")


def _write_history(path: Path, payload: dict[str, Any], run_id: str) -> None:
    history: list[dict[str, Any]] = []
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, list):
            raise ValueError(f"Cowork UX history at {path} must contain a JSON list.")
        history = [item for item in loaded if isinstance(item, dict)]
    summary = {
        "runId": run_id,
        "runLabel": payload["runLabel"],
        "generatedAt": payload["generatedAt"],
        "cases": payload["cases"],
        "passedCases": payload["passedCases"],
        "failedCases": payload["failedCases"],
        "passes": payload["passes"],
    }
    history = [item for item in history if item.get("runId") != run_id]
    history.append(summary)
    history = sorted(history, key=lambda item: str(item.get("generatedAt", "")))[-100:]
    _atomic_write(
        path,
        json.dumps(history, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
    )


def _render_html(payload: dict[str, Any]) -> str:
    status = "PASS" if payload["passes"] else "FAIL"
    rows = []
    for result in payload["caseResults"]:
        for gate in result["gates"]:
            gate_status = "PASS" if gate["passes"] else "FAIL"
            actual = html.escape(json.dumps(gate["actual"], ensure_ascii=True))
            required = html.escape(json.dumps(gate["required"], ensure_ascii=True))
            rows.append(
                "<tr>"
                f"<td>{html.escape(str(result['transcriptId']))}</td>"
                f"<td>{html.escape(str(gate['name']))}</td>"
                f'<td class="{gate_status.casefold()}">{gate_status}</td>'
                f"<td><code>{actual}</code></td>"
                f"<td><code>{required}</code></td>"
                "</tr>"
            )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent IQ Cowork UX - {status}</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 32px; color: #17213a; }}
    h1 {{ margin-bottom: 4px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 24px; }}
    th, td {{ border: 1px solid #dfe5f0; padding: 9px; text-align: left; vertical-align: top; }}
    th {{ background: #edf2fa; }}
    .pass {{ color: #0b6435; font-weight: 700; }}
    .fail {{ color: #9a1f1f; font-weight: 700; }}
    code {{ white-space: pre-wrap; }}
  </style>
</head>
<body>
  <h1>Agent IQ Cowork UX: {status}</h1>
  <p>{html.escape(str(payload["runLabel"]))} - {html.escape(str(payload["generatedAt"]))}</p>
  <table>
    <thead><tr><th>Transcript</th><th>Gate</th><th>Status</th><th>Actual</th>
      <th>Required</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table>
</body>
</html>
"""


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)
