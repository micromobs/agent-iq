"""Package import contract."""

from __future__ import annotations

import subprocess
import sys

import agent_iq


def test_package_has_version() -> None:
    assert agent_iq.__version__


def test_import_needs_no_credentials_or_network() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import agent_iq; print(agent_iq.__version__)",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()
