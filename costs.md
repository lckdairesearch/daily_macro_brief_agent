# costs.md — Daily Macro Brief Agent

This file tracks the cost surfaces for the current design. It is intentionally short; exact pricing should be updated when provider/model choices change.

## Current Cost Drivers

- LLM usage for synthesis, chart selection, and scout-related extraction
- market data usage across Alpha Vantage, Databento, and FRED
- optional X and podcast discovery usage
- Postmark delivery in live mode
- GitHub Actions or local compute for scheduling and execution
- optional Cloudflare R2 storage/bandwidth for chart hosting

## Notes

- Sample mode is the cheapest verification path because it uses fixtures and a deterministic fake writer.
- Dry-run avoids delivery cost because it never sends email.
- Live delivery cost is incurred only when `ENABLE_EMAIL_DELIVERY=true`.
- Any serious cost review should use `run_metadata` token/cost output plus current provider pricing, not static guesses in this file.
