"""Central bank scout — finds recent CB speeches, minutes, and policy releases."""

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

_CB_INSTITUTIONS = [
    "Federal Reserve", "ECB", "Bank of England", "Bank of Japan",
    "PBoC", "RBA", "SNB", "Bank of Canada",
]


class CentralBankScout:
    """Search for recent central bank policy signals from major institutions."""

    name = "central_bank"
    optional = True

    def __init__(self, model: str, temperature: float, api_key: str | None) -> None:
        self.model = model
        self.temperature = temperature
        self.api_key = api_key

    def run(self, context: DiscoveryContext) -> ScoutRunResult:
        system_prompt = load_prompt("scouts/central_bank_extract").text
        payload = build_market_context(context)
        payload["institutions"] = _CB_INSTITUTIONS
        payload["instruction"] = (
            "Search for speeches, statements, minutes, or official releases from the listed "
            "central banks published between "
            f"{context.evidence_window_start.isoformat()} and {context.evidence_window_end.isoformat()}. "
            "Extract policy-relevant signals: "
            "rate path signals, balance sheet changes, inflation or growth language shifts. "
            "Return up to 4 evidence cards as JSON."
        )
        result = web_search_and_structure(
            model=self.model,
            api_key=self.api_key,
            system_prompt=system_prompt,
            user_payload=payload,
            temperature=self.temperature,
        )
        return ScoutRunResult(
            cards=to_evidence_cards(result.candidates, SourceType.CENTRAL_BANK),
            llm_usage=result.llm_usage,
        )
