"""Smoke tests: sample mode runs and returns a valid PipelineResult (Step 4)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.data.market import FixtureMarketProvider
from app.discovery.scouts.base import DiscoveryContext, FixtureDiscoveryScout
from app.models import (
    BriefDraft,
    BriefItem,
    BriefSection,
    DeliveryStatus,
    LLMUsage,
    PipelineResult,
    RunMode,
)
from app.pipeline import (
    _load_fixture_calendar,
    run_pipeline,
)
from app.settings import Settings


def _mock_write_brief(ranked_context, settings, run_date, mode=None):
    """Return a minimal valid BriefDraft without making any LLM call."""
    snap = ranked_context.dashboard_rows[0] if ranked_context.dashboard_rows else None
    three_things = BriefItem(
        section=BriefSection.THREE_THINGS,
        headline="Metals complex gains",
        body="Gold moved higher as real yields softened. Supporting the monetary debasement thesis with central bank buying continuing at elevated pace.",
        so_what="Supports long metals complex position.",
        supporting_market_ids=[snap.instrument_id] if snap else [],
    )
    radar = BriefItem(
        section=BriefSection.THEME_RADAR,
        headline="Fiscal Dominance Thesis — Research Note",
        body=(
            "Author argues that fiscal dominance is now the primary driver of long yields. "
            "Evidence includes widening deficits and sustained Treasury issuance. "
            "Central banks are losing credibility as inflation anchors. "
            "Historical parallels to the 1940s yield curve control era are noted."
        ),
        so_what="What this means for our book: reinforces short duration positioning.",
        supporting_evidence_ids=["ev_001"],
    )
    draft = BriefDraft(
        run_metadata={},
        overnight_dashboard=ranked_context.dashboard_rows,
        three_things=[three_things],
        todays_calendar=ranked_context.top_calendar_events,
        radar_items=[radar],
        contrarian_corner=None,
        warnings=[],
    )
    usage = LLMUsage(model="mock", latency_seconds=0.0)
    return draft, usage


@pytest.fixture(autouse=False)
def mock_writer():
    """Patch write_brief for pipeline smoke tests — no real LLM call needed."""
    with patch("app.synthesis.writer.write_brief", side_effect=_mock_write_brief):
        yield


@pytest.fixture(autouse=True)
def mock_delivery():
    """Pipeline smoke tests must not send real email."""
    from app.delivery import NoopDeliveryProvider
    with patch("app.delivery.get_provider", return_value=NoopDeliveryProvider()):
        yield


def test_sample_pipeline_returns_pipeline_result(mock_writer):
    """run_pipeline(sample) returns a PipelineResult instance."""
    settings = Settings.load()
    result = run_pipeline(RunMode.SAMPLE, settings)
    assert isinstance(result, PipelineResult)


def test_sample_pipeline_success(mock_writer):
    """run_pipeline(sample) returns success=True."""
    settings = Settings.load()
    result = run_pipeline(RunMode.SAMPLE, settings)
    assert result.success is True


def test_sample_pipeline_run_metadata_populated(mock_writer):
    """RunMetadata has required fields populated."""
    settings = Settings.load()
    result = run_pipeline(RunMode.SAMPLE, settings)
    meta = result.run_metadata
    assert meta.run_id
    assert meta.run_started_at is not None
    assert meta.data_cutoff_at is not None
    assert meta.timezone == "Asia/Hong_Kong"
    assert meta.llm_provider == "litellm"


def test_sample_pipeline_noop_delivery_when_postmark_is_mocked(mock_writer):
    """Sample pipeline can be smoke-tested without sending real email."""
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
