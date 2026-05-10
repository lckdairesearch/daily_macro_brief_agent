# plan.md — Daily Macro Brief Agent

## Purpose

This plan is the execution blueprint for contributors working on the current repo.

It is intentionally stepwise. Each substep should be small enough for a single coding agent to complete in one focused change, assuming any required human input is available up front.

## Source-of-Truth Hierarchy

1. `spec.md` defines product behavior and acceptance criteria.
2. `architecture.md` defines the current system shape and module boundaries.
3. `plan.md` defines execution order and agent-sized work units.
4. `AGENTS.md` and `CLAUDE.md` point contributors back to this hierarchy.

## Planning Rules

- Each substep should map to one clear owner and one small PR.
- A substep may touch multiple files, but it should solve one coherent problem.
- If a substep needs product judgment, provider choice, or scope expansion, call that out explicitly as human input needed.
- Avoid substeps that require broad cross-repo rewrites unless that is the actual task.
- Keep sample mode runnable throughout.

## Current Repo Position

The repo already has a working V1 pipeline with:

- sample, dry-run, and live modes
- fixture-backed sample mode
- live market and calendar ingestion
- discovery scouts
- ranking, writing, validation, rendering, and delivery layers
- per-run artifact persistence

The work now is to improve the current system, not to rebuild it from scratch.

## Step 1 — Protect the Product Contract

Goal: keep the brief PM-facing, selective, and grounded.

### 1.1 Validate brief shape against the spec

Scope:

- confirm the rendered output still matches the required section order and intended reading experience
- identify any current code or prompt behavior that drifts toward generic news summary

Primary files:

- `spec.md`
- `app/synthesis/writer.py`
- `app/render/email.py`
- `tests/test_render.py`

Acceptance:

- section ordering remains intentional
- no new section is introduced without spec change

Human input needed:

- only if a proposed section addition or removal changes the product contract

### 1.2 Protect narrative-only book impact behavior

Scope:

- ensure `Overnight Book Impact` remains commentary-only
- prevent accidental numeric exposure, P&L, or sizing language from entering the product

Primary files:

- `app/synthesis/writer.py`
- `app/synthesis/validator.py`
- `tests/test_validator.py`

Acceptance:

- numeric impact language is still blocked by validation

## Step 2 — Keep the Pipeline Boring

Goal: preserve `app/pipeline.py` as simple orchestration.

### 2.1 Refactor leaked stage logic out of the pipeline when needed

Scope:

- move stage-specific logic into owning modules when `app/pipeline.py` starts carrying too much detail
- keep orchestration readable from top to bottom

Primary files:

- `app/pipeline.py`
- whichever owning module should absorb the logic

Acceptance:

- the pipeline still reads as a stage list, not a provider implementation

### 2.2 Standardize stage outputs and failures

Scope:

- make stage boundaries more explicit where warnings, failed-source handling, or artifact persistence are inconsistent
- avoid one-off stage behavior that future contributors cannot predict

Primary files:

- `app/pipeline.py`
- `app/models.py`

Acceptance:

- stage outputs are inspectable and behave consistently across modes

## Step 3 — Harden Settings and Runtime Modes

Goal: keep runtime behavior predictable and explainable by mode.

### 3.1 Keep mode semantics strict

Scope:

- preserve sample as credential-free
- preserve dry-run as non-delivering
- preserve live as explicitly gated for delivery

Primary files:

- `app/main.py`
- `app/settings.py`
- `app/delivery.py`

Acceptance:

- mode behavior remains aligned with docs and tests

### 3.2 Rationalize configuration ownership

Scope:

- move new non-secret behavior into YAML
- keep env vars for secrets and deployment overrides only
- remove confusing duplicates when YAML and env behavior overlap poorly

Primary files:

- `app/settings.py`
- `app/config/*.yaml`
- `tests/test_config.py`

Acceptance:

- a contributor can tell where to change behavior without reading multiple modules

Human input needed:

- if a config change adds a new operator-facing setting with product implications

## Step 4 — Improve Market Data Reliability

Goal: make market ingestion more robust without widening the architecture.

### 4.1 Improve provider normalization or coverage

Scope:

- fix incorrect instrument behavior
- improve symbol mapping where current proxies are weak
- add coverage only when it materially improves the brief

Primary files:

- `app/data/market.py`
- `app/config/vol_params.yaml`
- relevant market tests

Acceptance:

- affected dashboard instruments remain source-backed and correctly normalized

Human input needed:

- if a new provider or instrument materially changes cost or product scope

### 4.2 Strengthen cache fallback behavior

Scope:

- improve fallback selection, stale warnings, or edge-case handling when live calls fail
- keep degraded behavior explicit in metadata and brief warnings

Primary files:

- `app/data/market.py`
- `app/pipeline.py`
- `tests/test_market.py`

Acceptance:

- fallback behavior is deterministic and disclosed

## Step 5 — Finish the Calendar Story

Goal: make the calendar section more trustworthy and complete.

### 5.1 Harden Investing.com ingestion and filtering

Scope:

- improve normalization, region/session classification, importance filtering, and caching behavior
- keep the ingestion boundary contained to `app/data/calendar.py`

Primary files:

- `app/data/calendar.py`
- `tests/test_calendar.py`

Acceptance:

- fixture and live payload handling produce stable typed events

### 5.2 Implement consensus enrichment as a bounded stage

Scope:

- wire enrichment for high-value missing-consensus events only
- require source URL, confidence metadata, and explicit unresolved behavior
- do not broaden this into generic research discovery

Primary files:

- `app/data/calendar.py`
- `app/discovery/scouts/consensus.py`
- `app/pipeline.py`
- tests for enrichment behavior

Acceptance:

- enriched consensus values are source-backed
- unresolved values stay blank and flagged

Human input needed:

- if the team wants a stricter or looser policy on which events deserve enrichment

## Step 6 — Improve Discovery Signal

Goal: get fewer, stronger evidence cards.

### 6.1 Tighten scout relevance filters

Scope:

- improve source filtering before extraction or card emission
- reduce low-signal cards that create downstream ranking noise

Primary files:

- one or more scout modules under `app/discovery/scouts/`
- `app/discovery/scouts/base.py`

Acceptance:

- affected scout emits fewer obviously weak cards without suppressing key themes

### 6.2 Improve one scout at a time

Scope:

- treat each scout as its own agent-sized lane
- avoid multi-scout rewrites in one task unless the shared abstraction is the real issue

Suggested sub-lanes:

- `news.py`
- `central_bank.py`
- `research.py`
- `podcast.py`
- `x.py`

Acceptance:

- one scout improves without destabilizing unrelated scouts

Human input needed:

- if curated source lists or topic priorities need subjective product judgment

## Step 7 — Keep Typed Contracts Tight

Goal: make `app/models.py` a stable boundary rather than a dumping ground.

### 7.1 Add only behavior-supporting fields

Scope:

- add fields only when a downstream stage genuinely needs them
- avoid mirroring provider raw payloads in shared models

Primary files:

- `app/models.py`
- any directly affected module

Acceptance:

- model changes have a clear producer and consumer

### 7.2 Align artifacts and models

Scope:

- keep persisted JSON artifacts aligned with the typed objects contributors inspect during debugging
- avoid ad hoc dictionaries when a typed model should exist

Primary files:

- `app/models.py`
- `app/pipeline.py`

Acceptance:

- artifact shapes remain understandable and stable

## Step 8 — Raise Ranking Quality

Goal: improve what the writer sees before prompt quality becomes the bottleneck.

### 8.1 Improve dashboard and market-move selection

Scope:

- refine significant-move handling
- improve how core dashboard instruments and extra movers are selected

Primary files:

- `app/synthesis/ranker.py`
- `app/render/email.py`
- related ranking/render tests

Acceptance:

- the dashboard reflects actual signal, not noisy coverage

### 8.2 Improve evidence and calendar prioritization

Scope:

- improve theme relevance, portfolio relevance, and event importance ranking
- keep ranking deterministic where possible

Primary files:

- `app/synthesis/ranker.py`
- `app/synthesis/deduper.py`
- `tests/test_ranker.py`

Acceptance:

- the top context fed to the writer is visibly sharper

## Step 9 — Keep the Writer Grounded

Goal: improve synthesis quality without letting the writer become a fact source.

### 9.1 Improve prompt sharpness

Scope:

- refine writing instructions, selection pressure, and book-impact expectations
- avoid prompt sprawl

Primary files:

- `app/llm/prompts/brief_writer.md`
- `app/synthesis/writer.py`
- `tests/test_writer.py`

Acceptance:

- writing quality improves without changing grounding rules

### 9.2 Preserve deterministic sample behavior

Scope:

- keep sample mode on the fake-response path
- do not introduce live LLM dependence into the default evaluation path

Primary files:

- `app/synthesis/writer.py`
- `tests/test_sample_mode_contract.py`

Acceptance:

- sample mode remains credential-free and deterministic

## Step 10 — Treat Validation as a Product Gate

Goal: keep deterministic validation as the final authority.

### 10.1 Add checks for new real failure modes

Scope:

- encode newly observed unsupported-claim patterns, section regressions, or disclosure failures
- prefer explicit checks over broad heuristic policing

Primary files:

- `app/synthesis/validator.py`
- `tests/test_validator.py`

Acceptance:

- the validator catches the targeted failure mode with a clear severity

### 10.2 Rebalance severity only when justified

Scope:

- change warning vs critical classification only when the product risk is clear
- avoid making the validator noisier without improving trust

Primary files:

- `app/synthesis/validator.py`
- tests covering severity expectations

Acceptance:

- severity changes are deliberate and documented in code/tests

Human input needed:

- if a severity change affects delivery policy or user-visible quality thresholds

## Step 11 — Improve Chart Relevance

Goal: make the chart worthy of inclusion.

### 11.1 Improve chart selection heuristics

Scope:

- improve candidate scoring, portfolio linkage, or fallback logic
- preserve deterministic fallback when LLM arbitration is unavailable

Primary files:

- `app/render/chart_selector.py`
- tests around chart selection if added or expanded

Acceptance:

- selected charts more clearly support one of the day’s key points

### 11.2 Improve chart generation resilience

Scope:

- improve handling when data is sparse, fonts fail, or optional chart publishing fails
- keep chart failure non-fatal unless the product contract changes

Primary files:

- `app/render/charts.py`
- `app/render/chart_uploader.py`
- `tests/test_chart_uploader.py`

Acceptance:

- chart issues degrade gracefully and remain inspectable

## Step 12 — Keep Rendering and Delivery Simple

Goal: separate content rendering from transport concerns.

### 12.1 Improve rendering without coupling to delivery

Scope:

- adjust HTML/text presentation, warnings, and metadata display
- avoid adding email-provider assumptions into rendering code

Primary files:

- `app/render/email.py`
- templates under `app/render/templates/`
- `tests/test_render.py`

Acceptance:

- rendered artifacts are reviewable without email delivery

### 12.2 Improve delivery behavior without changing mode semantics

Scope:

- improve Postmark integration, delivery failure reporting, or recipient handling
- preserve:
  - sample: no delivery
  - dry-run: no delivery
  - live: delivery only when enabled

Primary files:

- `app/delivery.py`
- `tests/test_delivery.py`

Acceptance:

- delivery changes remain isolated from content generation

## Step 13 — Strengthen Artifacts and Observability

Goal: make every run understandable after the fact.

### 13.1 Improve run metadata usefulness

Scope:

- add or clean up metadata only when it helps debugging, auditability, or cost tracking
- keep output paths, failed sources, timings, and warnings easy to inspect

Primary files:

- `app/models.py`
- `app/pipeline.py`

Acceptance:

- a contributor can understand a failed or degraded run from saved artifacts

### 13.2 Improve artifact completeness

Scope:

- persist missing intermediate artifacts only if they materially help review or debugging
- avoid artifact spam that no one reads

Primary files:

- `app/pipeline.py`

Acceptance:

- artifact additions are intentional and useful

## Step 14 — Keep Sample Mode Sacred

Goal: preserve the easiest credible evaluation path.

### 14.1 Keep sample mode deterministic

Scope:

- preserve fixture-backed behavior
- prevent accidental credential checks or live calls from creeping into sample mode

Primary files:

- `app/main.py`
- `app/settings.py`
- `app/synthesis/writer.py`
- `tests/test_sample_mode_contract.py`

Acceptance:

- sample mode runs without live credentials and without live LLM calls

### 14.2 Keep sample artifacts reviewer-friendly

Scope:

- improve fixtures or sample rendering only when it helps humans evaluate the product
- do not turn sample mode into a fake branch unrelated to live logic

Primary files:

- `tests/fixtures/*.json`
- `app/render/email.py`
- `tests/test_pipeline_sample.py`

Acceptance:

- sample outputs remain believable, stable, and useful for review

## Step 15 — Keep Docs and Tests Aligned With Reality

Goal: stop the markdown set from drifting away from the code again.

### 15.1 Update docs when contributor-visible behavior changes

Scope:

- update docs when mode semantics, providers, workflow expectations, or major architecture boundaries change
- do not wait for a large “docs sweep” to fix stale claims

Primary files:

- `spec.md`
- `architecture.md`
- `plan.md`
- `readme.md`

Acceptance:

- a new contributor can trust the markdown set

### 15.2 Use tests as implementation truth anchors

Scope:

- add or update tests when behavior changes so docs are not the only expression of intent
- keep especially important runtime truths locked by focused tests

Primary files:

- relevant tests under `tests/`

Acceptance:

- core documented behavior is backed by code and tests, not just prose

## Recommended Workflow For Any Change

1. Read `spec.md`, `architecture.md`, and the relevant step and substep in this file.
2. Confirm the smallest owning module and whether human input is needed.
3. Implement one substep only.
4. Run the narrowest relevant test first.
5. Run a broader smoke path if the change crosses stage boundaries.
6. Update docs if the change alters contributor-visible behavior.
7. Leave a handoff note with changed files, tests run, known gaps, and human decisions still needed.

## Default Validation Commands

Core commands:

- `make test`
- `make lint`
- `make run-sample`

Useful narrow checks:

- `./.venv/bin/pytest tests/test_sample_mode_contract.py -q`
- `./.venv/bin/pytest tests/test_pipeline_sample.py -q`
- `./.venv/bin/pytest tests/test_validator.py -q`
- `./.venv/bin/pytest tests/test_delivery.py -q`
- `./.venv/bin/pytest tests/test_chart_uploader.py -q`

## Definition of Done

A change is ready to hand back when:

- it stays within the product contract in `spec.md`
- it respects the boundaries in `architecture.md`
- it completes one substep cleanly
- the relevant tests pass
- the markdown set still describes reality
