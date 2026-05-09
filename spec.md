# spec.md — Daily Macro Brief Agent

## Purpose

Build a Python agent that produces a concise morning macro brief for a macro PM before the market day starts.

The brief should answer:

> What changed overnight, and what does it mean for our book?

The product is a selective macro note, not a headline dump. It should highlight a small number of moves, events, and source items that matter for the configured portfolio themes.

## Reader and Tone

Primary reader: a macro-trained PM at a small investment firm.

The brief should be:

- high signal
- concise
- explicit about why each item matters
- clear about what is sourced versus assumed

Tone:

- professional
- direct
- no filler
- exact with numbers when numbers are available
- explicit when no confirmed catalyst is found

## Delivery Contract

V1 supports:

- scheduled weekday runs
- local CLI runs
- HTML output
- plain-text output
- saved run artifacts under `outputs/`
- optional live email delivery via Postmark

Operating schedule:

- timezone: `Asia/Hong_Kong`
- target send time: `07:15 HKT`, Monday to Friday
- default data cutoff: `06:45 HKT`

Runtime modes:

- `sample`: fixture-backed, deterministic, no credentials required, no email delivery
- `dry-run`: live/cached data path without delivery
- `live`: live/cached data path with delivery only when `ENABLE_EMAIL_DELIVERY=true`

## Brief Structure

The standard brief layout is:

1. Overnight market dashboard
2. Three things that matter today
3. Today’s calendar
4. One chart worth seeing
5. Theme radar
6. Contrarian corner

Section expectations:

- Overnight dashboard is a table of key market moves sourced from real or fixture market data.
- Three things forces selection; target three items, but fewer is acceptable if evidence quality is weak.
- Today’s calendar shows relevant scheduled events and any available consensus/previous values.
- One chart should add signal beyond the tables.
- Theme radar summarizes 1 to 3 deeper source items tied to configured themes or positions.
- Contrarian corner is optional when there is no credible watch item; if present it must be framed as a watch item, not a certainty.

The brief may include a short `Overnight Book Impact` note before the main sections. In V1 this is narrative only and must not imply position sizes or P&L estimates.

## Portfolio Assumptions

V1 uses synthetic book and theme assumptions from:

- `app/config/portfolio.yaml`
- `app/config/themes.yaml`

These assumptions are part of the product and should be visible in the brief context. They are configurable and are not meant to represent a real client portfolio.

## Non-Negotiables

- No invented market data.
- No invented calendar values.
- No invented source links.
- No invented consensus values.
- No secrets in code or committed files.

Market numbers must come from APIs, cached payloads, deterministic computation from sourced values, or explicit fixtures used for sample mode and tests.

## Functional Requirements

The system must:

- load YAML configuration for runtime defaults, portfolio assumptions, themes, sources, and chart settings
- keep secrets in environment variables
- fetch market data from providers or cached artifacts
- fetch and normalize calendar data
- discover source evidence through optional scouts
- convert inputs into typed pipeline objects
- rank and synthesize a grounded brief
- validate the output before rendering
- render HTML and plain text artifacts
- record run metadata and artifacts for review

## Validation Requirements

The shipped quality bar is deterministic validation, not model self-policing.

The validator must catch or flag:

- missing critical sections
- unsupported market references
- unsupported links in generated prose
- unsupported numeric claims in generated prose
- numeric language in `Overnight Book Impact`
- stale-data disclosures
- word-limit violations
- missing book-impact statements

Critical validation failures suppress live delivery.

## Acceptance Criteria

V1 is acceptable when:

- `make run-sample` succeeds and refreshes stable sample artifacts under `outputs/samples/`
- `make test` passes
- sample mode works without live credentials
- the brief is grounded in structured market, calendar, and evidence inputs
- live delivery is disabled by default and only enabled explicitly
- the code never relies on the LLM to fabricate market or calendar facts

## Non-Goals

- automated trading or portfolio rebalancing
- investment-advice claims
- a full production data platform
- unrestricted autonomous web browsing without source controls
- a broad UI/dashboard product

## Deferred Work

Likely next steps after V1:

- licensed calendar/data feeds
- stronger consensus enrichment
- richer portfolio-specific risk mapping
- more robust source connectors and evals
- additional delivery channels
