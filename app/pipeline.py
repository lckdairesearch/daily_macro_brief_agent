"""Pipeline orchestrator. Owns the run sequence; no provider-specific logic here."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

_log = logging.getLogger(__name__)

from app.data.calendar import load_fixture_calendar
from app.data.market import (
    FixtureMarketProvider,
    fetch_live_market_with_cache,
    load_vol_params,
)
from app.discovery.orchestrator import build_scouts, run_discovery
from app.discovery.scouts.base import DiscoveryContext
from app.models import (
    BriefDraft,
    CalendarEvent,
    DeliveryStatus,
    FreshnessStatus,
    LLMUsage,
    PipelineResult,
    RunMetadata,
    RunMode,
)

if TYPE_CHECKING:
    from app.settings import Settings


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
    llm_usages: list[LLMUsage] = []
    run_id = str(uuid.uuid4())

    # --- Step 1: Determine run window ---
    tz = ZoneInfo(settings.app.timezone)
    data_cutoff = _data_cutoff(settings, tz)
    llm_cfg = settings.sources.get("llm", {})
    llm_provider = llm_cfg.get("provider", "litellm")
    llm_model = llm_cfg.get("synthesis_model", "")

    # --- Step 2: Fetch market data ---
    if mode == RunMode.SAMPLE:
        _market_snapshots = FixtureMarketProvider().fetch_watchlist([], data_cutoff)
    else:
        vol_params = load_vol_params(settings.config_dir)
        _market_snapshots = fetch_live_market_with_cache(settings, vol_params)
        # Propagate stale-cache warnings into run metadata (deduplicated)
        for snap in _market_snapshots:
            if snap.freshness_status == FreshnessStatus.STALE_CACHE and snap.warning:
                if snap.warning not in warnings:
                    warnings.append(snap.warning)

    # --- Step 3 & 4: Fetch calendar and enrich missing consensus ---
    if mode == RunMode.SAMPLE:
        _calendar_events = _load_fixture_calendar()
    else:
        # TODO Step 6: InvestingComCalendarProvider + consensus enrichment scout
        _calendar_events = []
        warnings.append("Live calendar data not yet implemented — no events loaded")

    # --- Step 5: Discover source evidence ---
    scouts = build_scouts(settings, mode)
    discovery_context = DiscoveryContext(
        market_snapshots=_market_snapshots,
        calendar_events=_calendar_events,
        themes=settings.themes.get("themes", []),
        portfolio=settings.portfolio,
        lookback_hours=24,
        data_cutoff=data_cutoff,
        mode=mode,
    )
    evidence_cards = run_discovery(scouts, discovery_context, failed_sources)

    # --- Step 6: Deduplicate evidence ---
    from app.synthesis.deduper import deduplicate
    deduped_evidence = deduplicate(evidence_cards)

    # --- Step 7: Rank evidence and events ---
    from app.synthesis.ranker import rank
    ranked_context = rank(
        market_snapshots=_market_snapshots,
        calendar_events=_calendar_events,
        evidence_cards=deduped_evidence,
        themes=settings.themes.get("themes", []),
        portfolio=settings.portfolio,
    )

    # --- Step 8: Write grounded brief ---
    from app.llm.provider import LLMResponseError
    from app.synthesis.writer import write_brief
    try:
        brief_draft, llm_usage = write_brief(ranked_context, settings, data_cutoff, mode)
        llm_usages.append(llm_usage)
        warnings.extend(brief_draft.warnings)
    except LLMResponseError as exc:
        run_metadata = RunMetadata(
            run_id=run_id,
            run_started_at=started_at,
            data_cutoff_at=data_cutoff,
            timezone=settings.app.timezone,
            llm_provider=llm_provider,
            llm_model=llm_model,
            warnings=warnings,
            failed_sources=failed_sources,
            delivery_status=DeliveryStatus.DISABLED,
            output_paths={},
        )
        return PipelineResult(
            success=False,
            run_metadata=run_metadata,
            error_message=f"Brief writer failed: {exc}",
        )

    # --- Step 9: Validate output ---
    from app.synthesis.validator import validate_brief
    validation = validate_brief(brief_draft)
    if not validation.is_valid:
        run_metadata = RunMetadata(
            run_id=run_id,
            run_started_at=started_at,
            data_cutoff_at=data_cutoff,
            timezone=settings.app.timezone,
            llm_provider=llm_provider,
            llm_model=llm_model,
            token_usage=llm_usages,
            warnings=warnings,
            failed_sources=failed_sources,
            delivery_status=DeliveryStatus.DISABLED,
            output_paths={},
        )
        return PipelineResult(
            success=False,
            run_metadata=run_metadata,
            brief_draft=brief_draft,
            error_message="Validation critical failures: " + "; ".join(validation.critical_failures),
        )
    warnings.extend(validation.warnings)

    # --- Step 10: Build chart ---
    from app.render.charts import build_chart
    output_paths: dict[str, str] = {}
    chart_image_url: str | None = None
    try:
        chart_spec = build_chart(
            draft=brief_draft,
            settings=settings,
            output_path=f"{settings.app.output_dir}/sample_chart.png",
            sample_mode=(mode == RunMode.SAMPLE),
            as_of=data_cutoff,
        )
        if chart_spec.file_path:
            output_paths["chart"] = chart_spec.file_path
            brief_draft = brief_draft.model_copy(update={"chart": chart_spec})

            # Push chart to GitHub for inline email rendering
            from pathlib import Path as _Path
            from app.render.github_push import push_chart_to_github
            chart_image_url = push_chart_to_github(
                chart_path=_Path(chart_spec.file_path),
                repo=settings.creds.github_repo or "lckdairesearch/daily_macro_brief_agent",
                branch=settings.creds.github_branch or "master",
                token=settings.creds.github_token,
            )
            if not chart_image_url:
                warnings.append("Chart GitHub push failed — chart will appear as email attachment")
    except Exception as _chart_exc:
        _log.warning("Chart generation failed: %s", _chart_exc)
        warnings.append(f"Chart generation failed: {_chart_exc}")

    # --- Step 11: Render HTML/text ---
    from app.data.market import load_vol_params
    from app.render.email import render_brief
    render_paths: dict[str, str] = {}

    # Back-fill run_metadata so the render has full context
    if brief_draft is not None:
        # Temporary metadata for render (will be updated after delivery)
        _tmp_meta = RunMetadata(
            run_id=run_id,
            run_started_at=started_at,
            data_cutoff_at=data_cutoff,
            timezone=settings.app.timezone,
            llm_provider=llm_provider,
            llm_model=llm_model,
            token_usage=llm_usages,
            warnings=warnings,
            failed_sources=failed_sources,
            delivery_status=DeliveryStatus.DISABLED,
            output_paths=output_paths,
        )
        brief_draft.run_metadata = _tmp_meta.model_dump(mode="json")
        try:
            render_paths = render_brief(
                draft=brief_draft,
                settings=settings,
                vol_params=load_vol_params(settings.config_dir),
                chart_image_url=chart_image_url,
            )
            output_paths.update(render_paths)
        except Exception as render_exc:
            _log.warning("Brief rendering failed: %s", render_exc)
            warnings.append(f"Brief rendering failed: {render_exc}")

    # --- Step 12: Deliver email ---
    # Sample: always delivers to configured recipient (subject prefixed [SAMPLE])
    # Live:   delivers only when ENABLE_EMAIL_DELIVERY=true
    # Dry-run: no delivery
    from app.delivery import NoopDeliveryProvider, get_provider
    delivery_status = DeliveryStatus.DISABLED
    provider = get_provider(mode, settings)
    if not isinstance(provider, NoopDeliveryProvider):
        from pathlib import Path as _Path
        inline_images = None
        if not chart_image_url and brief_draft and brief_draft.chart and brief_draft.chart.file_path:
            _cpath = _Path(brief_draft.chart.file_path)
            if _cpath.exists():
                inline_images = [
                    {"cid": "chart@brief", "name": "chart.png", "data": _cpath.read_bytes()}
                ]
        html_body = _Path(render_paths["html"]).read_text(encoding="utf-8") if render_paths.get("html") else ""
        text_body = _Path(render_paths["text"]).read_text(encoding="utf-8") if render_paths.get("text") else ""
        subject = "[SAMPLE] Morning Macro Brief" if mode == RunMode.SAMPLE else "Morning Macro Brief"
        delivery_result = provider.send(
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            inline_images=inline_images,
        )
        delivery_status = DeliveryStatus.SUCCESS if delivery_result.success else DeliveryStatus.FAILED
        if not delivery_result.success:
            warnings.append(f"Email delivery failed: {delivery_result.error_message}")

    # --- Step 13: Record run metadata ---
    run_metadata = RunMetadata(
        run_id=run_id,
        run_started_at=started_at,
        data_cutoff_at=data_cutoff,
        timezone=settings.app.timezone,
        llm_provider=llm_provider,
        llm_model=llm_model,
        token_usage=llm_usages,
        estimated_cost_usd=sum(
            u.estimated_cost_usd for u in llm_usages if u.estimated_cost_usd
        ) or None,
        warnings=warnings,
        failed_sources=failed_sources,
        delivery_status=delivery_status,
        output_paths=output_paths,
    )
    if brief_draft is not None:
        brief_draft.run_metadata = run_metadata.model_dump(mode="json")

    return PipelineResult(success=True, run_metadata=run_metadata, brief_draft=brief_draft)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _data_cutoff(settings: "Settings", tz: ZoneInfo) -> datetime:
    """Compute the data cutoff datetime for this run in the configured timezone."""
    hour, minute = (int(p) for p in settings.app.data_cutoff_hkt.split(":"))
    return datetime.now(tz).replace(hour=hour, minute=minute, second=0, microsecond=0)


def _load_fixture_calendar() -> list[CalendarEvent]:
    return load_fixture_calendar()
