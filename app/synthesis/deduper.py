"""Evidence deduplication. Removes near-duplicate EvidenceCards across scouts."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from app.models import EvidenceCard, SourceType

# Official/primary source beats secondary/derivative content during tie-breaking.
_SOURCE_RANK: dict[SourceType, int] = {
    SourceType.CENTRAL_BANK: 5,
    SourceType.RESEARCH: 4,
    SourceType.NEWS: 3,
    SourceType.CALENDAR: 3,
    SourceType.PODCAST: 2,
    SourceType.SOCIAL: 1,
}

_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "ref", "source", "fbclid", "gclid", "cid",
})

# Jaccard thresholds for "same story" vs "different perspective"
_TITLE_SIMILARITY_THRESHOLD = 0.5
_THESIS_SIMILARITY_THRESHOLD = 0.5


def canonicalize_url(url: str) -> str:
    """Normalize a URL: strip tracking params, www, trailing slash, lowercase scheme/host."""
    parsed = urlparse(url.strip())
    scheme = "https" if parsed.scheme in ("http", "https") else parsed.scheme
    netloc = re.sub(r"^www\.", "", parsed.netloc.lower())
    path = parsed.path.rstrip("/") or "/"
    clean_qs = {
        k: v for k, v in parse_qs(parsed.query).items()
        if k not in _TRACKING_PARAMS
    }
    return urlunparse((scheme, netloc, path, parsed.params, urlencode(clean_qs, doseq=True), ""))


def _word_jaccard(a: str, b: str) -> float:
    """Jaccard similarity of word bags."""
    words_a = set(re.sub(r"[^\w\s]", "", a.lower()).split())
    words_b = set(re.sub(r"[^\w\s]", "", b.lower()).split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def _source_rank(card: EvidenceCard) -> int:
    return _SOURCE_RANK.get(card.source_type, 0)


def _better_card(a: EvidenceCard, b: EvidenceCard) -> EvidenceCard:
    """Return the higher-quality card; break ties by confidence."""
    rank_a, rank_b = _source_rank(a), _source_rank(b)
    if rank_a != rank_b:
        return a if rank_a > rank_b else b
    return a if a.confidence >= b.confidence else b


def _resolve_group(group: list[EvidenceCard]) -> list[EvidenceCard]:
    """Within a URL-equal group, keep one card per distinct thesis."""
    if len(group) == 1:
        return group
    kept: list[EvidenceCard] = []
    for card in group:
        merged = False
        for i, existing in enumerate(kept):
            if _word_jaccard(card.thesis, existing.thesis) >= _THESIS_SIMILARITY_THRESHOLD:
                kept[i] = _better_card(existing, card)
                merged = True
                break
        if not merged:
            kept.append(card)
    return kept


def deduplicate(cards: list[EvidenceCard]) -> list[EvidenceCard]:
    """Remove near-duplicate evidence cards while preserving distinct perspectives.

    Two cards are candidates for deduplication when:
    - Their canonical URLs match (exact dedup), OR
    - Their title Jaccard similarity >= TITLE_SIMILARITY_THRESHOLD

    In both cases the lower-quality source is dropped UNLESS thesis Jaccard
    similarity is below THESIS_SIMILARITY_THRESHOLD, which indicates a genuinely
    different viewpoint on the same story — in that case both are kept.
    """
    if not cards:
        return []

    # First pass: group by canonical URL.
    url_groups: dict[str, list[EvidenceCard]] = {}
    for card in cards:
        key = canonicalize_url(card.url)
        url_groups.setdefault(key, []).append(card)

    after_url_dedup: list[EvidenceCard] = []
    for group in url_groups.values():
        after_url_dedup.extend(_resolve_group(group))

    # Second pass: near-duplicate title detection across different URLs.
    result: list[EvidenceCard] = []
    for candidate in after_url_dedup:
        merged = False
        for i, existing in enumerate(result):
            if _word_jaccard(candidate.title, existing.title) >= _TITLE_SIMILARITY_THRESHOLD:
                if _word_jaccard(candidate.thesis, existing.thesis) < _THESIS_SIMILARITY_THRESHOLD:
                    # Same story, different thesis — keep both.
                    break
                result[i] = _better_card(existing, candidate)
                merged = True
                break
        if not merged:
            result.append(candidate)

    return result
