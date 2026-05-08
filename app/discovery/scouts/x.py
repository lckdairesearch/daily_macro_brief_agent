"""X scout — uses Grok-4 with xAI Agent Tools x_search for live X post retrieval.

Live x_search requires the xai-sdk (not LiteLLM) and grok-4 family models.
grok-3 and older do not support server-side tools.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from xai_sdk import Client
from xai_sdk.chat import user as xai_user
from xai_sdk.tools import x_search

from app.discovery.scouts.base import (
    DiscoveryContext,
    _parse_or_structure,
    build_market_context,
    to_evidence_cards,
)
from app.llm.prompt_registry import load_prompt
from app.models import EvidenceCard, SourceType

logger = logging.getLogger(__name__)

# Real X post URLs: https://x.com/{username}/status/{numeric_id}
_X_URL_RE = re.compile(r"https?://(x|twitter)\.com/[\w]{1,50}/status/\d{10,20}$")


class XScout:
    """Find market narratives on X using Grok-4's live x_search Agent Tool."""

    name = "x"
    optional = True

    def __init__(self, model: str, api_key: str | None, temperature: float = 0.2) -> None:
        self.model = model  # e.g. "grok-4"
        self.api_key = api_key
        self.temperature = temperature

    def run(self, context: DiscoveryContext) -> list[EvidenceCard]:
        system_prompt = load_prompt("scouts/x_narrative_search").text
        payload = build_market_context(context)
        portfolio_themes = [
            p.get("rationale", "") for p in context.portfolio.get("core_positions", [])
        ]
        payload["portfolio_themes"] = portfolio_themes
        payload["instruction"] = (
            "Use live X search to find real posts from the last 24 hours relevant to "
            "the flagged market moves or portfolio themes. Only include posts you have "
            "retrieved and can verify are real. Return up to 4 evidence cards as JSON. "
            'If you cannot find real verified posts, return {"cards": []}.'
        )

        prompt_text = (
            system_prompt
            + '\n\nReturn valid JSON only, using schema: {"cards": [...]}\n\n'
            + json.dumps(payload, ensure_ascii=False)
        )

        since = datetime.now(timezone.utc) - timedelta(hours=24)
        # Strip provider prefix if present (xai_sdk uses bare model names)
        clean_model = self.model.removeprefix("xai/")

        client = Client(api_key=self.api_key)
        chat = client.chat.create(
            model=clean_model,
            tools=[x_search(from_date=since)],
        )
        chat.append(xai_user(prompt_text))
        result = chat.sample()

        text: str = result.content or ""
        # Use grok-4 itself for cleanup structuring (no web search needed for JSON formatting)
        candidates = _parse_or_structure(text, model=clean_model, api_key=self.api_key)
        cards = to_evidence_cards(candidates, SourceType.SOCIAL)
        return _filter_verified(cards)


def _filter_verified(cards: list[EvidenceCard]) -> list[EvidenceCard]:
    """Drop cards with missing titles or URLs that don't look like real X posts."""
    verified = []
    for card in cards:
        if not card.title:
            logger.warning("XScout: dropping card with empty title (likely hallucinated)")
            continue
        if not _X_URL_RE.match(card.url):
            logger.warning("XScout: dropping card with non-X URL: %s", card.url)
            continue
        verified.append(card)
    return verified
