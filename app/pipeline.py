"""Pipeline orchestrator. Owns the run sequence; no provider-specific logic here."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Callable
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
    EvidenceCard,
    LLMUsage,
    PipelineResult,
    RunMetadata,
    RunMode,
)
from app.synthesis.ranker import RankedBriefContext

if TYPE_CHECKING:
    from app.settings import Settings


def run_pipeline(
    mode: RunMode | str,
    settings: "Settings",
    data_cutoff: datetime | None = None,
    progress: Callable[[str], None] | None = None,
) -> PipelineResult:
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
    tz = ZoneInfo(settings.app.timezone)
    started_at = datetime.now(tz)
    warnings: list[str] = []
    failed_sources: list[str] = []
    llm_usages: list[LLMUsage] = []
    timings: list[dict] = []
    output_paths: dict[str, str] = {}

    # --- Step 1: Determine run window ---
    _progress(progress, "Determine run window")
    _step_started = perf_counter()
    data_cutoff = _data_cutoff(settings, tz, data_cutoff)
    run_id = _run_id(mode, started_at)
    run_output_dir = _run_output_dir(settings, data_cutoff, run_id)
    llm_cfg = settings.sources.get("llm", {})
    llm_provider = llm_cfg.get("provider", "litellm")
    llm_model = llm_cfg.get("synthesis_model", "")
    _record_timing(timings, "Determine run window", _step_started)

    # --- Step 2: Fetch market data ---
    _progress(progress, "Fetch market data")
    _step_started = perf_counter()
    if mode == RunMode.SAMPLE:
        _market_snapshots = FixtureMarketProvider().fetch_watchlist([], data_cutoff)
    else:
        vol_params = load_vol_params(settings.config_dir)
        _market_snapshots = fetch_live_market_with_cache(settings, vol_params, data_cutoff)
        # Propagate stale-cache warnings into run metadata (deduplicated)
        for snap in _market_snapshots:
            if snap.freshness_status == FreshnessStatus.STALE_CACHE and snap.warning:
                if snap.warning not in warnings:
                    warnings.append(snap.warning)
    _save_json_artifact(
        run_output_dir / "market_snapshots.json",
        [snap.model_dump(mode="json") for snap in _market_snapshots],
        output_paths,
        "market_snapshots",
    )
    _record_timing(timings, "Fetch market data", _step_started)

    # --- Step 3 & 4: Fetch calendar and enrich missing consensus ---
    _progress(progress, "Fetch calendar")
    _step_started = perf_counter()
    if mode == RunMode.SAMPLE:
        _calendar_events = _load_fixture_calendar()
    else:
        from app.data.calendar import CalendarIngestionError, InvestingCalendarProvider
        _cal_cfg = settings.sources.get("calendar", {})
        try:
            _calendar_events = InvestingCalendarProvider(
                config=_cal_cfg,
                cache_dir=Path(settings.app.cache_dir) / "calendar",
            ).fetch_for_date(data_cutoff)
        except CalendarIngestionError as exc:
            _calendar_events = []
            warnings.append(f"Calendar fetch failed — no events loaded: {exc}")
    _save_json_artifact(
        run_output_dir / "calendar_events.json",
        [event.model_dump(mode="json") for event in _calendar_events],
        output_paths,
        "calendar_events",
    )
    _record_timing(timings, "Fetch calendar", _step_started)

    # --- Step 5: Discover source evidence ---
    _progress(progress, "Discover evidence")
    _step_started = perf_counter()
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
    evidence_cards = run_discovery(scouts, discovery_context, failed_sources, timings)
    _record_timing(timings, "Discover evidence", _step_started)

    # --- Step 6: Deduplicate evidence ---
    _progress(progress, "Deduplicate evidence")
    _step_started = perf_counter()
    from app.synthesis.deduper import deduplicate
    deduped_evidence = deduplicate(evidence_cards)
    _save_evidence_cards(settings, data_cutoff, run_id, deduped_evidence, output_paths)
    _record_timing(timings, "Deduplicate evidence", _step_started)

    # --- Step 7: Rank evidence and events ---
    _progress(progress, "Rank context")
    _step_started = perf_counter()
    from app.synthesis.ranker import rank
    ranked_context = rank(
        market_snapshots=_market_snapshots,
        calendar_events=_calendar_events,
        evidence_cards=deduped_evidence,
        themes=settings.themes.get("themes", []),
        portfolio=settings.portfolio,
    )
    _save_ranked_context_artifact(
        run_output_dir,
        ranked_context,
        failed_sources,
        output_paths,
    )
    _record_timing(timings, "Rank context", _step_started)

    # --- Step 8: Write grounded brief ---
    _progress(progress, "Write brief")
    _step_started = perf_counter()
    from app.llm.provider import LLMResponseError
    from app.synthesis.writer import write_brief
    try:
        brief_draft, llm_usage = write_brief(ranked_context, settings, data_cutoff, mode)
        llm_usages.append(llm_usage)
        _save_json_artifact(
            run_output_dir / "brief_draft.json",
            brief_draft.model_dump(mode="json"),
            output_paths,
            "brief_draft",
        )
    except LLMResponseError as exc:
        _record_timing(timings, "Write brief", _step_started, status="failed")
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
            output_paths=output_paths,
            timings=timings,
        )
        _save_run_metadata(run_output_dir, run_metadata, output_paths)
        return PipelineResult(
            success=False,
            run_metadata=run_metadata,
            error_message=f"Brief writer failed: {exc}",
        )
    _record_timing(timings, "Write brief", _step_started)

    # --- Step 9: Validate output ---
    _progress(progress, "Validate brief")
    _step_started = perf_counter()
    from app.synthesis.validator import validate_brief
    validation = validate_brief(brief_draft)
    if not validation.is_valid:
        _record_timing(timings, "Validate brief", _step_started, status="failed")
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
            output_paths=output_paths,
            timings=timings,
        )
        _save_run_metadata(run_output_dir, run_metadata, output_paths)
        return PipelineResult(
            success=False,
            run_metadata=run_metadata,
            brief_draft=brief_draft,
            error_message="Validation critical failures: " + "; ".join(validation.critical_failures),
        )
    warnings.extend(validation.warnings)
    _record_timing(timings, "Validate brief", _step_started)

    # --- Step 10: Build chart ---
    _progress(progress, "Build chart")
    _step_started = perf_counter()
    from app.render.charts import build_chart
    chart_image_url: str | None = None
    chart_status = "success"
    try:
        chart_spec = build_chart(
            draft=brief_draft,
            settings=settings,
            output_path=_chart_output_path(mode, settings, data_cutoff, run_id=run_id),
            sample_mode=(mode == RunMode.SAMPLE),
            as_of=data_cutoff,
        )
        if chart_spec.file_path:
            output_paths["chart"] = chart_spec.file_path
            brief_draft = brief_draft.model_copy(update={"chart": chart_spec})

            # Upload chart to public hosting for inline email rendering.
            from pathlib import Path as _Path
            from app.render.chart_uploader import publish_chart
            chart_image_url = publish_chart(_Path(chart_spec.file_path), settings)
            if not chart_image_url:
                warnings.append("Chart upload failed — chart will appear as email attachment")
    except Exception as _chart_exc:
        _log.warning("Chart generation failed: %s", _chart_exc)
        warnings.append(f"Chart generation failed: {_chart_exc}")
        chart_status = "failed"
    _record_timing(timings, "Build chart", _step_started, status=chart_status)

    # --- Step 11: Render HTML/text ---
    _progress(progress, "Render brief")
    _step_started = perf_counter()
    from app.render.email import render_brief
    render_paths: dict[str, str] = {}
    render_status = "success"

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
                output_dir=run_output_dir,
                html_filename="brief.html",
                text_filename="brief.txt",
                vol_params=load_vol_params(settings.config_dir),
                chart_image_url=chart_image_url,
            )
            output_paths.update(render_paths)
            _save_rendered_html_artifact(run_output_dir, output_paths)
        except Exception as render_exc:
            _log.warning("Brief rendering failed: %s", render_exc)
            warnings.append(f"Brief rendering failed: {render_exc}")
            render_status = "failed"
    _record_timing(timings, "Render brief", _step_started, status=render_status)

    # --- Step 12: Deliver email ---
    _progress(progress, "Deliver email")
    _step_started = perf_counter()
    delivery_timing_status = "skipped"
    # Sample/dry-run: deliver test output to the maintainer recipient.
    # Live: deliver to production recipients only when ENABLE_EMAIL_DELIVERY=true.
    from app.delivery import NoopDeliveryProvider, get_provider
    delivery_status = DeliveryStatus.DISABLED
    provider = get_provider(mode, settings)
    if not isinstance(provider, NoopDeliveryProvider):
        delivery_timing_status = "success"
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
        if mode == RunMode.SAMPLE:
            subject = "[SAMPLE] Morning Macro Brief"
        elif mode == RunMode.DRY_RUN:
            subject = "[DRY RUN] Morning Macro Brief"
        else:
            subject = "Morning Macro Brief"
        delivery_result = provider.send(
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            inline_images=inline_images,
        )
        delivery_status = DeliveryStatus.SUCCESS if delivery_result.success else DeliveryStatus.FAILED
        if not delivery_result.success:
            warnings.append(f"Email delivery failed: {delivery_result.error_message}")
            delivery_timing_status = "failed"
    _record_timing(timings, "Deliver email", _step_started, status=delivery_timing_status)

    # --- Step 13: Record run metadata ---
    _progress(progress, "Record metadata")
    _step_started = perf_counter()
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
        timings=timings,
    )
    _save_run_metadata(run_output_dir, run_metadata, output_paths)
    if brief_draft is not None:
        brief_draft.run_metadata = run_metadata.model_dump(mode="json")
    _record_timing(timings, "Record metadata", _step_started)
    run_metadata.timings = timings
    if brief_draft is not None:
        brief_draft.run_metadata = run_metadata.model_dump(mode="json")

    return PipelineResult(success=True, run_metadata=run_metadata, brief_draft=brief_draft)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _data_cutoff(
    settings: "Settings",
    tz: ZoneInfo,
    override: datetime | None = None,
) -> datetime:
    """Compute the data cutoff datetime for this run in the configured timezone."""
    if override is not None:
        if override.tzinfo is None:
            return override.replace(tzinfo=tz)
        return override.astimezone(tz)

    hour, minute = (int(p) for p in settings.app.data_cutoff_hkt.split(":"))
    return datetime.now(tz).replace(hour=hour, minute=minute, second=0, microsecond=0)


def _chart_output_path(
    mode: RunMode,
    settings: "Settings",
    data_cutoff: datetime,
    run_id: str | None = None,
) -> str:
    """Return deterministic sample chart path or timestamped run-scoped chart path."""
    if run_id is not None:
        output_dir = _run_output_dir(settings, data_cutoff, run_id)
    else:
        output_dir = Path(settings.app.output_dir)
    if mode == RunMode.SAMPLE:
        return str(output_dir / "sample_chart.png")

    timestamp = data_cutoff.strftime("%Y%m%d_%H%M")
    return str(output_dir / f"chart_{timestamp}.png")


def _run_output_dir(settings: "Settings", data_cutoff: datetime, run_id: str) -> Path:
    """Return the per-run artifact directory."""
    return Path(settings.app.output_dir) / "runs" / data_cutoff.strftime("%Y-%m-%d") / run_id


def _run_id(mode: RunMode, started_at: datetime) -> str:
    """Return a timestamp-based run id, with sample runs explicitly labeled."""
    run_id = started_at.strftime("%Y%m%d_%H%M%S")
    if mode == RunMode.SAMPLE:
        return f"{run_id}_sample"
    return run_id


def _evidence_output_path(settings: "Settings", data_cutoff: datetime, run_id: str) -> Path:
    """Return the per-run path for persisted evidence cards."""
    return _run_output_dir(settings, data_cutoff, run_id) / "evidence_cards.json"


def _save_evidence_cards(
    settings: "Settings",
    data_cutoff: datetime,
    run_id: str,
    evidence_cards: list[EvidenceCard],
    output_paths: dict[str, str],
) -> None:
    """Persist deduplicated evidence cards for later review."""
    evidence_path = _evidence_output_path(settings, data_cutoff, run_id)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(
            [card.model_dump(mode="json") for card in evidence_cards],
            indent=2,
            sort_keys=True,
            default=str,
        ),
        encoding="utf-8",
    )
    output_paths["evidence_cards"] = str(evidence_path)


def _save_json_artifact(path: Path, payload: object, output_paths: dict[str, str], key: str) -> None:
    """Persist a JSON artifact and record its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    output_paths[key] = str(path)


def _save_ranked_context_artifact(
    run_output_dir: Path,
    ranked_context: RankedBriefContext,
    failed_sources: list[str],
    output_paths: dict[str, str],
) -> None:
    """Persist the ranked context used by synthesis."""
    payload = {
        "top_market_moves": [s.instrument_id for s in ranked_context.top_market_moves[:10]],
        "top_calendar_events": [e.event_name for e in ranked_context.top_calendar_events[:10]],
        "ranked_evidence_cards": [c.model_dump(mode="json") for c in ranked_context.ranked_evidence_cards],
        "proposed_three_things": [c.id for c in ranked_context.proposed_three_things],
        "proposed_theme_radar": [c.id for c in ranked_context.proposed_theme_radar],
        "proposed_contrarian_corner_seed": (
            ranked_context.proposed_contrarian_corner_seed.id
            if ranked_context.proposed_contrarian_corner_seed
            else None
        ),
        "scores": ranked_context.scores,
        "failed_sources": failed_sources,
    }
    _save_json_artifact(run_output_dir / "ranked_context.json", payload, output_paths, "ranked_context")


def _save_rendered_html_artifact(run_output_dir: Path, output_paths: dict[str, str]) -> None:
    """Persist a second HTML artifact for direct browser inspection."""
    html_path = Path(output_paths["html"])
    rendered_html_path = run_output_dir / "brief_rendered.html"
    rendered_html_path.write_text(html_path.read_text(encoding="utf-8"), encoding="utf-8")
    output_paths["brief_rendered_html"] = str(rendered_html_path)


def _save_run_metadata(
    run_output_dir: Path,
    run_metadata: RunMetadata,
    output_paths: dict[str, str],
) -> None:
    """Persist the final run metadata alongside the other run artifacts."""
    run_output_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = run_output_dir / "run_metadata.json"
    run_metadata.output_paths["run_metadata"] = str(metadata_path)
    output_paths["run_metadata"] = str(metadata_path)
    metadata_path.write_text(
        json.dumps(run_metadata.model_dump(mode="json"), indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def _progress(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _record_timing(
    timings: list[dict],
    component: str,
    started_at: float,
    status: str = "success",
) -> None:
    timings.append(
        {
            "component": component,
            "status": status,
            "seconds": round(perf_counter() - started_at, 3),
        }
    )


def _load_fixture_calendar() -> list[CalendarEvent]:
    return load_fixture_calendar()
