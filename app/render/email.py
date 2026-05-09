"""HTML and plain-text email rendering via Jinja2 templates."""

from __future__ import annotations

from math import sqrt
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.models import BriefDraft, CalendarEvent, ChartSpec, MarketSnapshot

if TYPE_CHECKING:
    from app.settings import Settings

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
HTML_TEMPLATE = "template.html"

_FALLBACK_VOL: dict[str, dict[str, float | str]] = {
    "%": {"sd_1d": 1.0, "unit": "%"},
    "bps": {"sd_1d": 5.0, "unit": "bps"},
}
_FIVE_DAY_FLAT_Z = 0.25


def render_brief(
    draft: BriefDraft,
    settings: "Settings",
    output_dir: str | Path | None = None,
    vol_params: dict[str, dict[str, Any]] | None = None,
    chart_image_url: str | None = None,
) -> dict[str, str]:
    """Render HTML and plain-text artifacts and return their paths."""
    out_dir = Path(output_dir or settings.app.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    context = build_render_context(draft, settings, vol_params=vol_params, chart_image_url=chart_image_url)
    html = render_html(context)
    text = render_text(context)

    html_path = out_dir / "sample_brief.html"
    text_path = out_dir / "sample_brief.txt"
    html_path.write_text(html, encoding="utf-8")
    text_path.write_text(text, encoding="utf-8")

    return {"html": str(html_path), "text": str(text_path)}


def render_html(context: dict[str, Any]) -> str:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml"]),
    )
    return env.get_template(HTML_TEMPLATE).render(**context)


def render_text(context: dict[str, Any]) -> str:
    lines: list[str] = [
        context["header_line"],
        "Macro Synthesis",
        "",
    ]
    if context.get("book_impact"):
        lines.extend(["OVERNIGHT BOOK IMPACT", context["book_impact"], ""])

    lines.append("OVERNIGHT DASHBOARD")
    for row in context["dashboard_rows"]:
        lines.append(f"{row['asset']}: {row['last']} | {row['change']} | 5D {row['trend_arrow']}")
    lines.append("")

    lines.append("THE 3 THINGS")
    for i, item in enumerate(context["three_things"], start=1):
        lines.append(f"{i}. {item.headline}")
        lines.append(item.body)
        if item.so_what:
            lines.append(f"So what: {item.so_what}")
        lines.append("")

    lines.append("TODAY'S CALENDAR")
    for event in context["calendar_events"]:
        lines.append(
            f"{event['time_hkt']} {event['country_or_region']} - {event['event_name']} "
            f"(Exp: {event['consensus']}; Prev: {event['previous']})"
        )
    lines.append("")

    chart = context.get("chart")
    if chart:
        lines.extend(["ONE CHART WORTH SEEING", chart["caption"], chart["file_path"], ""])

    lines.append("THEME RADAR")
    for item in context["radar_items"]:
        source = item.source_name or "Source unavailable"
        url = item.source_url or ""
        lines.append(f"{item.headline} - {source}")
        if url:
            lines.append(url)
        lines.append(item.body)
        if item.so_what:
            lines.append(_so_what_label(item.so_what))
        lines.append("")

    contrarian = context.get("contrarian_corner")
    if contrarian:
        lines.extend(["CONTRARIAN CORNER", contrarian.body])
        if contrarian.so_what:
            lines.append(f"Watch item: {contrarian.so_what}")
        lines.append("")

    if context["warnings"]:
        lines.append("WARNINGS")
        lines.extend(f"- {w}" for w in context["warnings"])

    return "\n".join(lines).rstrip() + "\n"


def build_render_context(
    draft: BriefDraft,
    settings: "Settings",
    vol_params: dict[str, dict[str, Any]] | None = None,
    chart_image_url: str | None = None,
) -> dict[str, Any]:
    """Convert a BriefDraft into template-ready display fields."""
    metadata = draft.run_metadata or {}
    cutoff = metadata.get("data_cutoff_at")
    header_line = f"{_format_header_date(cutoff)} | Morning Brief"

    return {
        "header_line": header_line,
        "warnings": _dedupe(draft.warnings + list(metadata.get("warnings", []))),
        "book_impact": draft.book_impact,
        "dashboard_rows": [
            _format_dashboard_row(snapshot, vol_params or {})
            for snapshot in draft.overnight_dashboard
        ],
        "three_things": draft.three_things,
        "calendar_events": [_format_calendar_event(event) for event in draft.todays_calendar],
        "chart": _format_chart(draft.chart, image_url=chart_image_url),
        "radar_items": draft.radar_items,
        "contrarian_corner": draft.contrarian_corner,
    }


def _format_dashboard_row(
    snapshot: MarketSnapshot,
    vol_params: dict[str, dict[str, Any]],
) -> dict[str, str]:
    direction_class = _direction_class(snapshot.one_day_change)
    change_class = direction_class
    if snapshot.threshold_flag or (
        snapshot.one_day_zscore is not None and abs(snapshot.one_day_zscore) >= 1.0
    ):
        change_class = f"{change_class} sig-move"

    trend_arrow, trend_class = _five_day_trend(snapshot, vol_params)
    return {
        "asset": snapshot.display_name,
        "last": _format_level(snapshot),
        "change": _format_change(snapshot.one_day_change, snapshot.one_day_change_unit),
        "change_class": change_class,
        "trend_arrow": trend_arrow,
        "trend_class": trend_class,
    }


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _format_calendar_event(event: CalendarEvent) -> dict[str, str | bool]:
    return {
        "time_hkt": event.event_time_hkt.strftime("%H:%M"),
        "country_or_region": event.country_or_region,
        "event_name": event.event_name,
        "high_importance": event.importance >= 3,
        "consensus": _format_optional_value(event.consensus),
        "previous": _format_optional_value(event.previous),
    }


def _format_chart(chart: ChartSpec | None, image_url: str | None = None) -> dict[str, str] | None:
    if chart is None or not chart.file_path:
        return None
    path = Path(chart.file_path)
    if not path.exists() and not image_url:
        return None
    src = image_url or "cid:chart@brief"
    return {
        "src": src,
        "file_path": chart.file_path,
        "title": chart.title or "Market analysis chart",
        "caption": chart.caption,
    }


def _five_day_trend(
    snapshot: MarketSnapshot,
    vol_params: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    if snapshot.five_day_change is None:
        return "", "flat"

    params = vol_params.get(snapshot.instrument_id) or _FALLBACK_VOL.get(
        snapshot.five_day_change_unit or snapshot.one_day_change_unit,
        {"sd_1d": 1.0, "unit": snapshot.five_day_change_unit or snapshot.one_day_change_unit},
    )
    sd_1d = float(params.get("sd_1d") or 1.0)
    five_day_zscore = snapshot.five_day_change / (sd_1d * sqrt(5))

    if abs(five_day_zscore) < _FIVE_DAY_FLAT_Z:
        return "→", "flat"
    if snapshot.five_day_change > 0:
        return "↗", "up"
    return "↘", "down"


def _direction_class(value: float) -> str:
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def _format_level(snapshot: MarketSnapshot) -> str:
    value = snapshot.last_price_or_level
    if snapshot.one_day_change_unit == "bps":
        return f"{value:.3f}%"
    if abs(value) >= 1000:
        return f"{value:,.1f}"
    if abs(value) >= 100:
        return f"{value:,.2f}"
    return f"{value:,.4g}"


def _format_change(value: float, unit: str) -> str:
    sign = "+" if value > 0 else ""
    if unit == "%":
        return f"{sign}{value:.2f}%"
    if unit == "bps":
        return f"{sign}{value:.1f}bps"
    return f"{sign}{value:.2f}{unit}"


def _format_optional_value(value: Any) -> str:
    if value is None or value == "":
        return "n/a"
    return str(value)


def _so_what_label(text: str) -> str:
    return text.replace("What this means for our book:", "So what:")


def _format_header_date(value: Any) -> str:
    if not value:
        return "Daily Macro Brief"
    if hasattr(value, "strftime"):
        return f"{value:%A, %B} {value.day}, {value:%Y}"
    text = str(value)
    return text[:10] if len(text) >= 10 else text
