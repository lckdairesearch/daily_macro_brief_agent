# architecture.md — Daily Macro Brief Agent

## Goal

This document describes the current system shape for V1.

The core design is:

> collect real or fixture-backed evidence, normalize it into typed models, rank what matters, use the LLM only for grounded synthesis, validate the result, render artifacts, and optionally deliver the brief.

## Principles

1. Numbers come from data sources, not the LLM.
2. The LLM is a synthesis tool, not a source of facts.
3. Pipeline boundaries are typed and explicit.
4. Provider-specific code stays isolated.
5. Sample mode must run offline with no live credentials.
6. Optional discovery failures should degrade gracefully.
7. Config lives in YAML; secrets live in env vars.

## Current Pipeline

`app/main.py` parses the mode and optional cutoff, loads settings, validates credentials for the selected mode, and calls `run_pipeline()`.

`app/pipeline.py` orchestrates the run in this order:

1. determine run window and artifact directory
2. fetch market data
3. fetch calendar data
4. discover evidence
5. deduplicate evidence
6. rank market, calendar, and evidence context
7. write the brief
8. validate the brief
9. select and build a chart
10. render HTML and text
11. optionally deliver email
12. persist metadata and artifacts

## Runtime Modes

### Sample

- uses fixture market data
- uses fixture calendar data
- uses fixture discovery content
- uses a deterministic fake writer response
- requires no live credentials
- writes stable reviewer-facing files under `outputs/samples/`
- never sends email

### Dry-Run

- uses live or cached market/calendar paths
- runs synthesis, validation, charting, and rendering
- records artifacts like a real run
- never sends email

### Live

- uses live or cached market/calendar paths
- runs the full pipeline
- sends email only when `ENABLE_EMAIL_DELIVERY=true`

## Module Boundaries

### Core

- `app/main.py`: CLI entrypoint
- `app/pipeline.py`: orchestration only
- `app/settings.py`: YAML + env loading and mode-specific validation
- `app/models.py`: typed contracts shared across the pipeline

### Data

- `app/data/market.py`: fixture provider, live providers, cache fallback, move metadata, chart series support
- `app/data/calendar.py`: fixture loader, Investing.com ingestion, normalization, consensus guardrails

### Discovery

- `app/discovery/orchestrator.py`: builds scouts and runs them with optional-failure handling
- `app/discovery/scouts/`: source-specific evidence collection

Current live scouts:

- `news`
- `central_bank`
- `research`
- `podcast`
- `x`

### Synthesis

- `app/synthesis/deduper.py`: evidence deduplication
- `app/synthesis/ranker.py`: selects dashboard rows and writing context
- `app/synthesis/writer.py`: writes synthesis sections only
- `app/synthesis/validator.py`: deterministic quality gate

### Render and Delivery

- `app/render/chart_selector.py`: chooses a chart plan from deterministic candidates, with optional LLM arbitration in non-sample modes
- `app/render/charts.py`: renders chart images
- `app/render/email.py`: HTML and text rendering
- `app/render/chart_uploader.py`: optional Cloudflare R2 publishing for chart images
- `app/delivery.py`: Postmark delivery and no-op provider

## Data and Provider Boundaries

### Market Data

Current provider mix:

- Alpha Vantage for broad daily coverage
- Databento for selected futures-derived instruments
- FRED for HY OAS
- yfinance for selected volatility indexes
- fixture data for sample mode

If live market fetching fails, the pipeline may fall back to cached snapshots and disclose that staleness in metadata and warnings.

### Calendar Data

Current source:

- Investing.com economic calendar endpoint via `InvestingCalendarProvider`

Current behavior:

- sample mode uses fixture events
- live and dry-run fetch and normalize Investing.com payloads
- Investing.com forecast is used as consensus when present
- missing consensus is preserved as missing and flagged

Consensus enrichment is not currently wired into the run path. Guardrail helpers exist in `app/data/calendar.py`, and `app/discovery/scouts/consensus.py` remains a stub.

### Discovery Sources

- News, central bank, and research scouts use prompt-driven web-backed extraction.
- Podcast discovery uses Taddy for episode discovery, then transcript retrieval or web recall for extraction.
- X discovery uses `xai-sdk` directly because the live `x_search` tool is not routed through the shared LiteLLM wrapper.

## LLM Boundary

The codebase uses a thin shared LLM layer under `app/llm/`.

Current roles:

- structured synthesis for the brief writer
- optional chart-candidate arbitration
- scout-specific prompting and parsing helpers

The LLM does not own market facts, calendar facts, or provider fetches.

Sample mode intentionally avoids live LLM calls by using a deterministic fake response path.

## Validation and Delivery

The current quality gate is deterministic validation after writing.

Validation outcomes:

- critical failures: run fails and live delivery is suppressed
- warnings: preserved in metadata and surfaced in the rendered brief

Delivery behavior:

- sample: no delivery
- dry-run: no delivery
- live: Postmark only, gated by `ENABLE_EMAIL_DELIVERY=true`

## Artifacts

Per-run outputs are written under:

- `outputs/samples/YYYY-MM-DD/<run_id>/`
- `outputs/dry-runs/YYYY-MM-DD/<run_id>/`
- `outputs/runs/YYYY-MM-DD/<run_id>/`

Typical artifacts include:

- `market_snapshots.json`
- `calendar_events.json`
- `evidence_cards.json`
- `ranked_context.json`
- `brief_draft.json`
- `chart_candidates.json`
- `chart_plan.json`
- rendered `brief.html` and `brief.txt`
- `run_metadata.json`

Sample mode also refreshes stable aliases:

- `outputs/samples/sample_brief.html`
- `outputs/samples/sample_brief.txt`
- `outputs/samples/sample_chart.png`

## Known Gaps

These are the main implementation gaps that matter to future work:

- consensus enrichment is designed but not active in the pipeline
- the critic/repair prompt exists, but there is no second-pass repair stage in the current run path
- some docs and support files historically drifted faster than the code, so future changes should treat code plus tests as implementation truth

## Source of Truth

Use the documents this way:

- `spec.md`: product contract and quality bar
- `architecture.md`: current system shape and boundaries
- `plan.md`: contributor blueprint and active workstreams

For exact runtime details, use the code and tests:

- models: `app/models.py`
- settings: `app/settings.py`
- orchestration: `app/pipeline.py`
- behavioral tests under `tests/`
