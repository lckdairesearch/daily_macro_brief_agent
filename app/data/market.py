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
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

import requests
import yaml

from app.models import AssetClass, FreshnessStatus, MarketSnapshot

if TYPE_CHECKING:
    from app.settings import Settings

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


# ── Phase D: Move detection ───────────────────────────────────────────────────

_FALLBACK_VOL: dict[str, dict] = {
    "%": {"sd_1d": 1.0, "unit": "%"},
    "bps": {"sd_1d": 5.0, "unit": "bps"},
}


def load_vol_params(config_dir: Path) -> dict[str, dict]:
    """Load vol_params.yaml from config_dir. Raises FileNotFoundError if absent."""
    path = config_dir / "vol_params.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"vol_params.yaml not found at {path}. "
            "Run `make update-vol-params` to generate it before using live mode."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw.get("params", {})


def detect_moves(
    snapshots: list[MarketSnapshot],
    vol_params: dict[str, dict],
    threshold_sigma: float = 1.0,
) -> list[MarketSnapshot]:
    """
    Compute one_day_zscore and threshold_flag for each snapshot.

    - Missing instrument: conservative fallback (1.0 % or 5.0 bps) + warning log.
    - Unit mismatch between vol_params and snapshot: hard ValueError.
    - After individual flagging, applies group move detection by (asset_class, region).
    """
    updated: list[MarketSnapshot] = []
    for snap in snapshots:
        vp = vol_params.get(snap.instrument_id)
        if vp is None:
            vp = _FALLBACK_VOL.get(
                snap.one_day_change_unit,
                {"sd_1d": 1.0, "unit": snap.one_day_change_unit},
            )
            _log.warning(
                "vol_params missing for %s; using fallback sd_1d=%.2f %s",
                snap.instrument_id,
                vp["sd_1d"],
                vp["unit"],
            )

        vp_unit = str(vp["unit"])
        if vp_unit != snap.one_day_change_unit:
            raise ValueError(
                f"Unit mismatch for {snap.instrument_id}: "
                f"vol_params unit={vp_unit!r} but snapshot unit={snap.one_day_change_unit!r}. "
                "This would produce wrong z-scores — check vol_params.yaml."
            )

        sd = float(vp["sd_1d"])
        zscore = snap.one_day_change / sd if sd else 0.0
        flagged = abs(zscore) >= threshold_sigma
        updated.append(
            snap.model_copy(update={"one_day_zscore": round(zscore, 4), "threshold_flag": flagged})
        )

    return _apply_group_move_detection(updated, threshold_sigma)


def _apply_group_move_detection(
    snapshots: list[MarketSnapshot],
    threshold_sigma: float,
) -> list[MarketSnapshot]:
    """
    Elevate correlated group moves by (asset_class, region).

    Rule: if >= 2 instruments in the same group moved the same direction AND
    >= 1 of those breached threshold, flag all same-direction instruments and
    annotate any non-breaching ones with a warning string.
    """
    groups: dict[tuple, list[int]] = defaultdict(list)
    for i, snap in enumerate(snapshots):
        groups[(snap.asset_class, snap.region)].append(i)

    result = list(snapshots)

    for (ac, region), indices in groups.items():
        if len(indices) < 2:
            continue
        group_snaps = [snapshots[i] for i in indices]

        for direction in (1, -1):
            same_dir = [
                (orig_i, snap)
                for orig_i, snap in zip(indices, group_snaps)
                if (snap.one_day_change > 0 and direction == 1)
                or (snap.one_day_change < 0 and direction == -1)
            ]
            if len(same_dir) < 2:
                continue
            if not any(snap.threshold_flag for _, snap in same_dir):
                continue

            ac_str = ac.value if hasattr(ac, "value") else str(ac)
            dir_str = "positive" if direction == 1 else "negative"
            group_warning = (
                f"Group move: >= 2 {ac_str}/{region} instruments moved {dir_str} "
                f"with at least one breaching the {threshold_sigma}-sigma threshold"
            )
            for orig_i, snap in same_dir:
                if not snap.threshold_flag:
                    result[orig_i] = snap.model_copy(
                        update={"threshold_flag": True, "warning": group_warning}
                    )

    return result


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


# ── Phase E: Live providers ───────────────────────────────────────────────────
#
# Each class implements MarketDataProvider and wraps the Phase B client functions.
# Move detection is NOT applied here — the caller (fetch_live_market) handles it.

_LIVE_LOOKBACK_DAYS = 10  # calendar days; enough to capture 2 trading days across holidays

# Instrument metadata tables — canonical instrument_id is the dict key.

_AV_INSTRUMENT_META: dict[str, dict] = {
    "SPY":    {"function": "TIME_SERIES_DAILY",      "symbol": "SPY",    "display_name": "S&P 500 (proxy)",     "asset_class": AssetClass.EQUITY,    "region": "US",     "change_unit": "%"},
    "QQQ":    {"function": "TIME_SERIES_DAILY",      "symbol": "QQQ",    "display_name": "Nasdaq 100 (proxy)",  "asset_class": AssetClass.EQUITY,    "region": "US",     "change_unit": "%"},
    "UUP":    {"function": "TIME_SERIES_DAILY",      "symbol": "UUP",    "display_name": "DXY (proxy)",         "asset_class": AssetClass.FX,        "region": "US",     "change_unit": "%"},
    "USDJPY": {"function": "FX_DAILY",               "symbol": "USDJPY", "display_name": "USD/JPY",             "asset_class": AssetClass.FX,        "region": "Asia",   "change_unit": "%",   "extra": {"from_symbol": "USD", "to_symbol": "JPY"}},
    "EURUSD": {"function": "FX_DAILY",               "symbol": "EURUSD", "display_name": "EUR/USD",             "asset_class": AssetClass.FX,        "region": "EU",     "change_unit": "%",   "extra": {"from_symbol": "EUR", "to_symbol": "USD"}},
    "USDCNH": {"function": "FX_DAILY",               "symbol": "USDCNH", "display_name": "USD/CNH",             "asset_class": AssetClass.FX,        "region": "Asia",   "change_unit": "%",   "extra": {"from_symbol": "USD", "to_symbol": "CNH"}},
    "GOLD":   {"function": "TIME_SERIES_DAILY",      "symbol": "GLD",    "display_name": "Gold",                "asset_class": AssetClass.COMMODITY, "region": "Global", "change_unit": "%"},
    "WTI":    {"function": "WTI",                    "symbol": "",       "display_name": "WTI Crude",           "asset_class": AssetClass.COMMODITY, "region": "US",     "change_unit": "%"},
    "BRENT":  {"function": "BRENT",                  "symbol": "",       "display_name": "Brent Crude",         "asset_class": AssetClass.COMMODITY, "region": "Global", "change_unit": "%"},
    "COPPER": {"function": "COPPER",                 "symbol": "",       "display_name": "Copper",              "asset_class": AssetClass.COMMODITY, "region": "Global", "change_unit": "%"},
    "BTC":    {"function": "DIGITAL_CURRENCY_DAILY", "symbol": "BTC",    "display_name": "Bitcoin",             "asset_class": AssetClass.CRYPTO,    "region": "Global", "change_unit": "%"},
    "US2Y":   {"function": "TREASURY_YIELD",         "symbol": "",       "display_name": "US 2Y Yield",         "asset_class": AssetClass.RATES,     "region": "US",     "change_unit": "bps", "extra": {"maturity": "2year"}},
    "US10Y":  {"function": "TREASURY_YIELD",         "symbol": "",       "display_name": "US 10Y Yield",        "asset_class": AssetClass.RATES,     "region": "US",     "change_unit": "bps", "extra": {"maturity": "10year"}},
}

_DB_INSTRUMENT_META: dict[str, dict] = {
    "FESX":  {"dataset": "XEUR.EOBI", "symbol": "FESX.c.0", "display_name": "Euro Stoxx 50",     "asset_class": AssetClass.EQUITY, "region": "EU", "change_unit": "%",   "price_to_yield": False},
    "DE10Y": {"dataset": "XEUR.EOBI", "symbol": "FGBL.c.0", "display_name": "German 10Y (Bund)", "asset_class": AssetClass.RATES,  "region": "EU", "change_unit": "bps", "price_to_yield": True},
}

_FRED_INSTRUMENT_META: dict[str, dict] = {
    # BAMLH0A0HYM2 is reported by FRED in percent (3.30 = 330 bps).
    # level_multiplier converts the raw value to the display unit (bps).
    "HY_OAS": {"series_id": "BAMLH0A0HYM2", "display_name": "HY OAS (BAMLH0A0HYM2)", "asset_class": AssetClass.CREDIT, "region": "US", "change_unit": "bps", "level_multiplier": 100},
}

_YF_INSTRUMENT_META: dict[str, dict] = {
    "VIX":  {"symbol": "^VIX",  "display_name": "VIX",       "asset_class": AssetClass.VOLATILITY, "region": "US", "change_unit": "%"},
    "MOVE": {"symbol": "^MOVE", "display_name": "MOVE Index", "asset_class": AssetClass.VOLATILITY, "region": "US", "change_unit": "%"},
}

AV_INSTRUMENTS       = list(_AV_INSTRUMENT_META)
DB_INSTRUMENTS       = list(_DB_INSTRUMENT_META)
FRED_INSTRUMENTS     = list(_FRED_INSTRUMENT_META)
YFINANCE_INSTRUMENTS = list(_YF_INSTRUMENT_META)


def _compute_1d_change(rows: list[dict], change_unit: str) -> tuple[float, float]:
    """Return (last_close, 1d_change) from at least 2 sorted rows.

    %   instruments: change = (last - prev) / prev * 100
    bps instruments: change = (last - prev) * 100  (raw values are in percent)
    """
    prev = rows[-2]["close"]
    last = rows[-1]["close"]
    if change_unit == "%":
        return last, (last - prev) / prev * 100.0
    if change_unit == "bps":
        return last, (last - prev) * 100.0
    return last, last - prev


class AlphaVantageMarketProvider:
    """Live provider for AV instruments (equities, FX, commodities, yields, BTC)."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def fetch_watchlist(self, instruments: list[str], as_of: datetime) -> list[MarketSnapshot]:
        end = as_of.date()
        start = end - timedelta(days=_LIVE_LOOKBACK_DAYS)
        snapshots: list[MarketSnapshot] = []
        for iid in instruments:
            meta = _AV_INSTRUMENT_META.get(iid)
            if meta is None:
                _log.warning("AlphaVantage: unknown instrument %s — skipped", iid)
                continue
            try:
                rows = _av_fetch_daily(
                    meta["symbol"],
                    meta["function"],
                    self._api_key,
                    start,
                    end,
                    **meta.get("extra", {}),
                )
                if len(rows) < 2:
                    _log.warning("AlphaVantage: insufficient data for %s (%d rows) — skipped", iid, len(rows))
                    continue
                last_close, change = _compute_1d_change(rows, meta["change_unit"])
                snapshots.append(MarketSnapshot(
                    as_of=as_of,
                    instrument_id=iid,
                    display_name=meta["display_name"],
                    asset_class=meta["asset_class"],
                    region=meta["region"],
                    last_price_or_level=round(last_close, 6),
                    one_day_change=round(change, 4),
                    one_day_change_unit=meta["change_unit"],
                    source="alpha_vantage",
                    source_url=_AV_BASE,
                    freshness_status=FreshnessStatus.FRESH,
                ))
            except Exception as exc:
                _log.warning("AlphaVantage fetch failed for %s: %s", iid, exc)
        return snapshots


class DatabentoMarketProvider:
    """Live provider for FESX and DE10Y via Databento XEUR.EOBI continuous contracts."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def fetch_watchlist(self, instruments: list[str], as_of: datetime) -> list[MarketSnapshot]:
        end = as_of.date()
        start = end - timedelta(days=_LIVE_LOOKBACK_DAYS)
        snapshots: list[MarketSnapshot] = []
        for iid in instruments:
            meta = _DB_INSTRUMENT_META.get(iid)
            if meta is None:
                _log.warning("Databento: unknown instrument %s — skipped", iid)
                continue
            try:
                rows = _db_fetch_daily_ohlcv(
                    meta["dataset"],
                    meta["symbol"],
                    self._api_key,
                    start,
                    end,
                    price_to_yield=meta["price_to_yield"],
                )
                if len(rows) < 2:
                    _log.warning("Databento: insufficient data for %s (%d rows) — skipped", iid, len(rows))
                    continue
                last_close, change = _compute_1d_change(rows, meta["change_unit"])
                snapshots.append(MarketSnapshot(
                    as_of=as_of,
                    instrument_id=iid,
                    display_name=meta["display_name"],
                    asset_class=meta["asset_class"],
                    region=meta["region"],
                    last_price_or_level=round(last_close, 6),
                    one_day_change=round(change, 4),
                    one_day_change_unit=meta["change_unit"],
                    source="databento",
                    freshness_status=FreshnessStatus.FRESH,
                ))
            except Exception as exc:
                _log.warning("Databento fetch failed for %s: %s", iid, exc)
        return snapshots


class FredMarketProvider:
    """Live provider for HY_OAS via FRED REST API."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def fetch_watchlist(self, instruments: list[str], as_of: datetime) -> list[MarketSnapshot]:
        end = as_of.date()
        start = end - timedelta(days=_LIVE_LOOKBACK_DAYS)
        snapshots: list[MarketSnapshot] = []
        for iid in instruments:
            meta = _FRED_INSTRUMENT_META.get(iid)
            if meta is None:
                _log.warning("FRED: unknown instrument %s — skipped", iid)
                continue
            try:
                rows = _fred_fetch_series(meta["series_id"], self._api_key, start, end)
                if len(rows) < 2:
                    _log.warning("FRED: insufficient data for %s (%d rows) — skipped", iid, len(rows))
                    continue
                last_close, change = _compute_1d_change(rows, meta["change_unit"])
                level_mult = meta.get("level_multiplier", 1)
                snapshots.append(MarketSnapshot(
                    as_of=as_of,
                    instrument_id=iid,
                    display_name=meta["display_name"],
                    asset_class=meta["asset_class"],
                    region=meta["region"],
                    last_price_or_level=round(last_close * level_mult, 6),
                    one_day_change=round(change, 4),
                    one_day_change_unit=meta["change_unit"],
                    source="fred",
                    source_url=_FRED_BASE,
                    freshness_status=FreshnessStatus.FRESH,
                ))
            except Exception as exc:
                _log.warning("FRED fetch failed for %s: %s", iid, exc)
        return snapshots


class YfinanceMarketProvider:
    """Live provider for VIX and MOVE via yfinance. Always low_reliability."""

    def fetch_watchlist(self, instruments: list[str], as_of: datetime) -> list[MarketSnapshot]:
        end = as_of.date()
        start = end - timedelta(days=_LIVE_LOOKBACK_DAYS)
        snapshots: list[MarketSnapshot] = []
        for iid in instruments:
            meta = _YF_INSTRUMENT_META.get(iid)
            if meta is None:
                _log.warning("yfinance: unknown instrument %s — skipped", iid)
                continue
            try:
                rows = _yf_fetch_daily(meta["symbol"], start, end)
                if len(rows) < 2:
                    _log.warning("yfinance: insufficient data for %s (%d rows) — skipped", iid, len(rows))
                    continue
                last_close, change = _compute_1d_change(rows, meta["change_unit"])
                snapshots.append(MarketSnapshot(
                    as_of=as_of,
                    instrument_id=iid,
                    display_name=meta["display_name"],
                    asset_class=meta["asset_class"],
                    region=meta["region"],
                    last_price_or_level=round(last_close, 6),
                    one_day_change=round(change, 4),
                    one_day_change_unit=meta["change_unit"],
                    source="yfinance",
                    freshness_status=FreshnessStatus.LOW_RELIABILITY,
                    warning="yfinance data marked low_reliability",
                ))
            except Exception as exc:
                _log.warning("yfinance fetch failed for %s: %s", iid, exc)
        return snapshots


def fetch_live_market(settings: "Settings", vol_params: dict) -> list[MarketSnapshot]:
    """
    Compose all four live providers and apply move detection.

    Credentials are read from settings.creds. Per-instrument failures are
    logged as warnings and skipped; a hard provider failure propagates up
    so the 5.F cache-fallback wrapper can recover.
    """
    as_of = datetime.now(tz=ZoneInfo(settings.app.timezone))

    snapshots: list[MarketSnapshot] = (
        AlphaVantageMarketProvider(settings.creds.alpha_vantage_api_key).fetch_watchlist(AV_INSTRUMENTS, as_of)
        + DatabentoMarketProvider(settings.creds.databento_api_key).fetch_watchlist(DB_INSTRUMENTS, as_of)
        + FredMarketProvider(settings.creds.fred_api_key).fetch_watchlist(FRED_INSTRUMENTS, as_of)
        + YfinanceMarketProvider().fetch_watchlist(YFINANCE_INSTRUMENTS, as_of)
    )
    return detect_moves(snapshots, vol_params)


# ── Phase F: Cache fallback ───────────────────────────────────────────────────

def cache_market(snapshots: list[MarketSnapshot], cache_dir: Path, as_of: date) -> None:
    """Write snapshots to <cache_dir>/market/latest.json and market_YYYY-MM-DD.json."""
    market_dir = Path(cache_dir) / "market"
    market_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        [s.model_dump(mode="json") for s in snapshots],
        indent=2,
        default=str,
    )
    (market_dir / "latest.json").write_text(payload, encoding="utf-8")
    (market_dir / f"market_{as_of.isoformat()}.json").write_text(payload, encoding="utf-8")


def load_cached_market(cache_dir: Path) -> list[MarketSnapshot]:
    """Read <cache_dir>/market/latest.json. Raises FileNotFoundError if absent."""
    latest = Path(cache_dir) / "market" / "latest.json"
    if not latest.exists():
        raise FileNotFoundError(
            f"No cached market data at {latest}. "
            "Live fetch failed and no cache exists — cannot continue."
        )
    raw = json.loads(latest.read_text(encoding="utf-8"))
    return [MarketSnapshot.model_validate(snap) for snap in raw]


def fetch_live_market_with_cache(
    settings: "Settings",
    vol_params: dict,
) -> list[MarketSnapshot]:
    """
    Fetch live market data, cache on success, fall back to stale cache on failure.

    Raises RuntimeError if live fetch fails and no cache exists.
    Stale snapshots are returned with freshness_status=STALE_CACHE and a warning string
    so the pipeline can propagate them into RunMetadata.warnings.
    """
    cache_dir = REPO_ROOT / settings.app.cache_dir
    today = datetime.now(tz=ZoneInfo(settings.app.timezone)).date()

    try:
        snapshots = fetch_live_market(settings, vol_params)
        cache_market(snapshots, cache_dir, today)
        return snapshots
    except Exception as exc:
        _log.warning("Live market fetch failed (%s); trying cache fallback.", exc)
        try:
            stale = load_cached_market(cache_dir)
        except FileNotFoundError:
            raise RuntimeError(
                f"Live market fetch failed ({exc}) and no cached data exists at "
                f"{cache_dir / 'market' / 'latest.json'}. Cannot continue."
            ) from exc
        warning_msg = f"Using stale cached market data — live fetch failed: {exc}"
        return [
            s.model_copy(update={"freshness_status": FreshnessStatus.STALE_CACHE, "warning": warning_msg})
            for s in stale
        ]
