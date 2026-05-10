# AGENTS.md — Daily Macro Brief Agent

This file applies to all coding agents (Codex, etc.) working in this repository.

## Source-of-truth hierarchy

1. `spec.md` — product behavior, brief sections, acceptance criteria, non-goals.
2. `architecture.md` — system shape, module boundaries, data contracts, provider design.
3. `plan.md` — execution order only. Does not override spec or architecture.
4. This file and `CLAUDE.md` point agents back to that hierarchy.

## Coding principles

- **DRY (Don't Repeat Yourself):** If the same logic appears in two places, extract it. Prefer shared utilities over copy-pasted blocks.
- **KISS (Keep It Simple, Stupid):** Choose the simplest solution that works. Avoid clever abstractions, deep inheritance, or over-engineered patterns for a prototype.
- **YAGNI (You Aren't Gonna Need It):** Do not add features, config options, or abstractions that are not required by the current task. Build for what is specified, not for hypothetical future needs.

## Non-negotiables

- **No invented market data.** Market prices, yields, spreads, calendar times, and consensus values must come from APIs, scraping, cached data, or explicit fixtures — never from LLM generation.
- **No secrets in code.** Do not commit `.env`, API keys, or any live credentials.
- **Keep changes small and reviewable.** One task per branch or PR where possible.
- **Prompts live in `app/llm/prompts/`.** Do not embed prompt text in Python business logic.
- **Scout logic lives in `app/discovery/scouts/`.** Synthesis logic lives in `app/synthesis/`.
- **Add or update tests with every functional change.**
- **Ask before deleting or rewriting existing tests, fixtures, or source files.**
- **Prefer fixture-backed sample mode** over live integration when the task can be completed without credentials.

## Handoff note format

When handing back to the human coder, leave a note with:
- Files changed
- Tests run and result
- Known gaps or open questions
- Human decisions needed before the next step

## Suggested workflow

1. Read `spec.md`, `architecture.md`, and the assigned task in `plan.md`.
2. Write a short implementation note: files to touch, tests to run, open assumptions.
3. Implement only the assigned task.
4. Run the narrowest relevant test, then the broader smoke command for milestone tasks.
5. Leave a handoff note.
