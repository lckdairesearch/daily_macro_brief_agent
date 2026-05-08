"""Tests for CLI entry point (Step 4.1)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run(mode: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app.main", "--mode", mode],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )


def test_invalid_mode_exits_nonzero():
    """Argparse rejects an unrecognized --mode value with a non-zero exit code."""
    result = subprocess.run(
        [sys.executable, "-m", "app.main", "--mode", "bogus"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    assert result.returncode != 0


def test_sample_mode_exits_zero():
    """Sample mode completes without error and exits 0."""
    result = _run("sample")
    assert result.returncode == 0, result.stderr


def test_sample_mode_prints_run_id():
    """Sample mode stdout contains a run_id line."""
    result = _run("sample")
    assert "run_id=" in result.stdout


def test_sample_mode_prints_ok_status():
    """Sample mode stdout starts with [OK]."""
    result = _run("sample")
    assert "[OK]" in result.stdout
