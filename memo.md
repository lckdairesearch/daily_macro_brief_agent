# Memo — Daily Macro Brief Agent

## Design Tradeoffs

**LLM stack — OpenAI via Azure.** Azure OpenAI was chosen as the primary provider because it offers a plug-and-play enterprise API available and compliant in Hong Kong, with no data-residency friction. Models are tiered by task: GPT-5.5 (higher reasoning) for synthesis and editorial review; GPT-5.4 (faster, cheaper) for scout extraction and chart selection. This keeps quality high where it matters and cost controlled where it does not.

**X scout — XAI Grok.** Grok's native `x_search` tool is the only LLM interface with direct X/Twitter search capability, making it the best option for surfacing real-time social media signal on macro themes. If the XAI API is unavailable in a given region, the X scout is skipped automatically and the pipeline continues — there is no hard dependency on it.

**Data sources.** Alpha Vantage covers equities, FX, commodities, yields, and crypto within the free tier (≤25 calls/day). Databento is used only where futures precision matters (Bund yield, copper). Investing.com calendar and yfinance are acknowledged as unofficial or undocumented endpoints — acceptable for a prototype, flagged as reliability risks for production.

**LLM as synthesis tool, not fact source.** Market numbers, calendar events, and consensus values come exclusively from APIs. The LLM receives typed, structured inputs and is never asked to recall or generate market data. A deterministic validator runs after every write and gates live delivery on critical failures.

---

## Portfolio Assumptions

Missing position data was filled with a plausible family office book oriented around the PM's stated interests in metals and agricultural trade:

| Position | Instruments | Direction | Thesis |
|---|---|---|---|
| Metals complex | Gold, Silver, Copper | Long | Monetary debasement hedge |
| Long-end US rates | 30Y / 10Y Treasuries | Short | Fiscal dominance, structural inflation |
| FX | USD concentration | Diversify | De-dollarization risk |
| Agriculture | Wheat, Corn, Soybeans | Long | Food security, climate disruption, agricultural cost |

---

## 3 V2 Features

**1. Richer consensus enrichment.** The current build pulls consensus forecasts directly from Investing.com — convenient but limited to whatever their forecast field contains. A v2 consensus enrichment agent would query additional platforms (Bloomberg consensus, FactSet, broker survey aggregators) to fill in missing values and cross-check quality, giving the PM a more complete picture before key data releases.

**2. Telegram delivery channel.** A Telegram bot would let the PM receive the brief on mobile with no inbox latency. Sections could be split into individual messages for easier scanning — dashboard, three things, calendar — with the chart embedded inline. The delivery abstraction already in the codebase makes this a contained addition.

**3. Polymarket major event tracker.** Polymarket is a leading on-chain prediction market with real-money probabilities on macro events — Fed rate decisions, inflation prints, election outcomes, geopolitical developments. A Polymarket scout would surface the crowd's implied probability on upcoming events and allow the brief to explicitly compare the market's view against the house view. This is especially useful for the contrarian corner: if Polymarket prices 80% odds on a Fed hold while the house view is a cut, that gap is the signal worth surfacing.

---

## Where This Goes With 1 Month Full-Time

- **Week 1 — debug and cost optimization.** The MVP runs at ~$1.50/day. The immediate priority is prompt refinement and model tier tuning — moving scout extraction to a mini-class model while keeping GPT-5.5 only for synthesis could cut costs 50–70% with minimal quality loss.
- **Dynamic portfolio integration.** The current book lives in a static YAML config. A natural upgrade is direct file ingestion — connect an Excel sheet or internal document so position updates flow in automatically. With live position data, the brief can move from narrative book impact to directional exposure commentary and eventually model estimated P&L impact of overnight moves.
- **Operational stability.** Turn the MVP into something that runs reliably unattended: better source health checks, clearer failure alerts, more robust fallback behavior, and a friendlier email list management interface for non-developer users.
- **Backtest sprint.** Run the pipeline across 10–15 historical dates, review output quality against known market events, and iterate on prompts and ranking weights. This is the fastest path to improving brief quality beyond what unit tests can verify.
- **Telegram delivery** (achievable within the month given the delivery abstraction already in place).

---

## Actual Hours

~18 hours total. A working prototype with live market data, scouts, synthesis, validation, and email delivery was running in under 15 hours. The remaining time went into prompt fine-tuning, fixture hardening, and the LLM cost telemetry layer.
