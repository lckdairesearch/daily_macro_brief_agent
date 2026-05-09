"""Podcast scout.

Flow:
  1. Query Taddy GraphQL API for episodes published in the last N hours,
     from curated channels or by freeflow topic search.
  2. GPT filters the episode list to portfolio-relevant candidates.
  3. For each relevant episode:
     a. If a non-exclusive plain-text transcript URL is available, fetch and
        use it directly as episode content (avoids OpenAI web-search call).
     b. Otherwise, use the OpenAI Responses API with web_search_preview to
        recall public show notes / summaries.
  4. Extract macro insights and return EvidenceCards for Theme Radar.
"""

from __future__ import annotations

import json
import logging
import re
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
    DEFAULT_SCOUT_TIMEOUT_SECONDS,
    _EvidenceCandidateList,
    _parse_or_structure,
    build_market_context,
    to_evidence_cards,
)
from app.llm.prompt_registry import load_prompt
from app.models import EvidenceCard, SourceType

logger = logging.getLogger(__name__)

_TADDY_ENDPOINT = "https://api.taddy.org"
_DEFAULT_FREEFLOW_SEARCH_TERMS = [
    "global macro",
    "Federal Reserve",
    "central bank",
    "inflation",
    "interest rates",
    "bond market",
    "emerging markets",
    "commodities",
]
_DEFAULT_CURATED_MIN_EPISODES = 6
_DEFAULT_MAX_CURATED_QUERIES = 8
_DEFAULT_MAX_FREEFLOW_QUERIES = 4
_DEFAULT_MAX_RELEVANT_EPISODES = 3
_MAX_EPISODES_TO_FILTER = 20
_TRANSCRIPT_MAX_CHARS = 8000  # truncate long transcripts before passing to LLM


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
    """Find macro-relevant podcast episodes via Taddy; extract insights via GPT."""

    name = "podcast"
    optional = True

    def __init__(
        self,
        scout_model: str,
        api_key: str | None,
        taddy_user_id: str,
        taddy_api_key: str,
        lookback_hours: int = 24,
        curated_podcasts: list[dict[str, Any]] | None = None,
        freeflow_search_terms: list[str] | None = None,
        curated_min_episodes: int = _DEFAULT_CURATED_MIN_EPISODES,
        max_curated_queries: int = _DEFAULT_MAX_CURATED_QUERIES,
        max_freeflow_queries: int = _DEFAULT_MAX_FREEFLOW_QUERIES,
        max_relevant_episodes: int = _DEFAULT_MAX_RELEVANT_EPISODES,
        use_transcripts: bool = True,
    ) -> None:
        self.scout_model = scout_model
        self.api_key = api_key
        self.taddy_user_id = taddy_user_id
        self.taddy_api_key = taddy_api_key
        self.lookback_hours = lookback_hours
        self.curated_podcasts = curated_podcasts or []
        self.freeflow_search_terms = freeflow_search_terms or _DEFAULT_FREEFLOW_SEARCH_TERMS
        self.curated_min_episodes = curated_min_episodes
        self.max_curated_queries = max_curated_queries
        self.max_freeflow_queries = max_freeflow_queries
        self.max_relevant_episodes = max_relevant_episodes
        self.use_transcripts = use_transcripts

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, context: DiscoveryContext) -> list[EvidenceCard]:
        episodes = self._fetch_episodes()
        if not episodes:
            logger.info("PodcastScout: no episodes found")
            return []

        relevant = self._filter_relevant(episodes, context)
        if not relevant:
            logger.info("PodcastScout: no portfolio-relevant episodes after filtering")
            return []

        cards: list[EvidenceCard] = []
        for ep in relevant[: self.max_relevant_episodes]:
            try:
                card = self._extract_episode_insights(ep, context)
                if card:
                    cards.append(card)
            except Exception as exc:
                logger.warning(
                    "PodcastScout: failed to extract from '%s': %s",
                    ep.get("name", "?"),
                    exc,
                )

        return cards

    # ------------------------------------------------------------------
    # Step 1: Fetch episodes from Taddy
    # ------------------------------------------------------------------

    def _fetch_episodes(self) -> list[dict[str, Any]]:
        cutoff_ts = int(
            (datetime.now(timezone.utc) - timedelta(hours=self.lookback_hours)).timestamp()
        )
        episodes: list[dict[str, Any]] = []

        # Curated shows: fetch latest episodes directly from each series
        curated = sorted(
            self.curated_podcasts[: self.max_curated_queries],
            key=lambda item: float(item.get("priority", 0.5)),
            reverse=True,
        )
        for podcast in curated:
            name = str(podcast.get("name", "")).strip()
            if not name:
                continue
            results = self._fetch_curated_episodes(name, cutoff_ts)
            episodes.extend(results)
            time.sleep(0.3)

        # Freeflow topic search if curated didn't yield enough
        if len(_dedupe_episodes(episodes)) < self.curated_min_episodes:
            for term in self.freeflow_search_terms[: self.max_freeflow_queries]:
                episodes.extend(self._search_freeflow(term, cutoff_ts))
                time.sleep(0.3)

        return _dedupe_episodes(episodes)[:_MAX_EPISODES_TO_FILTER]

    def _fetch_curated_episodes(
        self, podcast_name: str, cutoff_ts: int
    ) -> list[dict[str, Any]]:
        """Get latest episodes for a named series; filter client-side by date."""
        query = """
        {
          getPodcastSeries(name: "%s") {
            uuid
            name
            websiteUrl
            episodes(sortOrder: LATEST, page: 1, limitPerPage: 5) {
              uuid name description audioUrl datePublished
              taddyTranscribeStatus
              transcriptUrlsWithDetails { url type isTaddyExclusive language }
              podcastSeries { uuid name websiteUrl }
            }
          }
        }
        """ % podcast_name.replace('"', '\\"')
        try:
            data = self._gql_post(query)
            series = (data.get("data") or {}).get("getPodcastSeries") or {}
            episodes = series.get("episodes") or []
            return [ep for ep in episodes if _is_fresh_episode(ep, cutoff_ts)]
        except Exception as exc:
            logger.warning("PodcastScout: curated fetch failed for '%s': %s", podcast_name, exc)
            return []

    def _search_freeflow(self, term: str, cutoff_ts: int) -> list[dict[str, Any]]:
        """Full-text search for recent episodes matching a macro topic."""
        query = """
        {
          search(
            term: "%s"
            filterForTypes: PODCASTEPISODE
            filterForPublishedAfter: %d
            limitPerPage: 5
          ) {
            searchId
            podcastEpisodes {
              uuid name description audioUrl datePublished
              taddyTranscribeStatus
              transcriptUrlsWithDetails { url type isTaddyExclusive language }
              podcastSeries { uuid name websiteUrl }
            }
          }
        }
        """ % (term.replace('"', '\\"'), cutoff_ts)
        try:
            data = self._gql_post(query)
            search = (data.get("data") or {}).get("search") or {}
            return search.get("podcastEpisodes") or []
        except Exception as exc:
            logger.warning("PodcastScout: freeflow search failed for '%s': %s", term, exc)
            return []

    def _gql_post(self, query: str) -> dict[str, Any]:
        """POST a GraphQL query to Taddy and raise on HTTP or GraphQL errors."""
        resp = requests.post(
            _TADDY_ENDPOINT,
            json={"query": query},
            headers={
                "Content-Type": "application/json",
                "X-USER-ID": self.taddy_user_id,
                "X-API-KEY": self.taddy_api_key,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"Taddy GraphQL error: {data['errors']}")
        return data

    # ------------------------------------------------------------------
    # Step 2: Filter to portfolio-relevant episodes
    # ------------------------------------------------------------------

    def _filter_relevant(
        self, episodes: list[dict[str, Any]], context: DiscoveryContext
    ) -> list[dict[str, Any]]:
        episode_summaries = [
            {
                "index": i,
                "title": ep.get("name", ""),
                "podcast": (ep.get("podcastSeries") or {}).get("name", ""),
                "description": _strip_html(ep.get("description") or "")[:300],
            }
            for i, ep in enumerate(episodes)
        ]

        portfolio_themes = [
            p.get("rationale", "") for p in context.portfolio.get("core_positions", [])
        ]
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
            "timeout": DEFAULT_SCOUT_TIMEOUT_SECONDS,
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
    # Step 3: Extract insights — transcript-backed or web-search fallback
    # ------------------------------------------------------------------

    def _extract_episode_insights(
        self, episode: dict[str, Any], context: DiscoveryContext
    ) -> EvidenceCard | None:
        title = episode.get("name", "")
        series = episode.get("podcastSeries") or {}
        podcast_name = series.get("name", "")
        episode_url = episode.get("audioUrl", "") or series.get("websiteUrl", "")
        pub_ts = episode.get("datePublished")
        pub_date = (
            datetime.fromtimestamp(pub_ts, tz=timezone.utc).strftime("%Y-%m-%d")
            if pub_ts else "recent"
        )

        system_prompt = load_prompt("scouts/podcast_extract").text
        portfolio_themes = [
            p.get("rationale", "") for p in context.portfolio.get("core_positions", [])
        ]
        market_ctx = build_market_context(context)

        # Try transcript first (if enabled and a non-exclusive plain-text URL exists)
        transcript_text: str | None = None
        if self.use_transcripts:
            transcript_text = _fetch_plain_transcript(episode)

        if transcript_text:
            candidates = self._extract_from_transcript(
                title, podcast_name, pub_date, transcript_text,
                system_prompt, portfolio_themes, market_ctx,
            )
        else:
            candidates = self._extract_via_web_search(
                title, podcast_name, episode_url, pub_date,
                system_prompt, portfolio_themes, market_ctx,
            )

        if not candidates.cards:
            return None

        c = candidates.cards[0]
        pub_dt = datetime.fromtimestamp(pub_ts, tz=timezone.utc) if pub_ts else None

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

    def _extract_from_transcript(
        self,
        title: str,
        podcast_name: str,
        pub_date: str,
        transcript_text: str,
        system_prompt: str,
        portfolio_themes: list[str],
        market_ctx: dict[str, Any],
    ) -> _EvidenceCandidateList:
        payload = {
            "episode_title": title,
            "podcast_name": podcast_name,
            "published_date": pub_date,
            "transcript": transcript_text[:_TRANSCRIPT_MAX_CHARS],
            "portfolio_themes": portfolio_themes,
            "flagged_market_moves": market_ctx["flagged_market_moves"],
            "theme_keywords": market_ctx["theme_keywords"][:15],
            "instruction": (
                "Extract the key macro insights from the transcript. "
                "Return a single evidence card as JSON: {\"cards\": [{...}]}. "
                "If no macro-relevant content, return {\"cards\": []}."
            ),
        }
        kwargs: dict[str, Any] = {
            "model": self.scout_model,
            "messages": [
                {"role": "system", "content": system_prompt + '\n\nReturn valid JSON only, using schema: {"cards": [...]}'},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "timeout": DEFAULT_SCOUT_TIMEOUT_SECONDS,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        try:
            response = litellm.completion(**kwargs)
            text = response.choices[0].message.content or '{"cards": []}'
            return _EvidenceCandidateList.model_validate_json(text)
        except Exception as exc:
            logger.warning("PodcastScout: transcript extraction failed: %s", exc)
            return _EvidenceCandidateList(cards=[])

    def _extract_via_web_search(
        self,
        title: str,
        podcast_name: str,
        episode_url: str,
        pub_date: str,
        system_prompt: str,
        portfolio_themes: list[str],
        market_ctx: dict[str, Any],
    ) -> _EvidenceCandidateList:
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
        client = OpenAI(api_key=self.api_key, timeout=DEFAULT_SCOUT_TIMEOUT_SECONDS)
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
        return _parse_or_structure(text, model=self.scout_model, api_key=self.api_key)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _gql_episode_fields() -> str:
    """Shared GraphQL fragment for episode fields (not used inline but kept for reference)."""
    return """
      uuid name description audioUrl datePublished
      taddyTranscribeStatus
      transcriptUrlsWithDetails { url type isTaddyExclusive language }
      podcastSeries { uuid name websiteUrl }
    """


def _fetch_plain_transcript(episode: dict[str, Any]) -> str | None:
    """
    Return plain-text transcript content if a non-exclusive text/plain URL exists.
    Returns None if no suitable URL is found or fetching fails.
    """
    transcript_urls = episode.get("transcriptUrlsWithDetails") or []
    for entry in transcript_urls:
        if entry.get("isTaddyExclusive"):
            continue
        if entry.get("type") != "text/plain":
            continue
        url = entry.get("url", "")
        if not url:
            continue
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            text = resp.text.strip()
            if text:
                logger.debug("PodcastScout: fetched plain-text transcript (%d chars)", len(text))
                return text
        except Exception as exc:
            logger.debug("PodcastScout: transcript fetch failed: %s", exc)
    return None


def _strip_html(text: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r"<[^>]+>", " ", text).strip()


def _dedupe_episodes(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate Taddy episodes by uuid, preserving priority order."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for ep in episodes:
        eid = ep.get("uuid", "")
        if not eid or eid in seen:
            continue
        seen.add(eid)
        unique.append(ep)
    return unique


def _is_fresh_episode(episode: dict[str, Any], cutoff_ts: int) -> bool:
    """Return True when the episode was published at or after cutoff_ts (epoch seconds)."""
    pub = episode.get("datePublished")
    if not pub:
        return False
    try:
        return int(pub) >= cutoff_ts
    except (TypeError, ValueError):
        return False
