"""X scout — uses Grok-4 with xAI Agent Tools x_search for live X post retrieval.

Live x_search requires the xai-sdk (not LiteLLM) and grok-4 family models.
grok-3 and older do not support server-side tools.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.discovery.scouts.base import (
    DiscoveryContext,
    DEFAULT_SCOUT_TIMEOUT_SECONDS,
    _parse_or_structure,
    build_market_context,
    to_evidence_cards,
)
from app.llm.prompt_registry import load_prompt
from app.models import EvidenceCard, SourceType

logger = logging.getLogger(__name__)

try:
    import grpc
except ImportError:  # pragma: no cover - exercised only when dependency is absent
    grpc = None  # type: ignore[assignment]

Client: Any = None
xai_user: Any = None
x_search: Any = None

# Real X post URLs: https://x.com/{username}/status/{numeric_id}
_X_URL_RE = re.compile(r"https?://(x|twitter)\.com/[\w]{1,50}/status/\d{10,20}$")


_XAI_TIMEOUT_SECONDS = DEFAULT_SCOUT_TIMEOUT_SECONDS
_XAI_MAX_TOKENS = 2000


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
        market_context = build_market_context(context)
        payload: dict[str, Any] = {
            "data_cutoff": market_context["data_cutoff"],
            "lookback_hours": context.lookback_hours,
            "flagged_market_moves": market_context["flagged_market_moves"][:5],
            "calendar_highlights": market_context["calendar_highlights"][:5],
            "theme_keywords": market_context["theme_keywords"][:15],
        }
        portfolio_themes = [
            p.get("label") or p.get("rationale", "")
            for p in context.portfolio.get("core_positions", [])[:5]
        ]
        payload["portfolio_themes"] = [theme for theme in portfolio_themes if theme]
        payload["instruction"] = (
            "Search only for the strongest verified posts. Return up to 3 cards. "
            'If none qualify, return {"cards":[]}.'
        )
        targets = _search_targets(payload)
        context_lines = [
            f"Data cutoff: {payload['data_cutoff']}",
            f"Search targets: {', '.join(targets)}",
            "Use the targets above; do not run a broad macro search.",
        ]
        if payload["calendar_highlights"]:
            events = [
                f"{item['country']} {item['event']}"
                for item in payload["calendar_highlights"][:3]
            ]
            context_lines.append(f"Calendar context: {', '.join(events)}")

        prompt_text = (
            system_prompt
            + "\n\n"
            + "\n".join(context_lines)
            + '\n\nReturn valid JSON only. If nothing qualifies, return {"cards":[]}.'
        )

        since = datetime.now(timezone.utc) - timedelta(hours=24)
        # Strip provider prefix if present (xai_sdk uses bare model names)
        clean_model = self.model.removeprefix("xai/")

        client_cls = _load_client()
        if client_cls is None:
            return []
        user_msg = xai_user or (lambda text: text)
        search_tool = x_search or (lambda **_: None)

        client = client_cls(api_key=self.api_key, timeout=_XAI_TIMEOUT_SECONDS)
        chat = client.chat.create(
            model=clean_model,
            tools=[search_tool(from_date=since)],
            temperature=self.temperature,
            max_tokens=_XAI_MAX_TOKENS,
            reasoning_effort="medium",
        )
        chat.append(user_msg(prompt_text))
        try:
            result = chat.sample()
        except _rpc_error_type() as exc:
            logger.warning("XScout: xAI x_search request failed: %s", exc)
            return []

        text: str = result.content or ""
        # Cleanup structuring falls back to LiteLLM — must use prefixed model name.
        candidates = _parse_or_structure(text, model=self.model, api_key=self.api_key)
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


def _load_client() -> Any | None:
    """Load xai-sdk lazily so optional X scout dependency failures do not break imports."""
    global Client, xai_user, x_search
    if Client is not None:
        return Client

    try:
        from xai_sdk import Client as sdk_client
        from xai_sdk.chat import user as sdk_user
        from xai_sdk.tools import x_search as sdk_x_search
    except Exception as exc:
        logger.warning("XScout: xai-sdk unavailable or incompatible: %s", exc)
        return None

    Client = sdk_client
    xai_user = sdk_user
    x_search = sdk_x_search
    return Client


def _rpc_error_type() -> type[BaseException]:
    if grpc is not None:
        return grpc.RpcError
    return RuntimeError


def _search_targets(payload: dict[str, Any]) -> list[str]:
    """Build a small concrete target list for x_search."""
    targets: list[str] = []
    for move in payload.get("flagged_market_moves", []):
        display_name = move.get("display_name")
        if display_name:
            targets.append(str(display_name))
    for event in payload.get("calendar_highlights", []):
        name = event.get("event")
        country = event.get("country")
        if name and country:
            targets.append(f"{country} {name}")
    for keyword in payload.get("theme_keywords", []):
        if keyword:
            targets.append(str(keyword))

    if not targets:
        targets = ["S&P 500", "US rates", "gold", "USD/JPY", "China macro"]

    deduped: list[str] = []
    seen: set[str] = set()
    for target in targets:
        normalized = target.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(target)
        if len(deduped) >= 6:
            break
    return deduped
