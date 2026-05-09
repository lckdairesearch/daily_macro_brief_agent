# plan.md — Daily Macro Brief Agent

## 1. Purpose of this plan

This plan turns `spec.md` and `architecture.md` into agent-sized implementation tasks.

Each task is intended to be self-contained: a coding agent should be able to pick it up, make a small change, run the listed checks, and hand it back to the human coder for review. The human coder remains responsible for secrets, source-access decisions, final product judgment, and merging work.

## 2. Source-of-truth hierarchy

1. `spec.md` defines product behavior, required brief sections, acceptance criteria, and non-goals.
2. `architecture.md` defines system shape, module boundaries, data contracts, provider boundaries, failure behavior, and runtime modes.
3. `plan.md` defines execution order only. Do not use this file to override `spec.md` or `architecture.md`.
4. `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` should point agents back to this hierarchy.

## 3. Implementation rules for all agents

- Keep changes small and reviewable.
- Do not invent market data, calendar values, consensus estimates, source links, or portfolio facts.
- Do not commit secrets, `.env`, live private outputs, or local `.venv` files.
- Keep provider-specific code behind wrappers.
- Keep prompts in `app/llm/prompts/**`, not embedded in Python business logic.
- Keep scout logic in `app/discovery/scouts/**` and final writing/validation logic in `app/synthesis/**`.
- Prefer fixture-backed sample mode over live integration when a task can be completed without credentials.
- Add or update tests with each functional change.
- Run the narrowest relevant test first, then the broader smoke command for milestone tasks.
- Ask the human coder before deleting or rewriting existing tests, fixtures, or source files.

## 4. Definition of done for V1

The full acceptance criteria are defined in `spec.md §12.2`. Do not duplicate them here.

Summary: V1 is done when `make run-sample`, `make test`, and `make lint` all pass, the sample brief contains all six required sections with no invented data, the validator catches hallucination-sensitive failures, GitHub Actions can run the scheduled workflow, production email delivery defaults to off, and `costs.md` and `memo/memo.md` are complete.

## 5. Suggested agent workflow

Use one task branch or one small pull request per main step when possible.

1. Agent reads `spec.md`, `architecture.md`, and the assigned task in this file.
2. Agent writes a short implementation note before coding: files to touch, tests to run, open assumptions.
3. Agent implements only the assigned task.
4. Agent runs the task-specific checks.
5. Agent leaves a handoff note with: files changed, tests run, known gaps, human decisions needed.
6. Human coder reviews behavior, not only code style.

## 6. Milestone map

| Milestone | Goal | Primary output | Human checkpoint |
| --- | --- | --- | --- |
| M0 | Align repo and rules | Repo scaffold and agent instructions | Approve file structure |
| M1 | Define contracts and config | Pydantic models, YAML configs, settings loader | Approve assumptions and watchlists |
| M2 | Make sample mode run | Fixtures, deterministic pipeline skeleton | Review sample output shape |
| M3 | Add data modules | Market and calendar providers with cache behavior | Confirm source choices and symbols |
| M4 | Add LLM boundary | LiteLLM wrapper, prompt registry, prompts | Confirm model/provider defaults |
| M5 | Add discovery/ranking | Evidence scouts, dedupe, ranker | Review relevance scoring |
| M6 | Add synthesis/validation/rendering | Brief writer, validator, HTML/text/chart | Review brief quality |
| M7 | Add delivery/scheduling | Postmark/noop delivery, GitHub Actions | Add secrets or keep dry-run |
| M8 | Finalize submission | README, costs, memo, acceptance pass | Final human review |

---

# Main implementation steps

## Step 0 — Repository baseline and guardrails

### 0.1 Create or verify the approved repository structure

**Agent objective:** Create the minimal project tree from `architecture.md` without adding unnecessary heavy folders.

**Files to create or verify:**

- `README.md`
- `costs.md`
- `pyproject.toml`
- `.env.example`
- `.gitignore`
- `Makefile`
- `AGENTS.md`
- `CLAUDE.md`
- `GEMINI.md`
- `spec.md`
- `architecture.md`
- `plan.md`
- `app/__init__.py`
- `app/main.py`
- `app/pipeline.py`
- `app/settings.py`
- `app/models.py`
- `app/config/*.yaml`
- `app/data/*.py`
- `app/llm/**`
- `app/discovery/**`
- `app/synthesis/**`
- `app/render/**`
- `app/delivery.py`
- `tests/fixtures/*.json`
- `.github/workflows/daily_brief.yml`
- `memo/memo.md`

**Human input needed:** Confirm the repo root name and whether an existing repo already has files that must not be overwritten.

**Acceptance checks:**

- Directory tree matches the approved structure.
- `.gitignore` excludes `.env`, `.venv/`, `.cache/`, `__pycache__/`, and private live outputs.
- No secrets are present.

**Suggested commands:**

```bash
find . -maxdepth 3 -type f | sort
```

### 0.2 Add project-level agent instructions

**Agent objective:** Write `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` so coding agents follow the same rules.

**Files to modify:**

- `AGENTS.md`
- `CLAUDE.md`
- `GEMINI.md`

**Human input needed:** Confirm whether Claude, Codex, and Gemini will all work in the same repo or separate branches.

**Acceptance checks:**

- Each file points to `spec.md`, `architecture.md`, and `plan.md`.
- Each file repeats the non-negotiables: no invented market data, no secrets, small changes, tests required, prompts centralized.
- Each file includes a handoff-note format.

---

## Step 1 — Python project setup and developer workflow

### 1.1 Configure Python package metadata and dependencies

**Agent objective:** Create a minimal, reproducible Python setup.

**Files to modify:**

- `pyproject.toml`
- `.env.example`

**Implementation notes:**

- Use Python 3.11+ or 3.12+.
- Include dependencies needed by the planned implementation: `pydantic`, `pydantic-settings` if used, `PyYAML`, `requests`, `python-dotenv`, `matplotlib`, `jinja2`, `litellm`, `pytest`, and lint/format tools.
- Pin `litellm` exactly as specified in `architecture.md` unless the human coder approves a different vetted version.

**Human input needed:** Confirm package manager preference if the repo already uses one. Otherwise use a simple `pyproject.toml` with standard tooling.

**Acceptance checks:**

- A fresh environment can install dependencies.
- `.env.example` contains required keys but no real values.

**Suggested commands:**

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

### 1.2 Create Makefile commands

**Agent objective:** Standardize local and CI commands.

**Files to modify:**

- `Makefile`

**Targets to implement:**

- `venv`
- `install`
- `format`
- `lint`
- `test`
- `run-sample`
- `run-live`
- `dry-run`
- `render-memo`

**Human input needed:** Confirm whether `render-memo` should produce PDF locally or remain a placeholder until memo writing is complete.

**Acceptance checks:**

- `make install` works from a clean checkout.
- `make test` runs pytest.
- `make run-sample` calls `python -m app.main --mode sample`.
- `make dry-run` calls `python -m app.main --mode dry-run`.

---

## Step 2 — Data models and typed contracts

### 2.1 Implement core Pydantic models

**Agent objective:** Create the typed boundaries used by data, discovery, synthesis, render, and delivery modules.

**Files to modify:**

- `app/models.py`
- `tests/test_models.py` if useful

**Models to implement:**

- `RunMode`
- `FreshnessStatus`
- `MarketSnapshot`
- `CalendarEvent`
- `EvidenceCard`
- `BriefItem`
- `ChartSpec` or `ChartItem`
- `BriefDraft`
- `RunMetadata`
- `PipelineResult`
- `DeliveryResult`

**Implementation notes:**

- Use explicit field names from `spec.md` and `architecture.md`.
- Include enough validation to catch missing required IDs, unsupported URLs where relevant, and invalid mode values.
- Avoid over-modeling provider raw payloads.

**Human input needed:** Confirm any field naming preferences before many modules depend on the models.

**Acceptance checks:**

- Models can serialize to JSON with `model_dump(mode="json")`.
- Tests can instantiate realistic sample objects.

**Suggested commands:**

```bash
pytest tests/test_models.py
```

### 2.2 Add fixture data contracts

**Agent objective:** Create sample JSON fixtures matching the models.

**Files to create:**

- `tests/fixtures/market_sample.json`
- `tests/fixtures/calendar_sample.json`
- `tests/fixtures/evidence_sample.json`

**Implementation notes:**

- Label fixture data clearly as sample/demo data.
- Include at least one stale-cache example, one missing-consensus calendar event, one source-backed consensus event, and one evidence item for each major source type where practical.

**Human input needed:** Review fixture examples for macro plausibility and whether they reflect the assumed PM book.

**Acceptance checks:**

- Fixtures load into Pydantic models.
- Fixture content is sufficient for `make run-sample` to produce all brief sections later.

---

## Step 3 — Configuration and settings

### 3.1 Write YAML config files

**Agent objective:** Create config files that encode non-secret defaults and demo assumptions.

**Files to modify:**

- `app/config/app.yaml`
- `app/config/portfolio.yaml`
- `app/config/themes.yaml`
- `app/config/sources.yaml`
- `app/config/chart_templates.yaml`

**Implementation notes:**

- `app.yaml` owns timezone, cutoff time, send time, output directory, cache directory, and default mode.
- `portfolio.yaml` owns synthetic positions and sensitivity tags.
- `themes.yaml` owns theme IDs, theme names, keywords, and ranking hints.
- `sources.yaml` owns provider toggles and delivery defaults.
- `chart_templates.yaml` owns chart candidates and required fields.

**Human input needed:** Approve synthetic book assumptions, theme names, watchlist instruments, and chart candidates.

**Acceptance checks:**

- YAML parses cleanly.
- No secrets are present.
- Assumptions are clearly labeled synthetic/demo.

### 3.2 Implement settings loader

**Agent objective:** Load YAML configs and environment variables into one runtime settings object.

**Files to modify:**

- `app/settings.py`
- `tests/test_config.py`

**Implementation notes:**

- Secrets come from environment variables only.
- Environment variables may override deployment-specific fields such as model, output directory, cache directory, and email toggle.
- Keep defaults explicit and visible.
- Validate missing credentials differently by mode: sample mode should not require live API keys.

**Human input needed:** Confirm names of environment variables used in the deployment environment.

**Acceptance checks:**

- Sample settings load with no API keys.
- Live settings report missing required secrets clearly.
- Config paths are resolved relative to repo root.

**Suggested commands:**

```bash
pytest tests/test_config.py
```

---

## Step 4 — CLI and pipeline skeleton

### 4.1 Implement CLI entry point

**Agent objective:** Make `python -m app.main --mode sample|live|dry-run` work.

**Files to modify:**

- `app/main.py`
- `tests/test_cli.py` if useful

**Implementation notes:**

- Parse mode.
- Load settings.
- Call `run_pipeline(mode, settings)`.
- Return non-zero only on hard failures.
- Print a concise run summary with output paths.

**Human input needed:** None unless the repo uses a CLI framework already.

**Acceptance checks:**

- Invalid mode fails with a useful message.
- Sample mode can call the pipeline skeleton.

**Suggested commands:**

```bash
python -m app.main --mode sample
```

### 4.2 Implement pipeline orchestration skeleton

**Agent objective:** Create a readable pipeline function with placeholder module calls.

**Files to modify:**

- `app/pipeline.py`
- `tests/test_pipeline_sample.py`

**Implementation notes:**

- The pipeline should show the whole product flow at a glance.
- Avoid provider-specific code in `pipeline.py`.
- Return `PipelineResult` with metadata and output paths.
- For the first version, use fixture-backed no-op implementations where downstream modules are not ready.

**Human input needed:** Review pipeline readability and whether orchestration order matches the product intent.

**Acceptance checks:**

- `run_pipeline(sample, settings)` returns a `PipelineResult`.
- The skeleton is easy to read and has clear TODO boundaries.

---

## Step 5 — Market data module

### Implementation order

The phases below must be built in sequence. Each phase is a dependency of the next.

```
Phase A: Fixture provider       → sample mode works immediately; no credentials needed
Phase B: Raw API clients        → shared fetch functions used by both Phase C and Phase E
Phase C: Vol params script      → fetches 252-day history, computes SDs, writes vol_params.yaml
Phase D: Move detection         → reads vol_params.yaml; requires Phase C to have been run first
Phase E: Live providers         → composes Phase B clients + Phase D move detection
Phase F: Cache fallback         → wraps Phase E; fails soft when live calls fail
```

### Canonical instrument IDs

All `MarketSnapshot.instrument_id` values must use these strings exactly. `vol_params.yaml`, the fixture, and all live providers must agree.

| instrument_id | Provider | Notes |
|---|---|---|
| `SPY` | Alpha Vantage | TIME_SERIES_DAILY |
| `QQQ` | Alpha Vantage | TIME_SERIES_DAILY |
| `UUP` | Alpha Vantage | TIME_SERIES_DAILY (DXY proxy) |
| `USDJPY` | Alpha Vantage | FX_DAILY |
| `EURUSD` | Alpha Vantage | FX_DAILY |
| `USDCNH` | Alpha Vantage | FX_DAILY |
| `GOLD` | Alpha Vantage | GOLD_SILVER_HISTORY |
| `SILVER` | Alpha Vantage | GOLD_SILVER_HISTORY |
| `WTI` | Alpha Vantage | COMMODITIES |
| `BRENT` | Alpha Vantage | COMMODITIES |
| `BTC` | Alpha Vantage | DIGITAL_CURRENCY_DAILY |
| `US2Y` | Alpha Vantage | TREASURY_YIELD maturity=2year; change unit = bps |
| `US10Y` | Alpha Vantage | TREASURY_YIELD maturity=10year; change unit = bps |
| `FESX` | Databento | dataset=XEUR.EOBI; front-month futures; change unit = % |
| `DE10Y` | Databento | dataset=XEUR.EOBI; yield derived from FGBL price; change unit = bps |
| `COPPER` | Databento | dataset=GLBX.MDP3; HG front-month futures; change unit = % |
| `VIX` | yfinance | ticker=^VIX (spot index); change unit = % |
| `HY_OAS` | FRED | series=BAMLH0A0HYM2; change unit = bps |
| `MOVE` | yfinance | ticker=^MOVE; change unit = % |

---

### 5.A Fixture provider

**Agent objective:** Make `make run-sample` work with deterministic market data. No credentials required.

**Files to modify:**

- `app/data/market.py`
- `tests/test_market.py`

**Implementation notes:**

- Define `MarketDataProvider` protocol with `fetch_watchlist(instruments, as_of) -> list[MarketSnapshot]`.
- Implement `FixtureMarketProvider`: load `tests/fixtures/market_sample.json`, normalize each row via `MarketSnapshot.model_validate(row)`.
- The fixture already has `one_day_zscore` and `threshold_flag` baked in — no SD computation in sample mode.
- Raise `FileNotFoundError` if fixture path is missing.
- Wire into `app/pipeline.py` Step 2: replace the fixture load stub with `FixtureMarketProvider`.

**Tests (`tests/test_market.py`):**

- Fixture loads and returns the correct number of `MarketSnapshot` objects.
- Each snapshot passes Pydantic validation.
- Missing required field raises `ValidationError`.

**Acceptance checks:**

```bash
make run-sample
pytest tests/test_market.py -k fixture -v
```

---

### 5.B Raw API client layer

**Agent objective:** Build the shared fetch functions that both the vol params script (5.C) and the live providers (5.E) depend on. No provider classes yet — plain functions only.

**Files to modify:**

- `app/data/market.py`

**Implementation notes:**

Build three internal functions returning `list[dict]` with `{date, close}` rows:

```python
def _av_fetch_daily(symbol, function, api_key, start_date, end_date, **extra_params) -> list[dict]: ...
def _db_fetch_daily_ohlcv(dataset, symbol, api_key, start_date, end_date) -> list[dict]: ...
def _fred_fetch_series(series_id, api_key, start_date, end_date) -> list[dict]: ...
```

- `_av_fetch_daily`: dispatches to `TIME_SERIES_DAILY`, `FX_DAILY`, `COMMODITIES`, `TREASURY_YIELD`, or `DIGITAL_CURRENCY_DAILY` based on `function` param. Slices response to `[start_date, end_date]`.
- `_db_fetch_daily_ohlcv`: uses `timeseries.get_range(schema="ohlcv-1d")`. Front-month roll: pick nearest expiry > today. For `DE10Y`, convert FGBL price to yield before returning.
- `_fred_fetch_series`: calls FRED REST endpoint `https://api.stlouisfed.org/fred/series/observations`. Forward-fill missing weekend observations before returning.

These functions are not exposed publicly — they are building blocks for 5.C and 5.E.

**Tests:** Mock HTTP responses with `unittest.mock.patch`. No live API calls.

---

### 5.C Vol params script

**Agent objective:** Fetch 252-day history per instrument, compute 1D standard deviations, and commit `app/config/vol_params.yaml`. Run this once before Phase D or E can be used.

**Files to create:**

- `scripts/update_vol_params.py`
- `app/config/vol_params.yaml` (generated output, committed to repo)

**Files to modify:**

- `Makefile` — add `update-vol-params` target

**Implementation notes:**

Instrument → change unit → SD formula:

| instrument_id | Change unit | SD formula |
|---|---|---|
| SPY, QQQ, UUP, USDJPY, EURUSD, USDCNH, GOLD, SILVER, WTI, BRENT, COPPER, BTC, FESX, VIX, MOVE | `%` | `pct_change().std(ddof=1) * 100` |
| US2Y, US10Y, DE10Y, HY_OAS | `bps` | `diff().std(ddof=1) * 100` (AV/FRED return percent; multiply to get true bps) |

Rules:
- Require >= 200 non-NaN observations; warn and skip the instrument if fewer.
- If any instrument fails to fetch, emit a warning and omit it from the yaml — live mode falls back to a conservative default.
- MOVE fetches via yfinance `^MOVE`; falls back to hardcoded `sd_1d: 4.5` only if yfinance returns no data.

Output format (`app/config/vol_params.yaml`):
```yaml
# Generated by scripts/update_vol_params.py — run `make update-vol-params` monthly.
generated_at: "2026-05-08"
lookback_days: 252
params:
  SPY:
    sd_1d: 1.05
    unit: "%"
  US10Y:
    sd_1d: 4.26
    unit: "bps"
  MOVE:
    sd_1d: 4.98
    unit: "%"
  # ... all instruments
```

Makefile target:
```makefile
update-vol-params:
    $(BIN)/python scripts/update_vol_params.py
```

**Human input needed:** Run `make update-vol-params` with real credentials locally to generate and commit the initial `vol_params.yaml` before wiring Phase D.

---

### 5.D Move detection

**Agent objective:** Add z-score computation and threshold flagging using the committed `vol_params.yaml`.

**Files to modify:**

- `app/data/market.py`
- `tests/test_market.py`

**Implementation notes:**

```python
def load_vol_params(config_dir: Path) -> dict[str, dict]:
    # raises FileNotFoundError with a helpful message if vol_params.yaml is absent

def detect_moves(
    snapshots: list[MarketSnapshot],
    vol_params: dict[str, dict],
    threshold_sigma: float = 1.0,
) -> list[MarketSnapshot]:
```

- Look up `vol_params[instrument_id]`. If missing, use conservative fallback (1.0% or 5.0 bps) and emit a warning.
- **Hard error** if `vol_params[id]["unit"]` does not match `snapshot.one_day_change_unit` — prevents silent wrong z-scores.
- Compute `one_day_zscore = one_day_change / sd_1d`. Set `threshold_flag = abs(one_day_zscore) >= threshold_sigma`.
- Return updated snapshots via `model_copy(update={...})`.

Group move detection (spec §4.1): after individual flags, scan by `(asset_class, region)`. If >= 2 instruments moved the same direction and >= 1 breached threshold, mark all as `threshold_flag=True` and annotate the non-breaching one with a `warning` string.

**Tests:**

- 1.2 sigma move → `threshold_flag=True`, correct z-score value.
- 0.8 sigma move → `threshold_flag=False`.
- Group move: 2 US equities both negative, one at 1.1 sigma → both flagged.
- Missing instrument in vol_params → fallback used, no crash.
- Unit mismatch (bps instrument, % SD) → explicit `ValueError`.

---

### 5.E Live providers

**Agent objective:** Implement the four provider classes that compose the Phase B client functions into `MarketSnapshot` output.

**Files to modify:**

- `app/data/market.py`
- `tests/test_market.py`

**Implementation notes:**

Each class implements `fetch_watchlist(instruments, as_of) -> list[MarketSnapshot]` by:
1. Calling the appropriate Phase B function for the latest 2 available trading days.
2. Computing 1D change from prior close to latest close.
3. Normalising to `MarketSnapshot` using canonical `instrument_id` strings from the table above.
4. Move detection is applied by the caller, not inside the provider.

```python
class AlphaVantageMarketProvider:   # SPY, QQQ, UUP, USDJPY, EURUSD, USDCNH, GOLD, SILVER, WTI, BRENT, BTC, US2Y, US10Y
class DatabentoMarketProvider:      # FESX, DE10Y, COPPER — continuous front-month futures
class FredMarketProvider:           # HY_OAS
class YfinanceMarketProvider:       # VIX (^VIX), MOVE (^MOVE) — both LOW_RELIABILITY
```

Composition function for pipeline use:
```python
def fetch_live_market(settings: Settings, vol_params: dict) -> list[MarketSnapshot]:
    snapshots = (
        AlphaVantageMarketProvider(...).fetch_watchlist(AV_INSTRUMENTS, as_of)
        + DatabentoMarketProvider(...).fetch_watchlist(DB_INSTRUMENTS, as_of)
        + FredMarketProvider(...).fetch_watchlist(FRED_INSTRUMENTS, as_of)
        + YfinanceMarketProvider().fetch_watchlist(YFINANCE_INSTRUMENTS, as_of)
    )
    return detect_moves(snapshots, vol_params)
```

**Human input needed:** Verify Databento symbol availability with a test call locally before the submission run.

**Tests:** Mock HTTP responses with `unittest.mock.patch` per provider. No live API calls in the test suite. Databento front-month roll logic tested with fixture contract data.

---

### 5.F Cache fallback

**Agent objective:** Make live mode fail soft when possible and warn clearly when using stale data.

**Files to modify:**

- `app/data/market.py`
- `app/pipeline.py`
- `tests/test_market.py`

**Implementation notes:**

```python
def cache_market(snapshots: list[MarketSnapshot], cache_dir: Path, as_of: date) -> None:
    # writes .cache/market/latest.json and .cache/market/market_YYYY-MM-DD.json

def load_cached_market(cache_dir: Path) -> list[MarketSnapshot]:
    # reads .cache/market/latest.json; raises FileNotFoundError if absent
```

Wrap `fetch_live_market()`:
```python
try:
    snapshots = fetch_live_market(settings, vol_params)
    cache_market(snapshots, settings.app.cache_dir, today)
    return snapshots
except Exception as exc:
    stale = load_cached_market(settings.app.cache_dir)   # raises if absent
    return [s.model_copy(update={"freshness_status": STALE_CACHE, "warning": f"..."}) for s in stale]
```

- Stale warnings propagate into `RunMetadata.warnings` via the pipeline.
- Fail loudly with `RuntimeError` if live fetch fails and no cache exists.

Wire into `app/pipeline.py` Step 2:
```python
if mode == RunMode.SAMPLE:
    market_snapshots = FixtureMarketProvider().fetch_watchlist(...)
else:
    vol_params = load_vol_params(settings.config_dir)
    market_snapshots = fetch_live_market_with_cache(settings, vol_params)
```

**Tests:**

- Live fail + cache present → stale snapshots returned, each has `STALE_CACHE` status.
- Live fail + no cache → `RuntimeError` raised.
- Warning strings propagate to `RunMetadata`.

---

### Files changed in Step 5

| File | Action |
|---|---|
| `app/data/market.py` | Create — protocol, fixture provider, API clients, live providers, detect_moves, cache |
| `app/config/vol_params.yaml` | Create (generated by script, committed to repo) |
| `scripts/update_vol_params.py` | Create — monthly SD refresh script |
| `tests/test_market.py` | Expand — all unit tests (mocked providers) |
| `app/pipeline.py` | Modify — wire fixture/live branch in Step 2 |
| `Makefile` | Modify — add `update-vol-params` target |

---

## Step 6 — Calendar module and consensus handling

### 6.1 Implement fixture calendar provider

**Agent objective:** Make sample mode calendar deterministic.

**Files to modify:**

- `app/data/calendar.py`
- `tests/test_calendar.py`

**Implementation notes:**

- Load `tests/fixtures/calendar_sample.json`.
- Normalize into `CalendarEvent` objects.
- Include session grouping, importance, previous, consensus, source metadata, and missing-consensus flags.

**Human input needed:** Review sample calendar events and why-it-matters text.

**Acceptance checks:**

- Fixture calendar loads in sample mode.
- Missing consensus is represented explicitly.

### 6.2 Implement Investing.com calendar ingestion boundary

**Agent objective:** Add V1 prototype calendar ingestion behind a small wrapper.

**Files to modify:**

- `app/data/calendar.py`
- `tests/test_calendar.py`
- possibly `app/config/sources.yaml`

**Implementation notes:**

- Build HKT start/end datetime.
- Request the configured Investing.com backend endpoint conservatively.
- Cache raw response daily as `calendar_YYYY-MM-DD.json`.
- Normalize rows into `CalendarEvent`.
- Treat Investing.com `forecast` as consensus when present.
- Filter after ingestion by country, session, and importance.

**Human input needed:** Confirm endpoint behavior, request headers if needed, country filters, and acceptable request frequency.

**Acceptance checks:**

- Tests use fixture/mock raw payloads, not live endpoint calls.
- Cache is used to avoid repeated calls.
- Missing forecast becomes `missing_consensus=true` when appropriate.

### 6.3 Implement consensus enrichment stub

**Agent objective:** Support high-importance missing consensus without letting the LLM invent values.

**Files to modify:**

- `app/data/calendar.py`
- `app/llm/prompts/scouts/consensus_enrichment.md`
- `tests/test_calendar.py`
- optional later: `app/discovery/scouts/consensus.py`

**Implementation notes:**

- Only trigger for high-importance events missing forecast/consensus.
- Accept three methods: `investing_forecast`, `source_extracted`, `computed_from_source`.
- Require source URL and confidence for `source_extracted`.
- Require formula and source-backed inputs for `computed_from_source`.
- Leave consensus blank if the scout cannot find a reliable value.

**Human input needed:** Review one or two real examples of important events where consensus enrichment should be attempted.

**Acceptance checks:**

- Tests prove the LLM scout output is rejected when no source URL is provided.
- Tests prove computed consensus requires formula and inputs.
- Tests prove missing consensus stays blank when unresolved.

---

## Step 7 — LLM provider wrapper and prompt registry

### 7.1 Implement prompt registry

**Agent objective:** Load versioned markdown prompts safely.

**Files to modify:**

- `app/llm/prompt_registry.py`
- `tests/test_llm.py`

**Implementation notes:**

- Load prompts from `app/llm/prompts/**`.
- Prevent path traversal.
- Cache reads.
- Return prompt name, path, and text for metadata.

**Human input needed:** Confirm prompt naming convention before agents start writing prompt files.

**Acceptance checks:**

- Expected prompt files load.
- Path traversal attempts fail.
- Missing prompt names fail clearly.

### 7.2 Implement thin LiteLLM wrapper

**Agent objective:** Provide one project-specific LLM boundary.

**Files to modify:**

- `app/llm/provider.py`
- `tests/test_llm.py`

**Implementation notes:**

- Implement `LLMConfig`, `LLMUsage`, `LLMResult`, and `LLMClient`.
- Support structured JSON output parsed into Pydantic models.
- Capture model name, latency, token usage, and estimated cost if available.
- Provide deterministic fake/stub behavior for tests or sample mode when configured.
- Do not include prompt text in the wrapper.

**Human input needed:** Confirm default model string and credentials strategy for local testing.

**Acceptance checks:**

- Tests mock LiteLLM and parse structured JSON into a Pydantic model.
- Tests cover invalid JSON or schema mismatch.
- Sample mode can avoid live LLM calls when configured.

### 7.3 Write initial prompt files

**Agent objective:** Create prompts that match the architecture boundaries.

**Files to create:**

- `app/llm/prompts/brief_writer.md`
- `app/llm/prompts/critic.md`
- `app/llm/prompts/scouts/news_search.md`
- `app/llm/prompts/scouts/central_bank_extract.md`
- `app/llm/prompts/scouts/research_search.md`
- `app/llm/prompts/scouts/podcast_extract.md`
- `app/llm/prompts/scouts/x_narrative_search.md`
- `app/llm/prompts/scouts/consensus_enrichment.md`

**Implementation notes:**

- Prompts must say to use only supplied evidence.
- Prompts must forbid invented numbers, links, consensus values, and unsupported claims.
- The writer prompt must enforce all six required sections and word limits.
- Scout prompts must return structured candidates with source metadata.

**Human input needed:** Review tone, word limits, and macro phrasing.

**Acceptance checks:**

- `test_llm.py` confirms all expected prompt files exist and load.
- Prompts do not include real secrets or private portfolio facts.

---

## Step 8 — Discovery scouts

### 8.1 Implement discovery base classes and context

**Agent objective:** Create reusable scout interfaces and shared helpers.

**Files to modify:**

- `app/discovery/scouts/base.py`
- `app/discovery/orchestrator.py`
- `tests/test_discovery.py`

**Implementation notes:**

- Define `DiscoveryContext` containing market moves, calendar events, themes, portfolio assumptions, time window, and mode.
- Define a simple scout protocol returning `EvidenceCard` objects.
- Include timeout/retry hooks where useful.
- Optional scout failure should not kill the run.

**Human input needed:** Confirm which scouts are required for sample mode and which are optional in live mode.

**Acceptance checks:**

- A failing optional scout is recorded as failed but pipeline continues.
- Scout output normalizes to `EvidenceCard`.

### 8.2 Implement fixture discovery scout

**Agent objective:** Make sample mode discovery deterministic.

**Files to modify:**

- `app/discovery/scouts/base.py` or a fixture scout module
- `app/discovery/orchestrator.py`
- `tests/test_discovery.py`

**Implementation notes:**

- Load `tests/fixtures/evidence_sample.json`.
- Return evidence cards with source URL, confidence, thesis, evidence, tags, macro relevance, and portfolio relevance.

**Human input needed:** Review fixture evidence quality and whether it supports all sample brief sections.

**Acceptance checks:**

- Sample evidence loads without live search.
- Every evidence card has source metadata.

### 8.3 Implement source-specific scout modules

**Agent objective:** Add all five confirmed scout modules with correct V1 behavior per `architecture.md §11`.

**Files to modify:**

- `app/discovery/scouts/news.py`
- `app/discovery/scouts/central_bank.py`
- `app/discovery/scouts/research.py`
- `app/discovery/scouts/podcast.py`
- `app/discovery/scouts/x.py`
- `tests/test_discovery.py`

**Implementation notes:**

- `news.py`, `central_bank.py`, `research.py`: use `web_search_and_structure()` from `base.py`, which calls the OpenAI Responses API (`client.responses.create()` with `web_search_preview`). LiteLLM blocks this tool type. Load prompts from `app/llm/prompts/scouts/`. Return structured `EvidenceCard` objects with source URLs. Model string strips the `openai/` prefix before passing to the OpenAI SDK.
- `podcast.py`: (1) query Listen Notes API for last 24h episodes; (2) LiteLLM GPT filter (`json_object` format) selects portfolio-relevant episodes; (3) OpenAI Responses API `web_search_preview` recalls public summaries/show notes per episode. No audio transcription or Gemini in V1. Requires `LISTEN_NOTES_API_KEY`, `OPENAI_API_KEY`.
- `x.py`: use `xai_sdk.Client` with the `x_search` Agent Tool (not LiteLLM — LiteLLM cannot route xAI server-side tools). Model must be `grok-4` or later; grok-3 does not support server-side tools. Post-process with `_filter_verified()` to drop empty titles and non-X URLs. Requires `XAI_API_KEY`.
- All scouts: can be disabled in `sources.yaml`. Failed optional scouts are recorded in `RunMetadata`, not raised.

**Human input needed:** Confirm which scouts must produce live output for the submission demo vs which can remain fixture-backed.

**Acceptance checks:**

- Each scout can be disabled in config.
- Each scout returns zero or more valid `EvidenceCard` objects with source metadata.
- Failed optional scouts are recorded without killing the run.
- All scouts are tested with mocked provider responses (OpenAI Responses API mock for news/CB/research/podcast, xai_sdk Client mock for X scout).

---

## Step 9 — Evidence deduplication and ranking

### 9.1 Implement evidence deduper

**Agent objective:** Remove duplicates while preserving genuinely different viewpoints.

**Files to modify:**

- `app/synthesis/deduper.py`
- `tests/test_deduper.py`

**Implementation notes:**

- Canonicalize URLs.
- Use source/title/time keys.
- Prefer primary or official sources over summaries when duplicates exist.
- Preserve alternative perspectives when thesis differs.

**Human input needed:** Review sample duplicate examples if the deduper seems too aggressive.

**Acceptance checks:**

- Duplicate URLs collapse to one item.
- Official source beats secondary summary.
- Different thesis items are retained.

### 9.2 Implement ranker

**Agent objective:** Score markets, calendar events, and evidence for brief selection.

**Files to modify:**

- `app/synthesis/ranker.py`
- `tests/test_ranker.py`

**Implementation notes:**

- Use ranking factors from `architecture.md`: portfolio relevance, asset move magnitude, freshness, source quality, novelty, cross-asset confirmation, calendar importance, theme match, confidence.
- Produce a `RankedBriefContext` or equivalent object.
- Select candidates for dashboard rows, three things, theme radar, chart, and contrarian corner.

**Human input needed:** Confirm initial scoring weights and whether the sample output feels PM-relevant.

**Acceptance checks:**

- Portfolio-relevant evidence ranks above generic news.
- High-importance calendar events rank above low-importance events.
- Large market moves with catalysts rank high.

---

## Step 10 — Synthesis writer and deterministic validator

### 10.1 Implement structured brief writer

**Agent objective:** Turn ranked structured context into a `BriefDraft`.

**Files to modify:**

- `app/models.py` — add `BriefWriterOutput` model
- `app/synthesis/writer.py`
- `app/llm/prompts/brief_writer.md`
- `app/pipeline.py`
- `tests/test_writer.py`

**Implementation notes:**

- Add `BriefWriterOutput` to `app/models.py`: contains only the LLM-written fields — `book_impact: str | None`, `three_things: list[BriefItem]`, `radar_items: list[BriefItem]`, `contrarian_corner: BriefItem | None`, `chart_caption: str | None`, `warnings: list[str]`. The LLM does not reproduce dashboard or calendar data; the pipeline assembles the final `BriefDraft` by merging `BriefWriterOutput` with the market snapshots and calendar events already in the pipeline.
- Add optional source metadata and topic fields to `BriefItem`: `source_name`, `source_url`, `source_type`, and `topic_label`. The LLM still returns only `supporting_evidence_ids`; code copies trusted source metadata from matching `EvidenceCard` objects and derives the topic label during draft assembly.
- Load prompt through prompt registry (`brief_writer.md`).
- Send market snapshots, calendar events, evidence cards, themes, portfolio assumptions, and warnings as a single structured JSON payload.
- Make one LiteLLM call via `app/llm/provider.py` requesting structured JSON output; parse response into `BriefWriterOutput`, then assemble `BriefDraft`.
- One shot only — the full brief is produced in a single call, not one call per section.
- Default to LiteLLM; switch to OpenAI SDK directly only if structured output is not performing.
- Sample mode may use a deterministic fixture-backed writer so reviewers can run without credentials; live/dry-run synthesis uses the configured LLM path.
- Wire the writer call into `app/pipeline.py` after the ranking step so `make run-sample` produces a real `BriefDraft`.

**`brief_writer.md` must include:**

- Hard constraint: use only supplied data; never invent prices, yields, links, authors, or consensus values; write `no confirmed fresh catalyst` when no catalyst is confirmed.
- `book_impact` is optional, narrative-only, and limited to 1–2 sentences. It may reference supplied market moves and configured synthetic book positions/themes, but must not estimate P&L, bps impact, portfolio delta, hedge offsets, position size, or percentages.
- Exact word limits per section: `three_things` body ≤ 80 words each; `radar_items` body 60–100 words each; `contrarian_corner` body 50–100 words; `chart_caption` ≤ 30 words.
- `so_what` in every `three_things` and `radar_items` item must reference a specific position or theme from supplied `portfolio_context`.
- `radar_items` must summarise the author's thesis and evidence, not just the abstract; prefer non-mainstream sources (central bank speeches, research notes, Substacks, podcasts, X threads, buy-side letters) over mainstream financial media. Radar headlines should not append source names; code attaches links/source metadata and derives the visible topic label.
- `contrarian_corner` must be framed as "the market is not pricing X, worth watching because Y"; return null if no genuine mispricing narrative is supported by the evidence — do not force one.
- Sparse evidence: if fewer than three high-quality evidence cards exist, produce only as many `three_things` items as are genuinely supported and add a warning.
- Stale data: any section referencing a `freshness_status=STALE_CACHE` instrument must note it inline (e.g. `note: price data is stale`).

**Human input needed:** Review wording and whether the brief has a point of view without overclaiming.

**Acceptance checks:**

- `make run-sample` produces a populated `BriefDraft` with all six required sections.
- LLM writer path can be tested with mocked LiteLLM structured output.
- No writer path invents data outside supplied context.
- `contrarian_corner` is null when no supporting evidence is provided.

### 10.2 Implement deterministic validator

**Agent objective:** Add the final quality gate for hallucination-sensitive output.

**Files to modify:**

- `app/synthesis/validator.py`
- `app/pipeline.py`
- `tests/test_validator.py`

**Validator failure severity:**

Critical failures hard-fail the run and suppress delivery:

- Any required section missing.
- Market number present without source metadata.
- Numeric `book_impact` language such as estimated bps/P&L/percentage impact without a future explicit exposure model.
- Link present without source metadata.
- Consensus value present without source metadata and not directly from cached Investing.com payload.

Minor failures attach a warning banner to the brief but allow delivery:

- Three-things item exceeds 80 words.
- Theme radar summary outside 60–100 words.
- Chart caption exceeds 30 words.
- Contrarian corner outside 50–100 words.
- A `so what` line does not map to configured portfolio/themes.
- Stale/cache warning not rendered when stale data is present.
- Computed consensus missing formula or inputs.
- LLM-enriched consensus missing source URL or confidence.

**Implementation notes:**

- Wire the validator call into `app/pipeline.py` immediately after the writer step.
- Critical failures exit non-zero and set `PipelineResult` to failed; minor failures populate `RunMetadata.warnings`.

**Acceptance checks:**

- Tests cover each critical rule (unsupported number, unsupported link, missing section, missing source metadata).
- Tests cover each minor rule (word-limit violation, missing `so what`, missing stale warning).
- Critical failure exits non-zero; minor failure produces a non-empty warnings list and does not exit non-zero.

### 10.3 Critic/repair pass — deferred to V2

---

## Step 11 — Rendering and charting

### 11.1 Implement chart builder (LLM-driven code generation)

**Agent objective:** Generate one chart per brief by having an LLM write matplotlib code on the fly, validated by subprocess execution. Replaces the static template approach with a flexible, dual-axis-capable renderer.

**Files to create / modify:**

- `app/data/market.py` — add `fetch_chart_series()` + `FixtureChartSeriesProvider`
- `app/render/charts.py` — entry point: `build_chart(draft, output_path, sample_mode) -> ChartSpec`
- `app/render/chart_codegen.py` — series selection, data prep, LLM call, subprocess execution, retry + fallback
- `app/llm/prompts/chart_codegen.md` — **already created** (prompt spec below)
- `app/models.py` — add `code_generated: bool = False` to `ChartSpec`
- `tests/test_render.py`

**Historical data layer (`app/data/market.py`):**

New function `fetch_chart_series(instruments, lookback_days, as_of, settings, sample_mode)` routes each instrument ID to the correct existing raw fetch function using the already-defined metadata tables (`_AV_INSTRUMENT_META`, `_DB_INSTRUMENT_META`, `_YF_INSTRUMENT_META`, `_FRED_INSTRUMENT_META`). Returns `dict[str, list[dict]]` — `{instrument_id: [{date, close}, ...]}` sorted ascending. Default lookback: 30 calendar days.

`FixtureChartSeriesProvider`: deterministic synthetic time-series (seeded random walk per instrument name) — no API calls, no new fixture file.

**Series selection — hybrid with fallback:**

1. Primary: match dominant theme from `BriefDraft.three_things[0]` against `chart_templates.yaml` theme_tags → use template's `instruments` list.
2. Fallback (no match): pass all `overnight_dashboard` instrument IDs to LLM; it picks 2–4.
3. LLM can only reference injected data — cannot invent values.

**Code generation & validation:**

1. Call `fetch_chart_series()` with selected instruments.
2. Inject data as Python dict literals + calendar events from `BriefDraft.todays_calendar` (dates within range).
3. Call LLM via `app/llm/client.py` (LiteLLM; model `gpt-4o` or project default) using `chart_codegen.md` prompt.
4. Execute in subprocess (`timeout=60s`); check exit code and PNG exists. The longer default covers first-run Matplotlib font-cache initialization in clean CI/local environments.
5. On failure: inject stderr, retry once.
6. On second failure: hardcoded bar chart of `one_day_change` for dashboard instruments.

**Prompt spec (`app/llm/prompts/chart_codegen.md` — already written):**

- `figsize=(10, 3.5)` → ~500×175px displayed in email (1/3 screen height, 3/4 column width)
- White bg `#FFFFFF`, axes `#FAFAFA`, subtle grey gridlines
- Dual y-axis: price/% left, index/bps right; single axis if all series share unit type
- Asset-class colors: equity `#2563EB`, rates `#EA580C`, vol `#DC2626`, FX `#16A34A`, commodity `#D97706`
- Calendar events → vertical dashed grey lines + rotated labels; LLM may add ≤2 from data context
- Bold title + grey date-range subtitle; legend `loc="best"`; `tight_layout(pad=1.5)`
- Output: `plt.savefig(output_path, dpi=150, bbox_inches="tight")`

**Acceptance checks:**

- `outputs/sample_chart.png` is created with fixture data (`make run-sample`).
- PNG dimensions are wide and short (~10:3.5 aspect ratio).
- Subprocess executes without error; file is a valid image.
- Caption passes validator (≤30 words).
- Dual-axis renders correctly when series scales differ by > 10×.
- Hardcoded fallback produces a PNG when codegen fails twice.
- Chart data is traceable to fixture or `overnight_dashboard` source data.

### 11.2 Implement HTML and text rendering

**Agent objective:** Render `BriefDraft` into readable email outputs.

**Files to modify:**

- `app/render/email.py`
- `app/render/templates/brief_template.html` — production Jinja email template
- `app/render/templates/example.html` — non-runtime visual reference with mock values only
- `tests/test_render.py`

**Implementation notes:**

- Include compact header with run timestamp and data cutoff.
- Render sections in required order.
- Render stale/failure warnings near the top.
- Include dashboard and calendar as tables.
- Include chart path or inline chart reference as appropriate.
- Create plain-text fallback.
- Do not ask an LLM to decide dashboard arrows, colors, or bold styling.
- Add deterministic presentation logic before template rendering. Convert each `MarketSnapshot` into a render row with formatted `last`, formatted `change`, `change_class`, `trend_arrow`, and `trend_class`.
- For 1D dashboard emphasis, use existing `threshold_flag` or `abs(one_day_zscore) >= 1.0`; bold significant moves via `sig-move`.
- For 1D direction color, use the sign of `one_day_change`: positive `up`, negative `down`, zero `flat`.
- For 5D trend arrows, use `five_day_change` and daily volatility from `vol_params.yaml`: `five_day_sigma = sd_1d * sqrt(5)`, `five_day_zscore = five_day_change / five_day_sigma`.
- Use a low flat threshold for 5D trend display, initially `abs(five_day_zscore) < 0.25` → `→`; otherwise positive → `↗`, negative → `↘`. This is a presentation heuristic, not a trading signal.
- LLM-generated synthesis may populate textual fields only. It must not mutate market numbers, calendar values, source labels, links, section order, CSS classes, or significance logic.

**Human input needed:** Review visual style and email readability.

**Acceptance checks:**

- HTML and text include all required sections.
- Warnings render when present.
- Output files are saved to the expected paths.
- Tests cover dashboard presentation mapping: 1D significant move class, up/down/flat color class, and 5D arrow calculation using the sqrt(5) volatility rule.

---

## Step 12 — Delivery layer

### 12.1 Implement delivery provider interface

**Agent objective:** Add replaceable email delivery without coupling it to the pipeline.

**Files to modify:**

- `app/delivery.py`
- `tests/test_delivery.py` if useful

**Implementation notes:**

- Define `DeliveryProvider` protocol.
- Implement `NoopDeliveryProvider`.
- Implement `PostmarkDeliveryProvider`.
- Sample and dry-run modes send test output to `POSTMARK_MAINTAINER_EMAIL` when Postmark send credentials are configured.
- Live mode sends to `POSTMARK_TO_EMAIL` only if delivery is enabled and required production recipients are configured.
- Always save artifacts even if email delivery fails.

**Human input needed:** Provide Postmark sender/recipient decisions and local secrets outside git.

**Acceptance checks:**

- Noop delivery records success without sending.
- Postmark path can be unit-tested with mocked HTTP request.
- Missing email secrets fail clearly when a Postmark send path is selected.

### 12.2 Wire delivery into pipeline

**Agent objective:** Connect rendered outputs to delivery and metadata.

**Files to modify:**

- `app/pipeline.py`
- `app/delivery.py`
- `tests/test_pipeline_sample.py`

**Implementation notes:**

- In sample and dry-run, use Postmark with `POSTMARK_MAINTAINER_EMAIL` when Postmark send credentials are configured.
- In live mode, use Postmark with `POSTMARK_TO_EMAIL` only when enabled.
- Record delivery status in `RunMetadata`.

**Human input needed:** Confirm whether live mode default should be dry-run until final manual toggle.

**Acceptance checks:**

- Sample and dry-run send to the maintainer recipient, not the production recipient list.
- Delivery status appears in run metadata.

---

## Step 13 — End-to-end sample mode

### 13.1 Wire sample mode from fixtures to outputs

**Agent objective:** Make the full local demo work without credentials.

**Files to modify:**

- `app/pipeline.py`
- `app/data/market.py`
- `app/data/calendar.py`
- `app/discovery/orchestrator.py`
- `app/synthesis/*`
- `app/render/*`
- `tests/test_pipeline_sample.py`

**Implementation notes:**

- Use fixture market data, fixture calendar data, fixture evidence, deterministic writer or mocked/stubbed LLM path, validator, renderer, and maintainer-routed Postmark delivery when configured.
- Always produce the three expected sample artifacts.

**Human input needed:** Review final sample brief quality and determine whether it is impressive enough for the case study.

**Acceptance checks:**

```bash
make run-sample
ls -l outputs/sample_brief.html outputs/sample_brief.txt outputs/sample_chart.png
pytest tests/test_pipeline_sample.py
```

### 13.2 Polish sample output

**Agent objective:** Improve the fixture data, ranking, prompt, and rendering until the sample brief feels like a PM-ready morning brief.

**Files likely to modify:**

- `tests/fixtures/*.json`
- `app/config/*.yaml`
- `app/llm/prompts/brief_writer.md`
**Implementation notes:**

- Keep language direct and concise.
- Force selection; do not summarize everything.
- Include explicit `so what` lines tied to the synthetic book.
- Ensure the chart adds insight rather than decoration.

**Human input needed:** Human coder should read the sample output as the target PM and mark weak sections.

**Acceptance checks:**

- Human review approves sample brief quality.
- Validator still passes.
- Required outputs still generate.

---

## Step 14 — Live mode and dry-run behavior

### 14.1 Wire live data collection safely

**Agent objective:** Connect live providers behind mode and config switches.

**Files to modify:**

- `app/pipeline.py`
- `app/data/market.py`
- `app/data/calendar.py`
- `app/settings.py`
- `app/config/sources.yaml`

**Implementation notes:**

- Live mode uses Alpha Vantage primary, Databento supplemental where configured, and Investing.com calendar endpoint.
- Dry-run uses live or cached data and sends the email to `contact@leonard-dai.com` instead of the production recipient list.
- Cache fallback behavior must be visible in warnings and metadata.

**Human input needed:** Run with real credentials locally and inspect provider responses. Confirm which live sources are stable enough for the submission.

**Acceptance checks:**

```bash
make dry-run
```

- Dry-run generates outputs.
- Dry-run sends email to `contact@leonard-dai.com` (not production recipients).
- Warnings are clear when fallback data is used.

### 14.2 Add run artifacts and metadata

**Agent objective:** Persist auditable output for each run.

**Files to modify:**

- `app/pipeline.py`
- `app/models.py`
- possibly `app/render/email.py`

**Implementation notes:**

- Save run outputs under `outputs/runs/YYYY-MM-DD_HHMM_HKT/`.
- Save `brief.html`, `brief.txt`, `chart.png`, `run_metadata.json`, `market_snapshot.json`, `calendar.json`, and `evidence_cards.json`.
- Do not commit private live run outputs unless scrubbed.

**Human input needed:** Decide whether to commit only sample outputs and exclude `outputs/runs/**` by default.

**Acceptance checks:**

- Run directory is created.
- Metadata includes mode, source failures, warnings, prompt names, model, token/cost fields if available, delivery status, and output paths.

---

## Step 15 — Testing, CI, and failure handling

### 15.1 Complete minimum test suite

**Agent objective:** Cover the high-value failure modes before polishing live integrations.

**Files to modify:**

- `tests/test_config.py`
- `tests/test_market.py`
- `tests/test_calendar.py`
- `tests/test_llm.py`
- `tests/test_deduper.py`
- `tests/test_ranker.py`
- `tests/test_validator.py`
- `tests/test_render.py`
- `tests/test_pipeline_sample.py`

**Implementation notes:**

- Prioritize hallucination-sensitive validation, consensus behavior, stale market cache warning, output section completeness, prompt registry, structured LLM wrapper, and sample mode.
- Mock live providers; do not depend on external APIs in tests.

**Human input needed:** Approve test scope if time is short.

**Acceptance checks:**

```bash
make test
```

### 15.2 Add GitHub Actions workflow

**Agent objective:** Run the project on schedule and support manual dispatch.

**Files to modify:**

- `.github/workflows/daily_brief.yml`

**Implementation notes:**

- Use cron `15 23 * * 0-4` for 07:15 HKT Monday–Friday.
- Include `workflow_dispatch`.
- Checkout, set up Python, install dependencies, run tests or smoke validation, run live or dry-run depending on secrets/config, upload artifacts.

**Human input needed:** Add GitHub repository secrets or choose dry-run-only CI for the case study.

**Acceptance checks:**

- Workflow syntax is valid.
- Manual dispatch can run.
- Artifacts are uploaded.

### 15.3 Implement failure handling matrix

**Agent objective:** Ensure known failure cases behave as designed.

**Files to modify:**

- `app/pipeline.py`
- `app/data/market.py`
- `app/data/calendar.py`
- `app/discovery/orchestrator.py`
- `app/synthesis/writer.py`
- `app/synthesis/validator.py`
- `app/delivery.py`
- tests as needed

**Implementation notes:**

- Market live fail with cache: continue with warning.
- Market live fail without cache: fail loudly in live mode.
- Optional discovery scout fail: continue and record failed source.
- Missing consensus: enrich only when high-importance and source-backed; otherwise blank.
- LLM output validation fail: one repair pass if implemented, then fail critical issues.
- Postmark fail: save artifacts and record failed delivery.

**Human input needed:** Confirm severity levels for validation failures and live-mode send/no-send behavior.

**Acceptance checks:**

- Tests or smoke checks cover each critical branch.

---

## Step 16 — Documentation and submission assets

### 16.1 Write README

**Agent objective:** Make the repo understandable to reviewers.

**Files to modify:**

- `README.md`

**README sections:**

- What the project does.
- Why the design is grounded and not a news regurgitator.
- Quickstart.
- Runtime modes.
- Required environment variables.
- How to run tests.
- How to run sample mode.
- How live mode works.
- Known limitations.

**Human input needed:** Confirm final screenshots/output paths and whether to include a sample brief image or snippet.

**Acceptance checks:**

- A reviewer can run sample mode from README instructions.

### 16.2 Write costs.md

**Agent objective:** Document daily run cost assumptions.

**Files to modify:**

- `costs.md`

**Content to include:**

- LLM token usage and estimated cost by configured model.
- LiteLLM package/version assumption.
- Alpha Vantage assumptions.
- Databento assumptions.
- Investing.com prototype calendar assumption.
- GitHub Actions cost assumption.
- Postmark cost assumption.
- Future paid calendar API assumption.

**Human input needed:** Provide actual plan tiers or approximate cost assumptions to use.

**Acceptance checks:**

- Costs are approximate but explicit.
- Free-tier assumptions are clearly labeled.

### 16.3 Write memo

**Agent objective:** Produce the required 1-page case-study memo.

**Files to modify:**

- `memo/memo.md`
- optionally `memo/memo.pdf`

**Memo must cover:**

- Design tradeoffs.
- Why these modules, data sources, and LLM boundary were chosen.
- Position/theme assumptions.
- Three V2 features that were not built.
- One-month full-time extension path.
- Actual hours spent.

**Human input needed:** Human coder must provide actual hours spent and approve the tone.

**Acceptance checks:**

- Memo is concise enough for one page when rendered.
- Memo states assumptions honestly.

### 16.4 Final acceptance audit

**Agent objective:** Compare implementation against `spec.md`, `architecture.md`, and this plan.

**Files to modify:**

- Maybe none.
- Optionally create `docs/acceptance_checklist.md` only if useful and approved.

**Human input needed:** Final product judgment and merge approval.

**Acceptance checks:**

```bash
make lint
make test
make run-sample
```

Manual audit checklist:

- Six sections present.
- No invented market/calendar numbers.
- Sample artifacts generated.
- Validator catches bad output.
- Email delivery disabled by default in sample/dry-run.
- GitHub Actions schedule present.
- Costs and memo complete.
- Repo has no secrets.

---

## 7-day execution suggestion

### Day 1 — Blueprint and scaffold

- Complete Steps 0–2.
- Human checkpoint: approve repo structure, models, and config field names.

### Day 2 — Config, fixtures, and sample pipeline skeleton

- Complete Steps 3–4.
- Start Step 13.1 with fixture-only pipeline.
- Human checkpoint: approve fixture plausibility.

### Day 3 — Market and calendar data

- Complete Steps 5–6 fixture paths and cache behavior.
- Add live wrappers only where stable enough.
- Human checkpoint: confirm symbols, thresholds, and calendar source behavior.

### Day 4 — LLM boundary and prompts

- Complete Step 7.
- Start writer/validator work.
- Human checkpoint: approve model string, prompt tone, and deterministic sample-writer fallback.

### Day 5 — Discovery, ranking, synthesis, validation

- Complete Steps 8–10.
- Human checkpoint: read sample brief and mark weak sections.

### Day 6 — Rendering, delivery, CI

- Complete Steps 11–15.
- Human checkpoint: approve HTML output, delivery toggle, and GitHub Actions behavior.

### Day 7 — Polish, docs, final submission

- Complete Step 16.
- Run final acceptance audit.
- Human checkpoint: final memo hours, source caveats, and submission review.

---

## Human coder checkpoints summary

The human coder should supervise these decisions directly:

1. Repo/file overwrites before scaffold work.
2. Synthetic portfolio assumptions and themes.
3. Watchlist symbols, thresholds, and instrument groups.
4. Databento datasets/symbols and Alpha Vantage symbol mappings.
5. Investing.com endpoint behavior and acceptable request frequency.
6. LLM model string, provider credentials, and whether sample mode may call the LLM.
7. Prompt tone and final PM-readability of the sample brief.
8. Postmark sender/recipient configuration.
9. Live-mode validation severity and send/no-send rules.
10. Actual hours spent in `memo/memo.md`.
11. Final acceptance audit before submission.

---

## Potential Future Changes

### Databento commodity migration review

**Status:** Exploration only — no code changes made.

**Context:** Alpha Vantage commodity data has shown inconsistencies. Databento was evaluated as a possible replacement source for commodity daily closes.

**Current design:** Keep the hybrid provider stack unless a product-definition change is explicitly approved:

- Alpha Vantage remains the source for `GOLD`, `SILVER`, `WTI`, and `BRENT`.
- Databento remains the source for `COPPER` via `GLBX.MDP3` / `HG.c.0`.
- Migrating all commodities to Databento would change spot/benchmark-style series into continuous futures proxies.

**Databento documentation findings:**

- Minimal required Databento schema for daily close is `ohlcv-1d`.
- `timeseries.get_range` uses an exclusive `end` timestamp.
- `ohlcv-1d` bars are UTC-date based and may differ from exchange-session settlement prices.
- Continuous futures symbols use `[ROOT].[ROLL_RULE].[RANK]`, for example `CL.c.0`, `GC.c.0`, `SI.c.0`, `HG.c.0`, and `BRN.c.0`.
- Continuous prices are original, unadjusted prices; they are not back-adjusted across rolls.

**Live probe summary, 2026-05-09 HKT:**

- `GLBX.MDP3` resolved `GC.c.0`, `SI.c.0`, `CL.c.0`, `HG.c.0`, and `BZ.c.0`.
- `IFEU.IMPACT` resolved `BRN.c.0`; `B.c.0` was not found.
- Narrow `ohlcv-1d` request for GLBX commodities over 2026-05-07 to 2026-05-08 returned 10 rows in about 5.1 seconds.
- Narrow `ohlcv-1d` request for ICE Brent over the same range returned rows in about 2.2 seconds.
- ICE Brent returned multiple rows per date from different publishers; existing highest-volume dedupe logic is relevant.
- A plain request ending on 2026-05-09 exceeded current historical entitlement cutoffs, so requests must avoid crossing Databento's available range.

**Important implementation risk found:** The current Databento provider computes `end = as_of.date() - 1 day`, then passes that date as Databento's exclusive `end`. For an `as_of` of 2026-05-09, the existing provider returned the 2026-05-07 copper close even though a direct probe showed a 2026-05-08 copper bar was available when using an intraday end timestamp before the entitlement cutoff. Before expanding Databento usage, fix or explicitly test the date-window semantics so "one day before" means the intended latest available daily bar.

**Recommendation:** Do not migrate all commodities to Databento yet. First decide whether the brief should represent spot/benchmark macro prices or tradable futures proxies. If futures proxies are acceptable, consider a staged migration:

1. Fix Databento `end` handling and add tests for exclusive-end behavior.
2. Compare Alpha Vantage versus Databento for WTI and Brent over 10-20 recent business days.
3. Keep `GOLD` and `SILVER` on Alpha Vantage unless futures proxy behavior is explicitly desired.
4. Consider Databento for `WTI` via `GLBX.MDP3` / `CL.c.0` and `BRENT` via `IFEU.IMPACT` / `BRN.c.0`.
5. Keep caching and timeout fallback because small Databento calls can still take several seconds.
