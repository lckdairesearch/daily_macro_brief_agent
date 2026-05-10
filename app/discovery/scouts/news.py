"""News scout — uses GPT with web search to find overnight macro news catalysts."""

from __future__ import annotations

from app.discovery.scouts.base import (
    DiscoveryContext,
    ScoutRunResult,
    build_market_context,
    to_evidence_cards,
    web_search_and_structure,
)
from app.llm.prompt_registry import load_prompt
from app.models import EvidenceCard, SourceType


class NewsScout:
    """Search for overnight news items that explain confirmed market moves."""

    name = "news"
    optional = True

    def __init__(self, model: str, temperature: float, api_key: str | None) -> None:
        self.model = model
        self.temperature = temperature
        self.api_key = api_key

    def run(self, context: DiscoveryContext) -> ScoutRunResult:
        system_prompt = load_prompt("scouts/news_search").text
        payload = build_market_context(context)
        payload["instruction"] = (
            "Search for news published between "
            f"{context.evidence_window_start.isoformat()} and {context.evidence_window_end.isoformat()} "
            "that explain the flagged market moves or are relevant to the themes and calendar events. "
            "Return up to 5 high-signal evidence cards as JSON."
        )
        result = web_search_and_structure(
            model=self.model,
            api_key=self.api_key,
            system_prompt=system_prompt,
            user_payload=payload,
            temperature=self.temperature,
        )
        return ScoutRunResult(
            cards=to_evidence_cards(result.candidates, SourceType.NEWS),
            llm_usage=result.llm_usage,
        )
