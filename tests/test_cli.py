"""Tests for CLI entry point (Step 4.1)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from datetime import datetime
from os import environ
from pathlib import Path
from zoneinfo import ZoneInfo

from app.main import _parse_data_cutoff

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run(mode: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="daily-macro-cli-") as tmp_dir:
        env = dict(environ)
        env["OUTPUT_DIR"] = str(Path(tmp_dir) / "outputs")
        return subprocess.run(
            [sys.executable, "-m", "app.main", "--mode", mode],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            env=env,
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


def test_sample_mode_prints_progress_steps():
    """CLI prints pipeline progress before final status."""
    result = _run("sample")

    assert "[1/13] Determine run window" in result.stdout
    assert "[13/13] Record metadata" in result.stdout
    assert "[OK]" in result.stdout


def test_sample_mode_prints_timing_summary():
    """CLI prints a final component timing table."""
    result = _run("sample")

    assert "Timing summary" in result.stdout
    assert "Component" in result.stdout
    assert "Fetch market data" in result.stdout
    assert "scout:fixture" in result.stdout


def test_parse_data_cutoff_naive_uses_app_timezone():
    cutoff = _parse_data_cutoff("2026-05-08 06:45", ZoneInfo("Asia/Hong_Kong"))

    assert cutoff == datetime(2026, 5, 8, 6, 45, tzinfo=ZoneInfo("Asia/Hong_Kong"))


def test_parse_data_cutoff_bare_date_uses_default_morning_cutoff():
    cutoff = _parse_data_cutoff("2026-05-08", ZoneInfo("Asia/Hong_Kong"))

    assert cutoff == datetime(2026, 5, 8, 6, 45, tzinfo=ZoneInfo("Asia/Hong_Kong"))


def test_parse_data_cutoff_zulu_converts_to_app_timezone():
    cutoff = _parse_data_cutoff("2026-05-07T22:45:00Z", ZoneInfo("Asia/Hong_Kong"))

    assert cutoff == datetime(2026, 5, 8, 6, 45, tzinfo=ZoneInfo("Asia/Hong_Kong"))


def test_parse_data_cutoff_rejects_invalid_value():
    try:
        _parse_data_cutoff("not-a-date", ZoneInfo("Asia/Hong_Kong"))
    except ValueError as exc:
        assert "Invalid --data-cutoff" in str(exc)
    else:
        raise AssertionError("Expected invalid data cutoff to raise ValueError")
