"""Chart generation. Produces a PNG from a selected ChartPlan."""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from statistics import mean
from typing import TYPE_CHECKING

from app.data.market import fetch_chart_series
from app.models import (
    AssetClass,
    BriefDraft,
    ChartBuildAttempt,
    ChartBuildInfo,
    ChartPlan,
    ChartSpec,
    ChartWindow,
)
from app.render.chart_windowing import (
    build_window_ticks,
    clip_rows_to_window,
    first_shared_observation_date,
    resolve_shared_window,
)

from .chart_codegen import execute_chart_code, generate_chart_code

if TYPE_CHECKING:
    from app.settings import Settings

_log = logging.getLogger(__name__)

_UNIT_MAP: dict[AssetClass, str] = {
    AssetClass.EQUITY: "price",
    AssetClass.FX: "price",
    AssetClass.COMMODITY: "price",
    AssetClass.CRYPTO: "price",
    AssetClass.RATES: "yield",
    AssetClass.CREDIT: "spread",
    AssetClass.VOLATILITY: "index",
}

_WINDOW_DAYS: dict[ChartWindow, int] = {
    ChartWindow.ONE_WEEK: 7,
    ChartWindow.ONE_MONTH: 30,
}

_LINE_COLORS = ["#F97316", "#B45309"]
_RAW_SINGLE_AXIS = "pair_line_single_axis_raw"
_RAW_DUAL_AXIS = "pair_line_dual_axis_raw"
_REBASED = "pair_line_rebased"


def build_chart(
    draft: BriefDraft,
    chart_plan: ChartPlan,
    settings: "Settings",
    output_path: str = "outputs/samples/sample_chart.png",
    sample_mode: bool = True,
    as_of: datetime | None = None,
) -> tuple[ChartSpec, ChartBuildInfo]:
    """Generate a chart PNG and return a ChartSpec with file_path set."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    dash_by_id = {s.instrument_id: s for s in draft.overnight_dashboard}
    requested_instrument_ids = list(chart_plan.instrument_ids)
    instrument_ids = [iid for iid in requested_instrument_ids if iid in dash_by_id]

    if chart_plan.window == ChartWindow.ONE_DAY:
        if not instrument_ids:
            instrument_ids = [s.instrument_id for s in draft.overnight_dashboard[:6]]
        spec = _hardcoded_fallback(draft, output_path, chart_plan, instrument_ids)
        return spec, ChartBuildInfo(
            final_status="hardcoded_fallback",
            fallback_reason="one_day_window",
            output_path=output_path,
            requested_window=chart_plan.window,
            requested_chart_type=chart_plan.chart_type,
            requested_instrument_ids=requested_instrument_ids,
            used_instrument_ids=list(spec.instrument_ids),
            code_generated=False,
        )

    if len(instrument_ids) < 2:
        instrument_ids = [s.instrument_id for s in draft.overnight_dashboard[:2]]

    effective_as_of = as_of or datetime.now()
    series_data = fetch_chart_series(
        instruments=instrument_ids,
        lookback_days=45,
        as_of=effective_as_of,
        settings=settings,
        sample_mode=sample_mode,
    )
    if not series_data:
        _log.warning("build_chart: no series data available — using hardcoded fallback")
        spec = _hardcoded_fallback(draft, output_path, chart_plan, instrument_ids)
        return spec, ChartBuildInfo(
            final_status="hardcoded_fallback",
            fallback_reason="no_series_data",
            output_path=output_path,
            requested_window=chart_plan.window,
            requested_chart_type=chart_plan.chart_type,
            requested_instrument_ids=requested_instrument_ids,
            used_instrument_ids=list(spec.instrument_ids),
            code_generated=False,
        )

    display_names = {iid: dash_by_id[iid].display_name for iid in series_data if iid in dash_by_id}
    asset_classes = {iid: dash_by_id[iid].asset_class.value for iid in series_data if iid in dash_by_id}
    units = {
        iid: _UNIT_MAP.get(dash_by_id[iid].asset_class, "price") if iid in dash_by_id else "price"
        for iid in series_data
    }
    resolved_render_family = _resolve_render_family(chart_plan, units, instrument_ids[:2])
    caption = _effective_caption(
        chart_plan.caption or (draft.chart.caption if draft.chart else "Macro cross-asset setup into the session."),
        resolved_render_family,
    )

    if _supports_deterministic_pair_line_chart(chart_plan, instrument_ids):
        try:
            used_rows = _render_deterministic_pair_line_chart(
                series_data=series_data,
                instrument_ids=instrument_ids[:2],
                display_names=display_names,
                asset_classes=asset_classes,
                units=units,
                chart_plan=chart_plan,
                output_path=output_path,
            )
            spec = ChartSpec(
                title=chart_plan.title,
                caption=caption,
                chart_type=chart_plan.chart_type,
                data_source="fixture" if sample_mode else "live",
                file_path=output_path,
                code_generated=False,
                window=chart_plan.window,
                instrument_ids=instrument_ids[:2],
                selection_reason=chart_plan.selection_reason,
                selection_method=chart_plan.selection_method,
                render_family=resolved_render_family,
                linked_three_things_index=chart_plan.linked_three_things_index,
            )
            return spec, ChartBuildInfo(
                final_status="deterministic_render",
                output_path=output_path,
                requested_window=chart_plan.window,
                requested_chart_type=chart_plan.chart_type,
                requested_instrument_ids=requested_instrument_ids,
                used_instrument_ids=list(spec.instrument_ids),
                series_instrument_ids=list(used_rows.keys()),
                code_generated=False,
            )
        except Exception as exc:
            _log.warning("build_chart: deterministic render failed: %s", exc)

    attempts: list[ChartBuildAttempt] = []
    error_context = ""
    for attempt in range(2):
        try:
            code = generate_chart_code(
                chart_plan=chart_plan,
                series_data=series_data,
                display_names=display_names,
                asset_classes=asset_classes,
                units=units,
                events=[],
                output_path=output_path,
                settings=settings,
                error_context=error_context,
            )
            execute_chart_code(code, output_path)
            attempts.append(ChartBuildAttempt(attempt_number=attempt + 1, status="success"))
            spec = ChartSpec(
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
                render_family=chart_plan.render_family,
                linked_three_things_index=chart_plan.linked_three_things_index,
            )
            return spec, ChartBuildInfo(
                final_status="generated_code",
                output_path=output_path,
                requested_window=chart_plan.window,
                requested_chart_type=chart_plan.chart_type,
                requested_instrument_ids=requested_instrument_ids,
                used_instrument_ids=list(spec.instrument_ids),
                series_instrument_ids=list(series_data.keys()),
                code_generated=True,
                attempts=attempts,
            )
        except Exception as exc:
            error_context = str(exc)
            attempts.append(
                ChartBuildAttempt(
                    attempt_number=attempt + 1,
                    status="failed",
                    error_message=str(exc),
                )
            )
            _log.warning("build_chart: attempt %d failed: %s", attempt + 1, exc)

    _log.warning("build_chart: both attempts failed — using hardcoded fallback")
    spec = _hardcoded_fallback(draft, output_path, chart_plan, instrument_ids)
    return spec, ChartBuildInfo(
        final_status="hardcoded_fallback",
        fallback_reason="codegen_failed",
        output_path=output_path,
        requested_window=chart_plan.window,
        requested_chart_type=chart_plan.chart_type,
        requested_instrument_ids=requested_instrument_ids,
        used_instrument_ids=list(spec.instrument_ids),
        series_instrument_ids=list(series_data.keys()),
        code_generated=False,
        attempts=attempts,
    )


def _supports_deterministic_pair_line_chart(chart_plan: ChartPlan, instrument_ids: list[str]) -> bool:
    return (
        chart_plan.chart_type == "line"
        and chart_plan.window in _WINDOW_DAYS
        and len(instrument_ids) == 2
    )


def _prepare_plot_rows(
    series_data: dict[str, list[dict]],
    instrument_ids: list[str],
    window: ChartWindow,
) -> tuple[dict[str, list[dict]], date, date]:
    window_days = _WINDOW_DAYS[window]
    source_rows = {instrument_id: series_data.get(instrument_id, []) for instrument_id in instrument_ids}
    window_start, window_end = resolve_shared_window(source_rows, window_days)
    plot_rows: dict[str, list[dict]] = {}
    for instrument_id in instrument_ids:
        rows = series_data.get(instrument_id, [])
        clipped = clip_rows_to_window(rows, window_start, window_end)
        if clipped:
            plot_rows[instrument_id] = clipped
    return plot_rows, window_start, window_end


def _resolve_render_family(
    chart_plan: ChartPlan,
    units: dict[str, str],
    instrument_ids: list[str],
) -> str:
    if chart_plan.render_family:
        return chart_plan.render_family
    if len(instrument_ids) < 2:
        return _RAW_SINGLE_AXIS
    if units.get(instrument_ids[0]) != units.get(instrument_ids[1]):
        return _RAW_DUAL_AXIS
    return _RAW_SINGLE_AXIS


def _effective_caption(caption: str, render_family: str) -> str:
    if render_family != _REBASED:
        return caption
    disclosure = "Both series are indexed to 100 on the first shared observation date."
    if disclosure in caption:
        return caption
    return f"{caption.rstrip('.')} {disclosure}"


def _axis_unit_label(asset_class: AssetClass, unit_kind: str) -> str:
    if asset_class == AssetClass.RATES:
        return "%"
    if asset_class == AssetClass.CREDIT:
        return "bps"
    if asset_class in {AssetClass.EQUITY, AssetClass.COMMODITY, AssetClass.CRYPTO}:
        return "$"
    if asset_class == AssetClass.FX:
        return "rate"
    if asset_class == AssetClass.VOLATILITY:
        return "index"
    return unit_kind


def _axis_policy_for_pair(
    plot_rows: dict[str, list[dict]],
    units: dict[str, str],
    instrument_ids: list[str],
) -> dict[str, float | bool | str | None]:
    left_id, right_id = instrument_ids[:2]
    left_unit = units.get(left_id, "price")
    right_unit = units.get(right_id, "price")

    left_values = [float(row["close"]) for row in plot_rows.get(left_id, [])]
    right_values = [float(row["close"]) for row in plot_rows.get(right_id, [])]
    if not left_values or not right_values:
        return {
            "prefer_dual_axis": False,
            "range_ratio": None,
            "pooled_occupancy": None,
            "center_gap_ratio": None,
            "reason": "missing_values",
        }

    if left_unit != right_unit:
        return {
            "prefer_dual_axis": True,
            "range_ratio": None,
            "pooled_occupancy": None,
            "center_gap_ratio": None,
            "reason": "different_units",
        }

    left_span = max(left_values) - min(left_values)
    right_span = max(right_values) - min(right_values)
    pooled_span = max(left_values + right_values) - min(left_values + right_values)
    if pooled_span <= 0:
        return {
            "prefer_dual_axis": False,
            "range_ratio": 1.0,
            "pooled_occupancy": 1.0,
            "center_gap_ratio": 0.0,
            "reason": "flat_series",
        }

    smaller_span = min(left_span, right_span)
    larger_span = max(left_span, right_span)
    range_ratio = 1.0 if smaller_span <= 0 and larger_span <= 0 else (
        float("inf") if smaller_span <= 0 else larger_span / smaller_span
    )
    pooled_occupancy = smaller_span / pooled_span
    center_gap_ratio = abs(mean(left_values) - mean(right_values)) / pooled_span
    prefer_dual_axis = not (
        range_ratio <= 3.0
        and pooled_occupancy >= 0.25
        and center_gap_ratio <= 0.5
    )
    return {
        "prefer_dual_axis": prefer_dual_axis,
        "range_ratio": range_ratio,
        "pooled_occupancy": pooled_occupancy,
        "center_gap_ratio": center_gap_ratio,
        "reason": "same_units_thresholds",
    }


def _render_deterministic_pair_line_chart(
    *,
    series_data: dict[str, list[dict]],
    instrument_ids: list[str],
    display_names: dict[str, str],
    asset_classes: dict[str, str],
    units: dict[str, str],
    chart_plan: ChartPlan,
    output_path: str,
) -> dict[str, list[dict]]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    render_family = _resolve_render_family(chart_plan, units, instrument_ids[:2])
    plot_rows, window_start, window_end = _prepare_plot_rows(series_data, instrument_ids[:2], chart_plan.window)
    if len(plot_rows) != 2:
        raise RuntimeError("deterministic line renderer requires two populated series")

    axis_policy = _axis_policy_for_pair(plot_rows, units, instrument_ids[:2])
    left_id, right_id = instrument_ids[:2]
    left_name = display_names.get(left_id, left_id)
    right_name = display_names.get(right_id, right_id)
    fig, ax = plt.subplots(figsize=(9.8, 3.9), facecolor="none")
    fig.patch.set_alpha(0)
    ax.set_facecolor("none")
    axes = [ax]
    prefer_dual_axis = render_family == _RAW_DUAL_AXIS or (
        render_family == _RAW_SINGLE_AXIS and axis_policy["prefer_dual_axis"]
    )
    if prefer_dual_axis:
        right_ax = ax.twinx()
        right_ax.set_facecolor("none")
        axes.append(right_ax)
    else:
        right_ax = ax

    for axis in axes:
        axis.spines["top"].set_visible(False)
        axis.grid(axis="y", color="#CBD5E1", linewidth=0.8, alpha=0.35)

    if render_family == _REBASED:
        shared_start = first_shared_observation_date(plot_rows)
        if shared_start is None:
            raise RuntimeError("rebased renderer requires a shared observation date")
        rebased_rows = {
            instrument_id: [
                row
                for row in rows
                if date.fromisoformat(row["date"]) >= shared_start
            ]
            for instrument_id, rows in plot_rows.items()
        }
        base_values = {
            instrument_id: next(
                float(row["close"])
                for row in rebased_rows[instrument_id]
                if date.fromisoformat(row["date"]) == shared_start
            )
            for instrument_id in instrument_ids[:2]
        }
        left_x = [date.fromisoformat(row["date"]) for row in rebased_rows[left_id]]
        right_x = [date.fromisoformat(row["date"]) for row in rebased_rows[right_id]]
        left_y = [100.0 * float(row["close"]) / base_values[left_id] for row in rebased_rows[left_id]]
        right_y = [100.0 * float(row["close"]) / base_values[right_id] for row in rebased_rows[right_id]]
        ax.plot(left_x, left_y, color=_LINE_COLORS[0], linewidth=2.2, label=left_name)
        ax.plot(right_x, right_y, color=_LINE_COLORS[1], linewidth=2.2, label=right_name)
        ax.set_ylabel("Indexed Level (First Shared Date = 100)", fontsize=9)
    else:
        left_x = [date.fromisoformat(row["date"]) for row in plot_rows[left_id]]
        right_x = [date.fromisoformat(row["date"]) for row in plot_rows[right_id]]
        left_y = [float(row["close"]) for row in plot_rows[left_id]]
        right_y = [float(row["close"]) for row in plot_rows[right_id]]
        ax.plot(left_x, left_y, color=_LINE_COLORS[0], linewidth=2.2, label=left_name)
        right_ax.plot(right_x, right_y, color=_LINE_COLORS[1], linewidth=2.2, label=right_name)

    ax.set_title(chart_plan.title, fontsize=12, fontweight="bold", loc="left")
    ax.set_xlim(window_start, window_end)
    ticks = build_window_ticks(window_start, window_end, daily=chart_plan.window != ChartWindow.ONE_MONTH)
    ax.set_xticks(ticks)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    plt.setp(ax.get_xticklabels(), rotation=0, ha="center")
    ax.spines["right"].set_visible(False)

    left_unit = units.get(left_id, "price")
    right_unit = units.get(right_id, "price")
    left_axis_unit = _axis_unit_label(AssetClass(asset_classes.get(left_id, AssetClass.EQUITY.value)), left_unit)
    right_axis_unit = _axis_unit_label(AssetClass(asset_classes.get(right_id, AssetClass.EQUITY.value)), right_unit)
    ax.tick_params(axis="y", colors=_LINE_COLORS[0])
    if render_family != _REBASED:
        ax.set_ylabel(f"{left_name} ({left_axis_unit})", color=_LINE_COLORS[0], fontsize=9)
    if right_ax is not ax and render_family != _REBASED:
        right_ax.tick_params(axis="y", colors=_LINE_COLORS[1])
        right_ax.set_ylabel(f"{right_name} ({right_axis_unit})", color=_LINE_COLORS[1], fontsize=9)

    handles, labels = [], []
    for axis in axes:
        axis_handles, axis_labels = axis.get_legend_handles_labels()
        handles.extend(axis_handles)
        labels.extend(axis_labels)
    ax.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.18),
        ncol=2,
        frameon=True,
        facecolor="#FFFFFF",
        edgecolor="#E2E8F0",
    )
    plt.tight_layout(pad=1.2)
    plt.savefig(output_path, dpi=150, bbox_inches="tight", transparent=True)
    plt.close(fig)
    return plot_rows


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
        render_family=chart_plan.render_family if chart_plan else "daily_snapshot_bar",
        linked_three_things_index=chart_plan.linked_three_things_index if chart_plan else None,
    )
