"""Evidence ranker. Scores and selects EvidenceCards by portfolio/theme relevance."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from app.models import (
    CalendarEvent,
    EvidenceCard,
    MarketSnapshot,
    SourceType,
)

# ---------------------------------------------------------------------------
# Source quality weights
# ---------------------------------------------------------------------------

_SOURCE_QUALITY: dict[SourceType, float] = {
    SourceType.CENTRAL_BANK: 1.0,
    SourceType.RESEARCH: 0.85,
    SourceType.NEWS: 0.65,
    SourceType.CALENDAR: 0.65,
    SourceType.PODCAST: 0.55,
    SourceType.SOCIAL: 0.40,
}

# Scoring factor weights
_W_PORTFOLIO = 0.30
_W_THEME = 0.20
_W_CONFIDENCE = 0.15
_W_SOURCE = 0.15
_W_NOVELTY = 0.10
_W_FRESHNESS = 0.10

# Bonus for corroborating a threshold-flagged market move
_CROSS_ASSET_BONUS = 0.10

_THREE_THINGS_COUNT = 3
_DEFAULT_MAX_THEME_RADAR = 3
_MAX_CALENDAR_EVENTS = 8

_PRIMARY_COUNTRY_BOOST = {
    "US": 1.3,
    "CN": 1.2,
    "CHINA": 1.2,
}
_SECONDARY_COUNTRY_BOOST = {
    "EU": 1.0,
    "DE": 1.0,
    "GERMANY": 1.0,
    "JP": 0.8,
    "JAPAN": 0.8,
}
_TERTIARY_COUNTRY_BOOST = {
    "GB": 0.4,
    "UK": 0.4,
}
_KEY_EVENT_TOKENS = (
    "cpi", "pce", "inflation", "payroll", "employment", "unemployment",
    "jobless", "wage", "earnings", "fomc", "fed", "rate decision",
    "press conference", "treasury", "auction", "retail sales", "pmi",
    "ism", "gdp", "credit", "loan", "tsf", "m2", "yuan", "fx reserves",
    "ecb", "boj", "pboc",
)
_IMPORTANT_EVENT_TOKENS = (
    "services", "manufacturing", "confidence", "sentiment", "industrial",
    "housing", "trade balance", "current account", "durable goods", "claims",
)


# ---------------------------------------------------------------------------
# Output container
# ---------------------------------------------------------------------------


@dataclass
class RankedBriefContext:
    """Scoring and selection outputs passed to the synthesis writer."""

    dashboard_rows: list[MarketSnapshot]
    top_market_moves: list[MarketSnapshot]
    top_calendar_events: list[CalendarEvent]
    ranked_evidence_cards: list[EvidenceCard]
    proposed_three_things: list[EvidenceCard]
    proposed_theme_radar: list[EvidenceCard]
    proposed_contrarian_corner_seed: EvidenceCard | None
    scores: dict[str, float] = field(default_factory=dict)  # card_id -> score


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------


def _portfolio_score(card: EvidenceCard, portfolio: dict) -> float:
    """1.0 if portfolio_relevance or tags match a core position label or sensitivity tag."""
    text = (card.portfolio_relevance + " " + " ".join(card.tags)).lower()

    all_positions = portfolio.get("core_positions", []) + portfolio.get("tactical_overlays", [])

    core_labels = [pos.get("label", "").lower() for pos in all_positions]
    sensitivity_tags: list[str] = []
    for pos in all_positions:
        sensitivity_tags.extend(pos.get("sensitivity_tags", []))

    for label in core_labels:
        for word in label.split():
            if len(word) > 3 and word in text:
                return 1.0
    for tag in sensitivity_tags:
        if tag.replace("_", " ") in text or tag in text:
            return 0.8
    return 0.4


def _theme_score(card: EvidenceCard, themes: list[dict]) -> float:
    """Match ratio against the best-matching theme, amplified and capped at 1.0."""
    if not themes:
        return 0.0
    card_text = " ".join([card.title, card.thesis, " ".join(card.tags)]).lower()
    best = 0.0
    for theme in themes:
        keywords = [k.lower() for k in theme.get("keywords", [])]
        if not keywords:
            continue
        matched = sum(1 for k in keywords if k in card_text)
        score = matched / len(keywords)
        if score > best:
            best = score
    return min(best * 3.0, 1.0)


def _freshness_score(card: EvidenceCard, now: datetime) -> float:
    """Linear decay: 1.0 at publication, 0.0 at 48 h old."""
    if card.published_at is None:
        return 0.5
    pub = card.published_at
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    age_hours = max((now - pub).total_seconds() / 3600, 0.0)
    return max(1.0 - age_hours / 48.0, 0.0)


def _cross_asset_bonus(card: EvidenceCard, snapshots: list[MarketSnapshot]) -> float:
    """Bonus if any threshold-flagged instrument is mentioned in the card text."""
    flagged_tokens = set()
    for s in snapshots:
        if s.threshold_flag:
            flagged_tokens.add(s.instrument_id.lower())
            flagged_tokens.add(s.display_name.lower())

    card_text = " ".join(card.tags + [card.thesis, card.macro_relevance]).lower()
    for token in flagged_tokens:
        if token and token in card_text:
            return _CROSS_ASSET_BONUS
    return 0.0


def _score_card(
    card: EvidenceCard,
    snapshots: list[MarketSnapshot],
    themes: list[dict],
    portfolio: dict,
    now: datetime,
) -> float:
    novelty = card.novelty_score if card.novelty_score is not None else 0.5
    return (
        _W_PORTFOLIO * _portfolio_score(card, portfolio)
        + _W_THEME * _theme_score(card, themes)
        + _W_CONFIDENCE * card.confidence
        + _W_SOURCE * _SOURCE_QUALITY.get(card.source_type, 0.5)
        + _W_NOVELTY * novelty
        + _W_FRESHNESS * _freshness_score(card, now)
        + _cross_asset_bonus(card, snapshots)
    )


# ---------------------------------------------------------------------------
# Market / calendar ordering
# ---------------------------------------------------------------------------


def _rank_market_moves(snapshots: list[MarketSnapshot]) -> list[MarketSnapshot]:
    """Flagged first, then by abs(zscore) descending."""
    return sorted(
        snapshots,
        key=lambda s: (
            1 if s.threshold_flag else 0,
            abs(s.one_day_zscore) if s.one_day_zscore is not None else 0.0,
        ),
        reverse=True,
    )


def _normalize_country(country: str) -> str:
    return country.strip().upper()


def _country_boost(country: str) -> float:
    normalized = _normalize_country(country)
    if normalized in _PRIMARY_COUNTRY_BOOST:
        return _PRIMARY_COUNTRY_BOOST[normalized]
    if normalized in _SECONDARY_COUNTRY_BOOST:
        return _SECONDARY_COUNTRY_BOOST[normalized]
    return _TERTIARY_COUNTRY_BOOST.get(normalized, 0.0)


def _matches_any(text: str, tokens: Iterable[str]) -> bool:
    return any(token in text for token in tokens)


def _is_top_tier_speech(event: CalendarEvent) -> bool:
    text = event.event_name.lower()
    return any(
        token in text for token in (
            "fed chair",
            "powell",
            "fomc press conference",
            "ecb president",
            "lagarde",
            "pboc governor",
            "boj governor",
            "ueda",
            "boe governor",
            "bailey",
        )
    )


def _event_is_speech(event: CalendarEvent) -> bool:
    if event.is_speech:
        return True
    text = event.event_name.lower()
    return any(token in text for token in ("speak", "speech", "testimony", "testifies"))


def _calendar_score(event: CalendarEvent) -> tuple[float, str]:
    text = event.event_name.lower()
    reasons: list[str] = []
    score = 0.75 * float(event.importance)
    if event.importance >= 3:
        reasons.append("high vendor importance")

    c_boost = _country_boost(event.country_or_region)
    score += c_boost
    if c_boost >= 1.2:
        reasons.append("primary country coverage")
    elif c_boost >= 0.8:
        reasons.append("secondary country coverage")

    if _matches_any(text, _KEY_EVENT_TOKENS):
        score += 1.2
        reasons.append("core macro sensitivity")
    elif _matches_any(text, _IMPORTANT_EVENT_TOKENS):
        score += 0.5
        reasons.append("useful macro read")

    if any(token in text for token in ("fed", "fomc", "ecb", "boj", "pboc", "rates")):
        score += 0.5
        reasons.append("policy-sensitive")

    return score, ", ".join(reasons) if reasons else None


def _apply_brief_importance(event: CalendarEvent) -> tuple[CalendarEvent, float]:
    is_speech = _event_is_speech(event)
    if is_speech and not _is_top_tier_speech(event):
        return event.model_copy(
            update={
                "is_speech": True,
                "brief_importance": 0,
                "brief_importance_reason": "speech filtered in v1",
            }
        ), -1.0

    score, reason = _calendar_score(event)
    if is_speech and _is_top_tier_speech(event):
        score += 0.8
        reason = "top-tier policy speech" if not reason else f"{reason}, top-tier policy speech"

    if score >= 4.1:
        brief_importance = 3
    elif score >= 2.7:
        brief_importance = 2
    elif score >= 1.5:
        brief_importance = 1
    else:
        brief_importance = 0

    return event.model_copy(
        update={
            "is_speech": is_speech,
            "brief_importance": brief_importance,
            "brief_importance_reason": reason,
        }
    ), score


def _rank_calendar_events(events: list[CalendarEvent]) -> list[CalendarEvent]:
    """Select portfolio-relevant events, then sort the final list chronologically."""
    scored_events = [_apply_brief_importance(event) for event in events]
    displayable = [
        (event, score) for event, score in scored_events
        if event.brief_importance >= 2
    ]

    if not displayable:
        fallback = [
            (event, score) for event, score in scored_events
            if not _event_is_speech(event)
        ]
        fallback.sort(key=lambda item: (-item[1], item[0].event_time_hkt))
        chosen = [event for event, _ in fallback[:3]]
        return sorted(chosen, key=lambda event: event.event_time_hkt)

    if len(displayable) > _MAX_CALENDAR_EVENTS:
        selected: list[tuple[CalendarEvent, float]] = []
        used_names: set[str] = set()
        for session in ("Asia", "Europe", "US"):
            session_events = [
                item for item in displayable if item[0].session == session
            ]
            if not session_events:
                continue
            session_events.sort(key=lambda item: (-item[0].brief_importance, -item[1], item[0].event_time_hkt))
            chosen = session_events[0]
            selected.append(chosen)
            used_names.add(chosen[0].event_name)

        remaining = [
            item for item in displayable
            if item[0].event_name not in used_names
        ]
        remaining.sort(key=lambda item: (-item[0].brief_importance, -item[1], item[0].event_time_hkt))
        selected.extend(remaining[: max(_MAX_CALENDAR_EVENTS - len(selected), 0)])
        chosen = [event for event, _ in selected]
        return sorted(chosen, key=lambda event: event.event_time_hkt)

    return sorted([event for event, _ in displayable], key=lambda event: event.event_time_hkt)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def rank(
    *,
    market_snapshots: list[MarketSnapshot],
    calendar_events: list[CalendarEvent],
    evidence_cards: list[EvidenceCard],
    themes: list[dict],
    portfolio: dict,
    max_theme_radar: int = _DEFAULT_MAX_THEME_RADAR,
    now: datetime | None = None,
) -> RankedBriefContext:
    """Score and select ranked candidates for each brief section."""
    if now is None:
        now = datetime.now(timezone.utc)

    scores: dict[str, float] = {
        card.id: _score_card(card, market_snapshots, themes, portfolio, now)
        for card in evidence_cards
    }

    ranked_cards = sorted(evidence_cards, key=lambda c: scores[c.id], reverse=True)

    # Three things: top 3 overall.
    three_things = ranked_cards[:_THREE_THINGS_COUNT]
    three_things_ids = {c.id for c in three_things}

    # Theme radar: best card per theme, up to max_theme_radar distinct themes.
    theme_radar: list[EvidenceCard] = []
    used_ids: set[str] = set()
    for theme in themes:
        if len(theme_radar) >= max_theme_radar:
            break
        keywords = [k.lower() for k in theme.get("keywords", [])]
        best: EvidenceCard | None = None
        best_score = -1.0
        for card in ranked_cards:
            if card.id in used_ids:
                continue
            card_text = " ".join([card.title, card.thesis, " ".join(card.tags)]).lower()
            if any(k in card_text for k in keywords):
                s = scores[card.id]
                if s > best_score:
                    best, best_score = card, s
        if best is not None:
            theme_radar.append(best)
            used_ids.add(best.id)

    # Contrarian: highest-scoring card not already picked for three_things or theme_radar.
    featured_ids = three_things_ids | used_ids
    remaining = [c for c in ranked_cards if c.id not in featured_ids]
    contrarian = remaining[0] if remaining else None

    return RankedBriefContext(
        dashboard_rows=market_snapshots,
        top_market_moves=_rank_market_moves(market_snapshots),
        top_calendar_events=_rank_calendar_events(calendar_events),
        ranked_evidence_cards=ranked_cards,
        proposed_three_things=three_things,
        proposed_theme_radar=theme_radar,
        proposed_contrarian_corner_seed=contrarian,
        scores=scores,
    )
