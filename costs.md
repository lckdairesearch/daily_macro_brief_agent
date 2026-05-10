# costs.md — Daily Macro Brief Agent

## Daily Run Cost Summary

| Service | Role | Daily | Monthly |
|---|---|---|---|
| OpenAI GPT-5.x (Azure) | Scouts, synthesis, chart selection | $1.00–2.00 | $22–44 |
| XAI Grok | X/Twitter social media scout | ~$0.02 | ~$0.50 |
| Databento | Futures bars (Bund, Copper, WTI, Brent) | ~$0.02 | ~$0.50 |
| Alpha Vantage | Equities, FX, commodities, yields, BTC | $0 | $0 (free tier) |
| FRED | HY OAS spread | $0 | $0 |
| Taddy | Podcast discovery | $0 | $0 (free tier) |
| Postmark | Email delivery | $0 | $0 (free tier) |
| Cloudflare R2 | Chart hosting + artifact storage | $0 | $0 (free tier) |
| GitHub Actions | CI/CD | $0 | $0 (free tier) |
| **Total** | | **~$1.05–2.10/day** | **~$23–45/mo** |

## Key Notes

- **OpenAI is the only meaningful cost.** The main levers are model tier selection
  (GPT-5.5 reserved for synthesis/review; GPT-5.4 for scouts and chart selection)
  and reducing web search tool calls per scout. Switching scouts to a mini-class
  model is the clearest path to a 50–70% cost reduction.
- **All other services are effectively free** at current scale. The Databento
  $125 signup credit covers years of normal daily OHLCV bar pulls.
- **Sample mode costs nothing.** Fixture-backed runs use no live APIs and incur
  no token spend — useful for testing and reviewer evaluation.
- **Actual billed cost is lower than the upper bound.** Run metadata captures
  per-run token usage; the $2/day figure reflects a heavy search day.
