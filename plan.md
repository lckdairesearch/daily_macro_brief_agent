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

Summary: V1 is done when `make run-sample`, `make test`, and `make lint` all pass, the sample brief contains all six required sections with no invented data, the validator catches hallucination-sensitive failures, GitHub Actions can run the scheduled workflow, email delivery defaults to off, and `costs.md` and `memo/memo.md` are complete.

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
| M7 | Add delivery/scheduling | SendGrid/noop delivery, GitHub Actions | Add secrets or keep dry-run |
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

### 5.1 Implement fixture market provider

**Agent objective:** Make sample mode market data deterministic.

**Files to modify:**

- `app/data/market.py`
- `tests/test_market.py`

**Implementation notes:**

- Load `tests/fixtures/market_sample.json` or a configured fixture path.
- Normalize fixture rows into `MarketSnapshot` objects.
- Include 1D and optional 5D moves.
- Include source and freshness metadata.

**Human input needed:** Review sample instrument list and macro plausibility.

**Acceptance checks:**

- Fixture provider returns valid `MarketSnapshot` objects.
- Missing source metadata fails tests.

**Suggested commands:**

```bash
pytest tests/test_market.py
```

### 5.2 Implement market move detection

**Agent objective:** Add deterministic notable-move flags used by discovery and ranking.

**Files to modify:**

- `app/data/market.py`
- `tests/test_market.py`

**Implementation notes:**

- Use configured thresholds or fixture z-scores.
- Include a move if absolute 1D move is at least one configured 1D standard deviation.
- Include group moves only when at least two related instruments move together and at least one breaches threshold.
- Use 5D context only when configured and useful.

**Human input needed:** Confirm initial thresholds and instrument groupings.

**Acceptance checks:**

- Tests cover single-proxy inclusion, group inclusion, and non-inclusion.
- Output can explain `no confirmed fresh catalyst` when needed later.

### 5.3 Implement live market provider wrappers

**Agent objective:** Add provider boundaries for all four confirmed live market data providers.

**Files to modify:**

- `app/data/market.py`
- `tests/test_market.py`
- `app/config/sources.yaml`

**Implementation notes:**

Implement all four provider classes per `architecture.md §9.2`:

- `AlphaVantageMarketProvider` — primary. Covers equities (SPY, QQQ), FX (USDJPY, EURUSD, USDCNH), commodities (Gold, WTI, Brent, Copper), crypto (BTC), US Treasury yields (2Y, 10Y via `TREASURY_YIELD` endpoint), DXY proxy (UUP ETF).
- `DatabentoMarketProvider` — FESX (Euro Stoxx 50, dataset `XEUR.EOBI`), FGBL (German Bund, dataset `XEUR.EOBI`), VX (VIX, dataset `GLBX.MDP3`/CFE). Must implement front-month roll logic for futures contracts.
- `FredMarketProvider` — HY OAS via series `BAMLH0A0HYM2`.
- `YfinanceMarketProvider` — MOVE index via `^MOVE`. Mark all outputs with `freshness_status=low_reliability`.

Normalize every provider response into `MarketSnapshot`. Do not leak raw provider payloads downstream.

**Human input needed:** Provide API keys locally and verify Databento symbol availability with a test call before the submission run.

**Acceptance checks:**

- Provider wrappers can be unit-tested with mocked payloads.
- Sample tests do not need live credentials.
- Live provider failures return controlled errors.
- Databento front-month roll logic is tested with fixture contract data.

### 5.4 Implement market cache fallback

**Agent objective:** Make live mode fail soft when possible and warn clearly when using stale data.

**Files to modify:**

- `app/data/market.py`
- `tests/test_market.py`
- possibly `app/pipeline.py`

**Implementation notes:**

- Save latest snapshots under `.cache/market/`.
- On live call failure, load latest cached snapshot if available.
- Mark snapshots with `freshness_status=stale_cache`.
- Add warnings to `RunMetadata`.
- Fail loudly in live mode when no live data and no cache exist.

**Human input needed:** Decide acceptable max age for cached market data in live mode.

**Acceptance checks:**

- Tests cover live failure with cache, live failure without cache, and warning propagation.

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

- `news.py`, `central_bank.py`, `research.py`: use `LLMClient` with `llm.scout_model` from `app/config/sources.yaml` (OpenAI GPT-5.4 by default). Load prompts from `app/llm/prompts/scouts/`. Return structured `EvidenceCard` objects with source URLs.
- `podcast.py`: query Listen Notes API for last 24h episodes → LLM filters for portfolio-relevant thesis → transcribe via OpenAI Whisper → fallback to Gemini for YouTube or failed audio. See `architecture.md §11.3` for full flow. Requires `LISTEN_NOTES_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`.
- `x.py`: use `LLMClient` with `x_scout_model` (Grok/xAI by default). Load prompt from `app/llm/prompts/scouts/x_narrative_search.md`. Model is hot-swappable via config. Treat output as discovery only; corroborate high-impact claims.
- All scouts: can be disabled in `sources.yaml`. Failed optional scouts are recorded in `RunMetadata`, not raised.

**Human input needed:** Confirm which scouts must produce live output for the submission demo vs which can remain fixture-backed.

**Acceptance checks:**

- Each scout can be disabled in config.
- Each scout returns zero or more valid `EvidenceCard` objects with source metadata.
- Failed optional scouts are recorded without killing the run.
- Podcast scout transcription fallback to Gemini is tested with a mocked response.

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

- `app/synthesis/writer.py`
- `app/llm/prompts/brief_writer.md`
- `tests/test_writer.py` if useful

**Implementation notes:**

- Load prompt through prompt registry.
- Send market snapshots, calendar events, evidence cards, themes, portfolio assumptions, and warnings as structured JSON.
- Produce a `BriefDraft` object.
- Support deterministic sample writer when LLM calls are disabled in sample mode.

**Human input needed:** Review wording and whether the brief has a point of view without overclaiming.

**Acceptance checks:**

- Sample writer returns all six required sections.
- LLM writer path can be tested with mocked structured output.
- No writer path invents data outside supplied context.

### 10.2 Implement deterministic validator

**Agent objective:** Add the final quality gate for hallucination-sensitive output.

**Files to modify:**

- `app/synthesis/validator.py`
- `tests/test_validator.py`

**Validator checks:**

- All required sections present.
- No unsupported market numbers.
- No unsupported links.
- No consensus values without source metadata unless directly from cached Investing.com payload.
- Computed consensus includes formula and inputs.
- LLM-enriched consensus includes source URL and confidence.
- Three-things items are no more than 80 words.
- Theme radar summaries are 60–100 words.
- Chart caption is no more than 30 words.
- Contrarian corner is 50–100 words.
- Each `so what` maps to configured portfolio/themes.
- Stale/cache warnings are rendered when needed.

**Human input needed:** Confirm whether validator failures should hard-fail live mode or allow send-with-warning for non-critical issues.

**Acceptance checks:**

- Tests cover unsupported number, unsupported link, missing `so what`, word-limit violation, missing stale warning, and missing source metadata.

### 10.3 Implement optional critic/repair pass

**Agent objective:** Add one LLM-assisted repair attempt before final deterministic validation failure.

**Files to modify:**

- `app/synthesis/writer.py`
- `app/synthesis/validator.py` if needed
- `app/llm/prompts/critic.md`
- `tests/test_writer.py` or `tests/test_validator.py`

**Implementation notes:**

- Critic suggests minimal edits only.
- Deterministic validator remains the final gate.
- Do not let the critic add unsupported facts.

**Human input needed:** Confirm whether this is needed for V1 or can be deferred after deterministic validator is working.

**Acceptance checks:**

- Failed first draft can be repaired in a mocked test.
- Still fails when repaired output violates critical rules.

---

## Step 11 — Rendering and charting

### 11.1 Implement chart builder

**Agent objective:** Generate one chart for the brief from real or fixture data.

**Files to modify:**

- `app/render/charts.py`
- `tests/test_render.py`

**Implementation notes:**

- Use fixture data in sample mode.
- Save sample chart to `outputs/sample_chart.png`.
- Keep chart simple and tied to a selected theme.
- Caption must be no more than 30 words.

**Human input needed:** Choose the best sample chart candidate: cross-asset risk move, rates/FX divergence, gold versus real-yield proxy, VIX/MOVE stress, or calendar/policy path.

**Acceptance checks:**

- Chart file is created.
- Caption passes validator.
- Chart data is traceable to fixture or source data.

### 11.2 Implement HTML and text rendering

**Agent objective:** Render `BriefDraft` into readable email outputs.

**Files to modify:**

- `app/render/email.py`
- `app/render/templates/brief_template.html`
- `tests/test_render.py`

**Implementation notes:**

- Include compact header with run timestamp and data cutoff.
- Render sections in required order.
- Render stale/failure warnings near the top.
- Include dashboard and calendar as tables.
- Include chart path or inline chart reference as appropriate.
- Create plain-text fallback.

**Human input needed:** Review visual style and email readability.

**Acceptance checks:**

- HTML and text include all required sections.
- Warnings render when present.
- Output files are saved to the expected paths.

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
- Implement `SendGridDeliveryProvider`.
- Sample and dry-run modes must never send real email.
- Live mode sends only if delivery is enabled and required recipients are configured.
- Always save artifacts even if email delivery fails.

**Human input needed:** Provide SendGrid sender/recipient decisions and local secrets outside git.

**Acceptance checks:**

- Noop delivery records success without sending.
- SendGrid path can be unit-tested with mocked client/request.
- Missing email secrets fail clearly only when live sending is enabled.

### 12.2 Wire delivery into pipeline

**Agent objective:** Connect rendered outputs to delivery and metadata.

**Files to modify:**

- `app/pipeline.py`
- `app/delivery.py`
- `tests/test_pipeline_sample.py`

**Implementation notes:**

- In sample and dry-run, use noop delivery.
- In live mode, use SendGrid only when enabled.
- Record delivery status in `RunMetadata`.

**Human input needed:** Confirm whether live mode default should be dry-run until final manual toggle.

**Acceptance checks:**

- Sample mode does not attempt SendGrid.
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

- Use fixture market data, fixture calendar data, fixture evidence, deterministic writer or mocked/stubbed LLM path, validator, renderer, and noop delivery.
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
- `app/render/templates/brief_template.html`

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
- Dry-run uses live or cached data but skips delivery.
- Cache fallback behavior must be visible in warnings and metadata.

**Human input needed:** Run with real credentials locally and inspect provider responses. Confirm which live sources are stable enough for the submission.

**Acceptance checks:**

```bash
make dry-run
```

- Dry-run generates outputs.
- Dry-run does not send email.
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
- SendGrid fail: save artifacts and record failed delivery.

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
- SendGrid cost assumption.
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
8. SendGrid sender/recipient configuration.
9. Live-mode validation severity and send/no-send rules.
10. Actual hours spent in `memo/memo.md`.
11. Final acceptance audit before submission.
