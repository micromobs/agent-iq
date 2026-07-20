"""Generate the complete Agent IQ repository file guide."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs" / "REPOSITORY_GUIDE.md"
_TITLE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$")


def _repository_paths(output: Path) -> tuple[Path, ...]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    paths = {
        Path(line.strip())
        for line in completed.stdout.splitlines()
        if line.strip() and (ROOT / line.strip()).is_file()
    }
    paths.add(output.relative_to(ROOT))
    return tuple(sorted(paths, key=lambda path: path.as_posix().casefold()))


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _first_heading(text: str) -> str | None:
    for line in text.splitlines():
        match = _HEADING.match(line)
        if match:
            return match.group(1).strip()
    return None


def _json_description(path: Path) -> str:
    try:
        value: Any = json.loads(_read_text(path))
    except json.JSONDecodeError:
        return "JSON data or configuration."
    if isinstance(value, dict):
        for key in ("title", "name", "$id", "kind"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return f"JSON contract or configuration: {candidate.strip()}."
        keys = ", ".join(sorted(str(key) for key in value)[:4])
        return f"JSON data with top-level fields: {keys}."
    if isinstance(value, list):
        return f"JSON data collection with {len(value)} entries."
    return "JSON data or configuration."


def _python_description(path: Path) -> str:
    try:
        module = ast.parse(_read_text(path))
    except SyntaxError:
        return "Python source module."
    docstring = ast.get_docstring(module)
    if not docstring:
        return "Python source module."
    summary = " ".join(docstring.strip().split())
    return summary if summary.endswith((".", "!", "?")) else f"{summary}."


def _yaml_description(path: Path) -> str:
    for line in _read_text(path).splitlines():
        if line.startswith("name:"):
            return f"YAML configuration: {line.partition(':')[2].strip()}."
    return "YAML configuration or workflow."


def _image_description(path: Path) -> str:
    data = path.read_bytes()
    if (
        path.suffix.casefold() == ".png"
        and data.startswith(b"\x89PNG\r\n\x1a\n")
        and len(data) >= 24
    ):
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        return f"PNG image asset ({width}x{height})."
    return "Image asset."


def _archive_description(path: Path) -> str:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    return f"Release archive ({len(data)} bytes, SHA-256 `{digest}`)."


def _fallback_description(path: Path) -> str:
    stem = re.sub(r"[-_.]+", " ", path.stem).strip()
    kind = {
        ".ps1": "PowerShell automation",
        ".toml": "TOML configuration",
        ".lock": "locked dependency data",
        ".txt": "text asset",
        ".css": "stylesheet",
        ".js": "JavaScript module",
        ".mjs": "JavaScript module",
        ".ts": "TypeScript module",
        ".tsx": "React TypeScript module",
        ".svg": "SVG image asset",
        ".env": "environment template",
    }.get(path.suffix.casefold(), "repository asset")
    return f"{kind.capitalize()} for {stem or path.name}."


def _describe(relative: Path) -> str:
    path = ROOT / relative
    if not path.exists():
        return "Generated repository guide."
    suffix = path.suffix.casefold()
    if suffix == ".md":
        heading = _first_heading(_read_text(path))
        return f"Documentation: {heading}." if heading else "Markdown documentation."
    if suffix == ".py":
        return _python_description(path)
    if suffix == ".json":
        return _json_description(path)
    if suffix == ".jsonl":
        rows = sum(1 for line in _read_text(path).splitlines() if line.strip())
        return f"JSON Lines dataset with {rows} records."
    if suffix in {".yaml", ".yml"}:
        return _yaml_description(path)
    if suffix == ".html":
        match = _TITLE.search(_read_text(path))
        title = " ".join(match.group(1).split()) if match else relative.stem
        return f"HTML experience: {title}."
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return _image_description(path)
    if suffix == ".zip":
        return _archive_description(path)
    return _fallback_description(relative)


def _escape(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", " ")


def render_guide(output: Path = DEFAULT_OUTPUT) -> str:
    """Render one deterministic Markdown inventory for every repository file."""

    paths = _repository_paths(output)
    counts = Counter(path.parts[0] if len(path.parts) > 1 else "(root)" for path in paths)
    lines = [
        "# Agent IQ Repository Guide",
        "",
        (
            f"This generated index covers all **{len(paths)}** files in the release tree. "
            "Regenerate it with:"
        ),
        "",
        "```powershell",
        "uv run python .\\scripts\\generate_repository_guide.py",
        "```",
        "",
        (
            "For task-oriented setup, start with the "
            "[README](../README.md), [Azure deployment guide](DEPLOY_TO_AZURE.md), "
            "and [Cowork installation guide](INSTALL_IN_COWORK.md)."
        ),
        "",
        "## Inventory",
        "",
        "| Area | Files |",
        "| --- | ---: |",
    ]
    for area, count in sorted(counts.items(), key=lambda item: item[0].casefold()):
        lines.append(f"| `{_escape(area)}` | {count} |")

    groups: dict[str, list[Path]] = {}
    for path in paths:
        area = path.parts[0] if len(path.parts) > 1 else "Root files"
        groups.setdefault(area, []).append(path)
    for area, grouped_paths in sorted(groups.items(), key=lambda item: item[0].casefold()):
        lines.extend(
            [
                "",
                f"## {area}",
                "",
                "| Path | Purpose |",
                "| --- | --- |",
            ]
        )
        for path in grouped_paths:
            lines.append(f"| `{_escape(path.as_posix())}` | {_escape(_describe(path))} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    output = arguments.output.resolve()
    rendered = render_guide(output)
    if arguments.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            print(f"Repository guide is stale: {output}")
            return 1
        print(f"Repository guide: PASS ({len(_repository_paths(output))} files)")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"Repository guide: wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
