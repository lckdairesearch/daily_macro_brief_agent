"""Chart generation. Produces a PNG from a selected ChartPlan."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from app.data.market import fetch_chart_series
from app.models import AssetClass, BriefDraft, ChartPlan, ChartSpec, ChartWindow

from .chart_codegen import (
    execute_chart_code,
    generate_chart_code,
)

if TYPE_CHECKING:
    from app.settings import Settings

_log = logging.getLogger(__name__)

_UNIT_MAP: dict[AssetClass, str] = {
    AssetClass.EQUITY: "price",
    AssetClass.FX: "price",
    AssetClass.COMMODITY: "price",
    AssetClass.CRYPTO: "price",
    AssetClass.RATES: "bps",
    AssetClass.CREDIT: "bps",
    AssetClass.VOLATILITY: "index",
}


def build_chart(
    draft: BriefDraft,
    chart_plan: ChartPlan,
    settings: "Settings",
    output_path: str = "outputs/sample_chart.png",
    sample_mode: bool = True,
    as_of: datetime | None = None,
) -> ChartSpec:
    """Generate a chart PNG and return a ChartSpec with file_path set.

    Tries LLM-generated code up to 2 times; falls back to a hardcoded bar chart.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    dash_by_id = {s.instrument_id: s for s in draft.overnight_dashboard}
    instrument_ids = [iid for iid in chart_plan.instrument_ids if iid in dash_by_id]
    if chart_plan.window == ChartWindow.ONE_DAY:
        if not instrument_ids:
            instrument_ids = [s.instrument_id for s in draft.overnight_dashboard[:6]]
        return _hardcoded_fallback(draft, output_path, chart_plan, instrument_ids)
    if len(instrument_ids) < 2:
        instrument_ids = [s.instrument_id for s in draft.overnight_dashboard[:2]]

    effective_as_of = as_of or datetime.now()
    series_data = fetch_chart_series(
        instruments=instrument_ids,
        lookback_days=30,
        as_of=effective_as_of,
        settings=settings,
        sample_mode=sample_mode,
    )

    if not series_data:
        _log.warning("build_chart: no series data available — using hardcoded fallback")
        return _hardcoded_fallback(draft, output_path, chart_plan, instrument_ids)

    display_names = {iid: dash_by_id[iid].display_name for iid in series_data if iid in dash_by_id}
    asset_classes = {iid: dash_by_id[iid].asset_class.value for iid in series_data if iid in dash_by_id}
    units = {
        iid: _UNIT_MAP.get(dash_by_id[iid].asset_class, "price") if iid in dash_by_id else "price"
        for iid in series_data
    }

    series_dates = {r["date"] for rows in series_data.values() for r in rows}
    events = _extract_events(draft, series_dates)
    caption = draft.chart.caption if draft.chart else "Market overview"

    error_context = ""
    for attempt in range(2):
        try:
            code = generate_chart_code(
                chart_plan=chart_plan,
                series_data=series_data,
                display_names=display_names,
                asset_classes=asset_classes,
                units=units,
                events=events,
                output_path=output_path,
                settings=settings,
                error_context=error_context,
            )
            execute_chart_code(code, output_path)
            return ChartSpec(
                title=chart_plan.title,
                caption=caption,
                chart_type=chart_plan.chart_type,
                data_source="fixture" if sample_mode else "live",
                file_path=output_path,
                code_generated=True,
                window=chart_plan.window,
                instrument_ids=instrument_ids,
                selection_reason=chart_plan.selection_reason,
                selection_method=chart_plan.selection_method,
                linked_three_things_index=chart_plan.linked_three_things_index,
            )
        except Exception as exc:
            error_context = str(exc)
            _log.warning("build_chart: attempt %d failed: %s", attempt + 1, exc)

    _log.warning("build_chart: both attempts failed — using hardcoded fallback")
    return _hardcoded_fallback(draft, output_path, chart_plan, instrument_ids)


def _extract_events(draft: BriefDraft, series_dates: set[str]) -> list[dict]:
    events = []
    for evt in draft.todays_calendar:
        date_str = evt.event_time_hkt.date().isoformat()
        if date_str in series_dates and evt.importance >= 2:
            events.append({
                "date": date_str,
                "label": evt.event_name[:20],
                "source": "calendar",
            })
    return events


def _hardcoded_fallback(
    draft: BriefDraft,
    output_path: str,
    chart_plan: ChartPlan | None = None,
    instrument_ids: list[str] | None = None,
) -> ChartSpec:
    """Horizontal bar chart of 1-day changes — always works without LLM."""
    import os
    os.environ.setdefault("MPLBACKEND", "Agg")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if instrument_ids:
        by_id = {snap.instrument_id: snap for snap in draft.overnight_dashboard}
        dashboard = [by_id[iid] for iid in instrument_ids if iid in by_id]
    else:
        dashboard = draft.overnight_dashboard[:10]
    labels = [s.display_name for s in dashboard]
    values = [s.one_day_change for s in dashboard]
    colors = ["#2563EB" if v >= 0 else "#DC2626" for v in values]

    fig, ax = plt.subplots(figsize=(10, 3.5), facecolor="#FFFFFF")
    ax.set_facecolor("#FAFAFA")
    ax.barh(labels, values, color=colors)
    ax.axvline(0, color="#94A3B8", linewidth=0.8)
    ax.set_xlabel("1-day change")
    ax.set_title("Overnight moves", fontsize=12, fontweight="bold", loc="left")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="x", color="#E5E7EB", linewidth=0.6, alpha=0.8)
    plt.tight_layout(pad=1.5)
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()

    caption = chart_plan.caption if chart_plan else (draft.chart.caption if draft.chart else "Overnight market moves")
    return ChartSpec(
        title=chart_plan.title if chart_plan else "Overnight moves",
        caption=caption,
        chart_type="bar",
        data_source="dashboard",
        file_path=output_path,
        code_generated=False,
        window=chart_plan.window if chart_plan else ChartWindow.ONE_DAY,
        instrument_ids=[snap.instrument_id for snap in dashboard],
        selection_reason=chart_plan.selection_reason if chart_plan else None,
        selection_method=chart_plan.selection_method if chart_plan else None,
        linked_three_things_index=chart_plan.linked_three_things_index if chart_plan else None,
    )
