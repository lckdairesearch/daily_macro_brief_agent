"""Tests for market data module (Phase A — FixtureMarketProvider, Phase B — raw API clients)."""

import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from pydantic import ValidationError

from app.data.market import (
    FixtureMarketProvider,
    _av_fetch_daily,
    _fgbl_price_to_yield_pct,
    _fred_fetch_series,
    _yf_fetch_daily,
)
from app.models import FreshnessStatus, MarketSnapshot

FIXTURE = Path(__file__).parent / "fixtures" / "market_sample.json"
FIXTURE_COUNT = 18  # must equal the number of snapshots in market_sample.json


# ---------------------------------------------------------------------------
# Existing fixture content tests
# ---------------------------------------------------------------------------

def test_fixture_loads():
    data = json.loads(FIXTURE.read_text())
    assert "_note" in data
    assert "fixture" in data["_note"].lower()
    assert len(data["snapshots"]) > 0


def test_fixture_no_invented_source():
    data = json.loads(FIXTURE.read_text())
    for snap in data["snapshots"]:
        assert snap.get("source") == "fixture", f"{snap['instrument_id']} missing fixture source tag"


# ---------------------------------------------------------------------------
# FixtureMarketProvider tests
# ---------------------------------------------------------------------------

def test_fixture_provider_returns_correct_count():
    """fetch_watchlist returns one MarketSnapshot per fixture row."""
    provider = FixtureMarketProvider()
    snapshots = provider.fetch_watchlist([], datetime.now(timezone.utc))
    assert len(snapshots) == FIXTURE_COUNT


def test_fixture_provider_returns_market_snapshot_instances():
    """Every item returned is a valid MarketSnapshot."""
    provider = FixtureMarketProvider()
    snapshots = provider.fetch_watchlist([], datetime.now(timezone.utc))
    for snap in snapshots:
        assert isinstance(snap, MarketSnapshot)


def test_fixture_provider_all_snapshots_pass_pydantic_validation():
    """Each snapshot in the fixture round-trips cleanly through model_validate."""
    data = json.loads(FIXTURE.read_text())
    for row in data["snapshots"]:
        # Should not raise
        snap = MarketSnapshot.model_validate(row)
        assert snap.instrument_id  # non-empty
        assert snap.freshness_status in FreshnessStatus


def test_fixture_provider_missing_path_raises_file_not_found(tmp_path):
    """FileNotFoundError is raised when the fixture file does not exist."""
    provider = FixtureMarketProvider(fixture_path=tmp_path / "nonexistent.json")
    with pytest.raises(FileNotFoundError):
        provider.fetch_watchlist([], datetime.now(timezone.utc))


def test_market_snapshot_missing_required_field_raises_validation_error():
    """MarketSnapshot.model_validate raises ValidationError when a required field is absent."""
    data = json.loads(FIXTURE.read_text())
    bad_row = dict(data["snapshots"][0])
    del bad_row["instrument_id"]
    with pytest.raises(ValidationError):
        MarketSnapshot.model_validate(bad_row)


# ---------------------------------------------------------------------------
# Phase B: _fgbl_price_to_yield_pct
# ---------------------------------------------------------------------------

def test_fgbl_price_to_yield_par():
    """Par price (100.0) with 6% coupon should yield ~6.0%."""
    y = _fgbl_price_to_yield_pct(100.0)
    assert abs(y - 6.0) < 0.01


def test_fgbl_price_to_yield_premium():
    """Premium price (≈130.6) should yield ≈2.5% (verified against bond pricing formula)."""
    y = _fgbl_price_to_yield_pct(130.6)
    assert 2.0 < y < 3.0


def test_fgbl_price_to_yield_round_trip():
    """Yield from bisection is consistent — calling twice gives same result."""
    y1 = _fgbl_price_to_yield_pct(120.0)
    y2 = _fgbl_price_to_yield_pct(120.0)
    assert y1 == y2


# ---------------------------------------------------------------------------
# Phase B: _av_fetch_daily — mock requests.get at the HTTP boundary
# ---------------------------------------------------------------------------

_START = date(2026, 5, 6)
_END = date(2026, 5, 8)


def _mock_response(payload: dict) -> MagicMock:
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = payload
    return m


def test_av_fetch_daily_time_series():
    payload = {
        "Time Series (Daily)": {
            "2026-05-08": {"4. close": "510.25"},
            "2026-05-07": {"4. close": "508.10"},
            "2026-05-04": {"4. close": "502.00"},  # outside window
        }
    }
    with patch("requests.get", return_value=_mock_response(payload)):
        rows = _av_fetch_daily("SPY", "TIME_SERIES_DAILY", "key", _START, _END)
    assert len(rows) == 2
    assert rows[0]["date"] == "2026-05-07"
    assert rows[1]["close"] == pytest.approx(510.25)


def test_av_fetch_daily_fx():
    payload = {
        "Time Series FX (Daily)": {
            "2026-05-08": {"4. close": "155.32"},
            "2026-05-07": {"4. close": "154.90"},
        }
    }
    with patch("requests.get", return_value=_mock_response(payload)):
        rows = _av_fetch_daily(
            "USDJPY", "FX_DAILY", "key", _START, _END,
            from_symbol="USD", to_symbol="JPY",
        )
    assert len(rows) == 2
    assert rows[0]["close"] == pytest.approx(154.90)


def test_av_fetch_daily_digital_currency():
    payload = {
        "Time Series (Digital Currency Daily)": {
            "2026-05-08": {"4. close": "61000.00"},
        }
    }
    with patch("requests.get", return_value=_mock_response(payload)):
        rows = _av_fetch_daily("BTC", "DIGITAL_CURRENCY_DAILY", "key", _START, _END)
    assert len(rows) == 1
    assert rows[0]["close"] == pytest.approx(61000.0)


def test_av_fetch_daily_commodity():
    payload = {
        "data": [
            {"date": "2026-05-08", "value": "80.12"},
            {"date": "2026-05-07", "value": "79.50"},
            {"date": "2026-05-04", "value": "78.00"},  # outside window
        ]
    }
    with patch("requests.get", return_value=_mock_response(payload)):
        rows = _av_fetch_daily("", "WTI", "key", _START, _END)
    assert len(rows) == 2
    assert rows[1]["date"] == "2026-05-08"


def test_av_fetch_daily_treasury_yield():
    payload = {
        "data": [
            {"date": "2026-05-08", "value": "4.52"},
            {"date": "2026-05-07", "value": "."},  # missing — skipped
        ]
    }
    with patch("requests.get", return_value=_mock_response(payload)):
        rows = _av_fetch_daily("", "TREASURY_YIELD", "key", _START, _END, maturity="10year")
    assert len(rows) == 1
    assert rows[0]["close"] == pytest.approx(4.52)


def test_av_fetch_daily_error_message_raises():
    payload = {"Error Message": "Invalid API call."}
    with patch("requests.get", return_value=_mock_response(payload)):
        with pytest.raises(ValueError, match="Alpha Vantage error"):
            _av_fetch_daily("BAD", "TIME_SERIES_DAILY", "key", _START, _END)


def test_av_fetch_daily_rate_limit_raises():
    payload = {"Information": "Thank you for using Alpha Vantage! ..."}
    with patch("requests.get", return_value=_mock_response(payload)):
        with pytest.raises(RuntimeError, match="rate limit"):
            _av_fetch_daily("SPY", "TIME_SERIES_DAILY", "key", _START, _END)


def test_av_fetch_daily_sorted_ascending():
    """Rows are returned sorted by date ascending regardless of API order."""
    payload = {
        "Time Series (Daily)": {
            "2026-05-08": {"4. close": "200.0"},
            "2026-05-06": {"4. close": "198.0"},
            "2026-05-07": {"4. close": "199.0"},
        }
    }
    with patch("requests.get", return_value=_mock_response(payload)):
        rows = _av_fetch_daily("QQQ", "TIME_SERIES_DAILY", "key", _START, _END)
    dates = [r["date"] for r in rows]
    assert dates == sorted(dates)


# ---------------------------------------------------------------------------
# Phase B: _fred_fetch_series — mock requests.get
# ---------------------------------------------------------------------------

def test_fred_fetch_series_basic():
    payload = {
        "observations": [
            {"date": "2026-05-06", "value": "350.2"},
            {"date": "2026-05-07", "value": "352.1"},
            {"date": "2026-05-08", "value": "355.0"},
        ]
    }
    with patch("requests.get", return_value=_mock_response(payload)):
        rows = _fred_fetch_series("BAMLH0A0HYM2", "key", _START, _END)
    assert len(rows) == 3
    assert rows[-1]["close"] == pytest.approx(355.0)


def test_fred_fetch_series_forward_fills_weekend():
    """Values on a weekend (Sat/Sun) should carry the last observed close."""
    # 2026-05-08 = Friday; 2026-05-09 = Saturday; 2026-05-10 = Sunday
    start = date(2026, 5, 8)
    end = date(2026, 5, 10)
    payload = {
        "observations": [
            {"date": "2026-05-08", "value": "355.0"},
        ]
    }
    with patch("requests.get", return_value=_mock_response(payload)):
        rows = _fred_fetch_series("BAMLH0A0HYM2", "key", start, end)
    assert len(rows) == 3
    assert rows[1]["date"] == "2026-05-09"
    assert rows[1]["close"] == pytest.approx(355.0)
    assert rows[2]["date"] == "2026-05-10"
    assert rows[2]["close"] == pytest.approx(355.0)


def test_fred_fetch_series_dot_values_skipped():
    """FRED '.' entries (missing values) are skipped; last good value is carried forward."""
    payload = {
        "observations": [
            {"date": "2026-05-06", "value": "350.0"},
            {"date": "2026-05-07", "value": "."},
            {"date": "2026-05-08", "value": "."},
        ]
    }
    with patch("requests.get", return_value=_mock_response(payload)):
        rows = _fred_fetch_series("BAMLH0A0HYM2", "key", _START, _END)
    assert len(rows) == 3
    assert all(r["close"] == pytest.approx(350.0) for r in rows)


def test_fred_fetch_series_no_data_before_range():
    """If no observation precedes the range, no rows are emitted until the first value."""
    start = date(2026, 5, 6)
    end = date(2026, 5, 8)
    payload = {
        "observations": [
            {"date": "2026-05-08", "value": "355.0"},
        ]
    }
    with patch("requests.get", return_value=_mock_response(payload)):
        rows = _fred_fetch_series("BAMLH0A0HYM2", "key", start, end)
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-05-08"


# ---------------------------------------------------------------------------
# Phase B: _db_fetch_daily_ohlcv — mock databento.Historical
# ---------------------------------------------------------------------------

def _make_ohlcv_df(entries: list[dict]) -> pd.DataFrame:
    """Build a DataFrame matching the structure returned by Databento's to_df()."""
    ts_events = pd.to_datetime([e["date"] + "T00:00:00Z" for e in entries], utc=True)
    df = pd.DataFrame(
        {
            "close": [e["close"] for e in entries],
            "volume": [e["volume"] for e in entries],
        },
        index=pd.DatetimeIndex(ts_events, name="ts_event"),
    )
    return df


@pytest.fixture()
def db_client_mock():
    """Return a MagicMock shaped like databento.Historical."""
    db = pytest.importorskip("databento")
    client = MagicMock()
    client.symbology.resolve.return_value = {"not_found": []}
    return client


def test_db_fetch_daily_symbol_not_found(db_client_mock):
    """ValueError is raised when symbology.resolve marks the symbol as not_found."""
    from app.data.market import _db_fetch_daily_ohlcv

    db_client_mock.symbology.resolve.return_value = {"not_found": ["VX.c.0"]}

    with patch("databento.Historical", return_value=db_client_mock):
        with pytest.raises(ValueError, match="could not be resolved"):
            _db_fetch_daily_ohlcv("GLBX.MDP3", "VX.c.0", "key", _START, _END)


def test_db_fetch_daily_empty_dataframe(db_client_mock):
    """Empty DataFrame (no data for the date range) returns an empty list."""
    from app.data.market import _db_fetch_daily_ohlcv

    store = MagicMock()
    store.to_df.return_value = pd.DataFrame()
    db_client_mock.timeseries.get_range.return_value = store

    with patch("databento.Historical", return_value=db_client_mock):
        rows = _db_fetch_daily_ohlcv("XEUR.EOBI", "FESX.c.0", "key", _START, _END)
    assert rows == []


def test_db_fetch_daily_deduplication(db_client_mock):
    """When two publishers report the same date, the higher-volume row is kept."""
    from app.data.market import _db_fetch_daily_ohlcv

    entries = [
        {"date": "2026-05-07", "close": 5000.0, "volume": 10000},  # primary (higher vol)
        {"date": "2026-05-07", "close": 5001.0, "volume": 500},    # secondary
        {"date": "2026-05-08", "close": 5010.0, "volume": 12000},
    ]
    store = MagicMock()
    store.to_df.return_value = _make_ohlcv_df(entries)
    db_client_mock.timeseries.get_range.return_value = store

    with patch("databento.Historical", return_value=db_client_mock):
        rows = _db_fetch_daily_ohlcv("XEUR.EOBI", "FESX.c.0", "key", _START, _END)

    assert len(rows) == 2
    assert rows[0]["close"] == pytest.approx(5000.0)  # high-volume row for 2026-05-07


def test_db_fetch_daily_price_to_yield(db_client_mock):
    """price_to_yield=True converts FGBL close price to YTM percent."""
    from app.data.market import _db_fetch_daily_ohlcv

    entries = [{"date": "2026-05-08", "close": 130.6, "volume": 8000}]
    store = MagicMock()
    store.to_df.return_value = _make_ohlcv_df(entries)
    db_client_mock.timeseries.get_range.return_value = store

    with patch("databento.Historical", return_value=db_client_mock):
        rows = _db_fetch_daily_ohlcv(
            "XEUR.EOBI", "FGBL.c.0", "key", _START, _END, price_to_yield=True
        )

    assert len(rows) == 1
    assert 2.0 < rows[0]["close"] < 3.0  # ≈2.5% yield


def test_db_fetch_daily_schema_is_ohlcv_1d(db_client_mock):
    """get_range must request ohlcv-1d schema — not minute or tick data."""
    from app.data.market import _db_fetch_daily_ohlcv

    store = MagicMock()
    store.to_df.return_value = pd.DataFrame()
    db_client_mock.timeseries.get_range.return_value = store

    with patch("databento.Historical", return_value=db_client_mock):
        _db_fetch_daily_ohlcv("XEUR.EOBI", "FESX.c.0", "key", _START, _END)

    call_kwargs = db_client_mock.timeseries.get_range.call_args
    assert call_kwargs.kwargs.get("schema") == "ohlcv-1d"


# ---------------------------------------------------------------------------
# Phase B: _yf_fetch_daily — mock yfinance.Ticker at the library boundary
# ---------------------------------------------------------------------------

def _make_yf_hist(entries: list[dict]) -> pd.DataFrame:
    """Build a DataFrame matching yfinance Ticker.history() output."""
    index = pd.DatetimeIndex(
        [pd.Timestamp(e["date"] + "T00:00:00", tz="America/Chicago") for e in entries],
        name="Date",
    )
    return pd.DataFrame({"Close": [e["close"] for e in entries]}, index=index)


def test_yf_fetch_daily_returns_rows():
    hist = _make_yf_hist([
        {"date": "2026-05-06", "close": 18.5},
        {"date": "2026-05-07", "close": 17.1},
        {"date": "2026-05-08", "close": 16.9},
    ])
    ticker_mock = MagicMock()
    ticker_mock.history.return_value = hist

    with patch("yfinance.Ticker", return_value=ticker_mock):
        rows = _yf_fetch_daily("^VIX", _START, _END)

    assert len(rows) == 3
    assert rows[0]["date"] == "2026-05-06"
    assert rows[-1]["close"] == pytest.approx(16.9)


def test_yf_fetch_daily_sorted_ascending():
    hist = _make_yf_hist([
        {"date": "2026-05-08", "close": 16.9},
        {"date": "2026-05-06", "close": 18.5},
        {"date": "2026-05-07", "close": 17.1},
    ])
    ticker_mock = MagicMock()
    ticker_mock.history.return_value = hist

    with patch("yfinance.Ticker", return_value=ticker_mock):
        rows = _yf_fetch_daily("^VIX", _START, _END)

    dates = [r["date"] for r in rows]
    assert dates == sorted(dates)


def test_yf_fetch_daily_empty_returns_empty_list():
    ticker_mock = MagicMock()
    ticker_mock.history.return_value = pd.DataFrame()

    with patch("yfinance.Ticker", return_value=ticker_mock):
        rows = _yf_fetch_daily("^VIX", _START, _END)

    assert rows == []


def test_yf_fetch_daily_end_date_inclusive():
    """end_date row must be included (yfinance receives end_date + 1 day)."""
    hist = _make_yf_hist([{"date": "2026-05-08", "close": 16.9}])
    ticker_mock = MagicMock()
    ticker_mock.history.return_value = hist

    with patch("yfinance.Ticker", return_value=ticker_mock):
        rows = _yf_fetch_daily("^VIX", _START, _END)

    assert any(r["date"] == "2026-05-08" for r in rows)
    # verify end+1 was passed to history()
    call_kwargs = ticker_mock.history.call_args
    assert call_kwargs.kwargs["end"] == "2026-05-09"
