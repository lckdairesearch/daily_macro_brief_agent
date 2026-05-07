# GEMINI.md — Daily Macro Brief Agent

This file applies to Gemini when working in this repository.

## Source-of-truth hierarchy

1. `spec.md` — product behavior, brief sections, acceptance criteria, non-goals.
2. `architecture.md` — system shape, module boundaries, data contracts, provider design.
3. `plan.md` — execution order. Does not override spec or architecture.
4. This file points back to that hierarchy.

## Coding principles

- **DRY (Don't Repeat Yourself):** If the same logic appears in two places, extract it. Prefer shared utilities over copy-pasted blocks.
- **KISS (Keep It Simple, Stupid):** Choose the simplest solution that works. Avoid clever abstractions, deep inheritance, or over-engineered patterns for a prototype.
- **YAGNI (You Aren't Gonna Need It):** Do not add features, config options, or abstractions that are not required by the current task. Build for what is specified, not for hypothetical future needs.

## Non-negotiables

- **No invented market data.** Never generate prices, yields, spreads, calendar values, or consensus estimates. All numbers must come from APIs, scraping, cached data, or explicit labeled fixtures.
- **No secrets in code or commits.** Keys and credentials come from environment variables only.
- **Keep changes small and reviewable.** Implement the assigned task only.
- **Centralize prompts.** Long-form LLM prompts belong in `app/llm/prompts/` — not inline in Python.
- **Isolate provider-specific logic.** Alpha Vantage, Databento, Investing.com, SendGrid wrappers stay behind their own modules.
- **Tests required.** Add or update tests with every functional change.
- **Ask before deleting existing tests or fixtures.**

## Module boundaries (see architecture.md §5 for full tree)

```
app/data/          — market and calendar data fetching
app/discovery/     — evidence scouts and orchestration
app/synthesis/     — deduplication, ranking, writing, validation
app/render/        — HTML/text/chart rendering
app/llm/           — LiteLLM provider wrapper, prompt registry, prompt files
app/delivery.py    — email delivery (SendGrid)
```

## Running the project

```bash
make install        # set up venv and install dependencies
make run-sample     # fixture data, no live credentials needed
make test           # pytest
make lint           # ruff + mypy
```

## Handoff note format

When completing a task, output:
- Files changed
- Tests run and result
- Known gaps
- Human decisions needed
