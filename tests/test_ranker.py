"""Tests for evidence ranking (Step 9)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import (
    AssetClass,
    CalendarEvent,
    EvidenceCard,
    FreshnessStatus,
    MarketSnapshot,
    SourceType,
)
from app.synthesis.ranker import RankedBriefContext, rank

NOW = datetime(2026, 5, 8, 6, 30, tzinfo=timezone.utc)

THEMES = [
    {
        "id": "monetary_debasement",
        "label": "Monetary debasement and fiscal dominance",
        "keywords": ["Fed", "FOMC", "monetary policy", "fiscal dominance", "debasement", "Treasury"],
        "portfolio_relevance": ["metals_complex_long", "duration_short"],
    },
    {
        "id": "metals_complex",
        "label": "Metals complex as monetary hedge",
        "keywords": ["gold", "silver", "copper", "precious metals", "central bank demand"],
        "portfolio_relevance": ["metals_complex_long"],
    },
]

PORTFOLIO = {
    "core_positions": [
        {
            "id": "metals_complex_long",
            "label": "Long Metals Complex Gold Silver Copper",
            "instruments": ["Gold", "Silver", "Copper"],
            "direction": "long",
            "rationale": "Monetary debasement hedge",
            "sensitivity_tags": ["real_rates", "usd_policy"],
        },
        {
            "id": "duration_short",
            "label": "Short Long-term US Duration",
            "instruments": ["US 30Y", "US 10Y"],
            "direction": "short",
            "rationale": "Fiscal dominance, structural inflation risk",
            "sensitivity_tags": ["fiscal_policy", "fed_policy"],
        },
    ]
}


def _snapshot(
    instrument_id: str,
    zscore: float,
    flagged: bool,
    asset_class: AssetClass = AssetClass.EQUITY,
    region: str = "US",
) -> MarketSnapshot:
    return MarketSnapshot(
        as_of=NOW,
        instrument_id=instrument_id,
        display_name=instrument_id,
        asset_class=asset_class,
        region=region,
        last_price_or_level=100.0,
        one_day_change=zscore,
        one_day_change_unit="%",
        one_day_zscore=zscore,
        threshold_flag=flagged,
        source="fixture",
        freshness_status=FreshnessStatus.FRESH,
    )


def _event(name: str, importance: int, hours: float = 2.0) -> CalendarEvent:
    t = NOW + timedelta(hours=hours)
    return CalendarEvent(
        event_time_local=t,
        event_time_hkt=t,
        session="US",
        country_or_region="US",
        event_name=name,
        importance=importance,
        source="fixture",
    )


def _card(
    id: str,
    title: str,
    thesis: str,
    portfolio_relevance: str,
    tags: list[str],
    source_type: SourceType = SourceType.NEWS,
    confidence: float = 0.7,
    novelty_score: float = 0.5,
) -> EvidenceCard:
    return EvidenceCard(
        id=id,
        title=title,
        source_name="Test",
        source_type=source_type,
        url=f"https://example.com/{id}",
        published_at=NOW,
        retrieved_at=NOW,
        thesis=thesis,
        evidence="Evidence text.",
        macro_relevance="Macro relevance.",
        portfolio_relevance=portfolio_relevance,
        confidence=confidence,
        novelty_score=novelty_score,
        tags=tags,
    )


# ---------------------------------------------------------------------------
# Basic smoke
# ---------------------------------------------------------------------------


def test_rank_returns_ranked_brief_context():
    cards = [_card("1", "Fed holds", "Fed holds rates", "portfolio", ["fed_policy"])]
    result = rank(
        market_snapshots=[],
        calendar_events=[],
        evidence_cards=cards,
        themes=THEMES,
        portfolio=PORTFOLIO,
    )
    assert isinstance(result, RankedBriefContext)
    assert len(result.ranked_evidence_cards) == 1


def test_empty_inputs():
    result = rank(
        market_snapshots=[],
        calendar_events=[],
        evidence_cards=[],
        themes=THEMES,
        portfolio=PORTFOLIO,
    )
    assert result.ranked_evidence_cards == []
    assert result.proposed_three_things == []
    assert result.proposed_contrarian_corner_seed is None
    assert result.top_market_moves == []
    assert result.top_calendar_events == []


# ---------------------------------------------------------------------------
# Evidence ordering
# ---------------------------------------------------------------------------


def test_portfolio_relevant_card_ranks_above_generic():
    relevant = _card(
        "rel",
        "Gold surges on central bank demand",
        "Gold will rally on central bank demand and monetary debasement",
        "Supports Long Metals Complex Gold Silver Copper position",
        ["gold", "metals_complex_long"],
        confidence=0.8,
        novelty_score=0.7,
    )
    generic = _card(
        "gen",
        "Stock market news today",
        "Equities moved on mixed signals",
        "No direct portfolio relevance identified",
        [],
        confidence=0.8,
        novelty_score=0.7,
    )
    result = rank(
        market_snapshots=[],
        calendar_events=[],
        evidence_cards=[generic, relevant],
        themes=THEMES,
        portfolio=PORTFOLIO,
    )
    assert result.ranked_evidence_cards[0].id == "rel"


def test_scores_populated_for_all_cards():
    cards = [_card(str(i), f"Title {i}", f"Thesis {i}", "generic", []) for i in range(3)]
    result = rank(
        market_snapshots=[],
        calendar_events=[],
        evidence_cards=cards,
        themes=THEMES,
        portfolio=PORTFOLIO,
    )
    assert len(result.scores) == 3
    for card in cards:
        assert card.id in result.scores


# ---------------------------------------------------------------------------
# Calendar ordering / selection
# ---------------------------------------------------------------------------


def test_calendar_events_are_sorted_chronologically_after_selection():
    later_key = _event("Fed Rate Decision", importance=3, hours=5.0)
    earlier_important = _event("Jobless Claims", importance=2, hours=1.0)
    result = rank(
        market_snapshots=[],
        calendar_events=[later_key, earlier_important],
        evidence_cards=[],
        themes=THEMES,
        portfolio=PORTFOLIO,
    )
    assert [event.event_name for event in result.top_calendar_events] == [
        "Jobless Claims",
        "Fed Rate Decision",
    ]


def test_calendar_brief_importance_uses_full_scale():
    hidden = _event("Minor Housing Sentiment", importance=1, hours=1.0).model_copy(
        update={"country_or_region": "AU", "session": "Asia"}
    )
    low = _event("UK Services Confidence", importance=2, hours=2.0).model_copy(
        update={"country_or_region": "GB", "session": "Europe"}
    )
    important = _event("Japan Services PMI", importance=2, hours=3.0).model_copy(
        update={"country_or_region": "Japan", "session": "Asia"}
    )
    key = _event("Fed Rate Decision", importance=3, hours=4.0)
    result = rank(
        market_snapshots=[],
        calendar_events=[hidden, low, important, key],
        evidence_cards=[],
        themes=THEMES,
        portfolio=PORTFOLIO,
    )
    by_name = {event.event_name: event for event in result.top_calendar_events}
    assert key.event_name in by_name
    assert by_name[key.event_name].brief_importance == 3
    assert by_name[important.event_name].brief_importance == 2
    assert hidden.model_copy().event_name not in by_name


def test_non_top_tier_speech_is_filtered_out():
    speech = _event("FOMC Member Bowman Speaks", importance=2).model_copy(
        update={"is_speech": True}
    )
    data = _event("US CPI", importance=3, hours=1.0)
    result = rank(
        market_snapshots=[],
        calendar_events=[speech, data],
        evidence_cards=[],
        themes=THEMES,
        portfolio=PORTFOLIO,
    )
    assert [event.event_name for event in result.top_calendar_events] == ["US CPI"]


def test_non_top_tier_speech_is_filtered_out_from_name_fallback():
    speech = _event("FOMC Member Bowman Speaks", importance=2)
    data = _event("US CPI", importance=3, hours=1.0)
    result = rank(
        market_snapshots=[],
        calendar_events=[speech, data],
        evidence_cards=[],
        themes=THEMES,
        portfolio=PORTFOLIO,
    )
    assert [event.event_name for event in result.top_calendar_events] == ["US CPI"]


def test_top_tier_speech_can_survive_filter():
    speech = _event("Fed Chair Powell Speaks", importance=2).model_copy(
        update={"is_speech": True}
    )
    result = rank(
        market_snapshots=[],
        calendar_events=[speech],
        evidence_cards=[],
        themes=THEMES,
        portfolio=PORTFOLIO,
    )
    assert result.top_calendar_events[0].event_name == "Fed Chair Powell Speaks"
    assert result.top_calendar_events[0].brief_importance >= 2


def test_dense_calendar_is_capped():
    events = [
        _event(f"US CPI {idx}", importance=3, hours=float(idx))
        for idx in range(10)
    ]
    result = rank(
        market_snapshots=[],
        calendar_events=events,
        evidence_cards=[],
        themes=THEMES,
        portfolio=PORTFOLIO,
    )
    assert len(result.top_calendar_events) == 8
    assert [event.event_time_hkt for event in result.top_calendar_events] == sorted(
        event.event_time_hkt for event in result.top_calendar_events
    )


# ---------------------------------------------------------------------------
# Market move ordering
# ---------------------------------------------------------------------------


def test_large_flagged_move_ranks_first():
    small = _snapshot("SPY", zscore=0.5, flagged=False)
    large = _snapshot("GOLD", zscore=2.5, flagged=True, asset_class=AssetClass.COMMODITY)
    result = rank(
        market_snapshots=[small, large],
        calendar_events=[],
        evidence_cards=[],
        themes=THEMES,
        portfolio=PORTFOLIO,
    )
    assert result.top_market_moves[0].instrument_id == "GOLD"


def test_unflagged_larger_move_ranks_above_unflagged_smaller():
    small = _snapshot("QQQ", zscore=0.3, flagged=False)
    large = _snapshot("SPY", zscore=0.9, flagged=False)
    result = rank(
        market_snapshots=[small, large],
        calendar_events=[],
        evidence_cards=[],
        themes=THEMES,
        portfolio=PORTFOLIO,
    )
    assert result.top_market_moves[0].instrument_id == "SPY"


# ---------------------------------------------------------------------------
# Section selection
# ---------------------------------------------------------------------------


def test_three_things_limited_to_three():
    cards = [_card(str(i), f"Title {i}", f"Thesis {i}", "generic", []) for i in range(6)]
    result = rank(
        market_snapshots=[],
        calendar_events=[],
        evidence_cards=cards,
        themes=THEMES,
        portfolio=PORTFOLIO,
    )
    assert len(result.proposed_three_things) <= 3


def test_theme_radar_respects_max_parameter():
    cards = [
        _card("fed1", "Fed signals patience", "Fed no rush to cut", "duration short", ["Fed", "FOMC", "monetary policy"]),
        _card("gold1", "Gold rally continues", "Gold benefits from debasement", "metals long", ["gold", "central bank demand"]),
        _card("food1", "Wheat prices surge", "Grain supply shortfall", "agriculture", ["food", "wheat"]),
    ]
    result = rank(
        market_snapshots=[],
        calendar_events=[],
        evidence_cards=cards,
        themes=THEMES,
        portfolio=PORTFOLIO,
        max_theme_radar=1,
    )
    assert len(result.proposed_theme_radar) <= 1


def test_contrarian_not_in_three_things():
    cards = [
        _card("a", "Gold strong", "Gold bull case", "metals long", ["gold"], confidence=0.9, novelty_score=0.9),
        _card("b", "Fed dovish", "Fed signals cuts ahead", "duration relevant", ["Fed"], confidence=0.9, novelty_score=0.9),
        _card("c", "Tech rally", "Tech gains on earnings", "tech exposure", ["tech"], confidence=0.9, novelty_score=0.9),
        _card("d", "Contrarian view", "Dollar strength will persist despite debasement concerns", "usd hedge", ["USD"], confidence=0.7),
    ]
    result = rank(
        market_snapshots=[],
        calendar_events=[],
        evidence_cards=cards,
        themes=THEMES,
        portfolio=PORTFOLIO,
    )
    three_ids = {c.id for c in result.proposed_three_things}
    if result.proposed_contrarian_corner_seed:
        assert result.proposed_contrarian_corner_seed.id not in three_ids


def test_contrarian_none_when_all_cards_used():
    # Only 3 cards — all go to three_things, nothing left for contrarian.
    cards = [_card(str(i), f"Title {i}", f"Thesis {i}", "generic", []) for i in range(3)]
    result = rank(
        market_snapshots=[],
        calendar_events=[],
        evidence_cards=cards,
        themes=[],  # no themes, so no theme radar consumption
        portfolio=PORTFOLIO,
    )
    assert result.proposed_contrarian_corner_seed is None


# ---------------------------------------------------------------------------
# Dashboard rows passthrough
# ---------------------------------------------------------------------------


def test_dashboard_rows_are_all_snapshots():
    snapshots = [_snapshot("SPY", 0.5, False), _snapshot("GOLD", 1.5, True)]
    result = rank(
        market_snapshots=snapshots,
        calendar_events=[],
        evidence_cards=[],
        themes=THEMES,
        portfolio=PORTFOLIO,
    )
    assert len(result.dashboard_rows) == 2
