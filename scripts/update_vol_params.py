"""Fetch 252-day price history per instrument and compute 1D standard deviations.

Run monthly (or whenever the vol regime has shifted materially):
    make update-vol-params

Writes app/config/vol_params.yaml and prints a summary table.
Requires ALPHA_VANTAGE_API_KEY, DATABENTO_API_KEY, FRED_API_KEY in .env.
yfinance instruments (^VIX, ^MOVE) need no key. ^MOVE falls back to a
hardcoded 4.5% SD only if yfinance returns no data.
"""

from __future__ import annotations

import logging
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path

import yaml
from dotenv import load_dotenv

# ── bootstrap: make `app` importable when run as a script ────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

load_dotenv(dotenv_path=REPO_ROOT / ".env")

from app.data.market import (  # noqa: E402
    _av_fetch_daily,
    _db_fetch_daily_ohlcv,
    _fred_fetch_series,
    _yf_fetch_daily,
)
from app.settings import Credentials  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
_log = logging.getLogger(__name__)

# ── constants ─────────────────────────────────────────────────────────────────

MIN_OBSERVATIONS = 200
LOOKBACK_DAYS = 252
OUTPUT_PATH = REPO_ROOT / "app" / "config" / "vol_params.yaml"

# (instrument_id, change_unit, fetch_spec)
# fetch_spec is a callable(start, end, creds) -> list[dict]
# Built lazily below after credentials are loaded.

_PCT_INSTRUMENTS = [
    "SPY", "QQQ", "UUP", "USDJPY", "EURUSD", "USDCNH",
    "GOLD", "WTI", "BRENT", "COPPER", "BTC", "FESX", "VIX",
]
_BPS_INSTRUMENTS = ["US2Y", "US10Y", "DE10Y", "HY_OAS"]
# MOVE uses hardcoded fallback — omitted from live fetch


def _build_fetchers(creds: Credentials) -> dict[str, tuple[str, object]]:
    """Return {instrument_id: (unit, fetch_fn)} where fetch_fn(start, end) -> list[dict]."""
    av = creds.alpha_vantage_api_key
    db = creds.databento_api_key
    fred = creds.fred_api_key

    def av_daily(symbol, function, **kw):
        return lambda s, e: _av_fetch_daily(symbol, function, av, s, e, **kw)

    def db_daily(dataset, symbol, **kw):
        return lambda s, e: _db_fetch_daily_ohlcv(dataset, symbol, db, s, e, **kw)

    def fred_series(series_id):
        return lambda s, e: _fred_fetch_series(series_id, fred, s, e)

    def yf_daily(symbol):
        return lambda s, e: _yf_fetch_daily(symbol, s, e)

    return {
        # Equities / proxies
        "SPY":    ("%", av_daily("SPY",  "TIME_SERIES_DAILY")),
        "QQQ":    ("%", av_daily("QQQ",  "TIME_SERIES_DAILY")),
        "UUP":    ("%", av_daily("UUP",  "TIME_SERIES_DAILY")),
        # FX
        "USDJPY": ("%", av_daily("USDJPY", "FX_DAILY", from_symbol="USD", to_symbol="JPY")),
        "EURUSD": ("%", av_daily("EURUSD", "FX_DAILY", from_symbol="EUR", to_symbol="USD")),
        "USDCNH": ("%", av_daily("USDCNH", "FX_DAILY", from_symbol="USD", to_symbol="CNH")),
        # Commodities
        "GOLD":   ("%", av_daily("GLD",  "TIME_SERIES_DAILY")),  # GLD ETF proxy
        "WTI":    ("%", av_daily("",     "WTI")),
        "BRENT":  ("%", av_daily("",     "BRENT")),
        "COPPER": ("%", av_daily("CPER", "TIME_SERIES_DAILY")),  # CPER ETF proxy; AV COPPER fn is monthly
        # Crypto
        "BTC":    ("%", av_daily("BTC",  "DIGITAL_CURRENCY_DAILY")),
        # Rates (bps — diff, not pct)
        "US2Y":   ("bps", av_daily("", "TREASURY_YIELD", maturity="2year")),
        "US10Y":  ("bps", av_daily("", "TREASURY_YIELD", maturity="10year")),
        # European futures (Databento)
        "FESX":   ("%",   db_daily("XEUR.EOBI", "FESX.c.0")),
        "DE10Y":  ("bps", db_daily("XEUR.EOBI", "FGBL.c.0", price_to_yield=True)),
        # Credit
        "HY_OAS": ("bps", fred_series("BAMLH0A0HYM2")),
        # Vol (yfinance)
        "VIX":    ("%", yf_daily("^VIX")),
        "MOVE":   ("%", yf_daily("^MOVE")),
    }


def _compute_sd(rows: list[dict], unit: str, instrument_id: str) -> float | None:
    """Return 1D SD from a list of {"date", "close"} rows, or None if insufficient data."""
    closes = [r["close"] for r in rows]
    n = len(closes)

    if n < MIN_OBSERVATIONS:
        warnings.warn(
            f"{instrument_id}: only {n} observations (need {MIN_OBSERVATIONS}); skipping.",
            stacklevel=2,
        )
        return None

    if unit == "%":
        # pct_change: (close[i] / close[i-1] - 1) * 100
        changes = [
            (closes[i] / closes[i - 1] - 1) * 100
            for i in range(1, n)
            if closes[i - 1] != 0
        ]
    else:
        # AV TREASURY_YIELD and FRED series return values in percent (e.g., 4.52 = 4.52%).
        # Multiply diff by 100 to convert to true basis points (0.05 pct-pt → 5 bps).
        changes = [(closes[i] - closes[i - 1]) * 100 for i in range(1, n)]

    m = len(changes)
    if m < MIN_OBSERVATIONS - 1:
        warnings.warn(
            f"{instrument_id}: only {m} change observations after diff; skipping.",
            stacklevel=2,
        )
        return None

    mean = sum(changes) / m
    variance = sum((x - mean) ** 2 for x in changes) / (m - 1)  # ddof=1
    return variance ** 0.5


def main() -> None:
    creds = Credentials()

    missing = []
    if not creds.alpha_vantage_api_key:
        missing.append("ALPHA_VANTAGE_API_KEY")
    if not creds.databento_api_key:
        missing.append("DATABENTO_API_KEY")
    if not creds.fred_api_key:
        missing.append("FRED_API_KEY")
    if missing:
        print(f"ERROR: missing credentials: {', '.join(missing)}")
        print("Set them in .env and retry.")
        sys.exit(1)

    # Use yesterday as end: Databento's free tier excludes same-day XEUR.EOBI data.
    end = date.today() - timedelta(days=1)
    # Fetch ~1.5x the target window to account for weekends/holidays
    start = end - timedelta(days=int(LOOKBACK_DAYS * 1.5))

    fetchers = _build_fetchers(creds)
    params: dict[str, dict] = {}
    rows_table: list[tuple] = []  # for summary printout

    for instrument_id, (unit, fetch_fn) in fetchers.items():
        print(f"  fetching {instrument_id} ...", flush=True)
        try:
            rows = fetch_fn(start, end)
        except Exception as exc:
            warnings.warn(f"{instrument_id}: fetch failed ({exc}); skipping.")
            rows_table.append((instrument_id, unit, 0, "SKIPPED", "fetch error"))
            continue

        sd = _compute_sd(rows, unit, instrument_id)
        if sd is None:
            rows_table.append((instrument_id, unit, len(rows), "SKIPPED", "insufficient data"))
            continue

        sd_rounded = round(sd, 4)
        params[instrument_id] = {"sd_1d": sd_rounded, "unit": unit}
        rows_table.append((instrument_id, unit, len(rows), f"{sd_rounded}", "OK"))

    # If MOVE fetch failed, fall back to hardcoded value rather than omitting it
    if "MOVE" not in params:
        params["MOVE"] = {"sd_1d": 4.5, "unit": "%", "note": "hardcoded fallback — yfinance unavailable"}
        rows_table.append(("MOVE", "%", 0, "4.5", "hardcoded fallback"))

    output = {
        "generated_at": end.isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "params": params,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as fh:
        fh.write(
            "# Generated by scripts/update_vol_params.py"
            " — run `make update-vol-params` monthly.\n"
            "# MOVE uses a hardcoded fallback; no reliable API source for historical data.\n"
        )
        yaml.dump(output, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)

    try:
        display_path = OUTPUT_PATH.relative_to(REPO_ROOT)
    except ValueError:
        display_path = OUTPUT_PATH
    print(f"\nWrote {display_path}\n")
    print(f"{'Instrument':<12} {'Unit':<5} {'Obs':>5}  {'SD':>8}  Status")
    print("-" * 52)
    for inst, unit, obs, sd_str, status in rows_table:
        print(f"{inst:<12} {unit:<5} {obs:>5}  {sd_str:>8}  {status}")


if __name__ == "__main__":
    main()
