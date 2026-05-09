"""Scout protocol, DiscoveryContext, shared LLM helpers, and FixtureDiscoveryScout."""

from __future__ import annotations

import json
import warnings

# litellm calls asyncio.get_event_loop() at import time — harmless noise in Python 3.10+.
warnings.filterwarnings("ignore", message="There is no current event loop")
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import litellm
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from app.models import EvidenceCard, RunMode, SourceType

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"
DEFAULT_SCOUT_TIMEOUT_SECONDS = 180


# ---------------------------------------------------------------------------
# Discovery context
# ---------------------------------------------------------------------------


@dataclass
class DiscoveryContext:
    """Shared context passed to every scout on each run."""

    market_snapshots: list[Any]   # list[MarketSnapshot]
    calendar_events: list[Any]    # list[CalendarEvent]
    themes: list[dict[str, Any]]
    portfolio: dict[str, Any]
    lookback_hours: int
    data_cutoff: datetime
    mode: RunMode


# ---------------------------------------------------------------------------
# Scout protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Scout(Protocol):
    """Contract every scout must satisfy."""

    name: str
    optional: bool

    def run(self, context: DiscoveryContext) -> list[EvidenceCard]: ...


# ---------------------------------------------------------------------------
# Internal LLM output schema
# ---------------------------------------------------------------------------


class _EvidenceCandidate(BaseModel):
    """Schema for one LLM-returned evidence candidate."""

    title: str
    source_name: str
    url: str
    published_at: str | None = None
    author: str | None = None
    thesis: str
    evidence: str
    macro_relevance: str
    portfolio_relevance: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)


class _EvidenceCandidateList(BaseModel):
    """Top-level LLM output."""

    cards: list[_EvidenceCandidate]


# ---------------------------------------------------------------------------
# Conversion helper
# ---------------------------------------------------------------------------


def to_evidence_cards(
    candidates: _EvidenceCandidateList,
    source_type: SourceType,
    retrieved_at: datetime | None = None,
) -> list[EvidenceCard]:
    """Convert raw LLM candidates into validated EvidenceCard objects."""
    now = retrieved_at or datetime.now(timezone.utc)
    cards: list[EvidenceCard] = []
    for c in candidates.cards:
        pub: datetime | None = None
        if c.published_at:
            try:
                pub = datetime.fromisoformat(c.published_at.replace("Z", "+00:00"))
            except ValueError:
                pass
        cards.append(
            EvidenceCard(
                id=f"ev_{uuid.uuid4().hex[:8]}",
                title=c.title,
                source_name=c.source_name,
                source_type=source_type,
                url=c.url,
                published_at=pub,
                retrieved_at=now,
                author=c.author,
                thesis=c.thesis,
                evidence=c.evidence,
                macro_relevance=c.macro_relevance,
                portfolio_relevance=c.portfolio_relevance,
                confidence=c.confidence,
                tags=c.tags,
            )
        )
    return cards


# ---------------------------------------------------------------------------
# Shared LLM web-search call
# ---------------------------------------------------------------------------


def web_search_and_structure(
    *,
    model: str,
    api_key: str | None,
    system_prompt: str,
    user_payload: dict[str, Any],
    temperature: float = 0.2,
    timeout: int = DEFAULT_SCOUT_TIMEOUT_SECONDS,
) -> _EvidenceCandidateList:
    """Call OpenAI Responses API with web_search_preview; parse into candidate list.

    LiteLLM's chat completions endpoint rejects the web_search_preview tool type,
    so this function uses the OpenAI client directly.
    Falls back to a cleanup structuring call if the response is not valid JSON.
    """
    clean_model = model.removeprefix("openai/")
    client = OpenAI(api_key=api_key, timeout=timeout)
    response = client.responses.create(
        model=clean_model,
        tools=[{"type": "web_search_preview"}],
        instructions=(
            system_prompt
            + '\n\nReturn valid JSON only, using schema: {"cards": [...]}'
        ),
        input=json.dumps(user_payload, ensure_ascii=False),
    )
    text: str = response.output_text or ""
    return _parse_or_structure(text, model=model, api_key=api_key)


def plain_completion_and_structure(
    *,
    model: str,
    api_key: str | None,
    system_prompt: str,
    user_payload: dict[str, Any],
    temperature: float = 0.2,
    timeout: int = 60,
) -> _EvidenceCandidateList:
    """Call LLM without web search (for pre-filtered content); parse into candidate list."""
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                system_prompt
                + '\n\nReturn valid JSON only, using schema: {"cards": [...]}'
            ),
        },
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "temperature": temperature,
        "timeout": timeout,
    }
    if api_key:
        kwargs["api_key"] = api_key

    response = litellm.completion(**kwargs)
    text: str = response.choices[0].message.content or ""
    try:
        return _EvidenceCandidateList.model_validate_json(text)
    except (ValidationError, ValueError) as exc:
        raise ValueError(f"LLM did not return valid candidate list JSON: {exc}") from exc


def _parse_or_structure(
    text: str,
    *,
    model: str,
    api_key: str | None,
) -> _EvidenceCandidateList:
    """Try direct JSON parse; fall back to a structuring call."""
    try:
        return _EvidenceCandidateList.model_validate_json(text)
    except (ValidationError, ValueError):
        pass

    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if match:
        try:
            return _EvidenceCandidateList.model_validate_json(match.group(1).strip())
        except (ValidationError, ValueError):
            pass

    # Last resort: ask the model to structure its own output
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    'Convert the following content into JSON with schema: '
                    '{"cards": [{"title": "", "source_name": "", "url": "", '
                    '"thesis": "", "evidence": "", "macro_relevance": "", '
                    '"portfolio_relevance": "", "confidence": 0.5, "tags": []}]}'
                ),
            },
            {"role": "user", "content": text},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
        "timeout": DEFAULT_SCOUT_TIMEOUT_SECONDS,
    }
    if api_key:
        kwargs["api_key"] = api_key
    cleanup = litellm.completion(**kwargs)
    return _EvidenceCandidateList.model_validate_json(
        cleanup.choices[0].message.content or "{\"cards\": []}"
    )


# ---------------------------------------------------------------------------
# Context payload builder (shared across GPT-based scouts)
# ---------------------------------------------------------------------------


def build_market_context(context: DiscoveryContext) -> dict[str, Any]:
    """Compact market-move summary for LLM payloads."""
    flagged = [
        {
            "instrument": s.instrument_id,
            "display_name": s.display_name,
            "change": s.one_day_change,
            "unit": s.one_day_change_unit,
            "zscore": s.one_day_zscore,
        }
        for s in context.market_snapshots
        if getattr(s, "threshold_flag", False)
    ]
    calendar_highlights = [
        {
            "event": e.event_name,
            "country": e.country_or_region,
            "importance": e.importance,
            "time_hkt": e.event_time_hkt.isoformat(),
        }
        for e in context.calendar_events
        if e.importance >= 2
    ][:10]
    theme_keywords: list[str] = []
    for t in context.themes:
        theme_keywords.extend(t.get("keywords", []))
    return {
        "data_cutoff": context.data_cutoff.isoformat(),
        "lookback_hours": context.lookback_hours,
        "flagged_market_moves": flagged,
        "calendar_highlights": calendar_highlights,
        "theme_keywords": theme_keywords[:30],
    }


# ---------------------------------------------------------------------------
# Fixture scout (sample mode)
# ---------------------------------------------------------------------------


class FixtureDiscoveryScout:
    """Returns deterministic evidence from the sample fixture file."""

    name = "fixture"
    optional = False

    def run(self, context: DiscoveryContext) -> list[EvidenceCard]:
        path = FIXTURE_DIR / "evidence_sample.json"
        if not path.exists():
            raise FileNotFoundError(f"Evidence fixture not found: {path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [EvidenceCard.model_validate(c) for c in raw["cards"]]
