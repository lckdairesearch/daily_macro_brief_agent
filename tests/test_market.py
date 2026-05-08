"""Tests for market data normalization using fixture data (Step 3)."""

import json
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "market_sample.json"


def test_fixture_loads():
    data = json.loads(FIXTURE.read_text())
    assert "_note" in data
    assert "fixture" in data["_note"].lower()
    assert len(data["snapshots"]) > 0


def test_fixture_no_invented_source():
    data = json.loads(FIXTURE.read_text())
    for snap in data["snapshots"]:
        assert snap.get("source") == "fixture", f"{snap['symbol']} missing fixture source tag"


@pytest.mark.skip(reason="Not yet implemented — Step 3")
def test_market_normalization_produces_snapshot_model():
    pass


@pytest.mark.skip(reason="Not yet implemented — Step 3")
def test_market_cache_fallback_warns():
    pass
