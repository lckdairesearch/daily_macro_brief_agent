"""Smoke tests: sample mode runs and returns a valid PipelineResult (Step 4)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.data.market import FixtureMarketProvider
from app.discovery.scouts.base import DiscoveryContext, FixtureDiscoveryScout
from app.models import (
    BriefDraft,
    BriefItem,
    BriefSection,
    DeliveryStatus,
    FreshnessStatus,
    LLMUsage,
    MarketSnapshot,
    PipelineResult,
    RunMode,
)
from app.pipeline import (
    _chart_output_path,
    _data_cutoff,
    _load_fixture_calendar,
    run_pipeline,
)
from app.settings import Settings


@pytest.fixture
def isolated_settings(tmp_path):
    settings = Settings.load()
    settings.app.output_dir = str(tmp_path / "outputs")
    return settings


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


def test_sample_pipeline_returns_pipeline_result(mock_writer, isolated_settings):
    """run_pipeline(sample) returns a PipelineResult instance."""
    settings = isolated_settings
    result = run_pipeline(RunMode.SAMPLE, settings)
    assert isinstance(result, PipelineResult)


def test_sample_pipeline_success(mock_writer, isolated_settings):
    """run_pipeline(sample) returns success=True."""
    settings = isolated_settings
    result = run_pipeline(RunMode.SAMPLE, settings)
    assert result.success is True


def test_sample_pipeline_run_metadata_populated(mock_writer, isolated_settings):
    """RunMetadata has required fields populated."""
    settings = isolated_settings
    result = run_pipeline(RunMode.SAMPLE, settings)
    meta = result.run_metadata
    assert re.fullmatch(r"\d{8}_\d{6}_sample", meta.run_id)
    assert meta.run_started_at is not None
    assert meta.run_started_at.tzinfo is not None
    assert meta.data_cutoff_at is not None
    assert meta.timezone == "Asia/Hong_Kong"
    assert meta.llm_provider == "litellm"


def test_sample_pipeline_noop_delivery_when_postmark_is_mocked(mock_writer, isolated_settings):
    """Sample pipeline keeps delivery disabled even when Postmark creds exist."""
    settings = isolated_settings
    settings.creds.postmark_api_key = "test-key"
    settings.creds.postmark_from_email = "brief@test.com"
    settings.creds.postmark_maintainer_email = "maintainer@test.com"
    result = run_pipeline(RunMode.SAMPLE, settings)
    assert result.run_metadata.delivery_status == DeliveryStatus.DISABLED


def test_sample_pipeline_preserves_writer_warnings_in_run_metadata(isolated_settings):
    settings = isolated_settings

    def _mock_writer_with_warning(ranked_context, settings, run_date, mode=None):
        draft, usage = _mock_write_brief(ranked_context, settings, run_date, mode)
        draft = draft.model_copy(update={"warnings": ["only 2 evidence cards available"]})
        return draft, usage

    with patch("app.synthesis.writer.write_brief", side_effect=_mock_writer_with_warning):
        result = run_pipeline(RunMode.SAMPLE, settings)

    assert "only 2 evidence cards available" in result.run_metadata.warnings


def test_sample_pipeline_persists_evidence_cards_in_run_dir(mock_writer, isolated_settings):
    """Sample pipeline writes artifacts to a date/run-specific artifact path."""
    settings = isolated_settings

    result = run_pipeline(RunMode.SAMPLE, settings)

    html_path = Path(result.run_metadata.output_paths["html"])
    text_path = Path(result.run_metadata.output_paths["text"])
    chart_path = Path(result.run_metadata.output_paths["chart"])
    evidence_path = Path(result.run_metadata.output_paths["evidence_cards"])
    ranked_path = Path(result.run_metadata.output_paths["ranked_context"])
    rendered_html_path = Path(result.run_metadata.output_paths["brief_rendered_html"])
    metadata_path = Path(result.run_metadata.output_paths["run_metadata"])
    market_path = Path(result.run_metadata.output_paths["market_snapshots"])
    calendar_path = Path(result.run_metadata.output_paths["calendar_events"])
    brief_draft_path = Path(result.run_metadata.output_paths["brief_draft"])
    chart_candidates_path = Path(result.run_metadata.output_paths["chart_candidates"])
    chart_plan_path = Path(result.run_metadata.output_paths["chart_plan"])
    stable_html_path = Path(settings.app.output_dir) / "samples" / "sample_brief.html"
    stable_text_path = Path(settings.app.output_dir) / "samples" / "sample_brief.txt"
    stable_chart_path = Path(settings.app.output_dir) / "samples" / "sample_chart.png"

    for path in (
        html_path,
        text_path,
        chart_path,
        evidence_path,
        ranked_path,
        rendered_html_path,
        metadata_path,
        market_path,
        calendar_path,
        brief_draft_path,
        chart_candidates_path,
        chart_plan_path,
    ):
        assert path.exists()
        assert path.parent.parent.parent.name == "samples"

    assert html_path.name == "brief.html"
    assert text_path.name == "brief.txt"
    assert chart_path.name == "sample_chart.png"
    assert rendered_html_path.name == "brief_rendered.html"
    assert metadata_path.name == "run_metadata.json"
    assert re.fullmatch(r"\d{8}_\d{6}_sample", result.run_metadata.run_id)
    assert evidence_path.parent.name == result.run_metadata.run_id
    assert evidence_path.parent.parent.name == result.run_metadata.data_cutoff_at.strftime("%Y-%m-%d")
    assert evidence_path.parent.parent.parent.name == "samples"
    assert stable_html_path.exists()
    assert stable_text_path.exists()
    assert stable_chart_path.exists()

    cards = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert isinstance(cards, list)
    assert len(cards) > 0
    assert all("id" in card for card in cards)


def test_pipeline_uses_data_cutoff_override(mock_writer, isolated_settings):
    settings = isolated_settings
    override = datetime(2026, 5, 8, 6, 45, tzinfo=ZoneInfo(settings.app.timezone))

    result = run_pipeline(RunMode.SAMPLE, settings, data_cutoff=override)

    assert result.run_metadata.data_cutoff_at == override


def test_pipeline_progress_callback_reports_steps(mock_writer, isolated_settings):
    settings = isolated_settings
    events: list[str] = []

    result = run_pipeline(RunMode.SAMPLE, settings, progress=events.append)

    assert result.success is True
    assert events[:3] == ["Determine run window", "Fetch market data", "Fetch calendar"]
    assert events[-1] == "Record metadata"


def test_pipeline_records_step_and_scout_timings(mock_writer, isolated_settings):
    settings = isolated_settings

    result = run_pipeline(RunMode.SAMPLE, settings)

    timings = result.run_metadata.timings
    components = [t["component"] for t in timings]
    assert "Fetch market data" in components
    assert "scout:fixture" in components
    assert "Record metadata" in components
    assert all(isinstance(t["seconds"], float) for t in timings)


def test_dry_run_pipeline_passes_data_cutoff_to_live_market_fetch(mock_writer, isolated_settings):
    settings = isolated_settings
    override = datetime(2026, 5, 8, 6, 45, tzinfo=ZoneInfo(settings.app.timezone))
    snapshot = MarketSnapshot(
        as_of=override,
        observation_date="2026-05-07",
        instrument_id="SPY",
        display_name="S&P 500 (proxy)",
        asset_class="equity",
        region="US",
        last_price_or_level=500.0,
        one_day_change=1.0,
        one_day_change_unit="%",
        source="test",
        freshness_status=FreshnessStatus.FRESH,
    )

    with patch("app.pipeline.load_vol_params", return_value={}), \
         patch("app.pipeline.fetch_live_market_with_cache", return_value=[snapshot]) as mock_fetch, \
         patch("app.data.calendar.InvestingCalendarProvider.fetch_for_date", return_value=[]), \
         patch("app.discovery.orchestrator.build_scouts", return_value=[]), \
         patch("app.discovery.orchestrator.run_discovery", return_value=[]), \
         patch("app.render.charts.build_chart", side_effect=RuntimeError("skip chart")):
        result = run_pipeline(RunMode.DRY_RUN, settings, data_cutoff=override)

    assert result.run_metadata.data_cutoff_at == override
    mock_fetch.assert_called_once_with(settings, {}, override)
    assert "/dry-runs/" in result.run_metadata.output_paths["market_snapshots"]


def test_dry_run_pipeline_calls_calendar_provider_with_cutoff_date(mock_writer, isolated_settings):
    settings = isolated_settings
    override = datetime(2026, 5, 8, 6, 45, tzinfo=ZoneInfo(settings.app.timezone))

    with patch("app.pipeline.load_vol_params", return_value={}), \
         patch("app.pipeline.fetch_live_market_with_cache", return_value=[]), \
         patch("app.data.calendar.InvestingCalendarProvider.fetch_for_date", return_value=[]) as mock_cal, \
         patch("app.discovery.orchestrator.build_scouts", return_value=[]), \
         patch("app.discovery.orchestrator.run_discovery", return_value=[]), \
         patch("app.render.charts.build_chart", side_effect=RuntimeError("skip chart")):
        run_pipeline(RunMode.DRY_RUN, settings, data_cutoff=override)

    mock_cal.assert_called_once_with(override)


def test_chart_output_path_sample_uses_tracked_artifact():
    settings = Settings.load()
    data_cutoff = datetime(2026, 5, 9, 8, 0, tzinfo=timezone.utc)

    path = _chart_output_path(RunMode.SAMPLE, settings, data_cutoff)

    assert path == f"{settings.app.output_dir}/samples/sample_chart.png"


def test_chart_output_path_live_uses_data_cutoff_timestamp():
    settings = Settings.load()
    data_cutoff = datetime(2026, 5, 9, 8, 30, tzinfo=timezone.utc)

    path = _chart_output_path(RunMode.LIVE, settings, data_cutoff)

    assert path == f"{settings.app.output_dir}/runs/chart_20260509_0830.png"


def test_chart_output_path_dry_run_uses_data_cutoff_timestamp():
    settings = Settings.load()
    data_cutoff = datetime(2026, 5, 9, 6, 45, tzinfo=ZoneInfo("Asia/Hong_Kong"))

    path = _chart_output_path(RunMode.DRY_RUN, settings, data_cutoff)

    assert path == f"{settings.app.output_dir}/dry-runs/chart_20260509_0645.png"


def test_data_cutoff_override_naive_uses_configured_timezone():
    settings = Settings.load()
    tz = ZoneInfo(settings.app.timezone)
    override = datetime(2026, 5, 8, 6, 45)

    cutoff = _data_cutoff(settings, tz, override)

    assert cutoff == datetime(2026, 5, 8, 6, 45, tzinfo=tz)


def test_data_cutoff_override_aware_converts_to_configured_timezone():
    settings = Settings.load()
    tz = ZoneInfo(settings.app.timezone)
    override = datetime(2026, 5, 7, 22, 45, tzinfo=timezone.utc)

    cutoff = _data_cutoff(settings, tz, override)

    assert cutoff == datetime(2026, 5, 8, 6, 45, tzinfo=tz)


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
