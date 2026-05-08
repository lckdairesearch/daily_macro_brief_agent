"""Discovery orchestrator — runs enabled scouts and collects EvidenceCards.

Failed optional scouts are caught and recorded; they do not kill the run.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.discovery.scouts.base import DiscoveryContext, FixtureDiscoveryScout, Scout
from app.models import EvidenceCard, RunMode

if TYPE_CHECKING:
    from app.settings import Settings

logger = logging.getLogger(__name__)


def run_discovery(
    scouts: list[Scout],
    context: DiscoveryContext,
    failed_sources: list[str],
) -> list[EvidenceCard]:
    """Run each scout; swallow failures for optional scouts and record them."""
    all_cards: list[EvidenceCard] = []
    for scout in scouts:
        try:
            cards = scout.run(context)
            all_cards.extend(cards)
            logger.info("Scout '%s' returned %d card(s)", scout.name, len(cards))
        except Exception as exc:
            if scout.optional:
                logger.warning("Optional scout '%s' failed: %s", scout.name, exc)
                failed_sources.append(scout.name)
            else:
                raise RuntimeError(f"Required scout '{scout.name}' failed: {exc}") from exc
    return all_cards


def build_scouts(settings: "Settings", mode: RunMode) -> list[Scout]:
    """Construct the list of enabled scouts for the given run mode."""
    if mode == RunMode.SAMPLE:
        return [FixtureDiscoveryScout()]

    from app.discovery.scouts.central_bank import CentralBankScout
    from app.discovery.scouts.news import NewsScout
    from app.discovery.scouts.podcast import PodcastScout
    from app.discovery.scouts.research import ResearchScout
    from app.discovery.scouts.x import XScout

    scouts_cfg = settings.sources.get("scouts", {})
    llm_cfg = settings.sources.get("llm", {})
    creds = settings.creds

    scout_model: str = llm_cfg.get("scout_model", "openai/gpt-5.4")
    temperature: float = float(llm_cfg.get("temperature", 0.2))
    scouts: list[Scout] = []

    if scouts_cfg.get("news", {}).get("enabled", True):
        scouts.append(
            NewsScout(model=scout_model, temperature=temperature, api_key=creds.openai_api_key)
        )

    if scouts_cfg.get("central_bank", {}).get("enabled", True):
        scouts.append(
            CentralBankScout(model=scout_model, temperature=temperature, api_key=creds.openai_api_key)
        )

    if scouts_cfg.get("research", {}).get("enabled", True):
        scouts.append(
            ResearchScout(model=scout_model, temperature=temperature, api_key=creds.openai_api_key)
        )

    if scouts_cfg.get("podcast", {}).get("enabled", True) and creds.listen_notes_api_key:
        scouts.append(
            PodcastScout(
                scout_model=scout_model,
                api_key=creds.openai_api_key,
                listen_notes_api_key=creds.listen_notes_api_key,
                lookback_hours=int(
                    scouts_cfg.get("podcast", {}).get("listen_notes_lookback_hours", 24)
                ),
            )
        )

    if scouts_cfg.get("x", {}).get("enabled", True) and creds.xai_api_key:
        x_model: str = llm_cfg.get("x_scout_model", "xai/grok-3")
        scouts.append(
            XScout(model=x_model, api_key=creds.xai_api_key, temperature=temperature)
        )

    return scouts
