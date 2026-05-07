"""Tests for calendar parsing, filtering, and consensus enrichment (Step 3)."""

import json
from pathlib import Path

import pytest


FIXTURE = Path(__file__).parent / "fixtures" / "calendar_sample.json"


def test_fixture_loads():
    data = json.loads(FIXTURE.read_text())
    assert "events" in data
    assert len(data["events"]) > 0


def test_fixture_high_importance_events_present():
    data = json.loads(FIXTURE.read_text())
    high = [e for e in data["events"] if e["importance"] == "high"]
    assert len(high) >= 1


@pytest.mark.skip(reason="Not yet implemented — Step 3")
def test_calendar_parsing_produces_calendar_event_model():
    pass


@pytest.mark.skip(reason="Not yet implemented — Step 3")
def test_consensus_enrichment_returns_source_url():
    pass


@pytest.mark.skip(reason="Not yet implemented — Step 3")
def test_missing_consensus_flagged_not_invented():
    pass
