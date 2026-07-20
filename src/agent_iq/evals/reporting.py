"""Persist evaluation results as machine-readable JSON and a local HTML scorecard."""

from __future__ import annotations

import errno
import html
import json
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from agent_iq.match.evaluation import EvaluationResult

REPORT_SCHEMA_VERSION = "1.0"
RUN_REPORT_PREFIX = "consumer-discovery-"
MAX_RUN_REPORTS = 20
MAX_HISTORY_ENTRIES = 100
REPORT_LOCK_TIMEOUT_SECONDS = 10.0
_REPORT_LOCK_POLL_SECONDS = 0.05
_REPORT_LOCK_NAME = ".agent-iq-report.lock"
_RUN_REPORT_NAME = re.compile(
    rf"^(?P<run_id>{re.escape(RUN_REPORT_PREFIX)}"
    r"[A-Za-z0-9][A-Za-z0-9._-]*-[0-9a-f]{32})\.(?P<extension>json|html)$"
)
_REPORT_THREAD_LOCKS_GUARD = threading.Lock()
_REPORT_THREAD_LOCKS: dict[str, threading.Lock] = {}
_LOCK_BUSY_ERRNOS = {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
if hasattr(errno, "EWOULDBLOCK"):
    _LOCK_BUSY_ERRNOS.add(errno.EWOULDBLOCK)


def _reset_thread_locks_after_fork() -> None:
    global _REPORT_THREAD_LOCKS_GUARD

    _REPORT_THREAD_LOCKS_GUARD = threading.Lock()
    _REPORT_THREAD_LOCKS.clear()


if sys.platform != "win32":
    os.register_at_fork(after_in_child=_reset_thread_locks_after_fork)


class ReportLockError(RuntimeError):
    """Raised when exclusive evaluation report publication cannot be secured."""


class ReportLockTimeoutError(ReportLockError, TimeoutError):
    """Raised when the evaluation report publication lock times out."""


@dataclass(frozen=True, slots=True)
class ReportPaths:
    """Stable and run-specific paths produced for one evaluation."""

    run_json: Path
    run_html: Path
    latest_json: Path
    latest_html: Path
    history_json: Path


def build_discovery_payload(
    result: EvaluationResult,
    *,
    agent_count: int,
    top_k: int,
    requested_semantic: str,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the complete, versioned payload shared by JSON and HTML reports."""

    timestamp = (generated_at or datetime.now(UTC)).astimezone(UTC)
    cases = [
        {
            "caseId": case.case_id,
            "status": "pass" if case.exact and not case.false_positives else "fail",
            "expected": list(case.expected),
            "selected": list(case.selected),
            "rankedTopK": list(case.ranked[:top_k]),
            "recallAtK": case.recall_at_k,
            "reciprocalRank": case.reciprocal_rank,
            "falsePositives": list(case.false_positives),
            "exact": case.exact,
            "durationMs": round(case.duration_ms, 3),
        }
        for case in result.cases
    ]
    failures = [case for case in cases if case["status"] == "fail"]
    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "suiteId": "consumer-discovery",
        "generatedAt": timestamp.isoformat().replace("+00:00", "Z"),
        "requestedSemantic": requested_semantic,
        "semanticBackend": result.semantic_backend,
        "topK": top_k,
        "agents": agent_count,
        "cases": len(result.cases),
        "recallAtK": result.recall_at_k,
        "meanReciprocalRank": result.mean_reciprocal_rank,
        "exactAccuracy": result.exact_accuracy,
        "falsePositives": result.false_positives,
        "p95LatencyMs": round(result.p95_latency_ms, 3),
        "passes": result.passes,
        "gates": {
            "recallAtK": {
                "actual": result.recall_at_k,
                "required": 1.0,
                "passes": result.recall_at_k == 1.0,
            },
            "meanReciprocalRank": {
                "actual": result.mean_reciprocal_rank,
                "required": 1.0,
                "passes": result.mean_reciprocal_rank == 1.0,
            },
            "exactAccuracy": {
                "actual": result.exact_accuracy,
                "required": 1.0,
                "passes": result.exact_accuracy == 1.0,
            },
            "falsePositives": {
                "actual": result.false_positives,
                "required": 0,
                "passes": result.false_positives == 0,
            },
        },
        "caseResults": cases,
        "failures": failures,
    }


def write_eval_report(payload: dict[str, Any], output_dir: Path) -> ReportPaths:
    """Atomically write a run, stable latest files, and bounded summary history."""

    _validate_payload(payload)
    output_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = output_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    json_text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    with _output_directory_lock(output_dir, timeout=REPORT_LOCK_TIMEOUT_SECONDS):
        _remove_incomplete_run_reports(runs_dir)
        run_id = _run_id(payload, runs_dir)
        run_json = runs_dir / f"{run_id}.json"
        run_html = runs_dir / f"{run_id}.html"
        latest_json = output_dir / "latest.json"
        latest_html = output_dir / "latest.html"
        history_json = output_dir / "history.json"
        html_text = _render_html(payload, run_id)

        _atomic_write(run_json, json_text)
        _atomic_write(run_html, html_text)
        _write_history(history_json, payload, run_id)
        _atomic_write(latest_html, html_text)
        _atomic_write(latest_json, json_text)
        _prune_run_reports(runs_dir, protected_run_id=run_id)

    return ReportPaths(
        run_json=run_json,
        run_html=run_html,
        latest_json=latest_json,
        latest_html=latest_html,
        history_json=history_json,
    )


@contextmanager
def _output_directory_lock(
    output_dir: Path,
    *,
    timeout: float = REPORT_LOCK_TIMEOUT_SECONDS,
) -> Iterator[None]:
    """Hold a process-death-safe OS lock without removing its persistent lock file."""

    if timeout < 0:
        raise ValueError("Evaluation report lock timeout cannot be negative.")

    deadline = time.monotonic() + timeout
    thread_lock = _thread_lock_for(output_dir)
    if not thread_lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
        raise ReportLockTimeoutError("Timed out acquiring the evaluation report publication lock.")

    stream: BinaryIO | None = None
    file_lock_acquired = False
    publication_error: BaseException | None = None
    release_failed = False
    try:
        stream = _open_lock_file(output_dir / _REPORT_LOCK_NAME)
        while not _try_file_lock(stream):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReportLockTimeoutError(
                    "Timed out acquiring the evaluation report publication lock."
                )
            time.sleep(min(_REPORT_LOCK_POLL_SECONDS, remaining))
        file_lock_acquired = True
        yield
    except BaseException as error:
        publication_error = error
        raise
    finally:
        if file_lock_acquired and stream is not None:
            try:
                _release_file_lock(stream)
            except OSError:
                release_failed = True
        if stream is not None:
            stream.close()
        thread_lock.release()
        if release_failed and publication_error is None:
            raise ReportLockError(
                "Could not release the evaluation report publication lock."
            ) from None


def _thread_lock_for(output_dir: Path) -> threading.Lock:
    key = os.path.normcase(os.path.abspath(output_dir))
    with _REPORT_THREAD_LOCKS_GUARD:
        return _REPORT_THREAD_LOCKS.setdefault(key, threading.Lock())


def _open_lock_file(path: Path) -> BinaryIO:
    stream: BinaryIO | None = None
    try:
        if path.is_symlink():
            raise OSError
        stream = path.open("a+b")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
            os.fsync(stream.fileno())
        stream.seek(0)
        return stream
    except OSError:
        if stream is not None:
            stream.close()
        raise ReportLockError("Could not open the evaluation report publication lock.") from None


def _try_file_lock(stream: BinaryIO) -> bool:
    stream.seek(0)
    if sys.platform == "win32":
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno in _LOCK_BUSY_ERRNOS:
                return False
            raise ReportLockError(
                "Could not acquire the evaluation report publication lock."
            ) from None
    else:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in _LOCK_BUSY_ERRNOS:
                return False
            raise ReportLockError(
                "Could not acquire the evaluation report publication lock."
            ) from None
    return True


def _release_file_lock(stream: BinaryIO) -> None:
    stream.seek(0)
    if sys.platform == "win32":
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _validate_payload(payload: dict[str, Any]) -> None:
    required = {
        "schemaVersion",
        "suiteId",
        "generatedAt",
        "semanticBackend",
        "agents",
        "cases",
        "passes",
        "gates",
        "caseResults",
    }
    missing = sorted(required - payload.keys())
    if missing:
        raise ValueError(f"Evaluation payload is missing required field {missing[0]!r}.")
    if payload["schemaVersion"] != REPORT_SCHEMA_VERSION:
        raise ValueError("Unsupported evaluation report schema version.")
    if not isinstance(payload["caseResults"], list):
        raise TypeError("Evaluation caseResults must be a list.")


def _run_id(payload: dict[str, Any], runs_dir: Path) -> str:
    raw = f"{payload['generatedAt']}-{payload['semanticBackend']}"
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(raw)).strip("-")
    if not normalized:
        raise ValueError("Evaluation report run ID cannot be empty.")
    for _ in range(100):
        run_id = f"{RUN_REPORT_PREFIX}{normalized}-{uuid.uuid4().hex}"
        if (
            not (runs_dir / f"{run_id}.json").exists()
            and not (runs_dir / f"{run_id}.html").exists()
        ):
            return run_id
    raise RuntimeError("Could not allocate a unique evaluation report name.")


def _write_history(path: Path, payload: dict[str, Any], run_id: str) -> None:
    history: list[dict[str, Any]] = []
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, list):
            raise ValueError(f"Evaluation history at {path} must contain a JSON list.")
        history = [item for item in loaded if isinstance(item, dict)]

    summary = {
        "runId": run_id,
        "generatedAt": payload["generatedAt"],
        "semanticBackend": payload["semanticBackend"],
        "agents": payload["agents"],
        "cases": payload["cases"],
        "recallAtK": payload["recallAtK"],
        "meanReciprocalRank": payload["meanReciprocalRank"],
        "exactAccuracy": payload["exactAccuracy"],
        "falsePositives": payload["falsePositives"],
        "p95LatencyMs": payload["p95LatencyMs"],
        "passes": payload["passes"],
    }
    history = [item for item in history if item.get("runId") != run_id]
    history.append(summary)
    history = sorted(history, key=lambda item: str(item.get("generatedAt", "")))[
        -MAX_HISTORY_ENTRIES:
    ]
    _atomic_write(
        path,
        json.dumps(history, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
    )


def _atomic_write(path: Path, text: str) -> None:
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _managed_run_reports(runs_dir: Path) -> dict[str, dict[str, Path]]:
    managed: dict[str, dict[str, Path]] = {}
    for path in runs_dir.iterdir():
        if path.is_symlink() or not path.is_file():
            continue
        match = _RUN_REPORT_NAME.fullmatch(path.name)
        if match is not None:
            managed.setdefault(match["run_id"], {})[match["extension"]] = path
    return managed


def _remove_incomplete_run_reports(runs_dir: Path) -> None:
    managed = _managed_run_reports(runs_dir)
    for run_id in sorted(managed):
        files = managed[run_id]
        if files.keys() >= {"json", "html"}:
            continue
        for extension in sorted(files):
            files[extension].unlink(missing_ok=True)


def _prune_run_reports(runs_dir: Path, *, protected_run_id: str) -> None:
    managed = _managed_run_reports(runs_dir)

    complete_runs = [
        protected_run_id,
        *sorted(
            (
                run_id
                for run_id, files in managed.items()
                if files.keys() >= {"json", "html"} and run_id != protected_run_id
            ),
            reverse=True,
        ),
    ]
    for run_id in complete_runs[MAX_RUN_REPORTS:]:
        for path in managed[run_id].values():
            path.unlink(missing_ok=True)


def _render_html(payload: dict[str, Any], run_id: str) -> str:
    status = "PASS" if payload["passes"] else "FAIL"
    status_class = "pass" if payload["passes"] else "fail"
    metrics = (
        ("Recall@K", _metric(payload["recallAtK"])),
        ("MRR", _metric(payload["meanReciprocalRank"])),
        ("Exact", _metric(payload["exactAccuracy"])),
        ("False positives", str(payload["falsePositives"])),
        ("p95 latency", f"{float(payload['p95LatencyMs']):.2f} ms"),
    )
    cards = "".join(
        f'<section class="metric"><span>{html.escape(label)}</span>'
        f"<strong>{html.escape(value)}</strong></section>"
        for label, value in metrics
    )
    rows = "".join(_case_row(case) for case in payload["caseResults"])
    generated = html.escape(str(payload["generatedAt"]))
    backend = html.escape(str(payload["semanticBackend"]))
    requested = html.escape(str(payload.get("requestedSemantic", "")))
    suite = html.escape(str(payload["suiteId"]))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="agent-iq-report-run-id" content="{html.escape(run_id, quote=True)}">
  <title>Agent IQ Eval Center - {status}</title>
  <style>
    :root {{ color-scheme: light dark; font-family: Segoe UI, Arial, sans-serif; }}
    body {{ margin: 0; background: #f5f7fb; color: #17213a; }}
    main {{ max-width: 1280px; margin: 0 auto; padding: 32px 24px 64px; }}
    header {{ display: flex; align-items: end; justify-content: space-between; gap: 24px; }}
    h1 {{ margin: 0; font-size: 30px; }}
    .subtitle {{ color: #526078; margin: 8px 0 0; }}
    .badge {{ border-radius: 999px; padding: 10px 18px; font-weight: 700; }}
    .badge.pass {{ background: #d8f5e5; color: #0b6435; }}
    .badge.fail {{ background: #ffe1e1; color: #9a1f1f; }}
    .metrics {{ display: grid; grid-template-columns: repeat(5, minmax(150px, 1fr));
      gap: 12px; margin: 28px 0; }}
    .metric {{ background: white; border: 1px solid #dfe5f0; border-radius: 12px;
      padding: 18px; box-shadow: 0 2px 8px #17213a0d; }}
    .metric span {{ display: block; color: #627089; font-size: 13px; }}
    .metric strong {{ display: block; font-size: 25px; margin-top: 6px; }}
    .panel {{ background: white; border: 1px solid #dfe5f0; border-radius: 12px;
      overflow: auto; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 900px; }}
    th, td {{ text-align: left; border-bottom: 1px solid #e7ebf2; padding: 11px 13px;
      vertical-align: top; }}
    th {{ background: #edf2fa; color: #394761; position: sticky; top: 0; }}
    tr:last-child td {{ border-bottom: 0; }}
    .case-pass {{ color: #0b6435; font-weight: 700; }}
    .case-fail {{ color: #9a1f1f; font-weight: 700; }}
    code {{ font-family: Cascadia Mono, Consolas, monospace; font-size: 12px; }}
    footer {{ color: #66738a; margin-top: 18px; font-size: 13px; }}
    @media (max-width: 900px) {{ .metrics {{ grid-template-columns: repeat(2, 1fr); }}
      header {{ align-items: start; flex-direction: column; }} }}
    @media (prefers-color-scheme: dark) {{
      body {{ background: #111722; color: #edf2fa; }}
      .subtitle, footer {{ color: #aeb9ca; }}
      .metric, .panel {{ background: #182131; border-color: #344056; }}
      th {{ background: #202b3e; color: #dbe4f2; }}
      th, td {{ border-color: #344056; }}
    }}
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>Agent IQ Eval Center</h1>
      <p class="subtitle">{suite} - {backend} (requested: {requested})</p>
    </div>
    <div class="badge {status_class}" role="status">{status}</div>
  </header>
  <div class="metrics">{cards}</div>
  <section class="panel" aria-label="Evaluation case results">
    <table>
      <thead><tr><th>Status</th><th>Case</th><th>Expected</th><th>Selected</th>
        <th>Top K</th><th>Latency</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </section>
  <footer>Generated {generated}. Full machine-readable details are in latest.json.</footer>
</main>
</body>
</html>
"""


def _case_row(case: dict[str, Any]) -> str:
    status = str(case["status"]).upper()
    status_class = "case-pass" if case["status"] == "pass" else "case-fail"
    expected = ", ".join(str(value) for value in case["expected"])
    selected = ", ".join(str(value) for value in case["selected"])
    ranked = ", ".join(str(value) for value in case["rankedTopK"])
    duration = f"{float(case['durationMs']):.2f} ms"
    return (
        "<tr>"
        f'<td class="{status_class}">{html.escape(status)}</td>'
        f"<td><code>{html.escape(str(case['caseId']))}</code></td>"
        f"<td>{html.escape(expected)}</td>"
        f"<td>{html.escape(selected)}</td>"
        f"<td>{html.escape(ranked)}</td>"
        f"<td>{html.escape(duration)}</td>"
        "</tr>"
    )


def _metric(value: Any) -> str:
    return f"{float(value):.3f}"
