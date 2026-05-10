# Daily Macro Brief Agent

A scheduled Python pipeline that pulls real market prices, calendar events, and source evidence overnight, ranks what matters for the book, and uses an LLM to synthesize a grounded macro brief — then validates, renders, and delivers it by email at 07:15 HKT.

---

## Live Output

When Cloudflare R2 is configured, `dry-run` and `live` runs publish to stable public links.

### [Latest live rendered brief](https://pub-8e3db3da8c4c4e36a23129f88de511b7.r2.dev/outputs/runs/latest/brief_rendered.html)

### [Latest dry-run rendered brief](https://pub-8e3db3da8c4c4e36a23129f88de511b7.r2.dev/outputs/dry-runs/latest/brief_rendered.html)

Each is a full HTML email showing the overnight dashboard, three things, calendar, one chart, theme radar, and contrarian corner.

---

## What It Does

The brief is a fixed six-section document written for a macro PM:

| Section | Content |
|---|---|
| Overnight dashboard | Market snapshot — equities, rates, FX, metals, commodities, crypto, vol |
| Three things that matter | Top macro themes with body, portfolio impact, and sourcing |
| Today's calendar | Economic events and central bank speakers with consensus |
| One chart worth seeing | A single rendered chart selected for today's context |
| Theme radar | Thematic signals from research, news, and podcasts |
| Contrarian corner | Opposing view or risk the consensus is underweighting |

---

## How It Works

The pipeline runs in five phases:

1. **Fetch** — market snapshots (Alpha Vantage, Databento, FRED, yfinance) and calendar events (Investing.com)
2. **Discover** — scouts pull evidence from news, central bank feeds, research, X/Grok, and podcasts
3. **Synthesize** — deduplicate, rank by portfolio relevance, write the brief with the LLM, validate output
4. **Render** — HTML email (Jinja2) + plain text fallback + PNG chart
5. **Deliver** — email via Postmark (live mode only) + publish all artifacts to Cloudflare R2

### Runtime Modes

| Mode | Command | Data | Email | Refreshes brief links above | R2 publish |
|---|---|---|---|---|---|
| Sample | `make run-sample` | Fixtures only, no credentials | Never | No | No |
| Dry-run | `make dry-run` | Live APIs + cache | Never | [Yes](https://pub-8e3db3da8c4c4e36a23129f88de511b7.r2.dev/outputs/dry-runs/latest/brief_rendered.html) | Yes |
| Live | `make run-live` | Live APIs | When `ENABLE_EMAIL_DELIVERY=true` | [Yes](https://pub-8e3db3da8c4c4e36a23129f88de511b7.r2.dev/outputs/runs/latest/brief_rendered.html) | Yes |

Sample mode is the default. It is deterministic, credential-free, and always safe to run.

---

## Design Docs

Read in this order when contributing:

- [`spec.md`](spec.md) — product contract: brief shape, reader persona, delivery contract, non-negotiables
- [`architecture.md`](architecture.md) — system design: module boundaries, data flow, provider isolation principles
- [`plan.md`](plan.md) — contributor roadmap: 15 ordered work units with rationale

---

## Setup

### Prerequisites

- Python 3.13
- Accounts required depend on the run mode:

| Mode | Required accounts |
|---|---|
| Sample | None |
| Dry-run | OpenAI, Alpha Vantage, Databento, FRED, XAI (Grok), Taddy |
| Live (adds) | Postmark, Cloudflare R2 |

### Install

```bash
git clone <repo>
cd daily_macro_brief_agent
make install          # creates .venv and installs all deps
cp .env.example .env  # then fill in your keys (see Configuration below)
```

Verify with a sample run — no credentials needed:

```bash
make run-sample
make test
```

---

## Configuration

### `.env` — secrets

Copy `.env.example` to `.env` and fill in the blocks you need:

```bash
# Always required
OPENAI_API_KEY=                    # LLM synthesis, web-search scouts

# Market data (required for dry-run and live)
ALPHA_VANTAGE_API_KEY=             # Equities, FX, commodities, Treasury yields, BTC, DXY
DATABENTO_API_KEY=                 # German Bund yield, copper futures
FRED_API_KEY=                      # HY OAS spread (free at fred.stlouisfed.org)

# Discovery scouts (required for dry-run and live)
XAI_API_KEY=                       # X/Grok scout
TADDY_USER_ID=                     # Podcast episode discovery (taddy.org)
TADDY_API_KEY=
GEMINI_API_KEY=                    # Podcast/YouTube fallback transcription

# Email delivery (required for live)
POSTMARK_API_KEY=
POSTMARK_FROM_EMAIL=               # Verified sender address in Postmark
POSTMARK_MAINTAINER_EMAIL=         # Test/maintainer recipient

# Delivery gate — off by default
# Set to true only in live mode with confirmed production recipients
ENABLE_EMAIL_DELIVERY=false

# Cloudflare R2 — chart hosting and artifact backup (required for live; optional for dry-run)
CLOUDFLARE_R2_ACCOUNT_ID=
CLOUDFLARE_R2_ACCESS_KEY_ID=
CLOUDFLARE_R2_SECRET_ACCESS_KEY=
CLOUDFLARE_R2_BUCKET=
CLOUDFLARE_R2_PUBLIC_BASE_URL=     # Custom domain or r2.dev URL
CLOUDFLARE_R2_PREFIX=              # Optional object prefix; defaults to "charts"

# LLM overrides — optional; leave blank to use app/config/sources.yaml
LLM_SYNTHESIS_MODEL=
LLM_SYNTHESIS_TEMPERATURE=
LLM_SYNTHESIS_REASONING_EFFORT=
# See .env.example for the full list of LLM_* overrides
```

Production email recipients live in `app/config/recipients.yaml`, not in `.env`.

### `app/config/` — YAML config

| File | Controls | When to touch it |
|---|---|---|
| `app.yaml` | Timezone, cutoff time (06:45 HKT), send time (07:15 HKT), output dir | Rarely |
| `sources.yaml` | LLM model selection, provider toggles, scout on/off switches | When changing models or disabling a scout |
| `recipients.yaml` | Production email recipient list | When adding/removing recipients |
| `portfolio.yaml` | Book profile used for evidence relevance ranking | When the book changes |
| `themes.yaml` | Structural themes driving scout and ranking focus | When the investment thesis shifts |
| `chart_templates.yaml` | Candidate charts for "one chart worth seeing" | When adding or retiring a chart idea |
| `vol_params.yaml` | Instrument volatility parameters (auto-generated) | Run `make update-vol-params` monthly |

---

## Running

### By mode

```bash
# No credentials required — uses fixture data, deterministic output
make run-sample

# Live APIs, no email — good for testing the full pipeline
make dry-run

# Dry-run with a specific historical data cutoff
make dry-run DATA_CUTOFF="2026-05-08T06:45"

# Full pipeline with email delivery (requires ENABLE_EMAIL_DELIVERY=true in .env)
make run-live
```

Equivalent direct commands if you prefer:

```bash
.venv/bin/python -m app.main --mode sample
.venv/bin/python -m app.main --mode dry-run
.venv/bin/python -m app.main --mode dry-run --data-cutoff "2026-05-08T06:45"
.venv/bin/python -m app.main --mode live
```

### CI/CD

The GitHub Actions workflow (`.github/workflows/daily_brief.yml`) runs at **07:15 HKT Mon–Fri** (23:15 UTC Sun–Thu) and supports manual trigger via `workflow_dispatch`. It runs in live mode and uploads run artifacts for 30 days.

---

## Make Reference

| Target | Description |
|---|---|
| `make install` | Create `.venv` and install all dependencies |
| `make test` | Run the full test suite |
| `make lint` | ruff check + mypy type checking |
| `make format` | ruff format |
| `make run-sample` | Run in sample mode (fixture data, no credentials) |
| `make dry-run` | Run with live data, no delivery |
| `make run-live` | Run the full pipeline with optional delivery |
| `make update-vol-params` | Refresh `vol_params.yaml` from live data (run monthly) |

---

## Artifacts

Each run writes a timestamped directory under `outputs/`:

```
outputs/
  samples/YYYY-MM-DD/<run_id>/    # sample runs
  dry-runs/YYYY-MM-DD/<run_id>/   # dry-run runs
  runs/YYYY-MM-DD/<run_id>/       # live runs
```

Each run directory contains:

| File | Content |
|---|---|
| `market_snapshots.json` | Raw market data used |
| `calendar_events.json` | Economic calendar events |
| `evidence_cards.json` | Discovered and deduplicated evidence |
| `ranked_context.json` | Ranked inputs fed to the writer |
| `brief_draft.json` | Structured LLM output before rendering |
| `chart_plan.json` | Chart selection rationale |
| `chart_build.json` | Chart generation audit trail |
| `chart_*.png` | Rendered chart image |
| `brief.html` | Email-ready HTML |
| `brief.txt` | Plain text fallback |
| `brief_rendered.html` | Final rendered HTML with backfilled metadata |
| `run_metadata.json` | Timings, token usage, warnings, delivery status |

Sample runs also refresh stable local aliases at `outputs/samples/sample_brief.{html,txt,png}`.

Dry-run and live runs publish their full directory to R2 and refresh a no-cache `latest/brief_rendered.html` alias.

---

## Known Gaps

- Consensus enrichment is designed in `architecture.md` but not yet wired into the pipeline
- Investing.com calendar is a prototype dependency, not a licensed feed
- The critic/repair second-pass prompt exists but is not active
- Sample and dry-run never send email — this is intentional, not a gap

---

## Contributing

Read `spec.md → architecture.md → plan.md` before making changes.

Every contribution should include a handoff note:

```
Files changed:
Tests run and result:
Known gaps:
Human decisions needed:
```

Ask before deleting tests, fixtures, or source files.
