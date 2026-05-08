"""Tests for market data module (Phase A–E: fixture, raw API clients, move detection, live providers)."""

import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from pydantic import ValidationError

from app.data.market import (
    AlphaVantageMarketProvider,
    DatabentoMarketProvider,
    FixtureMarketProvider,
    FredMarketProvider,
    YfinanceMarketProvider,
    _av_fetch_daily,
    _compute_1d_change,
    _fgbl_price_to_yield_pct,
    _fred_fetch_series,
    _yf_fetch_daily,
    cache_market,
    detect_moves,
    fetch_live_market_with_cache,
    load_cached_market,
    load_vol_params,
)
from app.models import AssetClass, FreshnessStatus, MarketSnapshot

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


# ---------------------------------------------------------------------------
# Phase D: load_vol_params and detect_moves
# ---------------------------------------------------------------------------

_VOL_YAML = """\
generated_at: "2026-05-08"
lookback_days: 252
params:
  SPY:
    sd_1d: 1.05
    unit: "%"
  QQQ:
    sd_1d: 1.02
    unit: "%"
  US10Y:
    sd_1d: 4.26
    unit: bps
"""


def _make_snap(
    instrument_id: str,
    one_day_change: float,
    one_day_change_unit: str = "%",
    asset_class: str = "equity",
    region: str = "US",
) -> MarketSnapshot:
    return MarketSnapshot(
        as_of=datetime(2026, 5, 8, tzinfo=timezone.utc),
        instrument_id=instrument_id,
        display_name=instrument_id,
        asset_class=asset_class,
        region=region,
        last_price_or_level=100.0,
        one_day_change=one_day_change,
        one_day_change_unit=one_day_change_unit,
        source="test",
        freshness_status="fresh",
    )


def test_load_vol_params_reads_yaml(tmp_path):
    (tmp_path / "vol_params.yaml").write_text(_VOL_YAML)
    params = load_vol_params(tmp_path)
    assert params["SPY"]["sd_1d"] == pytest.approx(1.05)
    assert params["US10Y"]["unit"] == "bps"


def test_load_vol_params_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="vol_params"):
        load_vol_params(tmp_path)


def test_detect_moves_above_threshold_flagged():
    """1.2 sigma move → threshold_flag=True with correct z-score."""
    snap = _make_snap("SPY", 1.26)  # 1.26 / 1.05 ≈ 1.2 sigma
    params = {"SPY": {"sd_1d": 1.05, "unit": "%"}}
    result = detect_moves([snap], params, threshold_sigma=1.0)
    assert result[0].threshold_flag is True
    assert result[0].one_day_zscore == pytest.approx(1.26 / 1.05, rel=1e-3)


def test_detect_moves_below_threshold_not_flagged():
    """0.8 sigma move → threshold_flag=False."""
    snap = _make_snap("SPY", 0.84)  # 0.84 / 1.05 = 0.8 sigma
    params = {"SPY": {"sd_1d": 1.05, "unit": "%"}}
    result = detect_moves([snap], params, threshold_sigma=1.0)
    assert result[0].threshold_flag is False


def test_detect_moves_missing_instrument_uses_fallback():
    """Missing instrument falls back to 1.0 % sd; no crash."""
    snap = _make_snap("UNKNOWN", 0.50, one_day_change_unit="%")
    result = detect_moves([snap], {})
    # fallback sd=1.0 → zscore=0.5 → not flagged
    assert result[0].threshold_flag is False
    assert result[0].one_day_zscore == pytest.approx(0.5)


def test_detect_moves_missing_bps_instrument_uses_fallback():
    """Missing bps instrument falls back to 5.0 bps sd."""
    snap = _make_snap("UNKNOWN2", 3.0, one_day_change_unit="bps")
    result = detect_moves([snap], {})
    # fallback sd=5.0 → zscore=0.6 → not flagged
    assert result[0].threshold_flag is False
    assert result[0].one_day_zscore == pytest.approx(3.0 / 5.0)


def test_detect_moves_unit_mismatch_raises_value_error():
    """vol_params unit != snapshot unit → explicit ValueError."""
    snap = _make_snap("US10Y", 5.0, one_day_change_unit="bps")
    params = {"US10Y": {"sd_1d": 1.05, "unit": "%"}}  # wrong unit in vol_params
    with pytest.raises(ValueError, match="[Uu]nit mismatch"):
        detect_moves([snap], params)


def test_detect_moves_group_both_negative_one_breached():
    """2 US equities both negative, one at 1.1 sigma → both flagged."""
    snap_spy = _make_snap("SPY", -1.155)   # 1.155 / 1.05 ≈ 1.1 sigma → breaches
    snap_qqq = _make_snap("QQQ", -0.714)   # 0.714 / 1.02 ≈ 0.7 sigma → does not breach
    params = {
        "SPY": {"sd_1d": 1.05, "unit": "%"},
        "QQQ": {"sd_1d": 1.02, "unit": "%"},
    }
    result = detect_moves([snap_spy, snap_qqq], params, threshold_sigma=1.0)
    assert result[0].threshold_flag is True
    assert result[1].threshold_flag is True  # elevated by group rule
    assert result[1].warning is not None     # annotated as group move


def test_detect_moves_group_not_triggered_mixed_direction():
    """Mixed direction instruments in same group → group rule does not apply."""
    snap_spy = _make_snap("SPY", -1.155)   # 1.1 sigma negative → breaches on own
    snap_qqq = _make_snap("QQQ", 0.714)    # positive direction
    params = {
        "SPY": {"sd_1d": 1.05, "unit": "%"},
        "QQQ": {"sd_1d": 1.02, "unit": "%"},
    }
    result = detect_moves([snap_spy, snap_qqq], params, threshold_sigma=1.0)
    assert result[0].threshold_flag is True   # breaches on its own
    assert result[1].threshold_flag is False  # not elevated (mixed direction)


def test_detect_moves_group_not_triggered_no_breach():
    """Both instruments move same direction but neither breaches → group rule off."""
    snap_spy = _make_snap("SPY", -0.50)
    snap_qqq = _make_snap("QQQ", -0.50)
    params = {
        "SPY": {"sd_1d": 1.05, "unit": "%"},
        "QQQ": {"sd_1d": 1.02, "unit": "%"},
    }
    result = detect_moves([snap_spy, snap_qqq], params, threshold_sigma=1.0)
    assert result[0].threshold_flag is False
    assert result[1].threshold_flag is False


# ---------------------------------------------------------------------------
# Phase E: _compute_1d_change and live providers (all HTTP mocked)
# ---------------------------------------------------------------------------

_AS_OF = datetime(2026, 5, 8, 7, 0, tzinfo=timezone.utc)


def test_compute_1d_change_pct():
    rows = [{"date": "2026-05-07", "close": 500.0}, {"date": "2026-05-08", "close": 505.0}]
    last, change = _compute_1d_change(rows, "%")
    assert last == pytest.approx(505.0)
    assert change == pytest.approx(1.0)  # (505-500)/500*100


def test_compute_1d_change_bps():
    rows = [{"date": "2026-05-07", "close": 4.50}, {"date": "2026-05-08", "close": 4.55}]
    last, change = _compute_1d_change(rows, "bps")
    assert last == pytest.approx(4.55)
    assert change == pytest.approx(5.0)  # (4.55-4.50)*100 = 5 bps


# --- AlphaVantageMarketProvider ---

def test_av_provider_spy_returns_snapshot():
    payload = {"Time Series (Daily)": {
        "2026-05-08": {"4. close": "505.00"},
        "2026-05-07": {"4. close": "500.00"},
    }}
    with patch("requests.get", return_value=_mock_response(payload)):
        snaps = AlphaVantageMarketProvider("key").fetch_watchlist(["SPY"], _AS_OF)
    assert len(snaps) == 1
    s = snaps[0]
    assert s.instrument_id == "SPY"
    assert s.source == "alpha_vantage"
    assert s.one_day_change_unit == "%"
    assert s.one_day_change == pytest.approx(1.0)
    assert s.last_price_or_level == pytest.approx(505.0)


def test_av_provider_us10y_bps_change():
    payload = {"data": [
        {"date": "2026-05-07", "value": "4.50"},
        {"date": "2026-05-08", "value": "4.55"},
    ]}
    with patch("requests.get", return_value=_mock_response(payload)):
        snaps = AlphaVantageMarketProvider("key").fetch_watchlist(["US10Y"], _AS_OF)
    assert len(snaps) == 1
    assert snaps[0].one_day_change_unit == "bps"
    assert snaps[0].one_day_change == pytest.approx(5.0)


def test_av_provider_insufficient_data_skipped():
    """Single-row response → instrument skipped, no crash."""
    payload = {"Time Series (Daily)": {"2026-05-08": {"4. close": "505.00"}}}
    with patch("requests.get", return_value=_mock_response(payload)):
        snaps = AlphaVantageMarketProvider("key").fetch_watchlist(["SPY"], _AS_OF)
    assert snaps == []


def test_av_provider_fetch_error_skipped(caplog):
    """API error → instrument skipped with warning, no crash."""
    with patch("requests.get", side_effect=RuntimeError("timeout")):
        snaps = AlphaVantageMarketProvider("key").fetch_watchlist(["SPY"], _AS_OF)
    assert snaps == []


def test_av_provider_unknown_instrument_skipped():
    snaps = AlphaVantageMarketProvider("key").fetch_watchlist(["UNKNOWN_XYZ"], _AS_OF)
    assert snaps == []


# --- DatabentoMarketProvider ---

def test_db_provider_fesx_returns_snapshot(db_client_mock):
    entries = [
        {"date": "2026-05-07", "close": 4900.0, "volume": 10000},
        {"date": "2026-05-08", "close": 4950.0, "volume": 12000},
    ]
    store = MagicMock()
    store.to_df.return_value = _make_ohlcv_df(entries)
    db_client_mock.timeseries.get_range.return_value = store

    with patch("databento.Historical", return_value=db_client_mock):
        snaps = DatabentoMarketProvider("key").fetch_watchlist(["FESX"], _AS_OF)

    assert len(snaps) == 1
    s = snaps[0]
    assert s.instrument_id == "FESX"
    assert s.one_day_change_unit == "%"
    assert s.one_day_change == pytest.approx((4950 - 4900) / 4900 * 100, rel=1e-3)
    assert s.source == "databento"


def test_db_provider_de10y_bps_change(db_client_mock):
    """DE10Y uses FGBL price→yield conversion; change reported in bps."""
    # Two FGBL prices; after conversion the yield difference should give bps change
    entries = [
        {"date": "2026-05-07", "close": 131.0, "volume": 8000},
        {"date": "2026-05-08", "close": 130.6, "volume": 9000},
    ]
    store = MagicMock()
    store.to_df.return_value = _make_ohlcv_df(entries)
    db_client_mock.timeseries.get_range.return_value = store

    with patch("databento.Historical", return_value=db_client_mock):
        snaps = DatabentoMarketProvider("key").fetch_watchlist(["DE10Y"], _AS_OF)

    assert len(snaps) == 1
    s = snaps[0]
    assert s.instrument_id == "DE10Y"
    assert s.one_day_change_unit == "bps"
    # prices dropped → yields rose → positive bps change
    assert s.one_day_change > 0


def test_db_provider_uses_continuous_symbols(db_client_mock):
    """FESX must resolve with FESX.c.0 (continuous), not FESX."""
    store = MagicMock()
    store.to_df.return_value = pd.DataFrame()
    db_client_mock.timeseries.get_range.return_value = store

    with patch("databento.Historical", return_value=db_client_mock):
        DatabentoMarketProvider("key").fetch_watchlist(["FESX"], _AS_OF)

    resolve_call = db_client_mock.symbology.resolve.call_args
    assert "FESX.c.0" in resolve_call.kwargs.get("symbols", [])


# --- FredMarketProvider ---

def test_fred_provider_hy_oas_returns_snapshot():
    # FRED BAMLH0A0HYM2 is in percent: 3.30 = 330 bps, 3.35 = 335 bps.
    payload = {"observations": [
        {"date": "2026-05-07", "value": "3.30"},
        {"date": "2026-05-08", "value": "3.35"},
    ]}
    with patch("requests.get", return_value=_mock_response(payload)):
        snaps = FredMarketProvider("key").fetch_watchlist(["HY_OAS"], _AS_OF)
    assert len(snaps) == 1
    s = snaps[0]
    assert s.instrument_id == "HY_OAS"
    assert s.one_day_change_unit == "bps"
    assert s.one_day_change == pytest.approx(5.0)       # (3.35 - 3.30) * 100 = 5 bps
    assert s.last_price_or_level == pytest.approx(335.0)  # 3.35 * 100 = 335 bps display
    assert s.source == "fred"


def test_fred_provider_fetch_error_skipped():
    with patch("requests.get", side_effect=RuntimeError("network error")):
        snaps = FredMarketProvider("key").fetch_watchlist(["HY_OAS"], _AS_OF)
    assert snaps == []


# --- YfinanceMarketProvider ---

def test_yf_provider_vix_returns_low_reliability_snapshot():
    hist = _make_yf_hist([
        {"date": "2026-05-07", "close": 18.0},
        {"date": "2026-05-08", "close": 16.5},
    ])
    ticker_mock = MagicMock()
    ticker_mock.history.return_value = hist

    with patch("yfinance.Ticker", return_value=ticker_mock):
        snaps = YfinanceMarketProvider().fetch_watchlist(["VIX"], _AS_OF)

    assert len(snaps) == 1
    s = snaps[0]
    assert s.instrument_id == "VIX"
    assert s.freshness_status.value == "low_reliability"
    assert s.warning is not None
    assert s.one_day_change_unit == "%"
    assert s.one_day_change == pytest.approx((16.5 - 18.0) / 18.0 * 100, rel=1e-3)


def test_yf_provider_move_returns_low_reliability_snapshot():
    hist = _make_yf_hist([
        {"date": "2026-05-07", "close": 105.0},
        {"date": "2026-05-08", "close": 108.0},
    ])
    ticker_mock = MagicMock()
    ticker_mock.history.return_value = hist

    with patch("yfinance.Ticker", return_value=ticker_mock):
        snaps = YfinanceMarketProvider().fetch_watchlist(["MOVE"], _AS_OF)

    assert len(snaps) == 1
    assert snaps[0].instrument_id == "MOVE"
    assert snaps[0].freshness_status.value == "low_reliability"


def test_yf_provider_insufficient_data_skipped():
    ticker_mock = MagicMock()
    ticker_mock.history.return_value = pd.DataFrame()
    with patch("yfinance.Ticker", return_value=ticker_mock):
        snaps = YfinanceMarketProvider().fetch_watchlist(["VIX"], _AS_OF)
    assert snaps == []


# ---------------------------------------------------------------------------
# Phase F: cache_market, load_cached_market, fetch_live_market_with_cache
# ---------------------------------------------------------------------------

def test_cache_market_writes_both_files(tmp_path):
    """cache_market writes latest.json and a dated file."""
    snaps = [_make_snap("SPY", 1.0)]
    cache_market(snaps, tmp_path, date(2026, 5, 8))
    assert (tmp_path / "market" / "latest.json").exists()
    assert (tmp_path / "market" / "market_2026-05-08.json").exists()


def test_cache_market_round_trips(tmp_path):
    """Snapshots written by cache_market load back correctly via load_cached_market."""
    snaps = [_make_snap("SPY", 1.5), _make_snap("QQQ", -0.8)]
    cache_market(snaps, tmp_path, date(2026, 5, 8))
    loaded = load_cached_market(tmp_path)
    assert len(loaded) == 2
    ids = {s.instrument_id for s in loaded}
    assert ids == {"SPY", "QQQ"}


def test_load_cached_market_missing_raises(tmp_path):
    """FileNotFoundError is raised when no cache exists."""
    with pytest.raises(FileNotFoundError, match="cached"):
        load_cached_market(tmp_path)


def _make_settings_mock(cache_dir: str, timezone: str = "UTC") -> MagicMock:
    settings = MagicMock()
    settings.app.cache_dir = cache_dir
    settings.app.timezone = timezone
    return settings


def test_fetch_live_market_with_cache_success(tmp_path):
    """Successful live fetch caches result and returns fresh snapshots."""
    snaps = [_make_snap("SPY", 1.0)]
    settings = _make_settings_mock(str(tmp_path))

    with patch("app.data.market.fetch_live_market", return_value=snaps), \
         patch("app.data.market.REPO_ROOT", tmp_path.parent):
        # Override REPO_ROOT so cache_dir resolves to tmp_path
        import app.data.market as mkt
        original_root = mkt.REPO_ROOT
        mkt.REPO_ROOT = tmp_path.parent
        settings.app.cache_dir = tmp_path.name
        try:
            result = fetch_live_market_with_cache(settings, {})
        finally:
            mkt.REPO_ROOT = original_root

    assert len(result) == 1
    assert result[0].freshness_status.value == "fresh"


def test_fetch_live_market_with_cache_fallback_on_failure(tmp_path):
    """Live fetch fails + cache present → stale snapshots with STALE_CACHE status."""
    # Pre-populate cache
    snaps = [_make_snap("SPY", 1.0), _make_snap("QQQ", -0.5)]
    cache_market(snaps, tmp_path, date(2026, 5, 7))

    import app.data.market as mkt
    original_root = mkt.REPO_ROOT
    mkt.REPO_ROOT = tmp_path.parent
    settings = _make_settings_mock(tmp_path.name)
    try:
        with patch("app.data.market.fetch_live_market", side_effect=RuntimeError("API down")):
            result = fetch_live_market_with_cache(settings, {})
    finally:
        mkt.REPO_ROOT = original_root

    assert len(result) == 2
    assert all(s.freshness_status.value == "stale_cache" for s in result)
    assert all(s.warning is not None for s in result)
    assert "API down" in result[0].warning


def test_fetch_live_market_with_cache_no_cache_raises(tmp_path):
    """Live fetch fails + no cache → RuntimeError."""
    import app.data.market as mkt
    original_root = mkt.REPO_ROOT
    mkt.REPO_ROOT = tmp_path.parent
    settings = _make_settings_mock(tmp_path.name)
    try:
        with patch("app.data.market.fetch_live_market", side_effect=RuntimeError("API down")):
            with pytest.raises(RuntimeError, match="cannot continue|Cannot continue"):
                fetch_live_market_with_cache(settings, {})
    finally:
        mkt.REPO_ROOT = original_root


def test_fetch_live_market_stale_warnings_all_snapshots(tmp_path):
    """All returned stale snapshots carry the same warning string."""
    snaps = [_make_snap("SPY", 1.0), _make_snap("QQQ", -0.5), _make_snap("BTC", 2.0)]
    cache_market(snaps, tmp_path, date(2026, 5, 7))

    import app.data.market as mkt
    original_root = mkt.REPO_ROOT
    mkt.REPO_ROOT = tmp_path.parent
    settings = _make_settings_mock(tmp_path.name)
    try:
        with patch("app.data.market.fetch_live_market", side_effect=ConnectionError("timeout")):
            result = fetch_live_market_with_cache(settings, {})
    finally:
        mkt.REPO_ROOT = original_root

    warnings_set = {s.warning for s in result}
    assert len(warnings_set) == 1  # same warning on all snapshots
    assert "timeout" in next(iter(warnings_set))
