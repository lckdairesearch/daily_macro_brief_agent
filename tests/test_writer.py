"""Tests for Step 10: brief writer."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.llm.provider import LLMResponseError, LLMResult
from app.models import (
    AssetClass,
    BriefDraft,
    BriefSection,
    BriefWriterOutput,
    CalendarEvent,
    FreshnessStatus,
    LLMUsage,
    MarketSnapshot,
    WriterItem,
)
from app.synthesis.ranker import RankedBriefContext
from app.synthesis.writer import write_brief, _build_payload, _to_brief_item
from app.models import RunMode


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 8, 7, 0, tzinfo=timezone.utc)


def _make_snapshot(instrument_id: str = "SPY", stale: bool = False) -> MarketSnapshot:
    return MarketSnapshot(
        as_of=_NOW,
        instrument_id=instrument_id,
        display_name=instrument_id,
        asset_class=AssetClass.EQUITY,
        region="US",
        last_price_or_level=500.0,
        one_day_change=-1.5,
        one_day_change_unit="%",
        one_day_zscore=-1.2,
        threshold_flag=True,
        source="fixture",
        freshness_status=FreshnessStatus.STALE_CACHE if stale else FreshnessStatus.FRESH,
    )


def _make_calendar_event() -> CalendarEvent:
    return CalendarEvent(
        event_time_local=_NOW,
        event_time_hkt=_NOW,
        session="US",
        country_or_region="US",
        event_name="FOMC Minutes",
        importance=3,
        source="fixture",
    )


def _make_ranked_context(stale: bool = False) -> RankedBriefContext:
    snap = _make_snapshot(stale=stale)
    event = _make_calendar_event()
    return RankedBriefContext(
        dashboard_rows=[snap],
        top_market_moves=[snap],
        top_calendar_events=[event],
        ranked_evidence_cards=[],
        proposed_three_things=[],
        proposed_theme_radar=[],
        proposed_contrarian_corner_seed=None,
    )


def _make_settings(has_key: bool = True) -> MagicMock:
    settings = MagicMock()
    settings.creds.openai_api_key = "sk-test" if has_key else None
    settings.sources = {"llm": {"synthesis_model": "openai/gpt-4o", "temperature": 0.2}}
    settings.portfolio = {}
    settings.themes = {"themes": []}
    return settings


def _make_writer_output(
    with_contrarian: bool = True,
    with_chart: bool = True,
) -> BriefWriterOutput:
    three = WriterItem(
        headline="Fed holds, dollar firms",
        body="Fed officials signaled patience. Dollar index up 0.5%.",
        so_what="Validates short duration position.",
        supporting_market_ids=["SPY"],
        confidence=0.8,
    )
    radar = WriterItem(
        headline="Research Note — Policy Watch",
        body="Author argues that fiscal dominance is now the primary driver of long yields. "
             "Evidence includes widening deficits and sustained Treasury issuance. "
             "Central banks are losing credibility as inflation anchors.",
        so_what="What this means for our book: reinforces short duration thesis.",
        supporting_evidence_ids=["ev_001"],
        confidence=0.7,
    )
    contrarian = WriterItem(
        headline="Gold decoupling from real rates",
        body="The market is not pricing a structural breakdown in the gold-real-rates "
             "correlation. Worth watching because central bank buying is now the marginal "
             "price setter, not ETF flows.",
        supporting_evidence_ids=["ev_002"],
        confidence=0.6,
    ) if with_contrarian else None
    return BriefWriterOutput(
        three_things=[three],
        radar_items=[radar],
        contrarian_corner=contrarian,
        chart_caption="Gold vs real yields — correlation breakdown suggests structural shift." if with_chart else None,
        warnings=[],
    )


def _make_llm_result(writer_output: BriefWriterOutput) -> LLMResult:
    usage = LLMUsage(model="openai/gpt-4o", latency_seconds=1.2, prompt_tokens=500, completion_tokens=300)
    return LLMResult(output=writer_output, usage=usage, raw_text="{}")


# ---------------------------------------------------------------------------
# Tests: write_brief assembles BriefDraft correctly
# ---------------------------------------------------------------------------

@patch("app.synthesis.writer.LLMClient")
def test_write_brief_returns_brief_draft(mock_llm_cls):
    writer_output = _make_writer_output()
    mock_llm_cls.return_value.generate_structured.return_value = _make_llm_result(writer_output)

    ctx = _make_ranked_context()
    settings = _make_settings()
    draft, usage = write_brief(ctx, settings, _NOW)

    assert isinstance(draft, BriefDraft)
    assert isinstance(usage, LLMUsage)


@patch("app.synthesis.writer.LLMClient")
def test_write_brief_dashboard_comes_from_pipeline_not_llm(mock_llm_cls):
    """overnight_dashboard must be the pipeline's market snapshots, not LLM output."""
    writer_output = _make_writer_output()
    mock_llm_cls.return_value.generate_structured.return_value = _make_llm_result(writer_output)

    ctx = _make_ranked_context()
    settings = _make_settings()
    draft, _ = write_brief(ctx, settings, _NOW)

    assert draft.overnight_dashboard == ctx.dashboard_rows


@patch("app.synthesis.writer.LLMClient")
def test_write_brief_calendar_comes_from_pipeline_not_llm(mock_llm_cls):
    writer_output = _make_writer_output()
    mock_llm_cls.return_value.generate_structured.return_value = _make_llm_result(writer_output)

    ctx = _make_ranked_context()
    settings = _make_settings()
    draft, _ = write_brief(ctx, settings, _NOW)

    assert draft.todays_calendar == ctx.top_calendar_events


@patch("app.synthesis.writer.LLMClient")
def test_write_brief_section_types_are_forced(mock_llm_cls):
    """Section enum values must be set by the writer, not trusted from LLM output."""
    writer_output = _make_writer_output()
    mock_llm_cls.return_value.generate_structured.return_value = _make_llm_result(writer_output)

    ctx = _make_ranked_context()
    settings = _make_settings()
    draft, _ = write_brief(ctx, settings, _NOW)

    for item in draft.three_things:
        assert item.section == BriefSection.THREE_THINGS
    for item in draft.radar_items:
        assert item.section == BriefSection.THEME_RADAR
    if draft.contrarian_corner:
        assert draft.contrarian_corner.section == BriefSection.CONTRARIAN_CORNER


@patch("app.synthesis.writer.LLMClient")
def test_write_brief_chart_none_when_caption_absent(mock_llm_cls):
    writer_output = _make_writer_output(with_chart=False)
    mock_llm_cls.return_value.generate_structured.return_value = _make_llm_result(writer_output)

    ctx = _make_ranked_context()
    settings = _make_settings()
    draft, _ = write_brief(ctx, settings, _NOW)

    assert draft.chart is None


@patch("app.synthesis.writer.LLMClient")
def test_write_brief_chart_present_when_caption_supplied(mock_llm_cls):
    writer_output = _make_writer_output(with_chart=True)
    mock_llm_cls.return_value.generate_structured.return_value = _make_llm_result(writer_output)

    ctx = _make_ranked_context()
    settings = _make_settings()
    draft, _ = write_brief(ctx, settings, _NOW)

    assert draft.chart is not None
    assert draft.chart.caption == writer_output.chart_caption


@patch("app.synthesis.writer.LLMClient")
def test_write_brief_contrarian_null_when_absent(mock_llm_cls):
    writer_output = _make_writer_output(with_contrarian=False)
    mock_llm_cls.return_value.generate_structured.return_value = _make_llm_result(writer_output)

    ctx = _make_ranked_context()
    settings = _make_settings()
    draft, _ = write_brief(ctx, settings, _NOW)

    assert draft.contrarian_corner is None


def test_write_brief_raises_when_no_api_key():
    ctx = _make_ranked_context()
    settings = _make_settings(has_key=False)

    with pytest.raises(LLMResponseError, match="OPENAI_API_KEY"):
        write_brief(ctx, settings, _NOW, mode=RunMode.LIVE)


def test_write_brief_sample_mode_needs_no_api_key():
    """Sample mode uses a pre-baked fake response — no API key required."""
    ctx = _make_ranked_context()
    settings = _make_settings(has_key=False)
    draft, usage = write_brief(ctx, settings, _NOW, mode=RunMode.SAMPLE)
    assert isinstance(draft, BriefDraft)
    assert usage.model == "fake/sample"


@patch("app.synthesis.writer.LLMClient")
def test_write_brief_propagates_llm_warnings(mock_llm_cls):
    writer_output = _make_writer_output()
    writer_output.warnings = ["only 2 evidence cards available"]
    mock_llm_cls.return_value.generate_structured.return_value = _make_llm_result(writer_output)

    ctx = _make_ranked_context()
    settings = _make_settings()
    draft, _ = write_brief(ctx, settings, _NOW)

    assert "only 2 evidence cards available" in draft.warnings


# ---------------------------------------------------------------------------
# Tests: _build_payload
# ---------------------------------------------------------------------------

def test_build_payload_includes_stale_instruments():
    ctx = _make_ranked_context(stale=True)
    settings = _make_settings()
    from app.synthesis.writer import _build_payload
    payload = _build_payload(ctx, settings, _NOW)
    assert "SPY" in payload["stale_instruments"]


def test_build_payload_excludes_stale_instruments_when_fresh():
    ctx = _make_ranked_context(stale=False)
    settings = _make_settings()
    from app.synthesis.writer import _build_payload
    payload = _build_payload(ctx, settings, _NOW)
    assert payload["stale_instruments"] == []


# ---------------------------------------------------------------------------
# Tests: _to_brief_item
# ---------------------------------------------------------------------------

def test_to_brief_item_assigns_section():
    item = WriterItem(headline="test", body="body text")
    brief_item = _to_brief_item(item, BriefSection.THREE_THINGS)
    assert brief_item.section == BriefSection.THREE_THINGS
    assert brief_item.headline == "test"
