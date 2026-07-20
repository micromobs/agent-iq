"""Local Eval Center report tests."""

from __future__ import annotations

import json
import multiprocessing
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from multiprocessing.synchronize import Barrier, Event
from pathlib import Path

import pytest

from agent_iq.evals import build_discovery_payload, reporting, write_eval_report
from agent_iq.match.evaluation import CaseResult, EvaluationResult


def _multiprocess_report_writer(
    output_dir: str,
    result_path: str,
    shared_payload: dict[str, object],
    writer_number: int,
    start_barrier: Barrier,
) -> None:
    start_barrier.wait(timeout=60)
    process_payload = dict(shared_payload)
    process_payload["agents"] = writer_number + 100
    process_payload["requestedSemantic"] = f"writer-{writer_number}"
    paths = write_eval_report(process_payload, Path(output_dir))
    Path(result_path).write_text(paths.run_json.stem, encoding="utf-8")


def _hold_report_lock(output_dir: str, ready: Event, release: Event) -> None:
    with reporting._output_directory_lock(Path(output_dir), timeout=30):
        ready.set()
        release.wait(timeout=60)


def _hold_report_lock_with_incomplete_run(
    output_dir: str,
    run_id: str,
    ready: Event,
    release: Event,
) -> None:
    with reporting._output_directory_lock(Path(output_dir), timeout=30):
        runs_dir = Path(output_dir) / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        (runs_dir / f"{run_id}.json").write_text("active", encoding="utf-8")
        ready.set()
        release.wait(timeout=60)


def result(*, exact: bool = True) -> EvaluationResult:
    false_positives = () if exact else ("agent-b",)
    return EvaluationResult(
        cases=(
            CaseResult(
                case_id="case-1",
                expected=("agent-a",),
                ranked=("agent-a", "agent-b"),
                selected=("agent-a",) if exact else ("agent-b",),
                recall_at_k=1.0,
                reciprocal_rank=1.0,
                false_positives=false_positives,
                exact=exact,
                duration_ms=12.3456,
            ),
        ),
        recall_at_k=1.0,
        mean_reciprocal_rank=1.0,
        exact_accuracy=1.0 if exact else 0.0,
        false_positives=len(false_positives),
        p95_latency_ms=12.3456,
        semantic_backend="offline-token-char-tfidf",
    )


def payload(*, exact: bool = True) -> dict[str, object]:
    return build_discovery_payload(
        result(exact=exact),
        agent_count=2,
        top_k=2,
        requested_semantic="offline",
        generated_at=datetime(2026, 7, 10, 12, 30, tzinfo=UTC),
    )


def test_payload_contains_all_cases_and_gate_provenance() -> None:
    report = payload()

    assert report["generatedAt"] == "2026-07-10T12:30:00Z"
    assert report["passes"] is True
    assert report["failures"] == []
    case = report["caseResults"][0]  # type: ignore[index]
    assert case["rankedTopK"] == ["agent-a", "agent-b"]
    assert case["durationMs"] == 12.346
    gates = report["gates"]
    assert gates["falsePositives"]["required"] == 0  # type: ignore[index]


def test_report_writer_publishes_latest_run_and_history(tmp_path: Path) -> None:
    first = write_eval_report(payload(), tmp_path)

    assert first.run_json.is_file()
    assert first.run_html.is_file()
    assert first.latest_json.read_text(encoding="utf-8") == first.run_json.read_text(
        encoding="utf-8"
    )
    html = first.latest_html.read_text(encoding="utf-8")
    assert "Agent IQ Eval Center" in html
    assert "offline-token-char-tfidf" in html
    assert "case-1" in html
    assert "<script" not in html

    second_payload = payload(exact=False)
    second_payload["generatedAt"] = "2026-07-10T12:31:00Z"
    second = write_eval_report(second_payload, tmp_path)
    history = json.loads(second.history_json.read_text(encoding="utf-8"))
    assert len(history) == 2
    assert history[-1]["passes"] is False
    assert 'role="status">FAIL' in second.latest_html.read_text(encoding="utf-8")


def test_report_writer_is_collision_safe_for_concurrent_identical_runs(
    tmp_path: Path,
) -> None:
    shared_payload = payload()

    with ThreadPoolExecutor(max_workers=8) as executor:
        reports = list(
            executor.map(
                lambda _: write_eval_report(shared_payload, tmp_path),
                range(12),
            )
        )

    run_names = {report.run_json.stem for report in reports}
    assert len(run_names) == len(reports)
    assert all(report.run_json.is_file() and report.run_html.is_file() for report in reports)
    assert all(report.run_json.stem == report.run_html.stem for report in reports)
    assert len(json.loads(reports[-1].history_json.read_text(encoding="utf-8"))) == 12
    assert not list(tmp_path.rglob("*.tmp"))


def test_report_writer_is_process_safe_and_keeps_latest_generation_consistent(
    tmp_path: Path,
) -> None:
    process_count = 4
    report_root = tmp_path / "reports"
    result_root = tmp_path / "worker-results"
    result_root.mkdir()
    preexisting_run_ids = {
        write_eval_report(payload(), report_root).run_json.stem
        for _ in range(reporting.MAX_RUN_REPORTS - 1)
    }
    context = multiprocessing.get_context("spawn")
    start_barrier = context.Barrier(process_count + 1)
    processes = [
        context.Process(
            target=_multiprocess_report_writer,
            args=(
                str(report_root),
                str(result_root / f"{writer_number}.txt"),
                payload(),
                writer_number,
                start_barrier,
            ),
        )
        for writer_number in range(process_count)
    ]

    try:
        for process in processes:
            process.start()
        start_barrier.wait(timeout=60)
        for process in processes:
            process.join(timeout=60)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)

    assert all(process.exitcode == 0 for process in processes)
    run_ids = {
        (result_root / f"{writer_number}.txt").read_text(encoding="utf-8")
        for writer_number in range(process_count)
    }
    assert len(run_ids) == process_count
    assert not run_ids & preexisting_run_ids

    history = json.loads((report_root / "history.json").read_text(encoding="utf-8"))
    assert len(history) == len(preexisting_run_ids) + process_count
    assert {entry["runId"] for entry in history} == run_ids | preexisting_run_ids
    assert {entry["generatedAt"] for entry in history} == {"2026-07-10T12:30:00Z"}
    assert {entry["semanticBackend"] for entry in history} == {"offline-token-char-tfidf"}
    assert {entry["agents"] for entry in history if entry["agents"] >= 100} == {
        writer_number + 100 for writer_number in range(process_count)
    }

    managed: dict[str, set[str]] = {}
    runs_dir = report_root / "runs"
    for path in runs_dir.iterdir():
        match = reporting._RUN_REPORT_NAME.fullmatch(path.name)
        if path.is_file() and not path.is_symlink() and match is not None:
            managed.setdefault(match["run_id"], set()).add(match["extension"])
    assert len(managed) == reporting.MAX_RUN_REPORTS
    assert all(extensions == {"json", "html"} for extensions in managed.values())
    for run_id in managed:
        json.loads((runs_dir / f"{run_id}.json").read_text(encoding="utf-8"))
        run_html = (runs_dir / f"{run_id}.html").read_text(encoding="utf-8")
        assert f'content="{run_id}"' in run_html

    latest_json_text = (report_root / "latest.json").read_text(encoding="utf-8")
    latest_html_text = (report_root / "latest.html").read_text(encoding="utf-8")
    marker = re.search(
        r'<meta name="agent-iq-report-run-id" content="([^"]+)">',
        latest_html_text,
    )
    assert marker is not None
    latest_run_id = marker.group(1)
    assert latest_run_id in managed
    assert latest_json_text == (runs_dir / f"{latest_run_id}.json").read_text(encoding="utf-8")
    assert latest_html_text == (runs_dir / f"{latest_run_id}.html").read_text(encoding="utf-8")
    latest_payload = json.loads(latest_json_text)
    latest_history = next(entry for entry in history if entry["runId"] == latest_run_id)
    assert latest_history["agents"] == latest_payload["agents"]
    assert not list(report_root.rglob("*.tmp"))


def test_incomplete_run_owned_by_live_process_is_removed_only_after_lock_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = f"{reporting.RUN_REPORT_PREFIX}live-writer-{'a' * 32}"
    incomplete = tmp_path / "runs" / f"{run_id}.json"
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_report_lock_with_incomplete_run,
        args=(str(tmp_path), run_id, ready, release),
    )
    holder.start()
    try:
        assert ready.wait(timeout=30)
        monkeypatch.setattr(reporting, "REPORT_LOCK_TIMEOUT_SECONDS", 0.2)
        with pytest.raises(reporting.ReportLockTimeoutError):
            write_eval_report(payload(), tmp_path)
        assert incomplete.read_text(encoding="utf-8") == "active"
    finally:
        release.set()
        holder.join(timeout=30)
        if holder.is_alive():
            holder.terminate()
            holder.join(timeout=5)

    assert holder.exitcode == 0
    monkeypatch.setattr(reporting, "REPORT_LOCK_TIMEOUT_SECONDS", 10.0)
    write_eval_report(payload(), tmp_path)
    assert not incomplete.exists()


def test_report_lock_times_out_without_deleting_live_or_stale_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_report_lock,
        args=(str(tmp_path), ready, release),
    )
    holder.start()
    try:
        assert ready.wait(timeout=30)
        monkeypatch.setattr(reporting, "REPORT_LOCK_TIMEOUT_SECONDS", 0.2)
        started = time.monotonic()
        with pytest.raises(
            reporting.ReportLockTimeoutError,
            match="Timed out acquiring",
        ) as error:
            write_eval_report(payload(), tmp_path)
        assert time.monotonic() - started < 2
        assert str(tmp_path) not in str(error.value)
        assert holder.is_alive()
        lock_path = tmp_path / reporting._REPORT_LOCK_NAME
        assert lock_path.is_file()
        assert not (tmp_path / "latest.json").exists()
        assert not (tmp_path / "history.json").exists()
        assert not list((tmp_path / "runs").iterdir())
    finally:
        release.set()
        holder.join(timeout=30)
        if holder.is_alive():
            holder.terminate()
            holder.join(timeout=5)

    assert holder.exitcode == 0
    assert lock_path.read_bytes()
    assert set(lock_path.read_bytes()) == {0}
    recovered = write_eval_report(payload(), tmp_path)
    assert recovered.latest_json.is_file()
    assert (tmp_path / reporting._REPORT_LOCK_NAME).is_file()


def test_report_writer_prunes_single_side_orphans_and_preserves_unmanaged_entries(
    tmp_path: Path,
) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True)
    unrelated = runs_dir / "unrelated.json"
    unrelated.write_text("keep", encoding="utf-8")
    old_shape = runs_dir / "2026-07-10T12-30-00Z-offline-token-char-tfidf.json"
    old_shape.write_text("keep", encoding="utf-8")
    wrong_prefix = runs_dir / (f"other-20260710T123000000000Z-{'a' * 32}.json")
    wrong_prefix.write_text("keep", encoding="utf-8")
    near_match = runs_dir / f"{reporting.RUN_REPORT_PREFIX}not-a-run.json"
    near_match.write_text("keep", encoding="utf-8")
    managed_directory = runs_dir / (f"{reporting.RUN_REPORT_PREFIX}directory-{'b' * 32}.json")
    managed_directory.mkdir()
    (managed_directory / "marker.txt").write_text("keep", encoding="utf-8")
    json_orphan = runs_dir / (f"{reporting.RUN_REPORT_PREFIX}json-orphan-{'c' * 32}.json")
    json_orphan.write_text("remove", encoding="utf-8")
    html_orphan = runs_dir / (f"{reporting.RUN_REPORT_PREFIX}html-orphan-{'d' * 32}.html")
    html_orphan.write_text("remove", encoding="utf-8")
    active_temp = runs_dir / (f".{reporting.RUN_REPORT_PREFIX}active-{'e' * 32}.json.writer.tmp")
    active_temp.write_text("keep", encoding="utf-8")

    reports = [write_eval_report(payload(), tmp_path) for _ in range(reporting.MAX_RUN_REPORTS + 3)]

    managed_files = [
        path
        for path in runs_dir.iterdir()
        if not path.is_symlink()
        and path.is_file()
        and reporting._RUN_REPORT_NAME.fullmatch(path.name)
    ]
    assert len({path.stem for path in managed_files}) == reporting.MAX_RUN_REPORTS
    assert len(managed_files) == reporting.MAX_RUN_REPORTS * 2
    assert len({report.run_json.name for report in reports}) == len(reports)
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert old_shape.read_text(encoding="utf-8") == "keep"
    assert wrong_prefix.read_text(encoding="utf-8") == "keep"
    assert near_match.read_text(encoding="utf-8") == "keep"
    assert not json_orphan.exists()
    assert not html_orphan.exists()
    assert active_temp.read_text(encoding="utf-8") == "keep"
    assert (managed_directory / "marker.txt").read_text(encoding="utf-8") == "keep"
    assert (tmp_path / "latest.json").is_file()
    assert (tmp_path / "latest.html").is_file()
    active_temp.unlink()
    assert not list(tmp_path.rglob("*.tmp"))


def test_repeated_failed_publications_leave_at_most_one_incomplete_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_atomic_write = reporting._atomic_write

    def fail_before_run_html(path: Path, text: str) -> None:
        if path.parent.name == "runs" and path.suffix == ".html":
            raise OSError("simulated writer crash")
        real_atomic_write(path, text)

    monkeypatch.setattr(reporting, "_atomic_write", fail_before_run_html)
    for _ in range(reporting.MAX_RUN_REPORTS * 2):
        with pytest.raises(OSError, match="simulated writer crash"):
            write_eval_report(payload(), tmp_path)
        managed = reporting._managed_run_reports(tmp_path / "runs")
        assert len(managed) == 1
        assert all(files.keys() == {"json"} for files in managed.values())

    monkeypatch.setattr(reporting, "_atomic_write", real_atomic_write)
    write_eval_report(payload(), tmp_path)
    managed = reporting._managed_run_reports(tmp_path / "runs")
    assert len(managed) == 1
    assert all(files.keys() == {"json", "html"} for files in managed.values())


def test_report_writer_pruning_does_not_follow_managed_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True)
    target = runs_dir / "symlink-target.json"
    target.write_text("keep", encoding="utf-8")
    link = runs_dir / f"{reporting.RUN_REPORT_PREFIX}linked-{'d' * 32}.json"
    try:
        link.symlink_to(target)
    except OSError:
        link.write_text("stand-in", encoding="utf-8")
        original_is_symlink = Path.is_symlink
        monkeypatch.setattr(
            Path,
            "is_symlink",
            lambda path: path == link or original_is_symlink(path),
        )

    for _ in range(reporting.MAX_RUN_REPORTS + 1):
        write_eval_report(payload(), tmp_path)

    assert link.is_symlink()
    assert target.read_text(encoding="utf-8") == "keep"


def test_writer_rejects_invalid_payload_and_history(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing required field"):
        write_eval_report({}, tmp_path)

    invalid = payload()
    invalid["schemaVersion"] = "2.0"
    with pytest.raises(ValueError, match="Unsupported"):
        write_eval_report(invalid, tmp_path)

    invalid = payload()
    invalid["caseResults"] = {}
    with pytest.raises(TypeError, match="must be a list"):
        write_eval_report(invalid, tmp_path)

    history = tmp_path / "history.json"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="must contain a JSON list"):
        write_eval_report(payload(), tmp_path)
