"""Smoke tests: sample mode runs and returns a valid PipelineResult (Step 4)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.data.market import FixtureMarketProvider
from app.discovery.scouts.base import DiscoveryContext, FixtureDiscoveryScout
from app.models import DeliveryStatus, PipelineResult, RunMode
from app.pipeline import (
    _load_fixture_calendar,
    run_pipeline,
)
from app.settings import Settings


def test_sample_pipeline_returns_pipeline_result():
    """run_pipeline(sample) returns a PipelineResult instance."""
    settings = Settings.load()
    result = run_pipeline(RunMode.SAMPLE, settings)
    assert isinstance(result, PipelineResult)


def test_sample_pipeline_success():
    """run_pipeline(sample) returns success=True."""
    settings = Settings.load()
    result = run_pipeline(RunMode.SAMPLE, settings)
    assert result.success is True


def test_sample_pipeline_run_metadata_populated():
    """RunMetadata has required fields populated."""
    settings = Settings.load()
    result = run_pipeline(RunMode.SAMPLE, settings)
    meta = result.run_metadata
    assert meta.run_id
    assert meta.run_started_at is not None
    assert meta.data_cutoff_at is not None
    assert meta.timezone == "Asia/Hong_Kong"
    assert meta.llm_provider == "litellm"


def test_sample_pipeline_no_email_sent():
    """Sample mode delivery status is never SUCCESS — email must not be sent."""
    settings = Settings.load()
    result = run_pipeline(RunMode.SAMPLE, settings)
    assert result.run_metadata.delivery_status != DeliveryStatus.SUCCESS


def test_load_fixture_market():
    """FixtureMarketProvider returns non-empty list of MarketSnapshot objects."""
    snapshots = FixtureMarketProvider().fetch_watchlist([], datetime.now(timezone.utc))
    assert len(snapshots) > 0
    for snap in snapshots:
        assert snap.instrument_id
        assert snap.source
        assert snap.freshness_status is not None


def test_load_fixture_calendar():
    """Fixture calendar loader returns non-empty list of CalendarEvent objects."""
    events = _load_fixture_calendar()
    assert len(events) > 0
    for event in events:
        assert event.event_name
        assert event.source


def test_load_fixture_evidence():
    """FixtureDiscoveryScout returns non-empty list of EvidenceCard objects."""
    ctx = DiscoveryContext(
        market_snapshots=[], calendar_events=[], themes=[], portfolio={},
        lookback_hours=24, data_cutoff=datetime.now(timezone.utc), mode=RunMode.SAMPLE,
    )
    cards = FixtureDiscoveryScout().run(ctx)
    assert len(cards) > 0
    for card in cards:
        assert card.id
        assert card.url
        assert card.confidence >= 0.0
