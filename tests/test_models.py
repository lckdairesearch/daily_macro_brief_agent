"""Tests for Pydantic models and fixture data contracts."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from app.models import (
    AssetClass,
    BriefSection,
    CalendarEvent,
    ConsensusMethod,
    EvidenceCard,
    FreshnessStatus,
    MarketSnapshot,
    SourceType,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_market_snapshot_validation():
    """Test MarketSnapshot model validation."""
    snapshot = MarketSnapshot(
        as_of=datetime.now(),
        instrument_id="SPY",
        display_name="S&P 500 (proxy)",
        asset_class=AssetClass.EQUITY,
        region="US",
        last_price_or_level=520.0,
        one_day_change=-0.23,
        one_day_change_unit="%",
        source="fixture",
        freshness_status=FreshnessStatus.FRESH,
    )
    
    assert snapshot.instrument_id == "SPY"
    assert snapshot.asset_class == AssetClass.EQUITY
    assert snapshot.freshness_status == FreshnessStatus.FRESH


def test_calendar_event_validation():
    """Test CalendarEvent model validation."""
    event = CalendarEvent(
        event_time_local=datetime(2026, 5, 8, 8, 30),
        event_time_hkt=datetime(2026, 5, 8, 20, 30),
        session="US",
        country_or_region="US",
        event_name="Initial Jobless Claims",
        importance=2,
        consensus="222K",
        consensus_method=ConsensusMethod.INVESTING_FORECAST,
        source="fixture",
    )
    
    assert event.event_name == "Initial Jobless Claims"
    assert event.importance == 2
    assert event.consensus_method == ConsensusMethod.INVESTING_FORECAST
    assert event.is_speech is False
    assert event.brief_importance == 0


def test_evidence_card_validation():
    """Test EvidenceCard model validation."""
    card = EvidenceCard(
        id="ev_001",
        title="Test Evidence",
        source_name="Test Source",
        source_type=SourceType.NEWS,
        url="https://example.com",
        retrieved_at=datetime.now(),
        thesis="Test thesis",
        evidence="Test evidence",
        macro_relevance="Test macro relevance",
        portfolio_relevance="Test portfolio relevance",
        confidence=0.8,
    )
    
    assert card.id == "ev_001"
    assert card.source_type == SourceType.NEWS
    assert card.confidence == 0.8


def test_market_fixtures_load():
    """Test market fixture data loads into MarketSnapshot models."""
    fixture_path = FIXTURES_DIR / "market_sample.json"
    
    with open(fixture_path) as f:
        fixture_data = json.load(f)
    
    snapshots = []
    for snapshot_data in fixture_data["snapshots"]:
        snapshot = MarketSnapshot(**snapshot_data)
        snapshots.append(snapshot)
    
    assert len(snapshots) > 0
    assert all(isinstance(s, MarketSnapshot) for s in snapshots)
    
    # Check that we have various asset classes
    asset_classes = {s.asset_class for s in snapshots}
    expected_classes = {AssetClass.EQUITY, AssetClass.RATES, AssetClass.FX}
    assert expected_classes.issubset(asset_classes)


def test_calendar_fixtures_load():
    """Test calendar fixture data loads into CalendarEvent models."""
    fixture_path = FIXTURES_DIR / "calendar_sample.json"
    
    with open(fixture_path) as f:
        fixture_data = json.load(f)
    
    events = []
    for event_data in fixture_data["events"]:
        event = CalendarEvent(**event_data)
        events.append(event)
    
    assert len(events) > 0
    assert all(isinstance(e, CalendarEvent) for e in events)
    
    # Check that we have different sessions
    sessions = {e.session for e in events}
    assert "US" in sessions
    assert "Asia" in sessions


def test_evidence_fixtures_load():
    """Test evidence fixture data loads into EvidenceCard models."""
    fixture_path = FIXTURES_DIR / "evidence_sample.json"
    
    with open(fixture_path) as f:
        fixture_data = json.load(f)
    
    cards = []
    for card_data in fixture_data["cards"]:
        card = EvidenceCard(**card_data)
        cards.append(card)
    
    assert len(cards) > 0
    assert all(isinstance(c, EvidenceCard) for c in cards)
    
    # Check that we have different source types
    source_types = {c.source_type for c in cards}
    expected_types = {SourceType.NEWS, SourceType.CENTRAL_BANK, SourceType.RESEARCH}
    assert expected_types.issubset(source_types)


def test_model_serialization():
    """Test that models can serialize to JSON."""
    snapshot = MarketSnapshot(
        as_of=datetime.now(),
        instrument_id="SPY",
        display_name="S&P 500 (proxy)", 
        asset_class=AssetClass.EQUITY,
        region="US",
        last_price_or_level=520.0,
        one_day_change=-0.23,
        one_day_change_unit="%",
        source="fixture",
        freshness_status=FreshnessStatus.FRESH,
    )
    
    # Test JSON serialization
    json_data = snapshot.model_dump(mode="json")
    assert isinstance(json_data, dict)
    assert json_data["asset_class"] == "equity"
    assert json_data["freshness_status"] == "fresh"
    
    # Test round-trip
    reconstructed = MarketSnapshot(**json_data)
    assert reconstructed.instrument_id == snapshot.instrument_id
