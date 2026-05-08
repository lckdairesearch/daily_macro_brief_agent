"""Market data fetching.

Providers:
  FixtureMarketProvider        — sample/demo mode; no credentials needed  (Step 5.A)
  AlphaVantageMarketProvider   — equities, FX, commodities, US yields, BTC, DXY proxy  (Step 5.E)
  DatabentoMarketProvider      — FESX, DE10Y (FGBL)  (Step 5.E)
  FredMarketProvider           — HY OAS (BAMLH0A0HYM2)  (Step 5.E)
  YfinanceMarketProvider       — ^VIX, ^MOVE (low_reliability)  (Step 5.E)

Falls back to latest cached snapshot with a warning if live calls fail.  (Step 5.F)
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from datetime import datetime

import requests

from app.models import MarketSnapshot

_log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "market_sample.json"

_AV_BASE = "https://www.alphavantage.co/query"
_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"


# ── Phase A: Protocol and fixture provider ────────────────────────────────────

@runtime_checkable
class MarketDataProvider(Protocol):
    """Common interface for all market data providers."""

    def fetch_watchlist(
        self,
        instruments: list[str],
        as_of: datetime,
    ) -> list[MarketSnapshot]:
        ...


class FixtureMarketProvider:
    """Loads fixture market data for sample/demo mode. No credentials required."""

    def __init__(self, fixture_path: Path = DEFAULT_FIXTURE_PATH) -> None:
        self._fixture_path = fixture_path

    def fetch_watchlist(
        self,
        instruments: list[str],
        as_of: datetime,
    ) -> list[MarketSnapshot]:
        if not self._fixture_path.exists():
            raise FileNotFoundError(
                f"Market fixture not found: {self._fixture_path}. "
                "Verify the fixture file exists under tests/fixtures/."
            )
        raw = json.loads(self._fixture_path.read_text(encoding="utf-8"))
        return [MarketSnapshot.model_validate(row) for row in raw["snapshots"]]


# ── Phase B: Raw API client layer ─────────────────────────────────────────────
#
# These three functions return list[dict] with {"date": "YYYY-MM-DD", "close": float}
# rows, sorted ascending, sliced to [start_date, end_date].
# They are building blocks for the vol-params script (5.C) and live providers (5.E).
# Tests must mock at the HTTP/SDK boundary — no live API calls in the test suite.


def _av_fetch_daily(
    symbol: str,
    function: str,
    api_key: str,
    start_date: date,
    end_date: date,
    **extra_params: Any,
) -> list[dict]:
    """
    Fetch daily close rows from Alpha Vantage.

    Confirmed response structures (from live API probing 2026-05-08):
      TIME_SERIES_DAILY       → "Time Series (Daily)"     → "4. close"
      FX_DAILY                → "Time Series FX (Daily)"  → "4. close"  (no volume)
      DIGITAL_CURRENCY_DAILY  → "Time Series (Digital Currency Daily)" → "4. close"
      WTI / BRENT / COPPER    → "data" array of {"date", "value"}
      TREASURY_YIELD          → "data" array of {"date", "value"}

    Instrument-to-symbol mapping notes:
      • GOLD: AV has no GOLD commodity function — use symbol="GLD" with
        function="TIME_SERIES_DAILY" (SPDR Gold ETF proxy).
      • FX pairs: pass from_symbol and to_symbol in extra_params
        e.g. from_symbol="USD", to_symbol="JPY"
      • TREASURY_YIELD: pass maturity="2year" or maturity="10year" in extra_params
    """
    params: dict[str, str] = {"function": function, "apikey": api_key}

    if function == "TIME_SERIES_DAILY":
        params["symbol"] = symbol
        params["outputsize"] = "full"
    elif function == "FX_DAILY":
        # from_symbol and to_symbol must be supplied via extra_params
        params["outputsize"] = "full"
    elif function == "DIGITAL_CURRENCY_DAILY":
        params["symbol"] = symbol
        params.setdefault("market", "USD")
    elif function == "TREASURY_YIELD":
        params["interval"] = "daily"
        # maturity (e.g. "2year", "10year") comes from extra_params
    elif function in ("WTI", "BRENT", "COPPER"):
        params["interval"] = "daily"
        # no symbol param needed — function name IS the commodity

    for k, v in extra_params.items():
        params[k] = str(v)

    resp = requests.get(_AV_BASE, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if "Error Message" in data:
        raise ValueError(
            f"Alpha Vantage error for {function}/{symbol}: {data['Error Message']}"
        )
    if "Information" in data:
        raise RuntimeError(
            f"Alpha Vantage rate limit for {function}/{symbol}: {data['Information']}"
        )

    rows: list[dict] = []

    if function == "TIME_SERIES_DAILY":
        for date_str, entry in data.get("Time Series (Daily)", {}).items():
            rows.append({"date": date_str, "close": float(entry["4. close"])})
    elif function == "FX_DAILY":
        for date_str, entry in data.get("Time Series FX (Daily)", {}).items():
            rows.append({"date": date_str, "close": float(entry["4. close"])})
    elif function == "DIGITAL_CURRENCY_DAILY":
        for date_str, entry in data.get("Time Series (Digital Currency Daily)", {}).items():
            rows.append({"date": date_str, "close": float(entry["4. close"])})
    elif function in ("WTI", "BRENT", "COPPER", "TREASURY_YIELD"):
        for entry in data.get("data", []):
            try:
                rows.append({"date": entry["date"], "close": float(entry["value"])})
            except (ValueError, KeyError):
                pass  # "." entries appear for missing weekend/holiday values

    rows.sort(key=lambda r: r["date"])
    start_str, end_str = start_date.isoformat(), end_date.isoformat()
    return [r for r in rows if start_str <= r["date"] <= end_str]


_DB_TIMEOUT_SECS = 60


def _db_fetch_daily_ohlcv(
    dataset: str,
    symbol: str,
    api_key: str,
    start_date: date,
    end_date: date,
    price_to_yield: bool = False,
) -> list[dict]:
    """
    Fetch daily close rows from Databento using the ohlcv-1d schema.

    Symbol format confirmed by live probing (2026-05-08):
      FESX → symbol="FESX.c.0", dataset="XEUR.EOBI", stype_in="continuous"  ✓
      FGBL → symbol="FGBL.c.0", dataset="XEUR.EOBI", stype_in="continuous"  (untested, same pattern)

    SAFETY GUARD: validates the symbol resolves before calling get_range to avoid
    accidentally downloading the full dataset (74k+ records for GLBX.MDP3).

    Multiple rows per date (different publishers) are deduplicated by taking the
    highest-volume row, which is consistently the primary exchange feed.

    If price_to_yield=True, converts close price to YTM percent via
    _fgbl_price_to_yield_pct(). Required for DE10Y (FGBL notional coupon 6%, ~10Y).

    Both SDK calls are wrapped in a thread timeout (_DB_TIMEOUT_SECS) because the
    Databento SDK has no built-in timeout and will hang indefinitely on stalled connections.
    """
    import concurrent.futures
    import databento as db

    client = db.Historical(api_key)

    def _resolve():
        return client.symbology.resolve(
            dataset=dataset,
            symbols=[symbol],
            stype_in="continuous",
            stype_out="instrument_id",
            start_date=start_date,
            end_date=end_date,
        )

    def _get_range():
        return client.timeseries.get_range(
            dataset=dataset,
            symbols=[symbol],
            schema="ohlcv-1d",
            start=start_date.isoformat(),
            end=end_date.isoformat(),
            stype_in="continuous",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_resolve)
        try:
            resolved = fut.result(timeout=_DB_TIMEOUT_SECS)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(
                f"Databento symbology.resolve timed out after {_DB_TIMEOUT_SECS}s "
                f"for {symbol!r} in {dataset!r}."
            )

    if symbol in resolved.get("not_found", []):
        raise ValueError(
            f"Databento symbol {symbol!r} could not be resolved in dataset {dataset!r}. "
            "Check the Databento portal instrument catalog and update the symbol."
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_get_range)
        try:
            data = fut.result(timeout=_DB_TIMEOUT_SECS)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(
                f"Databento timeseries.get_range timed out after {_DB_TIMEOUT_SECS}s "
                f"for {symbol!r} in {dataset!r}."
            )

    df = data.to_df()

    if df.empty:
        return []

    # ts_event index is DatetimeIndex[UTC] — reset to access as column
    df = df.reset_index()
    df["_date"] = df["ts_event"].dt.normalize().dt.strftime("%Y-%m-%d")

    # Deduplicate: for each date keep the highest-volume row (primary publisher)
    df_dedup = df.loc[df.groupby("_date")["volume"].idxmax()]

    rows = []
    for _, row in df_dedup.iterrows():
        close = float(row["close"])
        if price_to_yield:
            close = _fgbl_price_to_yield_pct(close)
        rows.append({"date": str(row["_date"]), "close": close})

    rows.sort(key=lambda r: r["date"])
    return rows


def _fgbl_price_to_yield_pct(price: float, coupon: float = 6.0, tenor: int = 10) -> float:
    """
    Convert FGBL futures price to approximate YTM in percent using bisection.

    FGBL notional: 6% annual coupon, ~10Y tenor, price quoted as % of par.
    Example: price=130.6 → yield≈2.5%, price=100.0 → yield≈6.0%.
    Annual-coupon bond pricing equation is solved via 64-iteration bisection.
    """
    def _bond_px(y: float) -> float:
        return sum(coupon / (1 + y) ** t for t in range(1, tenor + 1)) + 100.0 / (1 + y) ** tenor

    lo, hi = -0.05, 0.30
    for _ in range(64):
        mid = (lo + hi) / 2
        if _bond_px(mid) > price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2 * 100.0


def _fred_fetch_series(
    series_id: str,
    api_key: str,
    start_date: date,
    end_date: date,
) -> list[dict]:
    """
    Fetch daily observations from the FRED REST API.

    Forward-fills over the full requested date range so weekends and holidays
    carry the most recent business-day value — useful for aligning calendar-day
    windows without gaps.

    FRED returns "." for missing values (weekends/holidays); those are skipped
    and the last known value is carried forward.
    """
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "observation_start": start_date.isoformat(),
        "observation_end": end_date.isoformat(),
        "file_type": "json",
    }
    resp = requests.get(_FRED_BASE, params=params, timeout=30)
    resp.raise_for_status()
    observations = resp.json().get("observations", [])

    value_map: dict[str, float] = {}
    for obs in observations:
        try:
            value_map[obs["date"]] = float(obs["value"])
        except (ValueError, KeyError):
            pass  # "." = missing; forward-fill below handles it

    rows: list[dict] = []
    last: float | None = None
    cursor = start_date
    while cursor <= end_date:
        date_str = cursor.isoformat()
        if date_str in value_map:
            last = value_map[date_str]
        if last is not None:
            rows.append({"date": date_str, "close": last})
        cursor += timedelta(days=1)

    return rows


def _yf_fetch_daily(
    symbol: str,
    start_date: date,
    end_date: date,
) -> list[dict]:
    """
    Fetch daily close rows from yfinance (no API key required).

    Marked low_reliability — yfinance has no SLA and the upstream Yahoo Finance
    feed occasionally returns empty results or stale data without warning.
    Callers should treat a missing result as non-fatal and fall back to cache.

    Confirmed working symbols (probed 2026-05-08):
      ^VIX  — CBOE Volatility Index (spot, not futures)
      ^MOVE — ICE BofA MOVE Index (bond vol)

    Returns rows sorted ascending, date strings in YYYY-MM-DD format.
    The end_date is exclusive in yfinance's history() call, so we pass end_date + 1 day.
    """
    import concurrent.futures
    import yfinance as yf

    def _fetch():
        ticker = yf.Ticker(symbol)
        return ticker.history(
            start=start_date.isoformat(),
            end=(end_date + timedelta(days=1)).isoformat(),
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_fetch)
        try:
            hist = fut.result(timeout=30)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(f"yfinance request timed out after 30s for {symbol!r}.")
    if hist.empty:
        return []

    rows = []
    for ts, row in hist.iterrows():
        # ts is a timezone-aware Timestamp; normalize to date string
        date_str = ts.date().isoformat()
        if start_date.isoformat() <= date_str <= end_date.isoformat():
            rows.append({"date": date_str, "close": float(row["Close"])})

    rows.sort(key=lambda r: r["date"])
    return rows
