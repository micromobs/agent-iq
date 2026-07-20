"""Release boundaries for runtime-generated report evidence."""

from __future__ import annotations

import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest
from scripts.check_distribution import archive_names, generated_report_entries
from scripts.check_repo import (
    generated_report_findings,
    git_source_paths,
    non_public_path_findings,
)


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def make_repository(tmp_path: Path) -> Path:
    git(tmp_path, "init", "--quiet")
    (tmp_path / ".gitignore").write_text("/reports/\n", encoding="utf-8")
    (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    report = tmp_path / "reports" / "evals" / "latest.json"
    report.parent.mkdir(parents=True)
    report.write_text('{"status":"local"}\n', encoding="utf-8")
    return report


def test_ignored_reports_are_not_source_candidates(tmp_path: Path) -> None:
    make_repository(tmp_path)

    assert git_source_paths(tmp_path) == (Path(".gitignore"), Path("source.py"))
    assert generated_report_findings(git_source_paths(tmp_path), tmp_path) == []


def test_force_added_report_is_rejected(tmp_path: Path) -> None:
    report = make_repository(tmp_path)
    git(tmp_path, "add", "--force", str(report))

    findings = generated_report_findings(git_source_paths(tmp_path), tmp_path)

    assert findings == [
        f"{Path('reports/evals/latest.json')}: "
        "runtime-generated report evidence cannot be committed"
    ]


@pytest.mark.parametrize(
    "relative",
    (
        Path("src/agent_iq/data/pg/agents.json"),
        Path("src/agent_iq/contracts/workday.py"),
        Path("packages/cowork/skills/single-agent-demo/SKILL.md"),
    ),
)
def test_force_added_local_only_content_is_rejected(
    tmp_path: Path,
    relative: Path,
) -> None:
    make_repository(tmp_path)
    overlay = tmp_path / relative
    overlay.parent.mkdir(parents=True)
    overlay.write_text("{}\n", encoding="utf-8")
    git(tmp_path, "add", "--force", str(overlay))

    findings = non_public_path_findings(git_source_paths(tmp_path))

    assert findings == [f"{relative}: local-only content cannot be committed"]


def test_distribution_archives_reject_report_entries(tmp_path: Path) -> None:
    wheel = tmp_path / "package.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("agent_iq/__init__.py", "")
        archive.writestr("reports/evals/latest.json", "{}")

    sdist_source = tmp_path / "latest.html"
    sdist_source.write_text("<html></html>", encoding="utf-8")
    sdist = tmp_path / "package.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        archive.add(sdist_source, arcname="package/reports/evals/latest.html")

    assert generated_report_entries(archive_names(wheel)) == ("reports/evals/latest.json",)
    assert generated_report_entries(archive_names(sdist)) == ("package/reports/evals/latest.html",)
