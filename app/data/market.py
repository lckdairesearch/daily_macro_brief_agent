"""Market data fetching.

Providers:
  - Alpha Vantage (primary): equities, FX, commodities, US yields, BTC, DXY proxy
  - Databento (supplemental): Euro Stoxx 50 (FESX), German Bund (FGBL), VIX (VX)
  - FRED: HY OAS (BAMLH0A0HYM2)
  - yfinance: ^MOVE (low_reliability)

Falls back to latest cached snapshot with a warning if live calls fail.

To be implemented in Step 3.
"""

# Stub
