"""Research scout — finds recent sell-side, Substack, and policy analysis."""

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


class ResearchScout:
    """Search for longform research, buy-side commentary, and non-mainstream analysis."""

    name = "research"
    optional = True

    def __init__(self, model: str, temperature: float, api_key: str | None) -> None:
        self.model = model
        self.temperature = temperature
        self.api_key = api_key

    def run(self, context: DiscoveryContext) -> ScoutRunResult:
        system_prompt = load_prompt("scouts/research_search").text
        payload = build_market_context(context)
        portfolio_themes = [
            p.get("rationale", "") for p in
            context.portfolio.get("core_positions", [])
        ]
        payload["portfolio_themes"] = portfolio_themes
        payload["instruction"] = (
            "Search for research notes, Substack analysis, or buy-side commentary published "
            f"between {context.evidence_window_start.isoformat()} and {context.evidence_window_end.isoformat()} "
            "that give a non-consensus or primary-source view on the "
            "themes and market moves listed. Favour material with a clear implication for the "
            "portfolio themes. Return up to 4 evidence cards as JSON."
        )
        result = web_search_and_structure(
            model=self.model,
            api_key=self.api_key,
            system_prompt=system_prompt,
            user_payload=payload,
            temperature=self.temperature,
        )
        return ScoutRunResult(
            cards=to_evidence_cards(result.candidates, SourceType.RESEARCH),
            llm_usage=result.llm_usage,
        )
