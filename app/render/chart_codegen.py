"""LLM-driven chart code generation.

Selects which instruments to plot, builds the data preamble, calls the LLM to
generate matplotlib code, and validates the result by subprocess execution.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import re

import yaml

from app.llm.prompt_registry import load_prompt
from app.llm.provider import LLMClient, LLMConfig
from app.models import BriefDraft

if TYPE_CHECKING:
    from app.settings import Settings

_log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_TEMPLATES_PATH = REPO_ROOT / "app" / "config" / "chart_templates.yaml"


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



def build_data_preamble(
    label: str,
    series_data: dict[str, list[dict]],
    display_names: dict[str, str],
    asset_classes: dict[str, str],
    units: dict[str, str],
    events: list[dict],
    output_path: str,
) -> str:
    """Build the Python variable-definition block injected before LLM code."""
    if not series_data:
        return ""

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
        f"series_dates = {{\n{chr(10).join(series_dates_lines)}\n}}\n"
        f"series = {{\n{chr(10).join(series_lines)}\n}}\n"
        f"units = {{\n{chr(10).join(units_lines)}\n}}\n"
        f"asset_classes = {{\n{chr(10).join(ac_lines)}\n}}\n"
        f"events = {events!r}\n"
        f"title = {label!r}\n"
        f"output_path = {output_path!r}\n"
    )


def generate_chart_code(
    label: str,
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
    preamble = build_data_preamble(
        label, series_data, display_names, asset_classes, units, events, output_path,
    )
    prompt = load_prompt("chart_codegen")
    llm_cfg = settings.sources.get("llm", {})
    model = llm_cfg.get("chart_model") or llm_cfg.get("synthesis_model", "openai/gpt-4o")

    user_msg = (
        "The following variables are already defined before your code — "
        "do NOT redefine them:\n\n"
        f"```python\n{preamble}\n```\n\n"
        "Data integrity check you MUST honour before plotting:\n"
        "- len(series[name]) == len(series_dates[name]) for every name — they are pre-aligned.\n"
        "- Always use series_dates[name] (NOT the shared `dates`) as x-values in ax.plot().\n"
        "- The shared `dates` is the union of all series dates — use it ONLY for x-axis tick "
        "setup and event-annotation lookups.\n\n"
        "Write only the visualization block that follows. "
        "Return ONLY Python code — no fences, no explanations."
    )
    if error_context:
        user_msg += (
            f"\n\nPrevious attempt failed — fix this error:\n```\n{error_context}\n```"
        )

    client = LLMClient(LLMConfig(model=model, temperature=0.2, max_tokens=3000))
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


def execute_chart_code(code: str, output_path: str, timeout: int = 30) -> None:
    """Execute code in a subprocess. Raises RuntimeError on failure or timeout."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(code)
        tmp_path = fh.name

    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
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
