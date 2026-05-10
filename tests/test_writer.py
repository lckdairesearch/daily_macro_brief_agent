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
    BriefReviewOutput,
    BriefSection,
    BriefWriterOutput,
    CalendarEvent,
    EvidenceCard,
    FreshnessStatus,
    LLMUsage,
    MarketSnapshot,
    SourceType,
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


def _make_evidence_card(card_id: str = "ev_001") -> EvidenceCard:
    return EvidenceCard(
        id=card_id,
        title="Policy Watch",
        source_name="Research Source",
        source_type=SourceType.RESEARCH,
        url="https://example.com/research",
        retrieved_at=_NOW,
        thesis="Fiscal dominance matters",
        evidence="Treasury issuance remains elevated",
        macro_relevance="Rates pressure persists",
        portfolio_relevance="Supports short duration",
        confidence=0.8,
    )


def _make_social_evidence_card(card_id: str = "ev_x_001") -> EvidenceCard:
    return EvidenceCard(
        id=card_id,
        title="Macro X thread",
        source_name="macro_user",
        source_type=SourceType.SOCIAL,
        url="https://x.com/macro_user/status/12345678901234567",
        retrieved_at=_NOW,
        thesis="Term premium is rebuilding",
        evidence="The thread points to supply pressure and sticky inflation.",
        macro_relevance="Rates regime is shifting.",
        portfolio_relevance="Supports short duration",
        confidence=0.7,
    )


def _make_podcast_evidence_card(card_id: str = "ev_p_001") -> EvidenceCard:
    return EvidenceCard(
        id=card_id,
        title="Macro podcast episode",
        source_name="Macro Voices",
        source_type=SourceType.PODCAST,
        url="https://example.com/podcast/episode-1",
        retrieved_at=_NOW,
        thesis="Liquidity is cushioning growth",
        evidence="The speaker cites bank credit resilience and fiscal carryover.",
        macro_relevance="Growth is holding up better than feared.",
        portfolio_relevance="Supports reflation positioning",
        confidence=0.72,
    )


def _make_ranked_context(stale: bool = False) -> RankedBriefContext:
    snap = _make_snapshot(stale=stale)
    event = _make_calendar_event()
    evidence = _make_evidence_card()
    return RankedBriefContext(
        dashboard_rows=[snap],
        top_market_moves=[snap],
        top_calendar_events=[event],
        ranked_evidence_cards=[evidence],
        proposed_three_things=[],
        proposed_theme_radar=[],
        proposed_contrarian_corner_seed=None,
    )


def _make_settings(has_key: bool = True) -> MagicMock:
    settings = MagicMock()
    settings.creds.openai_api_key = "sk-test" if has_key else None
    settings.sources = {
        "llm": {
            "synthesis_model": "openai/gpt-5.5",
            "synthesis_temperature": 0.1,
            "synthesis_reasoning_effort": "medium",
            "synthesis_verbosity": "medium",
            "synthesis_timeout_seconds": 180,
            "synthesis_review_enabled": True,
            "synthesis_review_model": "openai/gpt-5.5",
            "synthesis_review_temperature": 0.0,
            "synthesis_review_reasoning_effort": "high",
            "synthesis_review_verbosity": "low",
            "synthesis_review_timeout_seconds": 180,
            "synthesis_context_card_count": 10,
            "temperature": 0.2,
        }
    }
    settings.portfolio = {
        "core_positions": [
            {
                "id": "duration_short",
                "label": "Short Long-term US Duration",
                "rationale": "Fiscal dominance",
                "brief_aliases": ["short long-end Treasuries", "rates short"],
                "sensitivity_tags": ["fed_policy"],
            }
        ],
        "tactical_overlays": [
            {
                "id": "usd_diversification",
                "label": "Currency Diversification",
                "rationale": "Reduce USD concentration risk",
                "brief_aliases": ["FX diversification"],
                "sensitivity_tags": ["usd_policy"],
            }
        ],
    }
    settings.themes = {"themes": []}
    return settings


def _make_writer_output(
    with_contrarian: bool = True,
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
        book_impact="Higher yields support the short-duration sleeve.",
        three_things=[three],
        radar_items=[radar],
        contrarian_corner=contrarian,
        warnings=[],
    )


def _make_llm_result(writer_output: BriefWriterOutput) -> LLMResult:
    usage = LLMUsage(model="openai/gpt-4o", latency_seconds=1.2, prompt_tokens=500, completion_tokens=300)
    return LLMResult(output=writer_output, usage=usage, raw_text="{}")


def _make_review_output() -> BriefReviewOutput:
    return BriefReviewOutput(
        book_impact="Rates still back up the hedge, but the angle is cleaner now.",
        three_things_so_what=["Keeps the rates short in focus for today."],
        radar_items_so_what=["Sharpens the medium-term case for the hedge."],
        contrarian_corner_so_what="Watch the rates hedge if liquidity starts to ease too quickly.",
        warnings=[],
    )


def _make_review_result(review_output: BriefReviewOutput) -> LLMResult:
    usage = LLMUsage(model="openai/gpt-5.5", latency_seconds=0.8, prompt_tokens=300, completion_tokens=120)
    return LLMResult(output=review_output, usage=usage, raw_text="{}")


# ---------------------------------------------------------------------------
# Tests: write_brief assembles BriefDraft correctly
# ---------------------------------------------------------------------------

@patch("app.synthesis.writer.LLMClient")
def test_write_brief_returns_brief_draft(mock_llm_cls):
    writer_output = _make_writer_output()
    review_output = _make_review_output()
    mock_llm_cls.return_value.generate_structured.side_effect = [
        _make_llm_result(writer_output),
        _make_review_result(review_output),
    ]

    ctx = _make_ranked_context()
    settings = _make_settings()
    draft, usages = write_brief(ctx, settings, _NOW)

    assert isinstance(draft, BriefDraft)
    assert len(usages) == 2
    assert all(isinstance(usage, LLMUsage) for usage in usages)


@patch("app.synthesis.writer.LLMClient")
def test_write_brief_dashboard_comes_from_pipeline_not_llm(mock_llm_cls):
    """overnight_dashboard must be the pipeline's market snapshots, not LLM output."""
    writer_output = _make_writer_output()
    mock_llm_cls.return_value.generate_structured.side_effect = [
        _make_llm_result(writer_output),
        _make_review_result(_make_review_output()),
    ]

    ctx = _make_ranked_context()
    settings = _make_settings()
    draft, _ = write_brief(ctx, settings, _NOW)

    assert draft.overnight_dashboard == ctx.dashboard_rows


@patch("app.synthesis.writer.LLMClient")
def test_write_brief_calendar_comes_from_pipeline_not_llm(mock_llm_cls):
    writer_output = _make_writer_output()
    mock_llm_cls.return_value.generate_structured.side_effect = [
        _make_llm_result(writer_output),
        _make_review_result(_make_review_output()),
    ]

    ctx = _make_ranked_context()
    settings = _make_settings()
    draft, _ = write_brief(ctx, settings, _NOW)

    assert draft.todays_calendar == ctx.top_calendar_events


@patch("app.synthesis.writer.LLMClient")
def test_write_brief_section_types_are_forced(mock_llm_cls):
    """Section enum values must be set by the writer, not trusted from LLM output."""
    writer_output = _make_writer_output()
    mock_llm_cls.return_value.generate_structured.side_effect = [
        _make_llm_result(writer_output),
        _make_review_result(_make_review_output()),
    ]

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
def test_write_brief_leaves_chart_unset(mock_llm_cls):
    writer_output = _make_writer_output()
    mock_llm_cls.return_value.generate_structured.side_effect = [
        _make_llm_result(writer_output),
        _make_review_result(_make_review_output()),
    ]

    ctx = _make_ranked_context()
    settings = _make_settings()
    draft, _ = write_brief(ctx, settings, _NOW)

    assert draft.chart is None


@patch("app.synthesis.writer.LLMClient")
def test_write_brief_carries_book_impact(mock_llm_cls):
    writer_output = _make_writer_output()
    review_output = _make_review_output()
    mock_llm_cls.return_value.generate_structured.side_effect = [
        _make_llm_result(writer_output),
        _make_review_result(review_output),
    ]

    ctx = _make_ranked_context()
    settings = _make_settings()
    draft, _ = write_brief(ctx, settings, _NOW)

    assert draft.book_impact == review_output.book_impact


@patch("app.synthesis.writer.LLMClient")
def test_write_brief_attaches_source_metadata_from_evidence_id(mock_llm_cls):
    writer_output = _make_writer_output()
    mock_llm_cls.return_value.generate_structured.side_effect = [
        _make_llm_result(writer_output),
        _make_review_result(_make_review_output()),
    ]

    ctx = _make_ranked_context()
    settings = _make_settings()
    draft, _ = write_brief(ctx, settings, _NOW)

    assert draft.radar_items[0].source_name == "Research Source"
    assert draft.radar_items[0].source_url == "https://example.com/research"
    assert draft.radar_items[0].source_type == SourceType.RESEARCH


@patch("app.synthesis.writer.LLMClient")
def test_write_brief_derives_topic_label_from_evidence(mock_llm_cls):
    writer_output = _make_writer_output()
    mock_llm_cls.return_value.generate_structured.side_effect = [
        _make_llm_result(writer_output),
        _make_review_result(_make_review_output()),
    ]

    ctx = _make_ranked_context()
    settings = _make_settings()
    draft, _ = write_brief(ctx, settings, _NOW)

    assert draft.radar_items[0].topic_label == "Rates"


@patch("app.synthesis.writer.LLMClient")
def test_write_brief_contrarian_null_when_absent(mock_llm_cls):
    writer_output = _make_writer_output(with_contrarian=False)
    review_output = _make_review_output().model_copy(update={"contrarian_corner_so_what": None})
    mock_llm_cls.return_value.generate_structured.side_effect = [
        _make_llm_result(writer_output),
        _make_review_result(review_output),
    ]

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
    draft, usages = write_brief(ctx, settings, _NOW, mode=RunMode.SAMPLE)
    assert isinstance(draft, BriefDraft)
    assert len(usages) == 1
    assert usages[0].model == "fake/sample"


@patch("app.synthesis.writer.LLMClient")
def test_write_brief_propagates_llm_warnings(mock_llm_cls):
    writer_output = _make_writer_output()
    writer_output.warnings = ["only 2 evidence cards available"]
    review_output = _make_review_output().model_copy(update={"warnings": ["review kept the draft focused"]})
    mock_llm_cls.return_value.generate_structured.side_effect = [
        _make_llm_result(writer_output),
        _make_review_result(review_output),
    ]

    ctx = _make_ranked_context()
    settings = _make_settings()
    draft, _ = write_brief(ctx, settings, _NOW)

    assert "only 2 evidence cards available" in draft.warnings
    assert "review kept the draft focused" in draft.warnings


@patch("app.synthesis.writer.LLMClient")
def test_write_brief_review_failure_falls_back_to_writer_output(mock_llm_cls):
    writer_output = _make_writer_output()
    calls = 0

    def _side_effect(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _make_llm_result(writer_output)
        raise LLMResponseError("review failed")

    mock_llm_cls.return_value.generate_structured.side_effect = _side_effect

    ctx = _make_ranked_context()
    settings = _make_settings()
    draft, usages = write_brief(ctx, settings, _NOW)

    assert draft.book_impact == writer_output.book_impact
    assert "brief reviewer failed; using first-pass writer output" in draft.warnings
    assert len(usages) == 1


@patch("app.synthesis.writer.LLMClient")
def test_write_brief_skips_review_when_disabled(mock_llm_cls):
    writer_output = _make_writer_output()
    mock_llm_cls.return_value.generate_structured.return_value = _make_llm_result(writer_output)

    ctx = _make_ranked_context()
    settings = _make_settings()
    settings.sources["llm"]["synthesis_review_enabled"] = False
    draft, usages = write_brief(ctx, settings, _NOW)

    assert draft.book_impact == writer_output.book_impact
    assert len(usages) == 1


@patch("app.synthesis.writer.LLMClient")
def test_write_brief_uses_configured_synthesis_timeouts(mock_llm_cls):
    writer_output = _make_writer_output()
    review_output = _make_review_output()
    mock_llm_cls.return_value.generate_structured.side_effect = [
        _make_llm_result(writer_output),
        _make_review_result(review_output),
    ]

    ctx = _make_ranked_context()
    settings = _make_settings()
    settings.sources["llm"]["synthesis_timeout_seconds"] = 180
    settings.sources["llm"]["synthesis_review_timeout_seconds"] = 240

    write_brief(ctx, settings, _NOW)

    writer_config = mock_llm_cls.call_args_list[0].args[0]
    review_config = mock_llm_cls.call_args_list[1].args[0]
    assert writer_config.timeout_seconds == 180
    assert review_config.timeout_seconds == 240


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


def test_build_payload_includes_available_x_evidence():
    ctx = _make_ranked_context()
    social = _make_social_evidence_card()
    ctx = ctx.__class__(
        dashboard_rows=ctx.dashboard_rows,
        top_market_moves=ctx.top_market_moves,
        top_calendar_events=ctx.top_calendar_events,
        ranked_evidence_cards=ctx.ranked_evidence_cards + [social],
        proposed_three_things=ctx.proposed_three_things,
        proposed_theme_radar=ctx.proposed_theme_radar,
        proposed_contrarian_corner_seed=ctx.proposed_contrarian_corner_seed,
        scores=ctx.scores,
    )
    settings = _make_settings()
    payload = _build_payload(ctx, settings, _NOW)

    assert len(payload["available_x_evidence"]) == 1
    assert payload["available_x_evidence"][0]["id"] == social.id
    assert payload["available_x_evidence"][0]["source_type"] == SourceType.SOCIAL.value


def test_build_payload_includes_available_alternative_evidence():
    ctx = _make_ranked_context()
    social = _make_social_evidence_card()
    podcast = _make_podcast_evidence_card()
    ctx = ctx.__class__(
        dashboard_rows=ctx.dashboard_rows,
        top_market_moves=ctx.top_market_moves,
        top_calendar_events=ctx.top_calendar_events,
        ranked_evidence_cards=ctx.ranked_evidence_cards + [social, podcast],
        proposed_three_things=ctx.proposed_three_things,
        proposed_theme_radar=ctx.proposed_theme_radar,
        proposed_contrarian_corner_seed=ctx.proposed_contrarian_corner_seed,
        scores=ctx.scores,
    )
    settings = _make_settings()
    payload = _build_payload(ctx, settings, _NOW)

    assert len(payload["available_alternative_evidence"]) == 2
    assert {item["id"] for item in payload["available_alternative_evidence"]} == {
        social.id,
        podcast.id,
    }
    assert {item["source_type"] for item in payload["available_alternative_evidence"]} == {
        SourceType.SOCIAL.value,
        SourceType.PODCAST.value,
    }


def test_build_payload_includes_top_ranked_evidence_and_phrase_guide():
    ctx = _make_ranked_context()
    social = _make_social_evidence_card()
    ctx = ctx.__class__(
        dashboard_rows=ctx.dashboard_rows,
        top_market_moves=ctx.top_market_moves,
        top_calendar_events=ctx.top_calendar_events,
        ranked_evidence_cards=[ctx.ranked_evidence_cards[0], social],
        proposed_three_things=ctx.proposed_three_things,
        proposed_theme_radar=ctx.proposed_theme_radar,
        proposed_contrarian_corner_seed=ctx.proposed_contrarian_corner_seed,
        scores=ctx.scores,
    )
    settings = _make_settings()
    payload = _build_payload(ctx, settings, _NOW)

    assert [item["id"] for item in payload["top_ranked_evidence"]] == ["ev_001", "ev_x_001"]
    assert payload["portfolio_phrase_guide"][0]["brief_aliases"] == [
        "short long-end Treasuries",
        "rates short",
    ]


def test_build_payload_available_x_evidence_empty_when_no_social_cards():
    ctx = _make_ranked_context()
    settings = _make_settings()
    payload = _build_payload(ctx, settings, _NOW)

    assert payload["available_x_evidence"] == []


def test_build_payload_available_alternative_evidence_empty_when_no_social_or_podcast_cards():
    ctx = _make_ranked_context()
    settings = _make_settings()
    payload = _build_payload(ctx, settings, _NOW)

    assert payload["available_alternative_evidence"] == []


def test_brief_writer_prompt_mentions_support_first_so_what_rules():
    from app.llm.prompt_registry import clear_prompt_cache, load_prompt

    clear_prompt_cache()
    prompt = load_prompt("brief_writer")

    assert "If `available_alternative_evidence` is non-empty, generally include at least one radar item backed by social or podcast evidence" in prompt.text
    assert "Do not force a different implication just for variety" in prompt.text
    assert "Keep a professional house style, but vary sentence openings, cadence, and implication phrasing" in prompt.text
    assert "It does not need to begin with a fixed phrase." in prompt.text
    assert "Use `portfolio_phrase_guide` to vary how the book is referenced." in prompt.text
    assert "Use `top_ranked_evidence` as additional context" in prompt.text
    assert "When a policymaker, central banker, or official is referenced by surname only" in prompt.text
    assert "Keep first-mention labels short and functional." in prompt.text
    assert "`so_what` should answer: what matters for the book today or into the next event?" in prompt.text
    assert "If the same exposure appears more than once across the brief, change both the phrasing and the angle." in prompt.text
    assert "Macro events like payrolls, wages, CPI, or Fed communication should usually map first to rates, policy, or USD implications" in prompt.text
    assert "Radar `so_what` should answer: what does this reinforce, weaken, or change in the medium-term book thesis?" in prompt.text


def test_brief_reviewer_prompt_mentions_repetition_fatigue_and_aliases():
    from app.llm.prompt_registry import clear_prompt_cache, load_prompt

    clear_prompt_cache()
    prompt = load_prompt("brief_reviewer")

    assert "Your job is NOT to find new facts." in prompt.text
    assert "Use `portfolio_phrase_guide` to vary approved book references." in prompt.text
    assert "Repetition fatigue" in prompt.text
    assert "three_things_so_what" in prompt.text


# ---------------------------------------------------------------------------
# Tests: _to_brief_item
# ---------------------------------------------------------------------------

def test_to_brief_item_assigns_section():
    item = WriterItem(headline="test", body="body text")
    brief_item = _to_brief_item(item, BriefSection.THREE_THINGS)
    assert brief_item.section == BriefSection.THREE_THINGS
    assert brief_item.headline == "test"
