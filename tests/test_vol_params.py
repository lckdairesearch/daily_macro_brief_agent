"""Tests for scripts/update_vol_params.py (Step 5.C)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.update_vol_params import _compute_sd, _build_fetchers, main


# ---------------------------------------------------------------------------
# _compute_sd — pure computation, no I/O
# ---------------------------------------------------------------------------

def _make_rows(closes: list[float]) -> list[dict]:
    return [{"date": f"2026-{i:05}", "close": c} for i, c in enumerate(closes, start=1)]


def test_compute_sd_pct_basic():
    """200+ observations in % mode returns a positive float."""
    closes = [100.0 + i * 0.01 for i in range(210)]
    rows = _make_rows(closes)
    sd = _compute_sd(rows, "%", "SPY")
    assert sd is not None
    assert sd > 0


def test_compute_sd_bps_basic():
    """200+ observations in bps mode returns a positive float."""
    closes = [4.0 + i * 0.01 for i in range(210)]
    rows = _make_rows(closes)
    sd = _compute_sd(rows, "bps", "US10Y")
    assert sd is not None
    assert sd > 0


def test_compute_sd_insufficient_returns_none():
    """Fewer than MIN_OBSERVATIONS rows returns None and emits a warning."""
    rows = _make_rows([100.0] * 50)
    with pytest.warns(UserWarning, match="only 50"):
        sd = _compute_sd(rows, "%", "SPY")
    assert sd is None


def test_compute_sd_pct_constant_change_near_zero():
    """Constant 1% daily moves → all pct changes equal → SD ≈ 0."""
    closes = [100.0 * (1.01 ** i) for i in range(210)]
    rows = _make_rows(closes)
    sd = _compute_sd(rows, "%", "SPY")
    assert sd is not None
    assert sd < 1e-6  # constant series has essentially zero variance


def test_compute_sd_bps_known_value():
    """Constant 5 bps daily moves (0.05 pct-pt each) → diff * 100 = 5.0 → sd ≈ 0."""
    closes = [4.0 + i * 0.05 for i in range(210)]
    rows = _make_rows(closes)
    sd = _compute_sd(rows, "bps", "US10Y")
    assert sd is not None
    assert sd < 0.01  # constant series → essentially zero variance after *100


# ---------------------------------------------------------------------------
# _build_fetchers — verify routing without executing live calls
# ---------------------------------------------------------------------------

def _dummy_creds():
    c = MagicMock()
    c.alpha_vantage_api_key = "av_key"
    c.databento_api_key = "db_key"
    c.fred_api_key = "fred_key"
    return c


def test_build_fetchers_returns_all_instruments():
    fetchers = _build_fetchers(_dummy_creds())
    expected = {
        "SPY", "QQQ", "FEZ", "UUP", "USDJPY", "EURUSD", "USDCNH",
        "GOLD", "SILVER", "WTI", "BRENT", "COPPER", "BTC",
        "US2Y", "US10Y", "DE10Y", "HY_OAS", "VIX", "MOVE",
    }
    assert set(fetchers.keys()) == expected


def test_build_fetchers_units():
    fetchers = _build_fetchers(_dummy_creds())
    pct_instruments = {"SPY", "QQQ", "UUP", "USDJPY", "EURUSD", "USDCNH",
                       "GOLD", "SILVER", "WTI", "BRENT", "COPPER", "BTC", "FEZ", "VIX", "MOVE"}
    bps_instruments = {"US2Y", "US10Y", "DE10Y", "HY_OAS"}
    for inst in pct_instruments:
        assert fetchers[inst][0] == "%", f"{inst} should be % unit"
    for inst in bps_instruments:
        assert fetchers[inst][0] == "bps", f"{inst} should be bps unit"


# ---------------------------------------------------------------------------
# main() — integration smoke test with all fetchers mocked
# ---------------------------------------------------------------------------

def _make_closes(n: int = 210) -> list[dict]:
    """Enough rows to pass the MIN_OBSERVATIONS check."""
    return [{"date": f"2025-{i:05}", "close": 100.0 + i * 0.1} for i in range(n)]


def test_main_writes_yaml(tmp_path, monkeypatch):
    """main() writes a valid vol_params.yaml with all expected keys."""
    output_path = tmp_path / "vol_params.yaml"
    monkeypatch.setattr("scripts.update_vol_params.OUTPUT_PATH", output_path)

    creds_mock = _dummy_creds()
    good_rows = _make_closes(210)

    with (
        patch("scripts.update_vol_params.Credentials", return_value=creds_mock),
        patch("scripts.update_vol_params._av_fetch_daily", return_value=good_rows),
        patch("scripts.update_vol_params._db_fetch_daily_ohlcv", return_value=good_rows),
        patch("scripts.update_vol_params._fred_fetch_series", return_value=good_rows),
        patch("scripts.update_vol_params._yf_fetch_daily", return_value=good_rows),
    ):
        main()

    assert output_path.exists()
    with output_path.open() as fh:
        content = fh.read()
    # strip comment lines before parsing
    yaml_text = "\n".join(l for l in content.splitlines() if not l.startswith("#"))
    data = yaml.safe_load(yaml_text)

    assert "generated_at" in data
    assert data["lookback_days"] == 252
    params = data["params"]

    # all instruments present with valid structure
    for inst in ("SPY", "QQQ", "FEZ", "DE10Y", "VIX", "HY_OAS", "MOVE"):
        assert inst in params, f"{inst} missing from params"
        assert "sd_1d" in params[inst]
        assert "unit" in params[inst]


def test_main_skips_failed_fetch(tmp_path, monkeypatch, capsys):
    """An instrument whose fetch raises should be skipped without crashing."""
    output_path = tmp_path / "vol_params.yaml"
    monkeypatch.setattr("scripts.update_vol_params.OUTPUT_PATH", output_path)

    creds_mock = _dummy_creds()
    good_rows = _make_closes(210)

    def av_side_effect(*args, **kwargs):
        symbol = args[0]
        if symbol == "SPY":
            raise RuntimeError("simulated AV failure")
        return good_rows

    with (
        patch("scripts.update_vol_params.Credentials", return_value=creds_mock),
        patch("scripts.update_vol_params._av_fetch_daily", side_effect=av_side_effect),
        patch("scripts.update_vol_params._db_fetch_daily_ohlcv", return_value=good_rows),
        patch("scripts.update_vol_params._fred_fetch_series", return_value=good_rows),
        patch("scripts.update_vol_params._yf_fetch_daily", return_value=good_rows),
    ):
        with pytest.warns(UserWarning, match="SPY"):
            main()

    yaml_text = "\n".join(
        l for l in output_path.read_text().splitlines() if not l.startswith("#")
    )
    params = yaml.safe_load(yaml_text)["params"]
    assert "SPY" not in params
    assert "QQQ" in params  # others still written


def test_main_exits_on_missing_credentials(monkeypatch):
    """main() exits with code 1 when credentials are absent."""
    creds_mock = MagicMock()
    creds_mock.alpha_vantage_api_key = None
    creds_mock.databento_api_key = None
    creds_mock.fred_api_key = None

    with patch("scripts.update_vol_params.Credentials", return_value=creds_mock):
        with pytest.raises(SystemExit) as exc_info:
            main()
    assert exc_info.value.code == 1
