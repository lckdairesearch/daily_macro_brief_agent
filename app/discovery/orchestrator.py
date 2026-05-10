"""Discovery orchestrator — runs enabled scouts and collects EvidenceCards.

Failed optional scouts are caught and recorded; they do not kill the run.
"""

from __future__ import annotations

import logging
from time import perf_counter
from typing import TYPE_CHECKING

from app.discovery.scouts.base import DiscoveryContext, FixtureDiscoveryScout, Scout
from app.models import EvidenceCard, LLMUsage, RunMode

if TYPE_CHECKING:
    from app.settings import Settings

logger = logging.getLogger(__name__)


def run_discovery(
    scouts: list[Scout],
    context: DiscoveryContext,
    failed_sources: list[str],
    timings: list[dict] | None = None,
) -> tuple[list[EvidenceCard], list[LLMUsage]]:
    """Run each scout; swallow failures for optional scouts and record them."""
    all_cards: list[EvidenceCard] = []
    llm_usage: list[LLMUsage] = []
    for scout in scouts:
        started = perf_counter()
        try:
            result = scout.run(context)
            elapsed = perf_counter() - started
            all_cards.extend(result.cards)
            llm_usage.extend(result.llm_usage)
            if timings is not None:
                timings.append(
                    {
                        "component": f"scout:{scout.name}",
                        "status": "success",
                        "seconds": round(elapsed, 3),
                        "cards": len(result.cards),
                    }
                )
            logger.info("Scout '%s' returned %d card(s)", scout.name, len(result.cards))
        except Exception as exc:
            elapsed = perf_counter() - started
            if scout.optional:
                logger.warning("Optional scout '%s' failed: %s", scout.name, exc)
                failed_sources.append(scout.name)
                if timings is not None:
                    timings.append(
                        {
                            "component": f"scout:{scout.name}",
                            "status": "failed",
                            "seconds": round(elapsed, 3),
                            "cards": 0,
                            "error": str(exc),
                        }
                    )
            else:
                if timings is not None:
                    timings.append(
                        {
                            "component": f"scout:{scout.name}",
                            "status": "failed",
                            "seconds": round(elapsed, 3),
                            "cards": 0,
                            "error": str(exc),
                        }
                    )
                raise RuntimeError(f"Required scout '{scout.name}' failed: {exc}") from exc
    return all_cards, llm_usage


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

    if scouts_cfg.get("podcast", {}).get("enabled", True) and creds.taddy_api_key:
        podcast_cfg = scouts_cfg.get("podcast", {})
        _lookback_raw = podcast_cfg.get("lookback_hours") or podcast_cfg.get("listen_notes_lookback_hours") or 24
        lookback = int(_lookback_raw)
        scouts.append(
            PodcastScout(
                scout_model=scout_model,
                api_key=creds.openai_api_key,
                taddy_user_id=creds.taddy_user_id or "",
                taddy_api_key=creds.taddy_api_key,
                lookback_hours=lookback,
                curated_podcasts=podcast_cfg.get("curated_podcasts", []),
                freeflow_search_terms=podcast_cfg.get("freeflow_search_terms", []),
                curated_min_episodes=int(podcast_cfg.get("curated_min_episodes", 6)),
                max_curated_queries=int(podcast_cfg.get("max_curated_queries", 8)),
                max_freeflow_queries=int(podcast_cfg.get("max_freeflow_queries", 4)),
                max_relevant_episodes=int(podcast_cfg.get("max_relevant_episodes", 3)),
                use_transcripts=bool(podcast_cfg.get("use_transcripts", True)),
            )
        )

    if scouts_cfg.get("x", {}).get("enabled", True) and creds.xai_api_key:
        x_model: str = llm_cfg.get("x_scout_model", "xai/grok-3")
        scouts.append(
            XScout(model=x_model, api_key=creds.xai_api_key, temperature=temperature)
        )

    return scouts
