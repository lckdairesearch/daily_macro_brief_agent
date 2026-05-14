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

## Worktree workflow

When the human explicitly asks to "start a new worktree", treat that as an instruction to create and use a fresh git worktree before making code changes.

Rules:
- If the user provides a branch name, use it.
- If the user does not provide a path, create the worktree as a sibling directory named `../<repo-name>-<branch-name>`.
- Prefer `git worktree add ../<repo-name>-<branch-name> -b <branch-name>` when creating a new branch.
- If the branch already exists, use `git worktree add ../<repo-name>-<branch-name> <branch-name>`.
- After creating the worktree, copy repo-root local env files needed for development into the new worktree, including `.env` and `.env.*` files that exist in the repo root, but exclude template files such as `.env.example`.
- Use non-destructive copy behavior for env files. Do not overwrite an existing env file in the target worktree unless the human explicitly asks.
- Never commit copied env files, print their contents, or expose secrets in output.
- After the worktree is created, do all edits, installs, and tests from that worktree unless the human says otherwise.
- If sandbox or filesystem permissions block creating the worktree or copying env files, request escalation instead of silently skipping the step.
- In the handoff note, report the worktree path, branch name, and which env files were copied.

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
