"""Tests for Step 10: brief validator."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models import (
    AssetClass,
    BriefDraft,
    BriefItem,
    BriefSection,
    CalendarEvent,
    ChartSpec,
    FreshnessStatus,
    MarketSnapshot,
)
from app.synthesis.validator import ValidationResult, validate_brief

_NOW = datetime(2026, 5, 8, 7, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snap(instrument_id: str = "SPY", stale: bool = False) -> MarketSnapshot:
    return MarketSnapshot(
        as_of=_NOW,
        instrument_id=instrument_id,
        display_name=instrument_id,
        asset_class=AssetClass.EQUITY,
        region="US",
        last_price_or_level=500.0,
        one_day_change=-1.5,
        one_day_change_unit="%",
        source="fixture",
        freshness_status=FreshnessStatus.STALE_CACHE if stale else FreshnessStatus.FRESH,
    )


def _event() -> CalendarEvent:
    return CalendarEvent(
        event_time_local=_NOW,
        event_time_hkt=_NOW,
        session="US",
        country_or_region="US",
        event_name="FOMC",
        importance=3,
        source="fixture",
    )


def _three_things_item(
    body: str = "Fed signaled patience. Dollar firms. Validates our short duration view.",
    so_what: str = "Validates short duration position.",
    supporting_market_ids: list[str] | None = None,
    supporting_evidence_ids: list[str] | None = None,
) -> BriefItem:
    return BriefItem(
        section=BriefSection.THREE_THINGS,
        headline="Fed holds, dollar firms",
        body=body,
        so_what=so_what,
        supporting_market_ids=["SPY"] if supporting_market_ids is None else supporting_market_ids,
        supporting_evidence_ids=[] if supporting_evidence_ids is None else supporting_evidence_ids,
    )


def _radar_item(
    body: str | None = None,
    so_what: str = "What this means for our book: supports long metals thesis.",
    supporting_evidence_ids: list[str] | None = None,
) -> BriefItem:
    default_body = (
        "Author argues that fiscal dominance is now the primary driver of long yields. "
        "Evidence includes widening deficits and sustained Treasury issuance. "
        "Central banks are losing credibility as inflation anchors. "
        "Historical parallels to the 1940s yield curve control era are drawn."
    )
    return BriefItem(
        section=BriefSection.THEME_RADAR,
        headline="Policy Watch — Research Note",
        body=body or default_body,
        so_what=so_what,
        supporting_evidence_ids=supporting_evidence_ids or ["ev_001"],
    )


def _contrarian_item(body: str | None = None) -> BriefItem:
    default_body = (
        "The market is not pricing a structural breakdown in the gold-real-rates "
        "correlation. Worth watching because central bank buying is now the marginal "
        "price setter, not ETF flows. This divergence has widened for six months."
    )
    return BriefItem(
        section=BriefSection.CONTRARIAN_CORNER,
        headline="Gold decoupling",
        body=body or default_body,
        supporting_evidence_ids=["ev_002"],
    )


def _valid_draft(**overrides) -> BriefDraft:
    defaults = dict(
        run_metadata={},
        overnight_dashboard=[_snap("SPY")],
        three_things=[_three_things_item()],
        todays_calendar=[_event()],
        chart=ChartSpec(
            title="Gold chart",
            caption="Gold versus real yields — six-month divergence widens.",
            chart_type="line",
            data_source="fixture",
        ),
        radar_items=[_radar_item()],
        contrarian_corner=_contrarian_item(),
        warnings=[],
    )
    defaults.update(overrides)
    return BriefDraft(**defaults)


# ---------------------------------------------------------------------------
# Critical: required sections
# ---------------------------------------------------------------------------

def test_missing_overnight_dashboard_is_critical():
    draft = _valid_draft(overnight_dashboard=[])
    result = validate_brief(draft)
    assert not result.is_valid
    assert any("overnight_dashboard" in f for f in result.critical_failures)


def test_missing_three_things_is_critical():
    draft = _valid_draft(three_things=[])
    result = validate_brief(draft)
    assert not result.is_valid
    assert any("three_things" in f for f in result.critical_failures)


def test_missing_radar_items_is_critical():
    draft = _valid_draft(radar_items=[])
    result = validate_brief(draft)
    assert not result.is_valid
    assert any("theme_radar" in f for f in result.critical_failures)


# ---------------------------------------------------------------------------
# Critical: unknown market ID in supporting_market_ids
# ---------------------------------------------------------------------------

def test_unknown_market_id_is_critical():
    item = _three_things_item(supporting_market_ids=["FAKE_ID"])
    draft = _valid_draft(three_things=[item])
    result = validate_brief(draft)
    assert not result.is_valid
    assert any("FAKE_ID" in f for f in result.critical_failures)


def test_valid_market_id_passes():
    item = _three_things_item(supporting_market_ids=["SPY"])
    draft = _valid_draft(three_things=[item])
    result = validate_brief(draft)
    assert "SPY" not in " ".join(result.critical_failures)


# ---------------------------------------------------------------------------
# Critical: URL without supporting evidence IDs
# ---------------------------------------------------------------------------

def test_url_without_evidence_ids_is_critical():
    item = _three_things_item(
        body="See analysis at https://example.com/research for details.",
        supporting_evidence_ids=[],
        supporting_market_ids=[],
    )
    draft = _valid_draft(three_things=[item])
    result = validate_brief(draft)
    assert not result.is_valid
    assert any("URL" in f for f in result.critical_failures)


def test_url_with_evidence_ids_passes():
    item = _three_things_item(
        body="Analysis at https://example.com shows dollar strength.",
        supporting_evidence_ids=["ev_001"],
        supporting_market_ids=[],
    )
    draft = _valid_draft(three_things=[item])
    result = validate_brief(draft)
    assert not any("URL" in f for f in result.critical_failures)


# ---------------------------------------------------------------------------
# Critical: market numbers without supporting IDs
# ---------------------------------------------------------------------------

def test_number_without_any_supporting_ids_is_critical():
    item = _three_things_item(
        body="Yields rose 12 bps overnight with no catalyst identified.",
        supporting_market_ids=[],
        supporting_evidence_ids=[],
    )
    draft = _valid_draft(three_things=[item])
    result = validate_brief(draft)
    assert not result.is_valid
    assert any("market numbers" in f for f in result.critical_failures)


def test_number_with_market_ids_passes():
    item = _three_things_item(
        body="Yields rose 12 bps as Fed minutes surprised.",
        supporting_market_ids=["SPY"],
        supporting_evidence_ids=[],
    )
    draft = _valid_draft(three_things=[item])
    result = validate_brief(draft)
    assert not any("market numbers" in f for f in result.critical_failures)


def test_number_with_evidence_ids_passes():
    item = _three_things_item(
        body="Gold up 0.8% per research note.",
        supporting_market_ids=[],
        supporting_evidence_ids=["ev_001"],
    )
    draft = _valid_draft(three_things=[item])
    result = validate_brief(draft)
    assert not any("market numbers" in f for f in result.critical_failures)


def test_book_impact_numeric_language_is_critical():
    draft = _valid_draft(book_impact="Estimated net impact is +14bps overnight.")
    result = validate_brief(draft)
    assert not result.is_valid
    assert any("book_impact" in f for f in result.critical_failures)


def test_book_impact_narrative_language_passes():
    draft = _valid_draft(
        book_impact="Higher yields support the short-duration sleeve while metals strength supports the long metals theme."
    )
    result = validate_brief(draft)
    assert not any("book_impact" in f for f in result.critical_failures)


# ---------------------------------------------------------------------------
# Minor: word limits
# ---------------------------------------------------------------------------

def test_three_things_over_80_words_is_warning():
    long_body = " ".join(["word"] * 85)
    item = _three_things_item(body=long_body, supporting_market_ids=[], supporting_evidence_ids=[])
    # No numbers/URLs so no critical failure, just word limit warning
    draft = _valid_draft(three_things=[item])
    result = validate_brief(draft)
    assert any("80" in w for w in result.warnings)


def test_three_things_at_80_words_passes_word_limit():
    exact_body = " ".join(["word"] * 80)
    item = _three_things_item(body=exact_body, supporting_market_ids=[], supporting_evidence_ids=[])
    draft = _valid_draft(three_things=[item])
    result = validate_brief(draft)
    assert not any("80" in w for w in result.warnings)


def test_radar_item_under_60_words_is_warning():
    short_body = " ".join(["word"] * 40)
    item = _radar_item(body=short_body)
    draft = _valid_draft(radar_items=[item])
    result = validate_brief(draft)
    assert any("60" in w or "60–100" in w for w in result.warnings)


def test_radar_item_over_100_words_is_warning():
    long_body = " ".join(["word"] * 105)
    item = _radar_item(body=long_body)
    draft = _valid_draft(radar_items=[item])
    result = validate_brief(draft)
    assert any("100" in w or "60–100" in w for w in result.warnings)


def test_contrarian_corner_under_50_words_is_warning():
    short_body = " ".join(["word"] * 30)
    item = _contrarian_item(body=short_body)
    draft = _valid_draft(contrarian_corner=item)
    result = validate_brief(draft)
    assert any("50" in w or "50–100" in w for w in result.warnings)


def test_contrarian_corner_over_100_words_is_warning():
    long_body = " ".join(["word"] * 110)
    item = _contrarian_item(body=long_body)
    draft = _valid_draft(contrarian_corner=item)
    result = validate_brief(draft)
    assert any("100" in w or "50–100" in w for w in result.warnings)


def test_chart_caption_over_30_words_is_warning():
    long_caption = " ".join(["word"] * 35)
    chart = ChartSpec(title="t", caption=long_caption, chart_type="line", data_source="f")
    draft = _valid_draft(chart=chart)
    result = validate_brief(draft)
    assert any("30" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Minor: so_what present
# ---------------------------------------------------------------------------

def test_missing_so_what_on_three_things_is_warning():
    item = _three_things_item(so_what=None)
    draft = _valid_draft(three_things=[item])
    result = validate_brief(draft)
    assert any("so_what" in w for w in result.warnings)


def test_missing_so_what_on_radar_item_is_warning():
    item = _radar_item(so_what=None)
    draft = _valid_draft(radar_items=[item])
    result = validate_brief(draft)
    assert any("so_what" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Minor: stale data disclosure
# ---------------------------------------------------------------------------

def test_stale_instrument_undisclosed_is_warning():
    draft = _valid_draft(
        overnight_dashboard=[_snap("SPY", stale=True)],
        warnings=[],
    )
    result = validate_brief(draft)
    assert any("stale" in w.lower() for w in result.warnings)


def test_stale_instrument_disclosed_in_warnings_passes():
    draft = _valid_draft(
        overnight_dashboard=[_snap("SPY", stale=True)],
        warnings=["SPY price data is stale — using cached snapshot"],
    )
    result = validate_brief(draft)
    assert not any("SPY" in w and "stale" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# Minor: contrarian corner absent
# ---------------------------------------------------------------------------

def test_contrarian_corner_null_is_warning_not_critical():
    draft = _valid_draft(contrarian_corner=None)
    result = validate_brief(draft)
    assert result.is_valid  # not a critical failure
    assert any("contrarian" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# Valid draft passes all critical checks
# ---------------------------------------------------------------------------

def test_valid_draft_has_no_critical_failures():
    draft = _valid_draft()
    result = validate_brief(draft)
    assert result.is_valid
    assert result.critical_failures == []
