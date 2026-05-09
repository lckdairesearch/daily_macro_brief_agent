# GEMINI.md — Daily Macro Brief Agent

Use this repo with the following hierarchy:

1. `spec.md`
2. `architecture.md`
3. `plan.md`

Repo rules:

- no invented market, calendar, consensus, or source data
- no secrets in code or commits
- prompts live in `app/llm/prompts/`
- scout logic lives in `app/discovery/scouts/`
- synthesis logic lives in `app/synthesis/`
- keep provider-specific logic out of `app/pipeline.py`
- prefer sample mode when live integrations are unnecessary
- add or update tests with every functional change
- ask before deleting tests, fixtures, or source files

Handoff note:

- files changed
- tests run and result
- known gaps
- human decisions needed
