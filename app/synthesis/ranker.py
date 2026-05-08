"""Evidence ranker. Scores and selects EvidenceCards by portfolio/theme relevance."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

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


def _rank_calendar_events(events: list[CalendarEvent]) -> list[CalendarEvent]:
    """Importance descending, then chronological."""
    return sorted(events, key=lambda e: (-e.importance, e.event_time_hkt))


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
