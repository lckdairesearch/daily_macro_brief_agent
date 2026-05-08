.PHONY: venv install format lint test run-sample run-live dry-run render-memo

PYTHON := /opt/homebrew/bin/python3.13
VENV := .venv
BIN := $(VENV)/bin

venv:
	$(PYTHON) -m venv $(VENV)

install: venv
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e '.[dev]'

format:
	$(BIN)/ruff format app tests

lint:
	$(BIN)/ruff check app tests
	$(BIN)/mypy app

test:
	$(BIN)/pytest tests/ -v

run-sample:
	$(BIN)/python -m app.main --mode sample

run-live:
	$(BIN)/python -m app.main --mode live

dry-run:
	$(BIN)/python -m app.main --mode dry-run

render-memo:
	@echo "memo/memo.md — render to PDF manually or via pandoc when ready"
