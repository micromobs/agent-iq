"""Reject runtime-generated report evidence from publishable archives."""

from __future__ import annotations

import argparse
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

REPORTS_DIR = "reports"


def archive_names(path: Path) -> tuple[str, ...]:
    if path.suffix == ".whl" or zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            return tuple(archive.namelist())
    if tarfile.is_tarfile(path):
        with tarfile.open(path) as archive:
            return tuple(archive.getnames())
    raise ValueError(f"unsupported distribution archive: {path}")


def generated_report_entries(names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        name
        for name in names
        if REPORTS_DIR in (part.casefold() for part in PurePosixPath(name).parts)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archives", nargs="+", type=Path)
    args = parser.parse_args()

    findings: list[str] = []
    for archive in args.archives:
        try:
            forbidden = generated_report_entries(archive_names(archive))
        except (OSError, tarfile.TarError, ValueError, zipfile.BadZipFile) as exc:
            findings.append(f"{archive}: {exc}")
            continue
        findings.extend(f"{archive}: forbidden generated report entry {name}" for name in forbidden)

    if findings:
        print("Distribution policy violations:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1
    print(f"Distribution policy: PASS ({len(args.archives)} archives)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
