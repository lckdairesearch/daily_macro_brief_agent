"""LLM-driven chart code generation.

Selects which instruments to plot, builds the data preamble, calls the LLM to
generate matplotlib code, and validates the result by subprocess execution.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import re

import yaml

from app.llm.prompt_registry import load_prompt
from app.llm.provider import LLMClient, LLMConfig
from app.models import BriefDraft, ChartPlan

if TYPE_CHECKING:
    from app.settings import Settings

_log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_TEMPLATES_PATH = REPO_ROOT / "app" / "config" / "chart_templates.yaml"


def configure_matplotlib_cache_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Point Matplotlib/fontconfig cache paths at a writable temp directory."""
    target = os.environ if env is None else env
    mpl_cache_dir = Path(tempfile.gettempdir()) / "daily_macro_brief_mpl_cache"
    mpl_cache_dir.mkdir(parents=True, exist_ok=True)
    (mpl_cache_dir / "fontconfig").mkdir(exist_ok=True)
    target["MPLCONFIGDIR"] = str(mpl_cache_dir)
    target["XDG_CACHE_HOME"] = str(mpl_cache_dir)
    target["HOME"] = str(mpl_cache_dir)
    target["MPLBACKEND"] = "Agg"
    return target


def select_instruments(draft: BriefDraft) -> tuple[str, list[str]]:
    """Return (chart_label, instrument_ids) via template match or fallback.

    Primary: score chart_templates.yaml candidates by overlap with instruments
    cited in three_things and threshold-flagged dashboard items.
    Fallback: all dashboard instrument IDs (LLM selects 2-4 from context).
    """
    templates = _load_templates()

    cited: set[str] = set()
    for item in draft.three_things:
        cited.update(item.supporting_market_ids)
    flagged = {s.instrument_id for s in draft.overnight_dashboard if s.threshold_flag}
    candidates = cited | flagged

    best_template: dict | None = None
    best_score = 0
    for tmpl in templates:
        score = len(set(tmpl["instruments"]) & candidates)
        if score > best_score:
            best_score = score
            best_template = tmpl

    if best_template and best_score > 0:
        return best_template["label"], best_template["instruments"]

    all_ids = [s.instrument_id for s in draft.overnight_dashboard]
    return "Market overview", all_ids


def _load_templates() -> list[dict]:
    raw = yaml.safe_load(_TEMPLATES_PATH.read_text(encoding="utf-8"))
    return raw.get("templates", [])


def _slice_rows_by_calendar_window(rows: list[dict], window_days: int) -> list[dict]:
    if not rows:
        return []
    last_day = date.fromisoformat(rows[-1]["date"])
    start_day = last_day - timedelta(days=window_days - 1)
    return [row for row in rows if date.fromisoformat(row["date"]) >= start_day]


def _segment_rows(rows: list[dict]) -> list[list[dict]]:
    if not rows:
        return []
    segments: list[list[dict]] = [[rows[0]]]
    previous_day = date.fromisoformat(rows[0]["date"])
    for row in rows[1:]:
        current_day = date.fromisoformat(row["date"])
        if (current_day - previous_day).days > 1:
            segments.append([])
        segments[-1].append(row)
        previous_day = current_day
    return segments


def _compute_avg_shared_gap_ratio(plot_rows: dict[str, list[dict]], names: list[str]) -> float | None:
    if len(names) != 2:
        return None
    left_rows = plot_rows.get(names[0], [])
    right_rows = plot_rows.get(names[1], [])
    if not left_rows or not right_rows:
        return None
    shared_dates = sorted({row["date"] for row in left_rows} & {row["date"] for row in right_rows})
    if len(shared_dates) < 2:
        return None
    all_values = [float(row["close"]) for row in left_rows + right_rows]
    denom = max(all_values) - min(all_values)
    if denom <= 0:
        return 0.0
    left_by_date = {row["date"]: float(row["close"]) for row in left_rows}
    right_by_date = {row["date"]: float(row["close"]) for row in right_rows}
    gaps = [abs(left_by_date[d] - right_by_date[d]) / denom for d in shared_dates]
    return sum(gaps) / len(gaps)


def _build_plotting_context(
    series_data: dict[str, list[dict]],
    display_names: dict[str, str],
    window_days: int,
) -> dict[str, object]:
    plot_rows: dict[str, list[dict]] = {}
    series_segments: dict[str, list[list[dict]]] = {}
    names = [display_names.get(iid, iid) for iid in series_data]

    for iid, rows in series_data.items():
        name = display_names.get(iid, iid)
        clipped = _slice_rows_by_calendar_window(rows, window_days)
        plot_rows[name] = clipped
        series_segments[name] = _segment_rows(clipped)

    avg_shared_gap_ratio = _compute_avg_shared_gap_ratio(plot_rows, names)
    primary_series_name: str | None = None
    secondary_series_name: str | None = None
    if len(names) == 2:
        range_by_name = {}
        for name in names:
            values = [float(row["close"]) for row in plot_rows.get(name, [])]
            range_by_name[name] = (max(values) - min(values)) if values else 0.0
        ordered = sorted(names, key=lambda name: range_by_name[name], reverse=True)
        primary_series_name = ordered[0]
        secondary_series_name = ordered[1]

    prefer_dual_axis = not (avg_shared_gap_ratio is not None and avg_shared_gap_ratio < 0.10)
    return {
        "plot_rows": plot_rows,
        "series_segments": series_segments,
        "avg_shared_gap_ratio": avg_shared_gap_ratio,
        "prefer_dual_axis": prefer_dual_axis,
        "primary_series_name": primary_series_name,
        "secondary_series_name": secondary_series_name,
    }


def build_data_preamble(
    label: str,
    series_data: dict[str, list[dict]],
    display_names: dict[str, str],
    asset_classes: dict[str, str],
    units: dict[str, str],
    events: list[dict],
    output_path: str,
    window_days: int = 30,
) -> str:
    """Build the Python variable-definition block injected before LLM code."""
    if not series_data:
        return ""

    plotting_context = _build_plotting_context(series_data, display_names, window_days)

    # Shared date union for x-axis ticks and event lookups
    all_dates = sorted({r["date"] for rows in series_data.values() for r in rows})
    dates_repr = repr(all_dates)

    series_lines = []
    series_dates_lines = []
    units_lines = []
    ac_lines = []
    for iid, rows in series_data.items():
        name = display_names.get(iid, iid)
        values = [r["close"] for r in rows]
        own_dates = [r["date"] for r in rows]
        series_lines.append(f"    {name!r}: {values!r},")
        series_dates_lines.append(f"    {name!r}: {own_dates!r},")
        units_lines.append(f"    {name!r}: {units.get(iid, 'price')!r},")
        ac_lines.append(f"    {name!r}: {asset_classes.get(iid, 'equity')!r},")

    return (
        "import datetime\n"
        "import matplotlib\n"
        'matplotlib.use("Agg")\n'
        "import matplotlib.pyplot as plt\n"
        "import matplotlib.dates as mdates\n"
        "\n"
        f"dates = {dates_repr}\n"
        f"window_days = {window_days!r}\n"
        f"series_dates = {{\n{chr(10).join(series_dates_lines)}\n}}\n"
        f"series = {{\n{chr(10).join(series_lines)}\n}}\n"
        f"plot_rows = {plotting_context['plot_rows']!r}\n"
        f"series_segments = {plotting_context['series_segments']!r}\n"
        f"avg_shared_gap_ratio = {plotting_context['avg_shared_gap_ratio']!r}\n"
        f"prefer_dual_axis = {plotting_context['prefer_dual_axis']!r}\n"
        f"primary_series_name = {plotting_context['primary_series_name']!r}\n"
        f"secondary_series_name = {plotting_context['secondary_series_name']!r}\n"
        f"units = {{\n{chr(10).join(units_lines)}\n}}\n"
        f"asset_classes = {{\n{chr(10).join(ac_lines)}\n}}\n"
        f"events = {events!r}\n"
        f"title = {label!r}\n"
        f"output_path = {output_path!r}\n"
    )


def generate_chart_code(
    chart_plan: ChartPlan,
    series_data: dict[str, list[dict]],
    display_names: dict[str, str],
    asset_classes: dict[str, str],
    units: dict[str, str],
    events: list[dict],
    output_path: str,
    settings: "Settings",
    error_context: str = "",
) -> str:
    """Call LLM to generate matplotlib visualization code. Returns full executable Python."""
    window_days = 7 if chart_plan.window.value == "1w" else 30
    preamble = build_data_preamble(
        chart_plan.title,
        series_data,
        display_names,
        asset_classes,
        units,
        events,
        output_path,
        window_days=window_days,
    )
    prompt = load_prompt("chart_codegen")
    llm_cfg = settings.sources.get("llm", {})
    model = llm_cfg.get("chart_model") or llm_cfg.get("synthesis_model", "openai/gpt-4o")
    reasoning_effort = llm_cfg.get("chart_reasoning_effort", llm_cfg.get("synthesis_reasoning_effort"))
    verbosity = llm_cfg.get("chart_verbosity", llm_cfg.get("synthesis_verbosity"))
    timeout_seconds = int(llm_cfg.get("chart_timeout_seconds", llm_cfg.get("synthesis_timeout_seconds", 180)))
    max_tokens = int(llm_cfg.get("chart_max_tokens", 3000))

    user_msg = (
        "The following variables are already defined before your code — "
        "do NOT redefine them:\n\n"
        f"```python\n{preamble}\n```\n\n"
        "Data integrity check you MUST honour before plotting:\n"
        "- len(series[name]) == len(series_dates[name]) for every name — they are pre-aligned.\n"
        "- plot_rows[name] is the active calendar-window slice for each series.\n"
        "- series_segments[name] splits plot_rows[name] into contiguous date runs.\n"
        "- Plot each segment separately. Never connect across a missing calendar date.\n"
        "- Never synthesize weekend or holiday values. Never draw dotted weekend bridges.\n"
        "- The shared `dates` is the union of all series dates — use it ONLY for x-axis tick "
        "setup and event-annotation lookups.\n"
        "- For exactly 2 selected series, use dual axis unless prefer_dual_axis is False.\n"
        "- primary_series_name should stay solid; secondary_series_name should be dashed first "
        "if overlap remains annoying.\n\n"
        f"Render this fixed chart plan:\n"
        f"- chart_window = {chart_plan.window.value!r}\n"
        f"- chart_type = {chart_plan.chart_type!r}\n"
        f"- selected_instruments = {chart_plan.instrument_ids!r}\n"
        f"- selection_reason = {chart_plan.selection_reason!r}\n"
        "Do not choose a different window or a different series.\n\n"
        "Write only the visualization block that follows. "
        "Return ONLY Python code — no fences, no explanations."
    )
    if error_context:
        user_msg += (
            f"\n\nPrevious attempt failed — fix this error:\n```\n{error_context}\n```"
        )

    client = LLMClient(
        LLMConfig(
            model=model,
            temperature=0.2,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            reasoning_effort=reasoning_effort,
            verbosity=verbosity,
        )
    )
    raw = client.generate_text(system_prompt=prompt.text, user_message=user_msg)
    return preamble + "\n" + _remove_label_slicing(_strip_fences(raw))


def _remove_label_slicing(code: str) -> str:
    """Strip any [:n] truncation applied to the lbl variable in generated code."""
    return re.sub(r'\blbl\s*\[:[0-9]+\]', 'lbl', code)


def _strip_fences(text: str) -> str:
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


def execute_chart_code(code: str, output_path: str, timeout: int = 60) -> None:
    """Execute code in a subprocess. Raises RuntimeError on failure or timeout."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(code)
        tmp_path = fh.name

    try:
        env = configure_matplotlib_cache_env()
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        if result.returncode != 0:
            stderr = result.stderr[-2000:] if result.stderr else "(no stderr)"
            raise RuntimeError(f"Chart script exited {result.returncode}:\n{stderr}")
        if not Path(output_path).exists() or Path(output_path).stat().st_size == 0:
            raise RuntimeError(f"PNG not created at {output_path}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Chart code timed out after {timeout}s")
    finally:
        Path(tmp_path).unlink(missing_ok=True)
