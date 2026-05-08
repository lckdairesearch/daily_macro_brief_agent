"""Tests for evidence deduplication (Step 9)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models import EvidenceCard, SourceType
from app.synthesis.deduper import canonicalize_url, deduplicate

NOW = datetime(2026, 5, 8, 6, 30, tzinfo=timezone.utc)


def _card(
    id: str,
    title: str,
    url: str,
    thesis: str,
    source_type: SourceType = SourceType.NEWS,
    confidence: float = 0.7,
) -> EvidenceCard:
    return EvidenceCard(
        id=id,
        title=title,
        source_name="Test Source",
        source_type=source_type,
        url=url,
        published_at=NOW,
        retrieved_at=NOW,
        thesis=thesis,
        evidence="Supporting evidence.",
        macro_relevance="Relevant to markets.",
        portfolio_relevance="Relevant to portfolio.",
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# URL canonicalization
# ---------------------------------------------------------------------------


def test_canonicalize_strips_tracking_params():
    url = "https://example.com/article?utm_source=email&utm_campaign=daily"
    assert canonicalize_url(url) == "https://example.com/article"


def test_canonicalize_strips_www():
    assert canonicalize_url("https://www.example.com/page") == "https://example.com/page"


def test_canonicalize_http_to_https():
    assert canonicalize_url("http://example.com/page") == "https://example.com/page"


def test_canonicalize_strips_trailing_slash():
    assert canonicalize_url("https://example.com/article/") == "https://example.com/article"


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_empty_list_returns_empty():
    assert deduplicate([]) == []


def test_single_card_unchanged():
    cards = [_card("1", "Fed rate decision", "https://example.com/fed", "Fed holds rates")]
    assert len(deduplicate(cards)) == 1


def test_exact_duplicate_url_collapses():
    a = _card("1", "Fed rate decision", "https://example.com/fed", "Fed holds rates")
    b = _card("2", "Fed rate decision", "https://example.com/fed", "Fed holds rates")
    assert len(deduplicate([a, b])) == 1


def test_tracking_param_urls_treated_as_same():
    a = _card("1", "Fed rate decision", "https://example.com/fed?utm_source=email", "Fed holds rates")
    b = _card("2", "Fed rate decision", "https://example.com/fed?utm_campaign=news", "Fed holds rates")
    assert len(deduplicate([a, b])) == 1


def test_official_source_beats_news_summary():
    news = _card("1", "Fed rate decision", "https://news.com/fed", "Fed holds rates", SourceType.NEWS)
    official = _card("2", "Fed rate decision", "https://official.com/fed", "Fed holds rates", SourceType.CENTRAL_BANK)
    result = deduplicate([news, official])
    assert len(result) == 1
    assert result[0].source_type == SourceType.CENTRAL_BANK


def test_different_thesis_preserved_same_url():
    bullish = _card("1", "Gold outlook", "https://example.com/gold", "Gold will rally on rate cuts and central bank demand driving prices higher")
    bearish = _card("2", "Gold outlook", "https://example.com/gold", "Gold faces headwinds from dollar strength and equity risk-on sentiment")
    result = deduplicate([bullish, bearish])
    assert len(result) == 2


def test_different_thesis_preserved_different_url():
    bullish = _card("1", "Gold market outlook", "https://a.com/gold", "Gold will rally on rate cuts and central bank demand driving prices higher")
    bearish = _card("2", "Gold market outlook", "https://b.com/gold", "Gold faces headwinds from dollar strength and equity risk-on sentiment")
    result = deduplicate([bullish, bearish])
    assert len(result) == 2


def test_distinct_cards_all_preserved():
    cards = [
        _card("1", "Fed holds rates steady at 5.25%", "https://a.com/fed", "No rate cuts expected this year"),
        _card("2", "Oil spikes on OPEC supply cut decision", "https://b.com/oil", "Oil prices surge on production cut"),
        _card("3", "Gold hits all-time high on central bank buying", "https://c.com/gold", "Central bank demand drives gold higher"),
    ]
    assert len(deduplicate(cards)) == 3


def test_near_duplicate_titles_collapsed():
    a = _card("1", "Federal Reserve holds interest rates steady at 5.25%", "https://a.com/fed", "Fed holds rates steady")
    b = _card("2", "Fed holds interest rates steady at 5.25% citing data", "https://b.com/fed", "Fed holds rates steady")
    assert len(deduplicate([a, b])) == 1


def test_high_confidence_kept_on_same_source_type():
    low = _card("1", "Gold rally", "https://example.com/gold", "Gold rises", confidence=0.4)
    high = _card("2", "Gold rally", "https://example.com/gold", "Gold rises", confidence=0.9)
    result = deduplicate([low, high])
    assert len(result) == 1
    assert result[0].confidence == 0.9
