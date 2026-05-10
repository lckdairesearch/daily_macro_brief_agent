# Daily Macro Brief Agent

A scheduled Python pipeline that produces a PM-facing morning macro brief focused on one question:

> what changed overnight, and what does it mean for our book?

The system uses real or fixture-backed market, calendar, and source inputs. The LLM is used for grounded synthesis, not for making up facts.

## Brief Shape

The standard brief contains:

1. Overnight market dashboard
2. Three things that matter today
3. Today’s calendar
4. One chart worth seeing
5. Theme radar
6. Contrarian corner

## Quickstart

Sample mode is the default path for local verification and does not require live credentials.

```bash
cp .env.example .env
make install
make run-sample
make test
```

Sample outputs are written under `outputs/samples/YYYY-MM-DD/<run_id>/` and stable aliases are refreshed at:

- `outputs/samples/sample_brief.html`
- `outputs/samples/sample_brief.txt`
- `outputs/samples/sample_chart.png`

When Cloudflare R2 hosting is configured, `dry-run` and `live` runs also upload their full per-run folders under `outputs/dry-runs/...` and `outputs/runs/...`, plus refresh stable public latest-render links:

- [Latest dry-run rendered brief](https://pub-8e3db3da8c4c4e36a23129f88de511b7.r2.dev/outputs/dry-runs/latest/brief_rendered.html)
- [Latest live rendered brief](https://pub-8e3db3da8c4c4e36a23129f88de511b7.r2.dev/outputs/runs/latest/brief_rendered.html)

## Runtime Modes

| Mode | Command | Behavior | Delivery |
|---|---|---|---|
| Sample | `make run-sample` | Fixture-backed, deterministic, no live credentials required | None |
| Dry-run | `make dry-run` | Live/cached data path without delivery | None |
| Live | `make run-live` | Full live pipeline | Postmark only when `ENABLE_EMAIL_DELIVERY=true` |

You can override the cutoff for dry-run:

```bash
make dry-run DATA_CUTOFF="2026-05-08 06:45"
```

## Key Environment Variables

See `.env.example` for the full list.

Core:

- `OPENAI_API_KEY`
- `ALPHA_VANTAGE_API_KEY`
- `DATABENTO_API_KEY`
- `FRED_API_KEY`
- `XAI_API_KEY`
- `TADDY_USER_ID`
- `TADDY_API_KEY`

Delivery:

- `POSTMARK_API_KEY`
- `POSTMARK_FROM_EMAIL`
- `POSTMARK_TO_EMAIL`
- `ENABLE_EMAIL_DELIVERY`

Optional chart hosting:

- `CLOUDFLARE_R2_ACCOUNT_ID`
- `CLOUDFLARE_R2_ACCESS_KEY_ID`
- `CLOUDFLARE_R2_SECRET_ACCESS_KEY`
- `CLOUDFLARE_R2_BUCKET`
- `CLOUDFLARE_R2_PUBLIC_BASE_URL`
- `CLOUDFLARE_R2_PREFIX`

## Useful Commands

```bash
make lint
make test
make run-sample
make dry-run
make run-live
make update-vol-params
```

## Design Docs

- `spec.md`: product contract
- `architecture.md`: current system shape
- `plan.md`: contributor blueprint

## Known Gaps

- consensus enrichment is not yet active in the pipeline
- sample and dry-run do not send email by design
- Investing.com calendar data is a prototype dependency, not a licensed feed
