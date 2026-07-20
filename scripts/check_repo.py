"""Fail when source contains local-only paths or credential literals."""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = "reports"
TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsonl",
    ".md",
    ".mjs",
    ".ps1",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
TEXT_NAMES = {".editorconfig", ".env.example", ".gitattributes", ".gitignore"}

NON_PUBLIC_PREFIXES = (
    "deployments/foundry/pg",
    "docs/strategy",
    "packages/cowork/skills/single-agent-demo",
    "packages/cowork/skills/vacation-balance-demo",
    "packages/cowork/skills/workday-rescue",
    "src/agent_iq/data/pg",
)
NON_PUBLIC_PATHS = {
    "_count_assets.py",
    "apps/canvas/tests/browser/workday-rescue.spec.ts",
    "docs/build_history.md",
    "docs/product_backlog.md",
    "docs/strategy/agent_iq_cvp_pitch.md",
    "evals/pg_hero_tools_live.json",
    "schemas/v1/workday-rescue-lane.schema.json",
    "scripts/deploy_pg_canary.ps1",
    "scripts/deploy_pg_fleet.ps1",
    "scripts/generate_pg_deployments.ps1",
    "scripts/summarize_pg_fleet.ps1",
    "scripts/validate_pg_assets.py",
    "src/agent_iq/api/widgets/workday-rescue.html",
    "src/agent_iq/contracts/workday.py",
    "src/agent_iq/deployments/foundry_pg.py",
    "src/agent_iq/employee/workday.py",
    "src/agent_iq/runtime/hosted_pg.py",
    "src/agent_iq/runtime/pydantic_pg.py",
    "tests/catalog/test_pg_assets.py",
    "tests/contract/test_workday_rescue.py",
    "tests/runtime/test_hosted_pg.py",
    "tests/runtime/test_pydantic_pg.py",
}
POLICIES = {
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "Azure storage key": re.compile(r"(?i)\bAccountKey=[A-Za-z0-9+/=]{16,}"),
    "literal credential": re.compile(
        r"""(?ix)
        \b(?:api[_-]?key|client[_-]?secret|access[_-]?token)\b
        \s*[:=]\s*
        ["'][^<{$\s][^"']{7,}["']
        """
    ),
    "literal tenant or subscription ID": re.compile(
        r"""(?ix)
        \b(?:tenant|subscription)[_-]?id\b
        \s*[:=]\s*["']?
        [0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}
        """
    ),
}


def git_source_paths(root: Path = ROOT) -> tuple[Path, ...]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        check=True,
        capture_output=True,
    )
    return tuple(Path(item.decode("utf-8")) for item in result.stdout.split(b"\0") if item)


def reports_are_ignored(root: Path = ROOT) -> bool:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "check-ignore",
            "--no-index",
            "--quiet",
            f"{REPORTS_DIR}/.agent-iq-policy-probe.json",
        ],
        check=False,
    )
    return result.returncode == 0


def generated_report_findings(
    source_paths: tuple[Path, ...],
    root: Path = ROOT,
) -> list[str]:
    findings: list[str] = []
    if not reports_are_ignored(root):
        findings.append(".gitignore: runtime-generated reports/ evidence must be ignored")
    for path in source_paths:
        if path.parts and path.parts[0].casefold() == REPORTS_DIR:
            findings.append(f"{path}: runtime-generated report evidence cannot be committed")
    return findings


def non_public_path_findings(source_paths: tuple[Path, ...]) -> list[str]:
    findings: list[str] = []
    for path in source_paths:
        normalized = path.as_posix().casefold()
        if normalized in NON_PUBLIC_PATHS or any(
            normalized == prefix or normalized.startswith(f"{prefix}/")
            for prefix in NON_PUBLIC_PREFIXES
        ):
            findings.append(f"{path}: local-only content cannot be committed")
    return findings


def iter_text_files(root: Path, source_paths: tuple[Path, ...]) -> Iterator[Path]:
    for relative in source_paths:
        path = root / relative
        if not path.is_file():
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_NAMES:
            yield path


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def main() -> int:
    source_paths = git_source_paths()
    findings = generated_report_findings(source_paths)
    findings.extend(non_public_path_findings(source_paths))

    for path in iter_text_files(ROOT, source_paths):
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT)

        for label, pattern in POLICIES.items():
            for match in pattern.finditer(text):
                findings.append(f"{relative}:{line_number(text, match.start())}: {label}")

    if findings:
        print("Repository policy violations:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1

    print("Repository policy: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
