# architecture.md — Daily Macro Brief Agent

## 1. Architecture goal

This document translates `spec.md` into the system shape for V1.

The product is a scheduled Python pipeline that produces a PM-facing Daily Macro Brief before the market day starts. The system should be simple enough to finish as a 7-day case-study prototype, but structured enough that sources, prompts, models, and delivery providers can be replaced later.

Core architectural answer:

> Collect real market/calendar/source evidence, normalize it into typed objects, rank what matters, use the LLM only for grounded synthesis, validate the output, render it, and deliver it by email.

## 2. Architectural principles

1. **Numbers come from data sources, not the LLM**\
   Market prices, returns, yields, spreads, calendar times, previous values, and consensus/forecast values must come from APIs, scraping/backend endpoints, cached data, explicit fixtures, or deterministic computation from sourced input values.

2. **LLM is a synthesis layer, not a data source**\
   The LLM may summarize, rank, write, critique, and run a narrow consensus-enrichment scout when Investing.com is missing a forecast. It must not invent data, links, consensus values, or source claims. For consensus enrichment, the LLM scout may search/extract candidate consensus from public sources, but the accepted value must either be source-backed or deterministically computed from source-backed raw values.

3. **Typed boundaries between stages**\
   Each stage passes structured models such as `MarketSnapshot`, `CalendarEvent`, `EvidenceCard`, `BriefItem`, and `RunMetadata`.

4. **Provider-specific code is isolated**\
   Alpha Vantage, Databento, Investing.com, Grok/xAI, and SendGrid integrations should sit behind small wrappers. For LLM model providers specifically, use one thin project wrapper around LiteLLM instead of writing separate wrappers for Azure OpenAI, OpenAI, Anthropic, Gemini, Bedrock, etc.

5. **Cache first, scrape/call politely**\
   Calendar/source fetching should cache daily results and avoid repeated unnecessary endpoint calls.

6. **Fail soft for optional discovery, warn on stale critical data**\
   Optional scouts can fail without killing the run. If live market data is unavailable, use the latest cached market snapshot with a clear warning.

7. **Config over code edits**\
   Watchlists, portfolio assumptions, themes, source toggles, chart templates, thresholds, and delivery settings should live in YAML config where practical.

8. **Prompts are centralized, business logic is not**\
   Long-form LLM prompts should live under `app/llm/prompts/**`. Source-specific scout behavior stays in `app/discovery/scouts/**`, and synthesis behavior stays in `app/synthesis/**`. Prompt text should be easy to edit without hunting through Python files.

9. **Demo mode must always run**\
   A local sample mode should work without paid credentials using fixture data and sample evidence.

## 3. High-level system flow

```text
GitHub Actions / local CLI
        |
        v
app/main.py
        |
        v
app/pipeline.py
        |
        +--> load settings + YAML configs
        |
        +--> fetch market data
        |       Alpha Vantage primary
        |       Databento supplemental
        |       latest cache fallback with warning
        |
        +--> fetch economic calendar
        |       Investing.com backend endpoint
        |       daily cache
        |
        +--> enrich missing consensus (high-importance events only)
        |       LLM scout searches public sources for consensus value
        |       accepted only if source-backed or deterministically computed
        |       leaves blank + sets missing_consensus=true if unresolved
        |
        +--> discover source evidence
        |       news / central bank / research / podcast / Grok-xAI-X scout
        |
        +--> normalize to typed models
        |       MarketSnapshot / CalendarEvent / EvidenceCard
        |
        +--> dedupe + rank
        |
        +--> write grounded brief with LLM
        |       LiteLLM-backed app/llm/provider.py
        |       centralized prompts from app/llm/prompts/
        |
        +--> validate output
        |
        +--> render HTML, text, chart
        |
        +--> deliver through SendGrid if enabled
        |
        +--> save artifacts + run metadata
```

### 3.1 Current implementation status

As of the latest Step 5-9 implementation:

- `app/data/market.py` implements fixture and live market providers, move detection, generated `vol_params.yaml`, and cache fallback.
- `app/data/calendar.py` implements fixture loading, Investing.com normalization/caching, and consensus-candidate guardrails.
- `app/llm/` implements the LiteLLM-backed synthesis wrapper and prompt registry.
- `app/discovery/` implements fixture, news, central bank, research, podcast, and X scouts with optional-failure handling.
- `app/synthesis/deduper.py` and `app/synthesis/ranker.py` implement evidence deduplication and ranking.
- `scripts/live_eval_run.py` is a diagnostic runner that exercises live collection, discovery, ranking, LLM markdown generation, and saved evaluation artifacts.
- `app/pipeline.py` is not yet end-to-end for V1 output: Step 10+ work remains for structured brief writing, deterministic validation, HTML/text/chart rendering, delivery, and production run artifact persistence.

## 4. Runtime modes

### 4.1 Sample mode

Purpose: make the repo runnable for reviewers without live credentials.

Behavior:

- Uses fixture market data.
- Uses fixture calendar data.
- Uses fixture source/evidence items.
- After Step 10+ is implemented, can either call the configured LLM or use a deterministic sample writer, depending on config.
- After rendering is wired, must produce `outputs/sample_brief.html`, `outputs/sample_brief.txt`, and `outputs/sample_chart.png`.

Suggested command:

```bash
make run-sample
```

### 4.2 Live mode

Purpose: run the actual scheduled brief.

Behavior:

- Pulls market data from Alpha Vantage first.
- Pulls supplemental market data from Databento where configured.
- Pulls calendar data from the Investing.com backend calendar endpoint.
- Uses cached calendar/market data if live calls fail according to reliability rules.
- Runs discovery scouts.
- After Step 10+ is implemented, calls the configured model through the LiteLLM-backed `app/llm/provider.py` wrapper for synthesis.
- After delivery is wired, sends email via SendGrid when delivery is enabled.

Suggested command:

```bash
make run-live
```

### 4.3 Dry-run mode

Purpose: validate the full pipeline without sending email.

Behavior:

- Runs live or cached data collection.
- After Step 10+ is implemented, generates outputs.
- Skips delivery.
- Useful for local testing and GitHub Actions debugging.

Suggested command:

```bash
make dry-run
```

## 5. Repository and module responsibilities

Use the approved starting structure.

```text
daily-macro-brief/
  README.md
  costs.md
  pyproject.toml
  .env.example
  .env
  .gitignore
  Makefile

  AGENTS.md
  CLAUDE.md
  GEMINI.md
  spec.md
  architecture.md
  plan.md

  app/
    __init__.py
    main.py
    pipeline.py
    settings.py
    models.py

    config/
      app.yaml
      portfolio.yaml
      themes.yaml
      sources.yaml
      chart_templates.yaml
      vol_params.yaml        # generated by scripts/update_vol_params.py; committed to repo

    llm/
      __init__.py
      provider.py
      prompt_registry.py
      prompts/
        brief_writer.md
        critic.md
        scouts/
          news_search.md
          central_bank_extract.md
          research_search.md
          podcast_extract.md
          x_narrative_search.md
          consensus_enrichment.md

    data/
      market.py
      calendar.py

    discovery/
      orchestrator.py
      scouts/
        __init__.py
        base.py
        news.py
        central_bank.py
        research.py
        podcast.py
        x.py
        consensus.py  # optional if calendar enrichment becomes non-trivial

    synthesis/
      deduper.py
      ranker.py
      writer.py
      validator.py

    render/
      charts.py
      email.py
      templates/
        brief_template.html
        template.html
        example.html

    delivery.py

  outputs/
    sample_brief.html
    sample_brief.txt
    sample_chart.png

  tests/
    fixtures/
      market_sample.json
      calendar_sample.json
      evidence_sample.json
    test_config.py
    test_llm.py
    test_market.py
    test_calendar.py
    test_deduper.py
    test_ranker.py
    test_validator.py
    test_render.py
    test_pipeline_sample.py

  scripts/
    update_vol_params.py    # fetches 252-day history, computes SDs, writes vol_params.yaml

  memo/
    memo.md
    memo.pdf

  .github/
    workflows/
      daily_brief.yml
```

The `app/llm/` folder is now part of the approved V1 structure, not a later optional addition. It should remain narrow: shared LLM infrastructure, a thin LiteLLM-backed provider wrapper, prompt loading, prompt files, structured-output helpers, retries, and token/cost metadata.

Do not move scout or synthesis business logic into `app/llm/`. Scouts stay under `app/discovery/scouts/`; the final brief writer and validator stay under `app/synthesis/`.

Avoid adding heavier ML/data-science folders unless the implementation actually needs them.

## 6. Entry points

### 6.1 `app/main.py`

Role:

- CLI entry point.
- Parses mode: `sample`, `live`, or `dry-run`.
- Loads settings.
- Calls `run_pipeline()`.
- Returns non-zero exit code on hard failures.

Suggested commands:

```bash
python -m app.main --mode sample
python -m app.main --mode live
python -m app.main --mode dry-run
```

### 6.2 `app/pipeline.py`

Role:

- Owns the run orchestration.
- Does not contain provider-specific logic.
- Calls data, discovery, synthesis, render, delivery, and metadata components in order.

Key function:

```python
def run_pipeline(mode: RunMode, settings: Settings) -> PipelineResult:
    ...
```

The pipeline should remain boring and readable. It should show the whole product flow at a glance.

### 6.3 `Makefile`

Role:

- Standardizes commands for the developer and coding agents.
- Assumes local development uses a project-level `.venv` virtual environment.

Suggested targets:

```makefile
venv
install
format
lint
test
run-sample
run-live
dry-run
render-memo
update-vol-params   # fetches 252-day history, recomputes SDs, writes app/config/vol_params.yaml
```

The `venv`/`install` targets should create or use `.venv` and avoid relying on global Python packages.

## 7. Configuration architecture

### 7.1 `app/settings.py`

Role:

- Reads environment variables.
- Loads YAML configs and merges them into one runtime `Settings` object.
- Keeps secrets out of YAML files.
- Allows environment variables to override YAML defaults only for deployment-specific values.

Boundary:

- `app/config/app.yaml` is the source of truth for non-secret defaults such as timezone, cutoff time, output directory, cache directory, and default delivery toggle.
- `settings.py` is the source of truth for secrets, provider credentials, deployment-specific overrides, and final runtime validation.

Likely environment variables:

```text
APP_MODE optional override

# LLM — required for all modes
OPENAI_API_KEY                  synthesis model, web-search scouts (Responses API), podcast content recall
XAI_API_KEY                     X scout via xai-sdk + grok-4

# LLM optional overrides
LLM_SCOUT_MODEL                 optional override for sources.yaml llm.scout_model
LLM_X_SCOUT_MODEL               default: grok-4 (bare model name; grok-4+ required for xAI server-side x_search)
LLM_SYNTHESIS_MODEL             optional override for sources.yaml llm.synthesis_model
LLM_TEMPERATURE                 optional override for sources.yaml llm.temperature

# Market data — required for live/dry-run
ALPHA_VANTAGE_API_KEY
DATABENTO_API_KEY
FRED_API_KEY                    free at fred.stlouisfed.org

# Discovery — required for live/dry-run
LISTEN_NOTES_API_KEY            podcast scout

# Delivery — required for live email send
SENDGRID_API_KEY
SENDGRID_FROM_EMAIL
SENDGRID_TO_EMAIL               comma-separated list accepted
ENABLE_EMAIL_DELIVERY           optional override, default false

# Optional
AZURE_OPENAI_API_KEY            if routing through Azure via LiteLLM
AZURE_OPENAI_ENDPOINT
AZURE_OPENAI_DEPLOYMENT
CACHE_DIR                       optional override
OUTPUT_DIR                      optional override
```

### 7.2 `app/config/app.yaml`

Role:

- Non-secret runtime behavior.

Likely contents:

```yaml
timezone: Asia/Hong_Kong
data_cutoff_hkt: "06:45"
send_time_hkt: "07:15"
default_mode: sample
output_dir: outputs
cache_dir: .cache
email_enabled: false
max_discovery_items: 30
max_theme_radar_items: 3
```

### 7.3 `app/config/portfolio.yaml`

Role:

- Encodes synthetic book assumptions used for `so what` mapping.

Initial assumptions:

```yaml
core_positions:
  - id: metals_complex_long
    label: Long Metals Complex (Gold/Silver/Copper)
    sensitivity_tags: [real_rates, usd_policy, monetary_policy]
  - id: agriculture_long
    label: Long Agriculture Futures (Grains Complex)
    sensitivity_tags: [weather, geopolitics, fertilizer_costs, population_growth]
  - id: duration_short
    label: Short Long-term US Duration
    sensitivity_tags: [fiscal_policy, fed_policy, inflation_regime, debt_sustainability]
tactical_overlays:
  - id: usd_diversification
    label: Currency Diversification
    sensitivity_tags: [usd_policy, de_dollarization, reserve_currencies, dollar_liquidity]
```

These are demo assumptions, not real client positions.

### 7.4 `app/config/themes.yaml`

Role:

- Defines macro themes, keywords, watchlist mappings, and ranking hints.

Initial themes:

```yaml
themes:
  - id: monetary_debasement
    label: Monetary debasement and fiscal dominance
  - id: metals_complex
    label: Metals complex as monetary hedge
  - id: food_security
    label: Food security and agricultural supply disruption
  - id: currency_diversification
    label: De-dollarization and currency diversification
  - id: resource_scarcity
    label: Resource scarcity and population pressure
  - id: geopolitical_fragmentation
    label: Geopolitical fragmentation and trade disruption
  - id: credit_stress
    label: Credit stress and risk appetite
```

### 7.5 `app/config/sources.yaml`

Role:

- Controls source providers and toggles.

Likely contents:

```yaml
market:
  primary: alpha_vantage
  supplemental: databento
  move_index: yfinance      # ^VIX, ^MOVE — both low_reliability
  hy_oas: fred              # BAMLH0A0HYM2

calendar:
  provider: investing_com
  cache_daily: true
  filter_countries: ["US", "EU", "DE", "JP", "CN", "GB"]
  filter_importance: ["high", "medium"]

llm:
  provider: litellm
  scout_model: openai/gpt-5.4      # news, central_bank, research, podcast scouts (OpenAI Responses API)
  x_scout_model: xai/grok-4-1-fast-reasoning
  synthesis_model: openai/gpt-5.4  # brief writer, critic, ranker
  temperature: 0.2

scouts:
  news: {enabled: true}
  central_bank: {enabled: true}
  research: {enabled: true}
  x: {enabled: true}
  podcast:
    enabled: true
    listen_notes_lookback_hours: 24

delivery:
  provider: sendgrid
  default_enabled: false
```

### 7.6 `app/config/chart_templates.yaml`

Role:

- Defines chart candidates and required fields.
- Keeps chart selection configurable.

## 8. Data models and contracts

All major boundaries should use typed models in `app/models.py`. Pydantic is the preferred choice.

### 8.1 `MarketSnapshot`

Represents one instrument observation for the dashboard.

Key fields:

```text
as_of
instrument_id
display_name
asset_class
region
last_price_or_level
one_day_change
one_day_change_unit
one_day_zscore or threshold_flag
five_day_change optional
five_day_change_unit optional
source
source_url optional
freshness_status
warning optional
```

### 8.2 `CalendarEvent`

Represents one economic/policy calendar event.

Key fields:

```text
event_time_local
event_time_hkt
session
country_or_region
event_name
importance
consensus optional
consensus_source optional
consensus_source_url optional
consensus_confidence optional
consensus_method optional: investing_forecast | source_extracted | computed_from_source
consensus_formula optional
consensus_inputs optional
previous optional
missing_consensus optional
source
source_url optional
why_it_matters optional
```

### 8.3 `EvidenceCard`

Represents one normalized source item for ranking/synthesis.

Key fields:

```text
id
title
source_name
source_type
url
published_at
retrieved_at
author optional
summary_raw optional
thesis
evidence
macro_relevance
portfolio_relevance
novelty_score optional
confidence
tags
```

### 8.4 `BriefItem`

Represents an item included in the final brief.

Key fields:

```text
section
headline
body
so_what
supporting_market_ids
supporting_evidence_ids
source_name
source_url
source_type
topic_label
confidence
validation_flags
```

### 8.5 `BriefDraft`

Represents the structured brief before rendering.

Suggested fields:

```text
run_metadata
book_impact
overnight_dashboard
three_things
todays_calendar
chart
radar_items
contrarian_corner
warnings
```

### 8.6 `RunMetadata`

Represents audit information for each run.

Key fields:

```text
run_id
run_started_at
data_cutoff_at
timezone
config_hash
git_commit optional
llm_provider
llm_model
prompt_versions optional
token_usage
estimated_cost_usd
warnings
failed_sources
delivery_status
output_paths
```

## 9. Market data architecture

File: `app/data/market.py`

### 9.1 Provider priority and instrument map

Confirmed V1 provider assignments. The `instrument_id` column is the canonical string used in `MarketSnapshot.instrument_id`, `vol_params.yaml`, and all tests — it must match exactly across all three.

| instrument_id | Instrument | Provider | Underlying symbol | Notes |
|---|---|---|---|---|
| `SPY` | S&P 500 | Alpha Vantage | SPY | ETF proxy |
| `QQQ` | Nasdaq 100 | Alpha Vantage | QQQ | ETF proxy |
| `FESX` | Euro Stoxx 50 | Databento (XEUR.EOBI) | FESX front month | Actual EU session close |
| `US2Y` | US 2Y Yield | Alpha Vantage | TREASURY_YIELD maturity=2year | change unit = bps |
| `US10Y` | US 10Y Yield | Alpha Vantage | TREASURY_YIELD maturity=10year | change unit = bps |
| `DE10Y` | German 10Y (Bund) | Databento (XEUR.EOBI) | FGBL front month | Yield derived from FGBL price; change unit = bps |
| `UUP` | DXY | Alpha Vantage | UUP | ETF proxy — label clearly |
| `USDJPY` | USD/JPY | Alpha Vantage | USDJPY | Forex API |
| `EURUSD` | EUR/USD | Alpha Vantage | EURUSD | Forex API |
| `USDCNH` | USD/CNH | Alpha Vantage | USDCNH | Forex API |
| `GOLD` | Gold | Alpha Vantage | GOLD | `GOLD_SILVER_HISTORY` spot history |
| `SILVER` | Silver | Alpha Vantage | SILVER | `GOLD_SILVER_HISTORY` spot history |
| `WTI` | WTI Crude | Alpha Vantage | WTI | Commodities API |
| `BRENT` | Brent Crude | Alpha Vantage | BRENT | Commodities API |
| `COPPER` | Copper | Databento (GLBX.MDP3) | HG.c.0 | CME front-month; AV copper was not suitable for daily move detection |
| `BTC` | Bitcoin | Alpha Vantage | BTC | Crypto API |
| `VIX` | VIX | yfinance | ^VIX | Spot index; `low_reliability`; VX continuous contract unresolved on GLBX.MDP3 |
| `HY_OAS` | HY OAS | FRED | BAMLH0A0HYM2 | Free; change unit = bps |
| `MOVE` | MOVE Index | yfinance | ^MOVE | No official API; always `low_reliability` |

Databento FGBL/FESX/HG wrappers use continuous front-month symbols and must guard against accidental broad downloads. FGBL is converted to an approximate 10Y Bund yield in percent before bps changes are computed.

Fixture data is used for sample/demo tests only.

### 9.2 Provider interface

Use a small common interface:

```python
class MarketDataProvider(Protocol):
    def fetch_watchlist(self, watchlist: list[InstrumentConfig], as_of: datetime) -> list[MarketSnapshot]:
        ...
```

Provider-specific classes:

```text
AlphaVantageMarketProvider    primary — equities, FX, commodities, yields, crypto
DatabentoMarketProvider       FESX, FGBL, HG copper — continuous front-month symbols
FredMarketProvider            HY OAS (BAMLH0A0HYM2)
YfinanceMarketProvider        ^VIX (spot VIX), ^MOVE — both low_reliability flag
FixtureMarketProvider         sample/demo mode only
```

### 9.3 Normalization

Each provider should normalize raw responses into `MarketSnapshot`. The rest of the app should not depend on raw Alpha Vantage or Databento payloads.

### 9.4 Move detection

Market move detection is deterministic and reads thresholds from `app/config/vol_params.yaml`.

`vol_params.yaml` is generated by `scripts/update_vol_params.py`, which fetches 252 trading days of history per instrument via the Phase B API clients, computes 1D standard deviations, and commits the result. It should be refreshed monthly. MOVE fetches via yfinance `^MOVE`; falls back to a hardcoded 4.5% SD only if yfinance returns no data.

Inputs:

- Current level/price.
- Prior close.
- 5D reference level where available.
- `vol_params.yaml` — per-instrument 1D standard deviation and change unit.

Outputs:

- `one_day_zscore` per snapshot.
- `threshold_flag` (True when `abs(zscore) >= 1.0`).
- Group move flags when >= 2 instruments in the same `(asset_class, region)` move together and >= 1 breaches threshold.

Hard constraint: `vol_params[id]["unit"]` must match `snapshot.one_day_change_unit`. Mismatch raises `ValueError` immediately — it prevents silent wrong z-scores from bps/percent confusion.

### 9.5 Cache fallback

If live market data fails:

1. Load latest cached market snapshot.
2. Mark each stale item with `freshness_status=stale_cache`.
3. Add a warning to `RunMetadata`.
4. Display a clear warning in the rendered brief.
5. If no cache exists, fail loudly in live mode.

## 10. Calendar architecture

File: `app/data/calendar.py`

### 10.1 V1 source

Use Investing.com’s ungated backend economic-calendar endpoint as the V1 prototype source.

Important constraints:

- This is not an official paid data partnership.
- Endpoint stability is not guaranteed.
- Request volume must be conservative.
- Daily caching is required.
- Future production should move to a licensed/paid economic-calendar API with institutional-grade consensus data.

### 10.2 Calendar fetch flow

```text
Build HKT start/end datetime
        |
        v
Request Investing.com calendar endpoint
        |
        v
Save raw cached response
        |
        v
Normalize events to CalendarEvent
        |
        v
Filter by configured countries/sessions/importance
        |
        v
Mark missing consensus/forecast fields
```

### 10.3 Consensus handling

Investing.com forecast fields should be treated as consensus when present.

If a high-importance event is missing forecast/consensus, invoke a narrow LLM-powered consensus-enrichment scout. This is the only permitted exception to the rule that the LLM is not involved in data collection, and it is limited to finding or extracting consensus values from public sources.

Accepted consensus methods:

1. `investing_forecast`: use the Investing.com forecast field directly.
2. `source_extracted`: the LLM scout finds a public source that explicitly states consensus/forecast; attach `consensus_source`, `consensus_source_url`, and `consensus_confidence`.
3. `computed_from_source`: when consensus is not directly available but can be mechanically derived from sourced raw forecast values, compute it deterministically in code and store `consensus_formula` and `consensus_inputs`. Example: if a source gives a next-month raw forecast and the needed YoY figure can be derived from this-year forecast divided by last-year value, compute the YoY consensus rather than asking the LLM to infer it.

If no reliable source or computable input values are found, leave consensus blank and set `missing_consensus=true`. Never infer, estimate, or invent consensus values from model knowledge.

Implementation options:

- Keep the orchestration in `calendar.py` for V1 if the enrichment path is short.
- Store the enrichment prompt at `app/llm/prompts/scouts/consensus_enrichment.md`.
- Move reusable enrichment logic into `discovery/scouts/consensus.py` only if the flow becomes non-trivial or needs its own tests.
- In all cases, the prompt text is centralized; calendar/scout modules own the business logic.

### 10.4 Manual override

Allow manual YAML overrides for known important events where the endpoint is missing or wrong.

Suggested file later if needed:

```text
app/config/calendar_overrides.yaml
```

Do not add this file unless the implementation needs it.

## 11. Discovery architecture

Directory: `app/discovery/`

### 11.1 `orchestrator.py`

Role:

- Runs enabled scouts.
- Applies per-scout limits and timeouts.
- Converts outputs into `EvidenceCard` objects.
- Records failed scouts in `RunMetadata` without killing the run unless a required source fails.

Suggested function:

```python
def discover_evidence(context: DiscoveryContext) -> list[EvidenceCard]:
    ...
```

### 11.2 Scout responsibilities

```text
news.py
  Uses llm.scout_model with web search to find catalysts for large market moves.
  Useful for confirming why a price move happened overnight.
  Prompt: app/llm/prompts/scouts/news_search.md

central_bank.py
  Uses llm.scout_model with web search to find speeches, statements, minutes,
  policy releases, and official remarks from central banks.
  Highest trust source type for policy interpretation.
  Prompt: app/llm/prompts/scouts/central_bank_extract.md

research.py
  Uses llm.scout_model with web search to find longform/research-like material.
  Favors non-mainstream sources for Theme Radar over generic financial media.
  Prompt: app/llm/prompts/scouts/research_search.md

podcast.py
  See §11.3 for full flow. Uses Listen Notes API, LiteLLM filtering,
  and OpenAI Responses API web recall. V1 does not transcribe audio.
  Prompt: app/llm/prompts/scouts/podcast_extract.md

x.py
  Uses xai-sdk with a Grok x_search-capable model for live X post retrieval.
  This scout bypasses LiteLLM because LiteLLM cannot route xAI server-side
  Agent Tools. Direct X API ingestion deferred to V2.
  Prompt: app/llm/prompts/scouts/x_narrative_search.md

base.py
  Shared scout dataclasses, timeout/retry helpers, prompt loading helpers,
  and EvidenceCard conversion utilities.

consensus.py optional
  Narrow consensus-enrichment scout if calendar enrichment grows beyond
  simple V1 logic.
```

### 11.3 Podcast scout

File: `app/discovery/scouts/podcast.py`

V1 behavior:

1. Query Listen Notes API (free tier) for episodes published in the last 24 hours, from curated channels or by topic search aligned to configured macro themes.
2. LiteLLM (GPT-5.4, `json_object` response format) ranks and filters candidates against macro themes, portfolio assumptions, and watchlist. Gate question: "Is there a tradeable or portfolio-relevant thesis here?" Do not summarize every episode.
3. For matched episodes, use the OpenAI Responses API (`client.responses.create()` with `web_search_preview`) to recall public summaries/notes about the episode content. No audio transcription or Gemini video analysis in V1.
4. Extract from web-recalled content: speaker's core thesis, evidence/argument, why it matters now, one-line "what this means for our book."
5. Return structured `EvidenceCard` objects for ranking. Output flows into Theme Radar.

Required keys: `LISTEN_NOTES_API_KEY`, `OPENAI_API_KEY` (Responses API web search).

**V1 limitation:** Content recall relies on web search finding public summaries/show notes for matched episodes rather than actual audio transcription. Episodes without public web presence may have lower-quality extractions.

### 11.4 Grok/xAI X scout

File: `app/discovery/scouts/x.py`

V1 behavior:

- Use `xai_sdk.Client` with grok-4 and the `x_search` Agent Tool to retrieve real, live X posts from the last 24 hours tied to flagged market moves or portfolio themes.
- Load prompt text from `app/llm/prompts/scouts/x_narrative_search.md`.
- Grok-4 performs a live server-side search over X and returns posts with real URLs in the format `https://x.com/{username}/status/{id}`.
- Post-process with `_filter_verified()`: drop cards with empty titles or URLs that don't match the X post URL pattern — this catches hallucinated cards.
- Convert verified results into `EvidenceCard` objects with `SourceType.SOCIAL`.
- Treat results as discovery, not final truth. Corroborate high-impact claims through another source before strong use.
- Model is configurable via `x_scout_model` in `sources.yaml`. Must be `grok-4` or later — grok-3 does not support server-side tools.

**Implementation note:** LiteLLM cannot route xAI server-side Agent Tools. The X scout uses `xai_sdk` directly (not the LiteLLM wrapper) and is the only scout that bypasses `app/llm/provider.py`.

Future behavior (V2):

- Direct X API ingestion with archival and more controlled query construction.

### 11.4 Evidence quality metadata

Every `EvidenceCard` should carry:

- Source type.
- Source URL.
- Retrieval time.
- Confidence.
- Tags/themes.
- Portfolio relevance.

This allows ranker/writer/validator to reason about source quality.

## 12. Synthesis architecture

Directory: `app/synthesis/`

### 12.1 `deduper.py`

Role:

- Remove duplicate or near-duplicate evidence items.
- Prefer primary/official sources over summaries.
- Preserve alternate perspectives when they genuinely differ.

Possible V1 approach:

- URL canonicalization.
- Title similarity.
- Source/time/title keying.
- Optional embedding similarity only if quick and available.

### 12.2 `ranker.py`

Role:

- Score market moves, calendar events, and evidence cards.
- Select candidates for `three things`, `theme radar`, chart, and contrarian corner.

Suggested ranking factors:

```text
portfolio_relevance
asset_move_magnitude
freshness
source_quality
novelty
cross_asset_confirmation
calendar_importance
theme_match
confidence
```

Output:

```text
RankedBriefContext
  dashboard_rows
  top_market_moves
  top_calendar_events
  ranked_evidence_cards
  proposed_three_things
  proposed_theme_radar
  proposed_chart_candidate
  proposed_contrarian_corner_seed
```

### 12.3 `writer.py`

Role:

- Calls the LLM with structured context.
- Produces a structured `BriefDraft`.
- Keeps output concise and grounded.

Writer should use `app/llm/prompt_registry.py` to load `app/llm/prompts/brief_writer.md` for the main brief-writing prompt.

Prompt rules:

- Use only supplied market/calendar/source evidence.
- Optional `book_impact` is narrative-only; do not estimate bps impact, P&L, hedge ratios, or exposure sizes unless a future exposure model supplies them.
- Include `so what` tied to portfolio/theme config.
- Do not invent numbers or links.
- Say `no confirmed fresh catalyst` when relevant.
- Obey word limits.

### 12.4 `validator.py`

Role:

- Deterministic checks after LLM writing.
- Optional critic pass can suggest revisions, but deterministic validation is the final gate.

Validator checks:

- All required sections present.
- No unsupported numbers.
- No unsupported links.
- No consensus values without source metadata unless from cached Investing.com payload.
- Computed consensus values must include formula, source-backed inputs, and method `computed_from_source`.
- LLM-enriched consensus must include source URL and confidence metadata.
- `Three things` items are no more than 80 words.
- Theme radar summaries are 60–100 words.
- Chart caption is no more than 30 words.
- Contrarian corner is 50–100 words.
- Each `so what` maps to configured portfolio/themes.
- Warnings are rendered when stale/cached data is used.

Validation behavior:

- In sample mode: fail tests if validation fails.
- In dry-run/live mode: attempt one rewrite/repair pass, then fail or send with warnings depending on severity.

## 13. LLM architecture

The `app/llm/` folder should stay intentionally small. It exists only for shared LLM call mechanics, the LiteLLM-backed provider boundary, token/cost logging, retries, structured-output helpers, prompt loading, and versioned prompt files. Business logic stays in `discovery/` and `synthesis/`.

Core decision:

> Use LiteLLM for provider compatibility, but keep one thin project wrapper for Daily Macro Brief-specific behavior.

LiteLLM replaces raw per-model SDK adapters. It should not replace the project boundary.

Recommended folder:

```text
app/llm/
  __init__.py
  provider.py
  prompt_registry.py
  prompts/
    brief_writer.md
    critic.md
    scouts/
      news_search.md
      central_bank_extract.md
      research_search.md
      podcast_extract.md
      x_narrative_search.md
      consensus_enrichment.md
```

### 13.1 Dependency choice

Use LiteLLM as the common LLM provider layer.

`pyproject.toml` should pin an exact version rather than using a floating dependency. Initial V1 pin:

```toml
litellm = "==1.83.14"
```

Before final submission, verify the chosen version and avoid known compromised versions. In particular, do not use `1.82.7` or `1.82.8`.

### 13.2 Why use LiteLLM

LiteLLM gives one OpenAI-style interface across many model providers, including OpenAI, Azure OpenAI, Anthropic, Gemini/Vertex, Bedrock, and local/OpenAI-compatible endpoints.

This matters for the case study because synthesis model choice should be replaceable through config rather than code edits. The exception is the X scout: `x_scout_model` is still configured in `sources.yaml`, but the implementation uses `xai-sdk` directly because LiteLLM cannot route xAI server-side Agent Tools such as `x_search`.

Confirmed V1 model defaults:

```yaml
llm:
  provider: litellm
  scout_model: openai/gpt-5.4      # news, central_bank, research, podcast — OpenAI Responses API
  x_scout_model: grok-4            # X scout — xai-sdk (bare name, grok-4+ required)
  synthesis_model: openai/gpt-5.4  # brief writer, critic, ranker
  temperature: 0.2
  max_tokens: 2000
```

The `llm` block in `app/config/sources.yaml` is the source of truth for workflow-level model defaults. Environment variables are deployment overrides only. The split between `scout_model` and `synthesis_model` allows using different models for retrieval vs writing without changing any code — only config. Both default to GPT-5.4 in V1 but can be diverged independently (e.g. GPT-5.5 for final synthesis in a premium run).

**Important:** The news, central_bank, research, and podcast scouts use the OpenAI Responses API (`client.responses.create()` with `web_search_preview`) rather than LiteLLM, because LiteLLM blocks the `web_search_preview` built-in tool type. The `scout_model` value is stripped of the `openai/` prefix before passing to the OpenAI SDK. The X scout uses `xai_sdk` directly for the same reason. LiteLLM is still used for synthesis (brief writer, critic, ranker) and the LiteLLM-backed `LLMClient` wrapper in `app/llm/provider.py` handles all synthesis calls.

Do not create these unless a real provider-specific need appears:

```text
AzureOpenAIProvider
OpenAIProvider
AnthropicProvider
GeminiProvider
BedrockProvider
```

### 13.3 Why still keep our own wrapper

The project still needs a small wrapper at `app/llm/provider.py` because LiteLLM does not know the Daily Macro Brief contract.

Our wrapper owns:

- structured output parsing into Pydantic models
- consistent return objects for `RunMetadata`
- token, latency, model, and estimated-cost metadata
- retry and timeout policy hooks
- prompt registry integration
- consistent exception boundaries for `pipeline.py`
- deterministic fake/stub behavior for sample tests

The wrapper should be thin. It should not contain prompt text or macro/business logic.

Suggested interface:

```python
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from litellm import completion
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class LLMConfig:
    model: str
    temperature: float = 0.2
    max_tokens: int = 2000
    timeout_seconds: int = 60


@dataclass(frozen=True)
class LLMUsage:
    model: str
    latency_seconds: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None


@dataclass(frozen=True)
class LLMResult(Generic[T]):
    output: T
    usage: LLMUsage
    raw_text: str


class LLMClient:
    """Small project wrapper around LiteLLM."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        schema: type[T],
    ) -> LLMResult[T]:
        started = time.perf_counter()
        response = completion(
            model=self.config.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            timeout=self.config.timeout_seconds,
            response_format={"type": "json_object"},
        )
        latency = time.perf_counter() - started

        raw_text = response.choices[0].message.content or "{}"
        try:
            parsed = schema.model_validate_json(raw_text)
        except ValidationError:
            parsed = schema.model_validate(json.loads(raw_text))

        usage_obj = getattr(response, "usage", None)
        usage = LLMUsage(
            model=getattr(response, "model", self.config.model),
            latency_seconds=latency,
            prompt_tokens=getattr(usage_obj, "prompt_tokens", None),
            completion_tokens=getattr(usage_obj, "completion_tokens", None),
            total_tokens=getattr(usage_obj, "total_tokens", None),
        )
        return LLMResult(output=parsed, usage=usage, raw_text=raw_text)
```

### 13.4 Prompt registry

File: `app/llm/prompt_registry.py`

Role:

- Load prompt markdown files from `app/llm/prompts/**`.
- Prevent path traversal.
- Cache prompt reads.
- Make prompt names stable for tests and run metadata.

Suggested implementation:

```python
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPT_ROOT = Path(__file__).resolve().parent / "prompts"


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    path: Path
    text: str


@lru_cache(maxsize=64)
def load_prompt(name: str) -> PromptTemplate:
    safe_name = name.removesuffix(".md")
    path = (PROMPT_ROOT / f"{safe_name}.md").resolve()

    if not str(path).startswith(str(PROMPT_ROOT.resolve())):
        raise ValueError(f"Invalid prompt path: {name}")
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")

    return PromptTemplate(name=safe_name, path=path, text=path.read_text(encoding="utf-8"))
```

### 13.5 Prompt versioning

The V1 prompt set should include:

- `brief_writer.md` for final grounded brief drafting.
- `critic.md` for the optional rewrite/repair critique.
- `scouts/news_search.md` for market-move catalyst discovery.
- `scouts/central_bank_extract.md` for policy-source extraction.
- `scouts/research_search.md` for non-mainstream research/theme discovery.
- `scouts/podcast_extract.md` for podcast/show-note evidence extraction.
- `scouts/x_narrative_search.md` for X narrative discovery through Grok/xAI or another configured model.
- `scouts/consensus_enrichment.md` for narrow calendar consensus enrichment.

Prompts should be saved as files, not buried in Python strings.

Benefits:

- Easier review.
- Easier prompt iteration.
- Easier citation in memo/design explanation.
- Easier rollback.
- Easier testing of prompt existence and loading.

### 13.6 Scout usage pattern

A scout should own the source-specific context and normalization. It should load prompt text from the registry and send structured input to `LLMClient`.

```python
from app.llm.prompt_registry import load_prompt

prompt = load_prompt("scouts/x_narrative_search")
result = llm.generate_structured(
    system_prompt=prompt.text,
    user_payload=context.model_dump(mode="json"),
    schema=XScoutOutput,
)
```

The prompt registry owns text. The LLM client owns the provider call. The scout owns source-specific behavior and `EvidenceCard` normalization.

### 13.7 Critic pass

Use a critic prompt after initial drafting to check:

- Missing `so what`.
- Unsupported market claims.
- Generic news-regurgitation language.
- Word limit violations.
- Missing warnings.
- Weak or unsubstantiated contrarian framing.

The critic should suggest minimal changes. The deterministic validator remains the final gate.

## 14. Rendering architecture

Directory: `app/render/`

### 14.1 `charts.py`

Role:

- Select or build one chart worth seeing.
- Use real or fixture data.
- Save chart to `outputs/sample_chart.png` or run-specific output path.

V1 charting:

- Use matplotlib or Plotly static export.
- Prefer simple line/bar/scatter chart.
- Keep chart tied to the selected theme.
- Do not overbuild interactive dashboards.

### 14.2 `email.py`

Role:

- Render `BriefDraft` into HTML and plain text.
- Use `render/templates/template.html` for HTML layout; `brief_template.html` may remain as a compatibility include.
- Keep `render/templates/example.html` as a non-runtime visual reference only. It may contain mock values, but it must never be used as a data source or sent as a live brief.
- Include warnings near the top when stale/cache fallback data is used.
- Convert `MarketSnapshot` objects into deterministic presentation rows before template rendering, including formatted values, CSS classes, and 5D trend arrows.
- Do not ask an LLM to decide market-dashboard arrows, colors, bold/significant-move styling, source labels, links, market numbers, or calendar values.

### 14.3 HTML template

The template should prioritize readability:

- Compact header with run timestamp and data cutoff.
- Dashboard table.
- Three short selected items.
- Calendar table.
- One chart with caption.
- Theme radar.
- Contrarian corner.
- Source/failure warnings if applicable.

Dashboard presentation rules:

- 1D direction color is derived from the sign of `one_day_change`.
- Significant 1D moves are bolded from `threshold_flag` or `abs(one_day_zscore) >= 1.0`.
- 5D trend arrows are derived in code from `five_day_change` and daily volatility in `vol_params.yaml`: `five_day_sigma = sd_1d * sqrt(5)`.
- The initial flat threshold is `abs(five_day_zscore) < 0.25`; otherwise positive changes render up and negative changes render down. This is a display heuristic, not a trading signal.

## 15. Delivery architecture

File: `app/delivery.py`

### 15.1 Provider

Use SendGrid as the V1 default delivery provider because the developer has more experience with it.

Keep the delivery layer replaceable so Azure Communication Services can be added later if desired.

### 15.2 Delivery interface

Suggested interface:

```python
class DeliveryProvider(Protocol):
    def send(self, message: EmailMessage) -> DeliveryResult:
        ...
```

Provider classes:

```text
SendGridDeliveryProvider
NoopDeliveryProvider
```

### 15.3 Delivery behavior

- `sample` mode: never send real email.
- `dry-run` mode: never send real email.
- `live` mode: send only if `ENABLE_EMAIL_DELIVERY=true` and recipients are configured.
- `SENDGRID_TO_EMAIL` accepts a comma-separated list for multiple recipients.
- Always save output artifacts even if email fails.
- Record delivery status in `RunMetadata`.

Validation and delivery interaction:

- **Critical failures** (missing required sections, market numbers without source metadata): hard fail, no email sent, failure logged to `RunMetadata`.
- **Minor failures** (word limit violations, weak `so_what`, missing stale-data warning): email is sent with a validation warning banner rendered at the top of the HTML and text outputs. Recipients can judge the output themselves.
- Warning banner format: `[VALIDATION WARNINGS: <list of flags>]` rendered before the market dashboard section.

## 16. Caching and artifacts

Suggested cache layout:

```text
.cache/
  market/
    latest.json
    market_YYYY-MM-DD.json
  calendar/
    calendar_YYYY-MM-DD.json
  discovery/
    evidence_YYYY-MM-DD.json
```

Suggested output layout:

```text
outputs/
  sample_brief.html
  sample_brief.txt
  sample_chart.png
  runs/
    YYYY-MM-DD_HHMM_HKT/
      brief.html
      brief.txt
      chart.png
      run_metadata.json
      market_snapshot.json
      calendar.json
      evidence_cards.json
```

The committed repo should include sample outputs, not private live outputs unless scrubbed.

## 17. Scheduling architecture

File: `.github/workflows/daily_brief.yml`

### 17.1 Schedule

Target send time:

```text
07:15 HKT, Monday–Friday
```

GitHub Actions cron:

```yaml
on:
  schedule:
    - cron: "15 23 * * 0-4"
  workflow_dispatch:
```

This cron runs at 23:15 UTC on Sunday–Thursday, corresponding to 07:15 HKT Monday–Friday.

### 17.2 Workflow behavior

Workflow steps:

1. Checkout repo.
2. Set up Python.
3. Install dependencies.
4. Run tests or at least a smoke validation.
5. Run `make run-live` or `make dry-run` depending on secrets/config.
6. Upload output artifacts.

Secrets needed:

```text
ALPHA_VANTAGE_API_KEY
DATABENTO_API_KEY optional
AZURE_OPENAI_API_KEY or OPENAI_API_KEY
AZURE_OPENAI_ENDPOINT optional
AZURE_OPENAI_DEPLOYMENT optional
SENDGRID_API_KEY
SENDGRID_FROM_EMAIL
SENDGRID_TO_EMAIL
```

## 18. Testing architecture

Directory: `tests/`

### 18.1 Minimum tests

```text
test_config.py
  settings/config load successfully from fixtures/env defaults

test_market.py
  provider normalization from fixture payloads
  stale cache fallback behavior

test_calendar.py
  Investing.com payload normalization
  forecast-as-consensus mapping
  missing-consensus flag
  high-importance LLM consensus enrichment stub
  computed consensus from source-backed raw inputs

test_llm.py
  prompt registry loads expected prompt files
  prompt registry blocks path traversal
  LLMClient parses structured JSON into Pydantic models using a fake LiteLLM response

test_ranker.py
  ranks portfolio-relevant evidence above generic news
  selects market moves above thresholds

test_validator.py
  catches unsupported numbers
  catches unsupported links
  catches missing so-what
  catches word-limit violations
  catches missing stale-data warning

test_render.py
  HTML/text outputs include all required sections
  chart path/caption rendered correctly

test_pipeline_sample.py
  sample mode creates expected output files
```

### 18.2 Testing priorities

Highest value tests for this project:

1. Validation against hallucinated/unsupported numbers.
2. Calendar consensus behavior.
3. Stale market cache warning behavior.
4. Output section completeness.
5. Prompt registry and structured LLM output wrapper behavior.
6. Sample mode end-to-end run.

### 18.3 CI gate

Minimum CI gate:

```bash
make lint
make test
make run-sample
```

If time is short, keep `run-sample` as the core smoke test.

## 19. Cost and observability architecture

### 19.1 Runtime metadata

Every run should record:

- Run ID.
- Mode.
- Start/end time.
- Data cutoff.
- Source calls attempted.
- Source calls failed.
- Cache fallbacks used.
- LLM provider.
- LLM model.
- Prompt names/versions used.
- Token usage.
- Estimated LLM cost.
- Delivery status.
- Output paths.

### 19.2 `costs.md`

`costs.md` should summarize estimated daily cost:

```text
LLM tokens and estimated cost by configured LiteLLM model
LiteLLM package/version assumption
Alpha Vantage plan/free-tier assumptions
Databento assumptions
GitHub Actions cost assumption
SendGrid cost assumption
Any future paid calendar API assumption
```

For the case study, cost numbers can be approximate, but assumptions should be explicit.

## 20. Failure handling matrix

| Failure                                                                              | V1 behavior                                                                                                                                 |
| ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Alpha Vantage market call fails                                                      | Try supplemental provider if configured; otherwise use latest cached snapshot with warning.                                                 |
| Databento supplemental call fails                                                    | Continue without supplemental data; record warning.                                                                                         |
| No cached market snapshot exists in live mode                                        | Fail loudly; do not ask LLM to fill numbers.                                                                                                |
| Investing.com calendar endpoint fails                                                | Use latest same-day or most recent cache if acceptable; otherwise use fixture only in sample mode and warn/fail in live depending severity. |
| Investing.com missing forecast                                                       | Use narrow LLM consensus-enrichment scout for high-importance events; otherwise flag `missing_consensus`.                                   |
| Consensus is not directly stated but source-backed raw forecast inputs are available | Compute consensus deterministically in code, store formula/inputs, and mark method `computed_from_source`.                                  |
| Consensus scout finds no reliable source or computable inputs                        | Leave consensus blank and flag `missing_consensus`.                                                                                         |
| Optional discovery scout fails                                                       | Continue and record failed source.                                                                                                          |
| Grok/xAI returns unverifiable claim                                                  | Keep as weak candidate or discard unless corroborated.                                                                                      |
| LiteLLM/model provider call fails                                                    | Retry according to wrapper policy; in sample mode fall back to deterministic sample writer if configured; in live mode fail or send warning depending on severity. |
| LLM output fails validation                                                          | Attempt one repair pass; fail if critical validation still fails.                                                                           |
| SendGrid delivery fails                                                              | Save artifacts and record failed delivery status.                                                                                           |

## 21. Local development environment

- Local development uses a repo-level `.venv` virtual environment.
- `Makefile` targets should assume `.venv` where practical.
- `.venv/` must be ignored by git and never committed.
- GitHub Actions should create its own clean Python environment rather than depending on `.venv`.

## 22. Security and source-use boundaries

- Never commit API keys or secrets.
- Keep `.env.example` complete but secret-free.
- Treat portfolio assumptions as synthetic demo data.
- Do not scrape restricted paid content.
- Use the Investing.com backend endpoint politely and cache results.
- Do not over-call Grok/xAI, market APIs, or calendar endpoints.
- Prefer official sources for central bank/policy material.
- Pin LLM infrastructure dependencies exactly and review supply-chain notices before updating LiteLLM.
- Do not use known compromised LiteLLM versions such as `1.82.7` or `1.82.8`.

## 23. What is intentionally not built in V1

- No trading execution.
- No portfolio optimizer.
- No full data warehouse.
- No fine-tuning/training loop.
- No complex dashboard UI.
- No direct X API ingestion.
- No licensed institutional calendar API integration yet.
- No broad autonomous web-browsing agent without source constraints.
- No heavy notebook/ML experiment structure.

## 24. Architecture open questions

These are implementation-planning questions, not product/spec blockers.

1. Should the consensus-enrichment scout stay inside `calendar.py` for speed, or move to `discovery/scouts/consensus.py` once it needs separate tests?
2. Should the sample writer avoid LLM calls entirely for deterministic tests, or should it call the configured LLM in sample mode when credentials exist?
3. Which LiteLLM model string should be the default for the final submission environment: Azure OpenAI deployment, OpenAI direct model, or Grok/xAI-compatible endpoint for scout-only use?
