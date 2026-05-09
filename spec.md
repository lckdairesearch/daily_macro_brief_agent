# spec.md — Daily Macro Brief Agent

## 1. Purpose

Build a Python prototype that automatically produces and sends a concise Daily Macro Brief for a macro-trained portfolio manager before the market day starts.

The product answer is:

> What changed overnight, and so what for our book?

It should select, synthesize, and explain why a small number of market moves, events, and source materials matter for an assumed macro book. The agent must not be a news regurgitator.

V1 is optimized for a strong case-study demo: runnable, scheduled, polished output, real market/calendar data where practical, clear assumptions, and clean enough structure for extension.

## 2. Reader profile

Primary reader: a macro-trained PM at a small investment firm.

Reader needs:

- Very high signal density before the market opens.
- Direct market implications, not generic commentary.
- Selection and point of view, not a second-hand summary of Bloomberg/FT headlines.
- Concise language and clear tables.
- Explicit distinction between confirmed data, sourced interpretation, and assumed portfolio/theme context.

Tone:

- Professional, direct, concise.
- No emojis.
- Avoid filler.
- Prefer exact numbers and short explanations.
- When no fresh catalyst is confirmed, say: `no confirmed fresh catalyst`.

## 3. Delivery target

V1 should support:

- Scheduled weekday run via GitHub Actions.
- Manual local run via CLI/Makefile.
- Email delivery as the default delivery channel.
- HTML output for email.
- Plain-text fallback output.
- Saved sample outputs in `outputs/`.

Agreed schedule:

- Send time: 07:15 HKT, Monday–Friday.
- Data cutoff: approximately 06:45–07:00 HKT.
- GitHub Actions cron equivalent: `15 23 * * 0-4` UTC for Monday–Friday HKT mornings.

Delivery provider preference:

- Use Postmark as the V1 email provider.
- Keep Azure Communication Services as a replaceable future option.
- Keep the delivery layer replaceable.
- Do not hard-code credentials.

Delivery behavior:

- Sample and dry-run sends go to `POSTMARK_MAINTAINER_EMAIL` for maintainer review.
- Live sends go to the configured production recipients (`POSTMARK_TO_EMAIL` accepts a comma-separated list) when `ENABLE_EMAIL_DELIVERY=true`.
- Validation warnings do not suppress delivery. Minor validation failures render as a warning banner at the top of the email so recipients can judge the output quality themselves.
- Critical validation failures (missing required sections, invented numbers without source metadata) hard-fail the run and suppress delivery entirely.
- Production email delivery is off by default. Set `ENABLE_EMAIL_DELIVERY=true` to enable live mode delivery.

## 4. Core brief sections

The generated brief must contain the following six required sections in this order unless later revised.

The email may include an optional `Overnight Book Impact` note before the required
sections. This note is narrative-only in V1 because the prototype does not have
real position sizes, hedge ratios, or P&L sensitivities. It must not include
estimated bps impact, portfolio delta, hedge offsets, or invented exposure numbers.

### 4.1 Overnight market dashboard

Purpose: show US/EU close versus prior day across key macro assets.

Format:

- Table, not chart.
- Include 1D move and optionally 5D move.
- Include freshness timestamp/source.
- Include only relevant instruments from configured watchlists.

Initial asset coverage:

- Equities: S&P 500, Nasdaq 100, Euro Stoxx 50 or equivalent ETF/index proxies.
- Rates: US 2Y, US 10Y, German 10Y where available.
- FX: DXY, USD/JPY, EUR/USD, CNH proxy if available.
- Commodities: gold, silver, WTI or Brent oil, copper.
- Credit/risk: VIX, HY OAS or ETF proxy, MOVE if available.
- Crypto: BTC.

Inclusion rules for notable moves:

- Include if absolute 1D move is at least one configured 1D standard deviation.
- Include a group move if at least two related instruments move in the same direction and at least one breaches the 1D standard deviation threshold.
- Include a single proxy if its 1D move breaches the threshold.
- Include 5D move only when it changes interpretation.
- For large moves, check recent/today catalyst sources using Asia/HKT or Asia/Tokyo dating conventions.
- If no fresh catalyst is confirmed, state `no confirmed fresh catalyst`.

### 4.2 The three things that matter today

Purpose: force selection.

Format:

- Exactly three items when enough evidence exists; fewer only if data/source failures make three low-quality.
- Each item should be no more than 80 words.
- Each item must include a clear `so what` sentence for the assumed book.
- Each item should reference source evidence or market data.

Selection priority:

1. Market moves with confirmed catalysts.
2. Upcoming calendar events with high portfolio relevance.
3. Central bank, official data, or research items that alter the day’s risk framing.
4. Non-mainstream source items with a clear thesis and evidence.

### 4.3 Today’s calendar

Purpose: show key data and events across Asia, Europe, and the US.

Format:

- Table grouped by session.
- Include time, region/country, event, importance, consensus/previous if available, and why it matters.
- Treat Investing.com `forecast` values as consensus when present.
- Include central bank speakers and key policy events.

V1 source preference:

- Use Investing.com’s ungated backend economic-calendar endpoint for calendar coverage, cached daily as `calendar_YYYY-MM-DD.json`.
- Use Investing.com forecast fields as the calendar consensus when present; these forecasts may be missing for some events.
- If a high-importance event is missing a forecast/consensus value, invoke a lightweight consensus-enrichment scout to search public online sources for consensus, capture the source URL, and attach confidence metadata.
- If no reliable consensus source is found, leave consensus blank and flag `missing_consensus`; do not infer or invent the value.
- Treat this as a pragmatic prototype source, not an official data partnership or guaranteed long-term API.
- Use polite request behavior: cache results, avoid repeated calls, and do not abuse the endpoint.
- Official central bank/statistical sources may supplement high-importance events where practical.
- Filter by country/session/importance after ingestion.

### 4.4 One chart worth seeing

Purpose: include one visual that adds insight beyond the tables.

Format:

- One generated or scraped chart.
- Caption no more than 30 words.
- Must be tied to one of the day’s key themes.
- Save as `outputs/sample_chart.png` for sample run.

Initial chart candidates:

- Cross-asset risk move.
- Rates/FX divergence.
- Gold versus real yields/proxy.
- VIX/MOVE stress comparison.
- Calendar surprise or policy path chart if data is available.

### 4.5 Theme radar

Purpose: provide 1–3 deeper content summaries tied to assumed positions/themes.

Source preference:

- Favor non-mainstream or primary material over generic financial media.
- Examples: central bank speeches, official releases, research notes, Substack-style longform, podcasts, X threads, buy-side letters.
- Mainstream news may be used to verify catalysts but should not dominate this section.

Format for each item:

- Topic/domain label, such as `Metals`, `Rates`, `Monetary Policy`, `Energy`, `Agriculture`, `FX`, or `Credit`.
- Linked title.
- Source metadata and URL must be retained in the structured draft, even if the visual template does not show a separate source label.
- 60–100 word summary of the author’s thesis and evidence, not just the abstract.
- One line: `So what:`.

### 4.6 Contrarian corner

Purpose: surface one narrative the market may not be pricing but is worth watching.

Format:

- 50–100 words.
- Must be explicitly framed as a watch item, not a forecast certainty.
- Should be supported by at least one source or cross-asset observation.

## 5. Assumed PM book and theme configuration

Some portfolio and theme context is unavailable. For V1, encode assumptions in `app/config/portfolio.yaml` and `app/config/themes.yaml`, and state them in the memo.

Initial assumed book:

- Long metals complex, including gold, silver, and copper.
- Long agriculture futures / grains complex.
- Short long-term US duration.
- Currency diversification overlay to reduce USD concentration risk.
- Macro risk book sensitive to real rates, fiscal dominance, USD policy regime, resource scarcity, food security, geopolitical fragmentation, and credit stress.

Initial themes:

- Monetary debasement and fiscal dominance.
- Metals complex as monetary hedge.
- Food security and agricultural supply disruption.
- De-dollarization and currency diversification.
- Resource scarcity and population pressure.
- Geopolitical fragmentation and trade disruption.
- Credit stress and risk appetite.

These assumptions must be configurable and easy to replace.

## 6. Data and source requirements

### 6.1 Hard rule: no invented market data

The LLM must not generate prices, returns, yields, spreads, calendar values, or consensus numbers.

Market and calendar numbers must come from:

- APIs.
- Scraping.
- Cached snapshots.
- Explicit sample fixtures for local tests/demo mode.

Any sample fixture must be clearly labeled as sample data. The system may make reasonable assumptions about unavailable portfolio positions/themes, but not about market prices.

### 6.2 Market data sources

Confirmed V1 provider stack:

| Provider | Instruments covered | Key |
|---|---|---|
| **Alpha Vantage** | SPY (S&P 500 proxy), QQQ (Nasdaq proxy), UUP (DXY proxy), USD/JPY, EUR/USD, USD/CNH, Gold spot, Silver spot, WTI, Brent, BTC, US 2Y yield, US 10Y yield | `ALPHA_VANTAGE_API_KEY` |
| **Databento** | FESX/Eurex (Euro Stoxx 50), FGBL/Eurex (German 10Y Bund proxy/yield derivation), HG/CME copper front-month | `DATABENTO_API_KEY` |
| **FRED** | BAMLH0A0HYM2 (ICE BofA HY OAS) | `FRED_API_KEY` (free) |
| **yfinance** | `^VIX` (CBOE VIX spot), `^MOVE` (MOVE index) — no API key | None |

Databento is used for instruments Alpha Vantage cannot cover cleanly in daily form: Euro Stoxx 50 close via FESX, German Bund proxy via FGBL with an approximate yield conversion, and copper via HG front-month because the Alpha Vantage copper endpoint was not suitable for daily move detection. Gold and silver use Alpha Vantage `GOLD_SILVER_HISTORY`. VIX and MOVE are sourced from yfinance.

The implementation should hide provider-specific logic behind small wrappers so a source can be replaced without rewriting the pipeline.

### 6.3 Calendar sources

Preferred V1:

- Investing.com ungated backend economic-calendar endpoint.
- Investing.com forecast fields as consensus where present.
- Consensus-enrichment scout for high-importance events where Investing.com forecast is missing.
- Daily cache file.
- Post-ingestion filtering by country/session/importance.
- Clear source metadata on every calendar event, including separate consensus source metadata when the consensus is enriched from another public source.

Important caveat:

- This is not an official paid data feed and should be treated as a prototype dependency.
- Investing.com forecasts should be treated as useful prototype consensus values, not guaranteed institutional-grade consensus data.
- The app must use caching and conservative request volume.
- Future production versions should subscribe to a paid/official economic-calendar API service with licensed consensus data.

Fallback:

- Static sample calendar fixture for tests/demo.
- Manual YAML override for important known events.

### 6.4 Discovery sources

The discovery layer has five scouts. All use LLM-prompted approaches in V1.

**News, central bank, research scouts** (`news.py`, `central_bank.py`, `research.py`):
Use OpenAI GPT-5 (latest) with web search capability. Each scout is prompted to search for relevant content, extract structured candidate evidence, and return source URLs. Prompts live in `app/llm/prompts/scouts/`.

**X scout** (`x.py`):
Uses `xai-sdk` with a Grok model that supports the `x_search` Agent Tool to retrieve live X posts tied to flagged market moves, calendar events, and portfolio themes. It bypasses the LiteLLM wrapper because LiteLLM cannot route xAI server-side Agent Tools. Post-processing drops empty cards and URLs that do not match a real X post URL pattern. Direct X API ingestion is deferred to V2.

**Podcast scout** (`podcast.py`):
1. Query Listen Notes API (free tier) for episodes published in the last 24 hours, from curated channels or topic search.
2. LLM ranks and filters candidates against macro themes, portfolio assumptions, and watchlist. Gate: "Is there a tradeable or portfolio-relevant thesis here?"
3. For matched episodes, use the OpenAI Responses API with `web_search_preview` to recall public show notes, transcripts, summaries, or related pages. V1 does not perform audio transcription or Gemini video/audio analysis.
4. Extract from source-backed web recall: speaker's core thesis, evidence/argument, why it matters now, one-line "what this means for our book."
5. Output flows into Theme Radar.

Each discovered item should become a structured `EvidenceCard` before synthesis.

## 7. LLM usage boundaries

LLM is allowed for:

- Summarizing sourced text.
- Ranking and synthesizing candidate evidence.
- Drafting the final brief.
- Critic/validator pass on clarity, missing `so what`, unsupported claims, and formatting.

LLM is not allowed for:

- Inventing market data.
- Inventing calendar events.
- Inventing source links.
- Inventing consensus estimates.
- Making unsourced claims look sourced.

LLM outputs must be grounded in structured inputs:

- Market snapshots.
- Calendar events.
- Evidence cards.
- Portfolio/theme configuration.
- Explicit assumptions.

## 8. Core data contracts

Use typed Pydantic models for all pipeline boundaries. Canonical field definitions for `MarketSnapshot`, `CalendarEvent`, `EvidenceCard`, `BriefItem`, `BriefDraft`, and `RunMetadata` are maintained in `architecture.md §8`. Do not duplicate field lists here.

## 9. Functional requirements

### 9.1 Configuration

The app must load YAML configuration for:

- App runtime settings.
- Portfolio assumptions.
- Themes and watchlists.
- Sources and provider settings.
- Chart templates.

Secrets must come from environment variables, not YAML.

### 9.2 Pipeline

The main pipeline should:

1. Load settings and config.
2. Determine run window and data cutoff.
3. Fetch market data.
4. Fetch today’s calendar.
5. Enrich missing consensus values for high-importance calendar events when a reliable public source can be found.
6. Discover source items.
7. Convert source items into EvidenceCards.
8. Deduplicate and rank evidence.
9. Generate/synthesize brief sections.
10. Validate numbers, links, required sections, and formatting.
11. Render HTML/text outputs.
12. Save artifacts.
13. Deliver email if enabled.
14. Record cost/run metadata.

### 9.3 Validation

The validator must check:

- No missing required brief sections.
- No market numbers without source metadata.
- No invented links.
- No consensus values without source metadata unless they come directly from the cached Investing.com calendar payload.
- Three-things items are no more than 80 words.
- Theme radar summaries are 60–100 words.
- Chart caption is no more than 30 words.
- Contrarian corner is 50–100 words.
- Every `so what` item ties back to configured portfolio/themes.
- Failed sources are disclosed in run metadata.

### 9.4 Cost tracking

`costs.md` must document estimated daily run cost, including:

- LLM token usage.
- Market/calendar data provider cost assumptions.
- Hosting/scheduling cost.
- Email delivery cost.
- Any free-tier assumptions.

The app should log per-run token usage and estimated LLM cost where available.

## 10. Non-functional requirements

### 10.1 Reliability

- Local sample/demo mode must work without paid credentials.
- Production/live mode should use configured APIs/scrapers.
- Source failures should degrade gracefully where possible.
- A failed optional discovery source should not kill the entire run.
- If live market data is unavailable, fall back to the latest cached market snapshot with a clear warning in run metadata and the rendered brief.

### 10.2 Maintainability

- Keep modules small and single-purpose.
- Keep provider-specific code isolated.
- Keep prompts versioned.
- Keep configs explicit; avoid hidden defaults.
- Prefer composition over inheritance.
- Avoid premature platform/data-warehouse abstractions in V1.

### 10.3 Security and compliance

- Do not commit API keys or secrets.
- Use `.env.example` for required environment variables.
- Respect source terms, endpoint stability, and scraping/API constraints.
- Cache politely and avoid unnecessary repeated scraping or backend endpoint calls.
- Do not expose private portfolio assumptions as real client data; label assumptions as synthetic/demo.

### 10.4 Observability

Each run should save or log:

- Run ID.
- Data cutoff.
- Sources used.
- Failed sources.
- Output artifact paths.
- Token/cost estimate.
- Delivery status.

## 11. Repository structure

The canonical repository structure including `app/llm/`, `tests/fixtures/`, and all approved modules is maintained in `architecture.md §5`. Do not duplicate it here.

## 12. Tests and acceptance criteria

### 12.1 Minimum tests

- Config loading test.
- Market data normalization test using fixture data.
- Calendar parsing/filtering test using fixture data.
- Consensus-enrichment test for missing high-importance forecasts.
- Evidence deduplication test.
- Ranking test.
- Validator tests for word limits, unsupported numbers, missing links, and missing `so what`.
- Render test confirming HTML/text output includes all required sections.
- Smoke test for local demo mode.

### 12.2 Acceptance criteria

V1 is acceptable when:

- `make run-sample` generates `outputs/sample_brief.html`, `outputs/sample_brief.txt`, and `outputs/sample_chart.png`.
- `make test` passes.
- The brief includes all six required sections.
- Market numbers in live mode come from APIs/scraping/cached data, not LLM generation.
- The LLM receives structured context and produces grounded synthesis.
- The validator catches unsupported or malformed output.
- GitHub Actions can run the workflow on schedule.
- Email delivery can be toggled on/off by config.
- `costs.md` documents daily run cost assumptions.
- `memo/memo.md` explains design tradeoffs, assumed positions/themes, deferred features, one-month extension path, and actual hours spent.

## 13. Non-goals for V1

- No fully automated trading or portfolio rebalancing.
- No claim of investment advice.
- No training/fine-tuning pipeline.
- No full production data warehouse.
- No intraday tick database.
- No paid-news bypass or scraping of restricted content.
- No complex UI/dashboard.
- No Telegram delivery unless email is already complete.
- No broad autonomous agent that browses the web without source constraints and validation.

## 14. Future work

V1 focuses on a runnable, validated daily brief. Future versions would make the system more production-grade and more personalized to an actual PM workflow.

- **Licensed data and calendar feeds:** Replace prototype calendar ingestion with a paid/official economic-calendar provider and expand market coverage for rates, credit, commodities, and cross-asset proxies.

- **Better source connectors:** Add more reliable connectors for research, podcasts/transcripts, central-bank material, and X/social content, with stronger source archival and verification. For the podcast scout, add direct X API ingestion, better episode ranking, and more curated channel lists.

- **Two-phase admin/PM delivery:** After the 07:15 HKT send to admin, hold the PM send until 09:00 HKT unless the admin intervenes. Admin can edit the HTML/text directly or re-invoke the agent for a targeted rewrite pass. If no intervention by 09:00, the brief auto-sends to the PM. Requires a second scheduled job, state persistence, and an agent interaction loop.

- **Portfolio-specific risk mapping:** Move beyond synthetic book assumptions by allowing users to configure real exposures, sensitivity tags, thresholds, and “so what” mapping by position or theme.

- **PM-friendly configuration:** Keep YAML as the runtime source of truth, but add an Excel or lightweight UI layer so non-technical users can edit portfolio assumptions, watchlists, and themes safely.

- **Historical archive and learning loop:** Store prior runs, market moves, selected evidence, and PM feedback so the agent can learn what was useful and detect recurring themes over time.

- **More robust evaluation:** Build a small eval set for hallucination-sensitive macro writing, unsupported numbers, bad links, weak “so what” reasoning, and missed high-priority calendar events.

- **Smarter chart selection:** Let the system choose and configure the most relevant chart dynamically at runtime with LLM assistance, while still validating all plotted data comes from real or fixture sources.

- **Additional delivery channels:** Add Slack, Telegram, or dashboard delivery after email is stable, while preserving the same rendered brief and validation pipeline.
