"""Podcast scout.

Flow:
  1. Query Listen Notes API for macro-relevant episodes in the last N hours.
  2. GPT filters the episode list to portfolio-relevant candidates.
  3. For each relevant episode, GPT with web search recalls the content
     (show notes, transcripts, summaries) and extracts macro insights.
  4. Returns EvidenceCards with source URLs pointing to the episode page.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import litellm
import requests
from openai import OpenAI
from pydantic import BaseModel, Field

from app.discovery.scouts.base import (
    DiscoveryContext,
    _EvidenceCandidate,
    _EvidenceCandidateList,
    _parse_or_structure,
    build_market_context,
    to_evidence_cards,
)
from app.llm.prompt_registry import load_prompt
from app.models import EvidenceCard, SourceType

logger = logging.getLogger(__name__)

_LISTEN_NOTES_BASE = "https://listen-api.listennotes.com/api/v2"
_DEFAULT_FREEFLOW_SEARCH_TERMS = [
    "macro economy", "Federal Reserve", "central bank", "inflation",
    "interest rates", "bond market", "global macro", "emerging markets",
]
_DEFAULT_CURATED_MIN_EPISODES = 6
_DEFAULT_MAX_CURATED_QUERIES = 8
_DEFAULT_MAX_FREEFLOW_QUERIES = 4
_MAX_EPISODES_TO_FILTER = 20
_MAX_RELEVANT_EPISODES = 3


class _EpisodeFilterResult(BaseModel):
    """Which episodes are relevant to the portfolio themes."""

    relevant_indices: list[int] = Field(default_factory=list)


class _EpisodeContent(BaseModel):
    """Macro insights extracted from one podcast episode."""

    thesis: str
    evidence: str
    macro_relevance: str
    portfolio_relevance: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)


class PodcastScout:
    """Find macro-relevant podcast episodes via Listen Notes; extract insights via GPT."""

    name = "podcast"
    optional = True

    def __init__(
        self,
        scout_model: str,
        api_key: str | None,
        listen_notes_api_key: str,
        lookback_hours: int = 24,
        curated_podcasts: list[dict[str, Any]] | None = None,
        freeflow_search_terms: list[str] | None = None,
        curated_min_episodes: int = _DEFAULT_CURATED_MIN_EPISODES,
        max_curated_queries: int = _DEFAULT_MAX_CURATED_QUERIES,
        max_freeflow_queries: int = _DEFAULT_MAX_FREEFLOW_QUERIES,
    ) -> None:
        self.scout_model = scout_model
        self.api_key = api_key
        self.listen_notes_api_key = listen_notes_api_key
        self.lookback_hours = lookback_hours
        self.curated_podcasts = curated_podcasts or []
        self.freeflow_search_terms = freeflow_search_terms or _DEFAULT_FREEFLOW_SEARCH_TERMS
        self.curated_min_episodes = curated_min_episodes
        self.max_curated_queries = max_curated_queries
        self.max_freeflow_queries = max_freeflow_queries

    def run(self, context: DiscoveryContext) -> list[EvidenceCard]:
        episodes = self._fetch_episodes()
        if not episodes:
            logger.info("PodcastScout: no episodes found in Listen Notes search")
            return []

        relevant = self._filter_relevant(episodes, context)
        if not relevant:
            logger.info("PodcastScout: no portfolio-relevant episodes after filtering")
            return []

        cards: list[EvidenceCard] = []
        for ep in relevant[:_MAX_RELEVANT_EPISODES]:
            try:
                card = self._extract_episode_insights(ep, context)
                if card:
                    cards.append(card)
            except Exception as exc:
                logger.warning("PodcastScout: failed to extract from '%s': %s", ep.get("title", "?"), exc)

        return cards

    # ------------------------------------------------------------------
    # Step 1: Fetch episodes from Listen Notes
    # ------------------------------------------------------------------

    def _fetch_episodes(self) -> list[dict[str, Any]]:
        cutoff_ts = int((datetime.now(timezone.utc) - timedelta(hours=self.lookback_hours)).timestamp())
        episodes: list[dict[str, Any]] = []

        curated = sorted(
            self.curated_podcasts[: self.max_curated_queries],
            key=lambda item: float(item.get("priority", 0.5)),
            reverse=True,
        )
        for podcast in curated:
            query = str(podcast.get("name", "")).strip()
            if not query:
                continue
            results = self._search_listen_notes(query, cutoff_ts, podcast.get("ocid"))
            matches = [ep for ep in results if _matches_curated_podcast(ep, podcast)]
            episodes.extend(matches or results[:1])
            time.sleep(0.3)  # conservative rate limit

        if len(_dedupe_episodes(episodes)) < self.curated_min_episodes:
            for term in self.freeflow_search_terms[: self.max_freeflow_queries]:
                episodes.extend(self._search_listen_notes(term, cutoff_ts))
                time.sleep(0.3)

        return _dedupe_episodes(episodes)[:_MAX_EPISODES_TO_FILTER]

    def _search_listen_notes(
        self,
        query: str,
        cutoff_ts: int,
        ocid: str | None = None,
    ) -> list[dict[str, Any]]:
        headers = {"X-ListenAPI-Key": self.listen_notes_api_key}
        params: dict[str, Any] = {
            "q": query,
            "type": "episode",
            "published_after": cutoff_ts,
            "len_min": 5,
            "page_size": 5,
            "language": "English",
        }
        if ocid:
            params["ocid"] = ocid
        try:
            resp = requests.get(
                f"{_LISTEN_NOTES_BASE}/search",
                headers=headers,
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            return [
                ep for ep in resp.json().get("results", [])
                if _is_fresh_episode(ep, cutoff_ts)
            ]
        except Exception as exc:
            logger.warning("PodcastScout: Listen Notes search failed for '%s': %s", query, exc)
            return []

    # ------------------------------------------------------------------
    # Step 2: Filter to portfolio-relevant episodes
    # ------------------------------------------------------------------

    def _filter_relevant(
        self, episodes: list[dict[str, Any]], context: DiscoveryContext
    ) -> list[dict[str, Any]]:
        episode_summaries = [
            {
                "index": i,
                "title": ep.get("title_original", ep.get("title", "")),
                "podcast": ep.get("podcast", {}).get("title_original", ""),
                "description": (ep.get("description_original", ep.get("description", "")) or "")[:300],
            }
            for i, ep in enumerate(episodes)
        ]

        portfolio_themes = [p.get("rationale", "") for p in context.portfolio.get("core_positions", [])]
        theme_keywords: list[str] = []
        for t in context.themes:
            theme_keywords.extend(t.get("keywords", []))

        payload = {
            "episodes": episode_summaries,
            "portfolio_themes": portfolio_themes,
            "theme_keywords": theme_keywords[:20],
            "instruction": (
                "Return the indices of episodes that discuss macro topics directly relevant "
                "to the portfolio themes or theme keywords. Exclude promotional, evergreen, "
                "or generic finance content. Return JSON: {\"relevant_indices\": [0, 2, ...]}"
            ),
        }
        kwargs: dict[str, Any] = {
            "model": self.scout_model,
            "messages": [
                {"role": "system", "content": "You are a macro research filter. Return valid JSON only."},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "timeout": 30,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        try:
            response = litellm.completion(**kwargs)
            text = response.choices[0].message.content or '{"relevant_indices": []}'
            result = _EpisodeFilterResult.model_validate_json(text)
            return [episodes[i] for i in result.relevant_indices if i < len(episodes)]
        except Exception as exc:
            logger.warning("PodcastScout: episode filter LLM call failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Step 3: Extract insights via GPT web search
    # ------------------------------------------------------------------

    def _extract_episode_insights(
        self, episode: dict[str, Any], context: DiscoveryContext
    ) -> EvidenceCard | None:
        title = episode.get("title_original", episode.get("title", ""))
        podcast_name = episode.get("podcast", {}).get("title_original", "")
        episode_url = episode.get("listennotes_url", "")
        pub_ms = episode.get("pub_date_ms")
        pub_date = (
            datetime.fromtimestamp(pub_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            if pub_ms else "recent"
        )

        system_prompt = load_prompt("scouts/podcast_extract").text
        portfolio_themes = [p.get("rationale", "") for p in context.portfolio.get("core_positions", [])]
        market_ctx = build_market_context(context)

        payload = {
            "episode_title": title,
            "podcast_name": podcast_name,
            "episode_url": episode_url,
            "published_date": pub_date,
            "portfolio_themes": portfolio_themes,
            "flagged_market_moves": market_ctx["flagged_market_moves"],
            "theme_keywords": market_ctx["theme_keywords"][:15],
            "instruction": (
                f"Search the web for content from the episode '{title}' by '{podcast_name}' "
                f"published on {pub_date}. Find the transcript, show notes, or any summary. "
                "Extract the key macro insights discussed. Return a single evidence card as JSON: "
                '{"cards": [{...}]}. If you cannot find any content, return {"cards": []}.'
            ),
        }

        clean_model = self.scout_model.removeprefix("openai/")
        client = OpenAI(api_key=self.api_key, timeout=90)
        oai_response = client.responses.create(
            model=clean_model,
            tools=[{"type": "web_search_preview"}],
            instructions=(
                system_prompt
                + '\n\nReturn valid JSON only, using schema: {"cards": [...]}'
            ),
            input=json.dumps(payload, ensure_ascii=False),
        )
        text = oai_response.output_text or ""
        candidates = _parse_or_structure(text, model=self.scout_model, api_key=self.api_key)

        if not candidates.cards:
            return None

        c = candidates.cards[0]
        pub_dt: datetime | None = None
        if pub_ms:
            pub_dt = datetime.fromtimestamp(pub_ms / 1000, tz=timezone.utc)

        return EvidenceCard(
            id=f"ev_{uuid.uuid4().hex[:8]}",
            title=c.title or title,
            source_name=podcast_name or c.source_name,
            source_type=SourceType.PODCAST,
            url=episode_url or c.url,
            published_at=pub_dt,
            retrieved_at=datetime.now(timezone.utc),
            thesis=c.thesis,
            evidence=c.evidence,
            macro_relevance=c.macro_relevance,
            portfolio_relevance=c.portfolio_relevance,
            confidence=c.confidence,
            tags=c.tags,
        )


def _dedupe_episodes(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate Listen Notes episodes by id, preserving priority order."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for ep in episodes:
        eid = ep.get("id", "")
        if not eid or eid in seen:
            continue
        seen.add(eid)
        unique.append(ep)
    return unique


def _matches_curated_podcast(episode: dict[str, Any], podcast: dict[str, Any]) -> bool:
    """Return True when an episode appears to belong to a configured curated show."""
    podcast_title = (
        episode.get("podcast", {}).get("title_original")
        or episode.get("podcast", {}).get("title")
        or ""
    )
    title_norm = _normalize_match_text(podcast_title)
    candidates = [podcast.get("name", ""), *podcast.get("aliases", [])]
    return any(
        _normalize_match_text(candidate) in title_norm
        for candidate in candidates
        if candidate
    )


def _normalize_match_text(value: str) -> str:
    return " ".join(value.casefold().replace("&", "and").split())


def _is_fresh_episode(episode: dict[str, Any], cutoff_ts: int) -> bool:
    """Defend against upstream search results that ignore published_after."""
    pub_ms = episode.get("pub_date_ms")
    if not pub_ms:
        return False
    try:
        return int(pub_ms) >= cutoff_ts * 1000
    except (TypeError, ValueError):
        return False
