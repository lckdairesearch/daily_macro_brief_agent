"""Pipeline orchestrator. Owns the run sequence; no provider-specific logic here."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from app.data.calendar import load_fixture_calendar
from app.models import (
    BriefDraft,
    CalendarEvent,
    DeliveryStatus,
    EvidenceCard,
    MarketSnapshot,
    PipelineResult,
    RunMetadata,
    RunMode,
)

if TYPE_CHECKING:
    from app.settings import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"


def run_pipeline(mode: RunMode | str, settings: "Settings") -> PipelineResult:
    """
    Main pipeline flow:
      1. Determine run window and data cutoff
      2. Fetch market data            (TODO Step 5: app/data/market.py)
      3. Fetch economic calendar      (TODO Step 6: app/data/calendar.py)
      4. Enrich missing consensus     (TODO Step 6: app/data/calendar.py)
      5. Discover source evidence     (TODO Step 8: app/discovery/orchestrator.py)
      6. Deduplicate evidence         (TODO Step 9: app/synthesis/deduper.py)
      7. Rank evidence and events     (TODO Step 9: app/synthesis/ranker.py)
      8. Write grounded brief         (TODO Step 10: app/synthesis/writer.py)
      9. Validate output              (TODO Step 10: app/synthesis/validator.py)
     10. Render HTML/text/chart       (TODO Step 11: app/render/)
     11. Deliver email if enabled     (TODO Step 12: app/delivery.py)
     12. Save artifacts and metadata
    """
    mode = RunMode(mode)
    started_at = datetime.now()
    warnings: list[str] = []
    failed_sources: list[str] = []

    # --- Step 1: Determine run window ---
    tz = ZoneInfo(settings.app.timezone)
    data_cutoff = _data_cutoff(settings, tz)
    llm_cfg = settings.sources.get("llm", {})
    llm_provider = llm_cfg.get("provider", "litellm")
    llm_model = llm_cfg.get("synthesis_model", "")

    # --- Step 2: Fetch market data ---
    if mode == RunMode.SAMPLE:
        _market_snapshots = _load_fixture_market()
    else:
        # TODO Step 5: AlphaVantageMarketProvider + DatabentoMarketProvider + cache fallback
        _market_snapshots = []
        warnings.append("Live market data not yet implemented — no snapshots loaded")

    # --- Step 3 & 4: Fetch calendar and enrich missing consensus ---
    if mode == RunMode.SAMPLE:
        _calendar_events = _load_fixture_calendar()
    else:
        # TODO Step 6: InvestingComCalendarProvider + consensus enrichment scout
        _calendar_events = []
        warnings.append("Live calendar data not yet implemented — no events loaded")

    # --- Step 5: Discover source evidence ---
    if mode == RunMode.SAMPLE:
        evidence_cards = _load_fixture_evidence()
    else:
        # TODO Step 8: discovery orchestrator (news, central_bank, research, podcast, x scouts)
        evidence_cards = []
        warnings.append("Live discovery not yet implemented — no evidence cards loaded")

    # --- Step 6: Deduplicate evidence ---
    # TODO Step 9: from app.synthesis.deduper import dedup_evidence
    deduped_evidence = evidence_cards

    # --- Step 7: Rank evidence and events ---
    # TODO Step 9: from app.synthesis.ranker import rank
    _ranked_evidence = deduped_evidence

    # --- Step 8: Write grounded brief ---
    # TODO Step 10: from app.synthesis.writer import write_brief
    brief_draft: BriefDraft | None = None

    # --- Step 9: Validate output ---
    # TODO Step 10: from app.synthesis.validator import validate

    # --- Step 10: Render HTML/text/chart ---
    # TODO Step 11: from app.render.email import render_brief
    #               from app.render.charts import build_chart
    output_paths: dict[str, str] = {}

    # --- Step 11: Deliver email if enabled ---
    # Sample and dry-run never send real email.
    if mode == RunMode.LIVE and settings.creds.enable_email_delivery:
        # TODO Step 12: from app.delivery import send_email
        delivery_status = DeliveryStatus.SKIPPED
    else:
        delivery_status = DeliveryStatus.DISABLED

    # --- Step 12: Record run metadata ---
    run_metadata = RunMetadata(
        run_id=str(uuid.uuid4()),
        run_started_at=started_at,
        data_cutoff_at=data_cutoff,
        timezone=settings.app.timezone,
        llm_provider=llm_provider,
        llm_model=llm_model,
        warnings=warnings,
        failed_sources=failed_sources,
        delivery_status=delivery_status,
        output_paths=output_paths,
    )

    return PipelineResult(success=True, run_metadata=run_metadata, brief_draft=brief_draft)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _data_cutoff(settings: "Settings", tz: ZoneInfo) -> datetime:
    """Compute the data cutoff datetime for this run in the configured timezone."""
    hour, minute = (int(p) for p in settings.app.data_cutoff_hkt.split(":"))
    return datetime.now(tz).replace(hour=hour, minute=minute, second=0, microsecond=0)


def _load_fixture_market() -> list[MarketSnapshot]:
    """Load fixture market snapshots for sample mode."""
    raw = json.loads((FIXTURE_DIR / "market_sample.json").read_text(encoding="utf-8"))
    return [MarketSnapshot.model_validate(s) for s in raw["snapshots"]]


def _load_fixture_calendar() -> list[CalendarEvent]:
    """Load fixture calendar events for sample mode."""
    return load_fixture_calendar()


def _load_fixture_evidence() -> list[EvidenceCard]:
    """Load fixture evidence cards for sample mode."""
    raw = json.loads((FIXTURE_DIR / "evidence_sample.json").read_text(encoding="utf-8"))
    return [EvidenceCard.model_validate(c) for c in raw["cards"]]
