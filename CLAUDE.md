# CLAUDE.md — Daily Macro Brief Agent

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

Worktree workflow:

- when the human explicitly asks to "start a new worktree", create and use a fresh git worktree before making code changes
- if the user provides a branch name, use it
- if the user does not provide a path, create the worktree as a sibling directory named `../<repo-name>-<branch-name>`
- prefer `git worktree add ../<repo-name>-<branch-name> -b <branch-name>` when creating a new branch
- if the branch already exists, use `git worktree add ../<repo-name>-<branch-name> <branch-name>`
- after creating the worktree, copy repo-root `.env` and `.env.*` files needed for development into the new worktree, excluding template files such as `.env.example`
- do not overwrite an existing env file in the target worktree unless the human explicitly asks
- never commit copied env files, print their contents, or expose secrets in output
- after the worktree is created, do all edits, installs, and tests from that worktree unless the human says otherwise
- if sandbox or filesystem permissions block creating the worktree or copying env files, request escalation instead of silently skipping the step
- include the worktree path, branch name, and copied env filenames in the handoff note

Handoff note:

- files changed
- tests run and result
- known gaps
- human decisions needed
