# memo.md — Daily Macro Brief Agent

This memo is the lightweight design handoff for humans reviewing the project.

## What It Should Cover

- why the system uses typed boundaries and a boring pipeline
- why sample mode is deterministic and credential-free
- where the LLM is allowed to operate and where it is forbidden
- key provider choices and why they are isolated behind module boundaries
- current known gaps, especially consensus enrichment and live-data reliability
- the next 30-day improvement path

## Current Position

At the moment, the code is a working V1 prototype with stronger implementation coverage than its markdown set had reflected. The main documentation task is to keep the written blueprint aligned with the code and tests rather than to restate every implementation detail.
