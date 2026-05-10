# costs.md — Daily Macro Brief Agent

## Daily Run Cost Summary

| Service | Role | Daily | Monthly |
|---|---|---|---|
| OpenAI GPT-5.x (Azure) | Scouts, synthesis, chart selection | $1.00–2.00 | $22–44 |
| Alpha Vantage (personal) | Equities, FX, commodities, yields, BTC | ~$2.27 | $50 (flat) |
| XAI Grok | X/Twitter social media scout | ~$0.02 | ~$0.50 |
| Databento | Futures bars (Bund, Copper, WTI, Brent) | ~$0.02 | ~$0.50 |
| FRED | HY OAS spread | $0 | $0 |
| Taddy | Podcast discovery | $0 | $0 (free tier) |
| Postmark | Email delivery | $0 | $0 (free tier) |
| Cloudflare R2 | Chart hosting + artifact storage | $0 | $0 (free tier) |
| GitHub Actions | CI/CD | $0 | $0 (free tier) |
| **Total** | *(existing market data subscription)* | **~$1.05–2.10** | **~$23–45** |
| **Total** | *(with Alpha Vantage)* | **~$3.30–4.30** | **~$73–95** |

## Key Notes

- **Alpha Vantage was chosen** for its accuracy, ease of use, and Nasdaq-backed
  reliability. The personal plan ($50/mo) removes free-tier rate limits. If the
  firm already subscribes to Bloomberg, Refinitiv, or another data service, the
  market data module can be adapted to use existing credentials — dropping this
  cost entirely.
- **OpenAI is the dominant recurring cost in both scenarios.** The main levers
  are model tier selection (GPT-5.5 reserved for synthesis/review; GPT-5.4 for
  scouts and chart selection) and reducing web search tool calls per scout.
  Switching scouts to a mini-class model is the clearest path to a 50–70%
  LLM cost reduction.
- **All other services are effectively free** at current scale. The Databento
  $125 signup credit covers years of normal daily OHLCV bar pulls.
- **Sample mode costs nothing.** Fixture-backed runs use no live APIs and incur
  no token spend — useful for testing and reviewer evaluation.
