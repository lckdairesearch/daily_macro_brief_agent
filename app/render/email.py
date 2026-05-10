"""HTML and plain-text email rendering via Jinja2 templates."""

from __future__ import annotations

from datetime import datetime
from math import sqrt
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.models import BriefDraft, CalendarEvent, ChartSpec, MarketSnapshot, SourceType

if TYPE_CHECKING:
    from app.settings import Settings

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
HTML_TEMPLATE = "brief_template.html"

_FALLBACK_VOL: dict[str, dict[str, float | str]] = {
    "%": {"sd_1d": 1.0, "unit": "%"},
    "bps": {"sd_1d": 5.0, "unit": "bps"},
}
_FIVE_DAY_FLAT_Z = 0.25
_DASHBOARD_BUCKET_ORDER = ("equities", "fx", "rates", "metals", "commodities", "btc", "other")
_METALS_IDS = frozenset({"GOLD", "SILVER", "COPPER"})
_POSITION_ID_RE = re.compile(r"\b[a-z0-9]+(?:_[a-z0-9]+)+\b")
_BOOK_IMPACT_PREFIX_RE = re.compile(r"^what this means for our book:\s*", re.IGNORECASE)
_SOURCE_TYPE_LABELS: dict[SourceType, str] = {
    SourceType.SOCIAL: "X Post",
    SourceType.PODCAST: "Podcast",
    SourceType.RESEARCH: "Research",
    SourceType.CENTRAL_BANK: "Central Bank",
    SourceType.NEWS: "News",
    SourceType.CALENDAR: "Calendar",
}


def render_brief(
    draft: BriefDraft,
    settings: "Settings",
    output_dir: str | Path | None = None,
    html_filename: str = "sample_brief.html",
    text_filename: str = "sample_brief.txt",
    vol_params: dict[str, dict[str, Any]] | None = None,
    chart_image_url: str | None = None,
) -> dict[str, str]:
    """Render HTML and plain-text artifacts and return their paths."""
    default_output_dir = Path(settings.app.output_dir) / "samples"
    out_dir = Path(output_dir or default_output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    context = build_render_context(draft, settings, vol_params=vol_params, chart_image_url=chart_image_url)
    html = render_html(context)
    text = render_text(context)

    html_path = out_dir / html_filename
    text_path = out_dir / text_filename
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
        lines.append(f"{i}. {item['headline']}")
        lines.append(item["body"])
        if item["so_what"]:
            lines.append(item["so_what"])
        lines.append("")

    lines.append(context.get("calendar_title", "Today's Calendar").upper())
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
        source = item["source_credit"] or "Source unavailable"
        url = item["source_url"] or ""
        lines.append(f"{item['headline']} - {source}")
        if url:
            lines.append(url)
        lines.append(item["body"])
        if item["so_what"]:
            lines.append(item["so_what"])
        lines.append("")

    contrarian = context.get("contrarian_corner")
    if contrarian:
        lines.extend(["CONTRARIAN CORNER", contrarian["body"]])
        if contrarian["so_what"]:
            lines.append(f"Watch item: {contrarian['so_what']}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def build_render_context(
    draft: BriefDraft,
    settings: "Settings",
    vol_params: dict[str, dict[str, Any]] | None = None,
    chart_image_url: str | None = None,
) -> dict[str, Any]:
    """Convert a BriefDraft into template-ready display fields."""
    metadata = draft.run_metadata or {}
    header_dt = metadata.get("brief_date") or metadata.get("data_cutoff_at")
    cutoff_raw = metadata.get("data_cutoff_at")
    is_weekend_run = False
    if cutoff_raw:
        try:
            cutoff_dt = datetime.fromisoformat(str(cutoff_raw).replace("Z", "+00:00"))
            is_weekend_run = cutoff_dt.weekday() in (5, 6)  # Sat=5, Sun=6
        except ValueError:
            pass
    weekend_suffix = " (For Upcoming Monday)" if is_weekend_run else ""
    header_line = f"{_format_header_date(header_dt)} | Morning Brief{weekend_suffix}"
    dashboard_snapshots = _select_dashboard_snapshots(draft.overnight_dashboard, settings)
    label_map = _build_display_label_map(settings)

    calendar_title = "Monday's Calendar" if is_weekend_run else "Today's Calendar"

    return {
        "header_line": header_line,
        "calendar_title": calendar_title,
        "warnings": _dedupe(list(metadata.get("warnings", []))),
        "book_impact": _normalize_display_text(draft.book_impact, label_map),
        "dashboard_rows": [
            _format_dashboard_row(snapshot, vol_params or {})
            for snapshot in dashboard_snapshots
        ],
        "three_things": [_format_brief_item(item, label_map) for item in draft.three_things],
        "calendar_events": [_format_calendar_event(event) for event in draft.todays_calendar],
        "calendar_event_rows": _pair_calendar_events(draft.todays_calendar),
        "chart": _format_chart(draft.chart, image_url=chart_image_url),
        "radar_items": [_format_brief_item(item, label_map) for item in draft.radar_items],
        "contrarian_corner": _format_brief_item(draft.contrarian_corner, label_map),
    }


def _select_dashboard_snapshots(
    snapshots: list[MarketSnapshot],
    settings: "Settings",
) -> list[MarketSnapshot]:
    core_ids = list(getattr(settings.app, "dashboard_core_instruments", []) or [])
    if not core_ids:
        return _order_dashboard_snapshots(snapshots)

    by_id = {snapshot.instrument_id: snapshot for snapshot in snapshots}
    core_snapshots = [by_id[instrument_id] for instrument_id in core_ids if instrument_id in by_id]
    if not core_snapshots:
        return _order_dashboard_snapshots(snapshots)

    selected_ids = {snapshot.instrument_id for snapshot in core_snapshots}
    indexed_snapshots = list(enumerate(snapshots))
    extra_snapshots = [
        (index, snapshot)
        for index, snapshot in indexed_snapshots
        if snapshot.instrument_id not in selected_ids and _is_significant_move(snapshot)
    ]
    extra_snapshots.sort(
        key=lambda item: (
            -(abs(item[1].one_day_zscore) if item[1].one_day_zscore is not None else 0.0),
            -abs(item[1].one_day_change),
            item[0],
        )
    )

    max_extra_movers = max(int(getattr(settings.app, "dashboard_max_extra_movers", 0) or 0), 0)
    extras = [snapshot for _, snapshot in extra_snapshots[:max_extra_movers]]
    return _order_dashboard_snapshots(core_snapshots + extras)


def _order_dashboard_snapshots(snapshots: list[MarketSnapshot]) -> list[MarketSnapshot]:
    grouped: dict[str, list[MarketSnapshot]] = {bucket: [] for bucket in _DASHBOARD_BUCKET_ORDER}
    for snapshot in snapshots:
        grouped[_dashboard_bucket(snapshot)].append(snapshot)

    ordered: list[MarketSnapshot] = []
    for bucket in _DASHBOARD_BUCKET_ORDER:
        ordered.extend(grouped[bucket])
    return ordered


def _dashboard_bucket(snapshot: MarketSnapshot) -> str:
    if snapshot.asset_class.value == "equity":
        return "equities"
    if snapshot.asset_class.value == "fx":
        return "fx"
    if snapshot.asset_class.value == "rates":
        return "rates"
    if snapshot.instrument_id in _METALS_IDS:
        return "metals"
    if snapshot.asset_class.value == "commodity":
        return "commodities"
    if snapshot.instrument_id == "BTC":
        return "btc"
    return "other"


def _is_significant_move(snapshot: MarketSnapshot) -> bool:
    if snapshot.threshold_flag:
        return True
    if snapshot.one_day_zscore is None:
        return False
    return abs(snapshot.one_day_zscore) >= 1.0


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
    trend_color = {"up": "#188038", "down": "#d93025", "flat": "#666666"}[trend_class]
    return {
        "asset": snapshot.display_name,
        "last": _format_level(snapshot),
        "change": _format_change(snapshot.one_day_change, snapshot.one_day_change_unit),
        "change_class": change_class,
        "trend_arrow": trend_arrow,
        "trend_class": trend_class,
        "trend_color": trend_color,
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
        "high_importance": event.brief_importance >= 3,
        "brief_importance": str(event.brief_importance),
        "importance_marker": "●" if event.brief_importance >= 3 else "",
        "consensus": _format_optional_value(event.consensus),
        "previous": _format_optional_value(event.previous),
    }


def _pair_calendar_events(events: list[CalendarEvent]) -> list[tuple[dict[str, str | bool], dict[str, str | bool] | None]]:
    formatted = [_format_calendar_event(event) for event in events]
    midpoint = (len(formatted) + 1) // 2
    left_column = formatted[:midpoint]
    right_column = formatted[midpoint:]
    rows: list[tuple[dict[str, str | bool], dict[str, str | bool] | None]] = []
    for idx, left in enumerate(left_column):
        right = right_column[idx] if idx < len(right_column) else None
        rows.append((left, right))
    return rows


def _format_brief_item(item: Any | None, label_map: dict[str, str]) -> dict[str, Any] | None:
    if item is None:
        return None
    source_url = getattr(item, "source_url", None)
    source_type = getattr(item, "source_type", None)
    return {
        "headline": item.headline,
        "body": _normalize_display_text(item.body, label_map),
        "so_what": _normalize_so_what_text(item.so_what, label_map),
        "source_name": getattr(item, "source_name", None),
        "source_url": source_url,
        "source_type": source_type,
        "source_credit": _format_source_credit(source_type, getattr(item, "source_name", None)),
        "topic_label": getattr(item, "topic_label", None),
        "has_source_url": bool(source_url),
    }


def _format_source_credit(source_type: SourceType | str | None, source_name: str | None) -> str | None:
    label: str | None = None
    if isinstance(source_type, SourceType):
        label = _SOURCE_TYPE_LABELS.get(source_type)
    elif isinstance(source_type, str):
        try:
            label = _SOURCE_TYPE_LABELS.get(SourceType(source_type))
        except ValueError:
            label = source_type.replace("_", " ").title()

    name = source_name.strip() if source_name else None
    if label and name:
        return f"{label} - {name}"
    return label or name


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


def _format_header_date(value: Any) -> str:
    if not value:
        return "Daily Macro Brief"
    if hasattr(value, "strftime"):
        return f"{value:%A, %B} {value.day}, {value:%Y}"
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is not None:
        return f"{parsed:%A, %B} {parsed.day}, {parsed:%Y}"
    return text[:10] if len(text) >= 10 else text


def _build_display_label_map(settings: "Settings") -> dict[str, str]:
    label_map: dict[str, str] = {}
    portfolio = getattr(settings, "portfolio", {}) or {}
    for position in portfolio.get("core_positions", []) + portfolio.get("tactical_overlays", []):
        position_id = str(position.get("id", "")).strip()
        position_label = str(position.get("label", "")).strip()
        if position_id and position_label:
            label_map[position_id] = position_label

    themes = getattr(settings, "themes", {}) or {}
    for theme in themes.get("themes", []):
        theme_id = str(theme.get("id", "")).strip()
        theme_label = str(theme.get("label", "")).strip()
        if theme_id and theme_label:
            label_map[theme_id] = theme_label
    return label_map


def _normalize_display_text(text: str | None, label_map: dict[str, str]) -> str | None:
    if not text:
        return text

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        return label_map.get(token, token.replace("_", " "))

    return _POSITION_ID_RE.sub(_replace, text)


def _normalize_so_what_text(text: str | None, label_map: dict[str, str]) -> str | None:
    normalized = _normalize_display_text(text, label_map)
    if not normalized:
        return normalized

    normalized = _BOOK_IMPACT_PREFIX_RE.sub("", normalized).strip()
    if not normalized:
        return normalized

    first = normalized[0]
    if first.isalpha():
        normalized = first.upper() + normalized[1:]
    return normalized
