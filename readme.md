# Daily Macro Brief Agent

A scheduled Python pipeline that runs at **07:15 HKT, Monday–Friday** and delivers a PM-facing morning brief answering one question: *what changed overnight, and so what for our book?*

All market numbers come from APIs and data sources — not the LLM. The LLM synthesizes only.

## Brief sections

1. Overnight market dashboard
2. Three things that matter today
3. Today's calendar
4. One chart worth seeing
5. Theme radar
6. Contrarian corner

## Quickstart

Sample mode can run without live market credentials. Add Postmark credentials when you want sample or dry-run output delivered to the maintainer inbox.

```bash
cp .env.example .env      # add at minimum: OPENAI_API_KEY
make install
make run-sample           # produces outputs/sample_brief.html, .txt, and chart.png
make test
```

## Runtime modes

| Mode | Command | Data source | Email sent |
|---|---|---|---|
| Sample | `make run-sample` | Fixture data + deterministic writer | To `POSTMARK_MAINTAINER_EMAIL` when Postmark credentials are present |
| Dry-run | `make dry-run` | Live APIs | To `POSTMARK_MAINTAINER_EMAIL` when Postmark credentials are present |
| Live | `make run-live` | Live APIs | Yes, if `ENABLE_EMAIL_DELIVERY=true` |

Use `make dry-run DATA_CUTOFF="2026-05-08 06:45"` to run with a specific cutoff. Naive datetimes are interpreted in the configured app timezone.

## Environment variables

See `.env.example` for the full list. Key variables by category:

**Always required**
- `OPENAI_API_KEY` — LLM synthesis, web-search scouts, Whisper transcription

**Required for live/dry-run**
- `ALPHA_VANTAGE_API_KEY` — equities, FX, commodities, US Treasury yields, crypto
- `DATABENTO_API_KEY` — Euro Stoxx 50 (FESX), German Bund (FGBL), VIX (VX)
- `FRED_API_KEY` — HY OAS spread (free at fred.stlouisfed.org)
- `XAI_API_KEY` — X/Grok scout
- `LISTEN_NOTES_API_KEY` — podcast scout

**Required for Postmark delivery**
- `POSTMARK_API_KEY`, `POSTMARK_FROM_EMAIL`
- `POSTMARK_MAINTAINER_EMAIL` — maintainer/test recipient for sample and dry-run sends
- `POSTMARK_TO_EMAIL` — comma-separated production recipient list for live sends

Validation warnings appear as a banner at the top of the email. Production email delivery is off by default; set `ENABLE_EMAIL_DELIVERY=true` to enable live sends.

## How live mode works

GitHub Actions runs `.github/workflows/daily_brief.yml` on cron `15 23 * * 0-4` (07:15 HKT Mon–Fri). Secrets must be configured in the repository settings. Manual dispatch is supported via `workflow_dispatch`.

Output artifacts are saved to `outputs/runs/YYYY-MM-DD_HHMM_HKT/` for each run.

## Tests

```bash
make test     # full pytest suite
make lint     # ruff / mypy
```

The default test suite is offline-safe and does not send email. To run the live
Postmark integration test, set `RUN_LIVE_POSTMARK_TEST=true` with valid
`POSTMARK_API_KEY` and `POSTMARK_TO_EMAIL`, then run:

```bash
.venv/bin/pytest tests/test_delivery.py -q
```

## Known limitations

- **Calendar data:** Investing.com backend endpoint is a prototype dependency, not a licensed feed. Treat consensus values as indicative only. Future production should use a paid calendar API.
- **MOVE index:** Sourced from Yahoo Finance (`^MOVE`) via yfinance — no official API. Marked `low_reliability` in run metadata.
- **HY OAS:** FRED series `BAMLH0A0HYM2` limited to 3 years of history from April 2026.
- **X scout:** Uses Grok/xAI by default. Model is configurable via `x_scout_model` in `app/config/sources.yaml` through LiteLLM — swapping to another model is a one-line config change.
- **Podcast scout:** Uses Listen Notes API (free tier) for episode discovery, OpenAI Whisper for transcription, with Gemini as fallback for both audio and YouTube-hosted episodes.

## Design documents

| File | Purpose |
|---|---|
| `spec.md` | Product behavior, brief sections, acceptance criteria, non-goals |
| `architecture.md` | System shape, module boundaries, data contracts, provider design |
| `plan.md` | Implementation steps, agent workflow, milestone map |
| `costs.md` | Daily run cost estimates |
| `memo/memo.md` | Design tradeoffs, assumptions, V2 path, hours spent |
