#!/usr/bin/env python
"""Run a saved live evaluation brief for a fixed morning cutoff.

This is a diagnostic runner for the currently implemented steps. It fetches
live market/calendar/source data, asks the configured LLM to synthesize a brief,
and saves the raw inputs plus rendered outputs for human inspection.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import warnings
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

warnings.filterwarnings("ignore", message="There is no current event loop")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.data.calendar import InvestingCalendarProvider
from app.data.market import (  # noqa: E402
    AV_INSTRUMENTS,
    DB_INSTRUMENTS,
    FRED_INSTRUMENTS,
    YFINANCE_INSTRUMENTS,
    AlphaVantageMarketProvider,
    DatabentoMarketProvider,
    FredMarketProvider,
    YfinanceMarketProvider,
    cache_market,
    detect_moves,
    load_vol_params,
)
from app.discovery.orchestrator import build_scouts, run_discovery  # noqa: E402
from app.discovery.scouts.base import DiscoveryContext  # noqa: E402
from app.llm.prompt_registry import load_prompt  # noqa: E402
from app.llm.provider import LLMClient, LLMConfig  # noqa: E402
from app.models import RunMode  # noqa: E402
from app.settings import Settings  # noqa: E402
from app.synthesis.deduper import deduplicate  # noqa: E402
from app.synthesis.ranker import rank  # noqa: E402


class EvalBrief(BaseModel):
    """LLM output saved by this diagnostic runner."""

    title: str
    markdown: str
    quality_notes: Any = Field(default_factory=list)


def main() -> None:
    parser = argparse.ArgumentParser(description="Saved live evaluation run")
    parser.add_argument("--date", default="2026-05-08", help="HKT run date, YYYY-MM-DD")
    parser.add_argument("--cutoff", default="08:00", help="HKT data cutoff, HH:MM")
    args = parser.parse_args()

    settings = Settings.load()
    missing = settings.validate_for_mode(RunMode.DRY_RUN)
    if missing:
        raise SystemExit(f"Missing required credentials: {', '.join(missing)}")

    tz = ZoneInfo(settings.app.timezone)
    target_day = date.fromisoformat(args.date)
    cutoff_hour, cutoff_minute = (int(part) for part in args.cutoff.split(":", 1))
    data_cutoff = datetime(
        target_day.year,
        target_day.month,
        target_day.day,
        cutoff_hour,
        cutoff_minute,
        tzinfo=tz,
    )

    run_id = f"live_eval_{target_day.isoformat()}_{datetime.now(tz).strftime('%H%M%S')}"
    output_dir = REPO_ROOT / settings.app.output_dir / "live_eval" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Run id: {run_id}")
    print(f"Cutoff: {data_cutoff.isoformat()}")

    vol_params = load_vol_params(settings.config_dir)
    snapshots = _fetch_market(settings, vol_params, data_cutoff)
    print(f"Market snapshots: {len(snapshots)}")

    calendar_events = _fetch_calendar(settings, target_day)
    print(f"Calendar events: {len(calendar_events)}")

    failed_sources: list[str] = []
    scouts = build_scouts(settings, RunMode.LIVE)
    context = DiscoveryContext(
        market_snapshots=snapshots,
        calendar_events=calendar_events,
        themes=settings.themes.get("themes", []),
        portfolio=settings.portfolio,
        lookback_hours=24,
        data_cutoff=data_cutoff,
        mode=RunMode.LIVE,
    )
    evidence = deduplicate(run_discovery(scouts, context, failed_sources))
    print(f"Evidence cards: {len(evidence)}")
    if failed_sources:
        print(f"Failed optional scouts: {', '.join(failed_sources)}")

    ranked = rank(
        market_snapshots=snapshots,
        calendar_events=calendar_events,
        evidence_cards=evidence,
        themes=settings.themes.get("themes", []),
        portfolio=settings.portfolio,
        now=datetime.now(timezone.utc),
    )

    _save_json(output_dir / "market_snapshots.json", [s.model_dump(mode="json") for s in snapshots])
    _save_json(
        output_dir / "calendar_events.json",
        [event.model_dump(mode="json") for event in calendar_events],
    )
    _save_json(output_dir / "evidence_cards.json", [card.model_dump(mode="json") for card in evidence])
    _save_json(
        output_dir / "ranked_context.json",
        {
            "top_market_moves": [s.instrument_id for s in ranked.top_market_moves[:10]],
            "top_calendar_events": [e.event_name for e in ranked.top_calendar_events[:10]],
            "proposed_three_things": [c.id for c in ranked.proposed_three_things],
            "proposed_theme_radar": [c.id for c in ranked.proposed_theme_radar],
            "proposed_contrarian_corner_seed": (
                ranked.proposed_contrarian_corner_seed.id
                if ranked.proposed_contrarian_corner_seed
                else None
            ),
            "scores": ranked.scores,
            "failed_sources": failed_sources,
        },
    )

    brief = _write_llm_brief(settings, data_cutoff, ranked, failed_sources)
    markdown = brief.markdown.strip() + "\n"
    (output_dir / "brief.md").write_text(markdown, encoding="utf-8")
    (output_dir / "brief.html").write_text(_markdown_to_html(brief.title, markdown), encoding="utf-8")
    (output_dir / "brief_rendered.html").write_text(
        _markdown_to_rendered_html(brief.title, markdown, data_cutoff),
        encoding="utf-8",
    )
    _save_json(output_dir / "llm_brief.json", brief.model_dump(mode="json"))

    manifest = {
        "run_id": run_id,
        "data_cutoff": data_cutoff.isoformat(),
        "timezone": settings.app.timezone,
        "model": settings.sources.get("llm", {}).get("synthesis_model"),
        "counts": {
            "market_snapshots": len(snapshots),
            "calendar_events": len(calendar_events),
            "evidence_cards": len(evidence),
        },
        "failed_sources": failed_sources,
        "outputs": {
            "brief_md": str(output_dir / "brief.md"),
            "brief_html": str(output_dir / "brief.html"),
            "brief_rendered_html": str(output_dir / "brief_rendered.html"),
            "raw_market": str(output_dir / "market_snapshots.json"),
            "raw_calendar": str(output_dir / "calendar_events.json"),
            "raw_evidence": str(output_dir / "evidence_cards.json"),
        },
    }
    _save_json(output_dir / "manifest.json", manifest)
    print(f"Saved outputs: {output_dir}")


def _fetch_market(settings: Settings, vol_params: dict[str, dict], data_cutoff: datetime) -> list[Any]:
    snapshots = (
        AlphaVantageMarketProvider(settings.creds.alpha_vantage_api_key).fetch_watchlist(
            AV_INSTRUMENTS,
            data_cutoff,
        )
        + DatabentoMarketProvider(settings.creds.databento_api_key).fetch_watchlist(
            DB_INSTRUMENTS,
            data_cutoff,
        )
        + FredMarketProvider(settings.creds.fred_api_key).fetch_watchlist(
            FRED_INSTRUMENTS,
            data_cutoff,
        )
        + YfinanceMarketProvider().fetch_watchlist(YFINANCE_INSTRUMENTS, data_cutoff)
    )
    snapshots = detect_moves(snapshots, vol_params)
    cache_market(snapshots, REPO_ROOT / settings.app.cache_dir, data_cutoff.date())
    return snapshots


def _fetch_calendar(settings: Settings, target_day: date) -> list[Any]:
    provider = InvestingCalendarProvider(
        config=settings.sources.get("calendar", {}),
        cache_dir=REPO_ROOT / settings.app.cache_dir / "calendar",
        timezone_name=settings.app.timezone,
    )
    return provider.fetch_for_date(target_day)


def _write_llm_brief(settings: Settings, data_cutoff: datetime, ranked: Any, failed_sources: list[str]) -> EvalBrief:
    llm_cfg = settings.sources.get("llm", {})
    client = LLMClient(
        LLMConfig(
            model=llm_cfg.get("synthesis_model", "openai/gpt-5.4"),
            temperature=float(llm_cfg.get("temperature", 0.2)),
            max_tokens=3500,
            timeout_seconds=120,
        )
    )
    prompt = load_prompt("brief_writer")
    payload = {
        "task": (
            "Write an evaluable Daily Macro Brief as Markdown for the morning run. "
            "Use only supplied data. Include all six specified sections. "
            "Return JSON with title, markdown, and quality_notes."
        ),
        "data_cutoff": data_cutoff.isoformat(),
        "portfolio": settings.portfolio,
        "themes": settings.themes.get("themes", []),
        "market_snapshots": [s.model_dump(mode="json") for s in ranked.dashboard_rows],
        "top_market_moves": [s.model_dump(mode="json") for s in ranked.top_market_moves[:10]],
        "calendar_events": [e.model_dump(mode="json") for e in ranked.top_calendar_events[:20]],
        "evidence_cards": [c.model_dump(mode="json") for c in ranked.ranked_evidence_cards[:12]],
        "proposed_three_things": [c.id for c in ranked.proposed_three_things],
        "proposed_theme_radar": [c.id for c in ranked.proposed_theme_radar],
        "proposed_contrarian_corner_seed": (
            ranked.proposed_contrarian_corner_seed.id
            if ranked.proposed_contrarian_corner_seed
            else None
        ),
        "failed_sources": failed_sources,
    }
    return client.generate_structured(
        system_prompt=prompt.text,
        user_payload=payload,
        schema=EvalBrief,
    ).output


def _save_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _markdown_to_html(title: str, markdown: str) -> str:
    return _render_markdown_document(title, markdown, dense=False, subtitle=None)


def _markdown_to_rendered_html(title: str, markdown: str, data_cutoff: datetime) -> str:
    subtitle = f"Data cutoff: {data_cutoff.strftime('%Y-%m-%d %H:%M %Z')}"
    return _render_markdown_document(title, markdown, dense=True, subtitle=subtitle)


def _render_markdown_document(
    title: str,
    markdown: str,
    *,
    dense: bool,
    subtitle: str | None,
) -> str:
    blocks: list[str] = []
    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        escaped = html.escape(stripped)
        if not stripped:
            i += 1
            continue
        if stripped.startswith("### "):
            blocks.append(f"<h3>{escaped[4:]}</h3>")
        elif stripped.startswith("## "):
            blocks.append(f"<h2>{escaped[3:]}</h2>")
        elif stripped.startswith("# "):
            blocks.append(f"<h1>{escaped[2:]}</h1>")
        elif stripped.startswith("- "):
            items: list[str] = []
            while i < len(lines) and lines[i].strip().startswith("- "):
                items.append(f"<li>{html.escape(lines[i].strip()[2:])}</li>")
                i += 1
            blocks.append(f"<ul>{''.join(items)}</ul>")
            continue
        else:
            para: list[str] = [stripped]
            i += 1
            while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith(("# ", "## ", "### ", "- ")):
                para.append(lines[i].strip())
                i += 1
            blocks.append(f"<p>{html.escape(' '.join(para))}</p>")
            continue
        i += 1

    spacing = "compact" if dense else "loose"
    subtitle_html = f"<p class=\"subtitle\">{html.escape(subtitle)}</p>" if subtitle else ""
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title>"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<style>"
        "body{margin:0;background:#f5f2ea;color:#161616;font-family:Georgia,'Times New Roman',serif}"
        ".page{max-width:920px;margin:0 auto;padding:36px 24px 48px}"
        ".sheet{background:#fff;border:1px solid #e4dfd5;box-shadow:0 14px 40px rgba(0,0,0,.06);padding:34px 32px}"
        f".sheet.{spacing}{{padding:28px 28px}}"
        "h1{font-size:34px;line-height:1.1;margin:0 0 8px;font-weight:700;letter-spacing:-.02em}"
        "h2{font-size:18px;line-height:1.25;margin:28px 0 10px;text-transform:uppercase;letter-spacing:.08em}"
        "h3{font-size:15px;line-height:1.3;margin:18px 0 8px;font-weight:700}"
        "p,li{font-size:15px;line-height:1.6}"
        "p{margin:0 0 12px}"
        "ul{margin:0 0 14px 22px;padding:0}"
        ".subtitle{margin:0 0 22px;color:#666;font-size:12px;text-transform:uppercase;letter-spacing:.12em}"
        ".note{margin-top:28px;padding-top:14px;border-top:1px solid #eee;color:#666;font-size:12px}"
        "</style></head><body><div class=\"page\"><main class=\"sheet "
        + spacing
        + "\">"
        + f"<h1>{html.escape(title)}</h1>"
        + subtitle_html
        + "".join(blocks)
        + "<div class=\"note\">Rendered live-eval artifact for review and later print/PDF use.</div>"
        + "</main></div></body></html>"
    )


if __name__ == "__main__":
    main()
