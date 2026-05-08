#!/usr/bin/env python
"""Debug live run: steps 1-9 with real API data, pretty-printed output."""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message="There is no current event loop")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.data.market import fetch_live_market_with_cache, load_vol_params
from app.data.calendar import InvestingCalendarProvider
from app.discovery.orchestrator import build_scouts, run_discovery
from app.discovery.scouts.base import DiscoveryContext
from app.models import RunMode
from app.settings import Settings
from app.synthesis.deduper import deduplicate
from app.synthesis.ranker import rank


def _bar(label: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")


def main() -> None:
    settings = Settings.load()
    tz = ZoneInfo(settings.app.timezone)
    data_cutoff = datetime.now(tz)

    # ── Step 2: Market data ────────────────────────────────────────────────
    _bar("STEP 2 — Market snapshots (live)")
    vol_params = load_vol_params(settings.config_dir)
    snapshots = fetch_live_market_with_cache(settings, vol_params)
    print(f"  {len(snapshots)} snapshots fetched")
    print(f"  {'ID':<12} {'Price':>10}  {'1D Chg':>12}  {'Z':>6}  {'5D Chg':>10}  Flag")
    print(f"  {'-'*12} {'-'*10}  {'-'*12}  {'-'*6}  {'-'*10}  ----")
    for s in snapshots:
        flag = "***" if s.threshold_flag else ""
        unit = s.one_day_change_unit or ""
        z = f"{s.one_day_zscore:+.2f}" if s.one_day_zscore is not None else "  n/a"
        chg = f"{s.one_day_change:+.3f} {unit}"
        chg5 = f"{s.five_day_change:+.3f} {unit}" if s.five_day_change is not None else "  n/a"
        print(f"  {s.instrument_id:<12} {s.last_price_or_level:>10.4g}  {chg:>12}  {z:>6}  {chg5:>10}  {flag}")

    # ── Step 3: Calendar ──────────────────────────────────────────────────
    _bar("STEP 3 — Economic calendar (live)")
    calendar_events: list = []
    try:
        cal_config = settings.sources.get("calendar", {})
        cache_dir = REPO_ROOT / settings.app.cache_dir
        provider = InvestingCalendarProvider(
            config=cal_config,
            cache_dir=cache_dir / "calendar",
            timezone_name=settings.app.timezone,
        )
        today = data_cutoff.date()
        calendar_events = provider.fetch_for_date(today)
        print(f"  {len(calendar_events)} events")
        high = [e for e in calendar_events if e.importance >= 3]
        for e in high[:10]:
            print(f"  [{e.importance}★] {e.event_name} ({e.country_or_region}) @ {e.event_time_hkt.strftime('%H:%M HKT')}")
    except Exception as exc:
        print(f"  Calendar failed: {exc}")

    # ── Step 5: Discovery scouts ──────────────────────────────────────────
    _bar("STEP 5 — Discovery scouts (live)")
    context = DiscoveryContext(
        market_snapshots=snapshots,
        calendar_events=calendar_events,
        themes=settings.themes.get("themes", []),
        portfolio=settings.portfolio,
        lookback_hours=24,
        data_cutoff=data_cutoff,
        mode=RunMode.LIVE,
    )
    failed_sources: list[str] = []
    scouts = build_scouts(settings, RunMode.LIVE)
    print(f"  Scouts enabled: {[s.name for s in scouts]}")
    raw_cards = run_discovery(scouts, context, failed_sources)
    if failed_sources:
        print(f"  Failed scouts: {failed_sources}")
    print(f"  {len(raw_cards)} raw cards collected")

    # ── Step 6: Deduplication ─────────────────────────────────────────────
    _bar("STEP 6 — Deduplication")
    deduped = deduplicate(raw_cards)
    print(f"  {len(raw_cards)} → {len(deduped)} cards after dedup (dropped {len(raw_cards)-len(deduped)})")

    # ── Step 7: Ranking ───────────────────────────────────────────────────
    _bar("STEP 7 — Ranking")
    rc = rank(
        market_snapshots=snapshots,
        calendar_events=calendar_events,
        evidence_cards=deduped,
        themes=settings.themes.get("themes", []),
        portfolio=settings.portfolio,
        now=datetime.now(timezone.utc),
    )

    print(f"\n  Ranked evidence cards (all {len(rc.ranked_evidence_cards)}):")
    for i, c in enumerate(rc.ranked_evidence_cards, 1):
        sc = rc.scores.get(c.id, 0.0)
        print(f"  {i:2}. [{sc:.3f}] [{c.source_type.value:<13}] {c.title[:75]}")

    print(f"\n  ── Proposed: Three Things ──")
    for c in rc.proposed_three_things:
        print(f"    • {c.title}")
        print(f"      Thesis: {c.thesis[:100]}")

    print(f"\n  ── Proposed: Theme Radar ──")
    for c in rc.proposed_theme_radar:
        print(f"    • {c.title}")

    if rc.proposed_contrarian_corner_seed:
        print(f"\n  ── Proposed: Contrarian Corner Seed ──")
        print(f"    • {rc.proposed_contrarian_corner_seed.title}")
        print(f"      Thesis: {rc.proposed_contrarian_corner_seed.thesis[:120]}")

    print(f"\n  ── Top Market Moves (flagged) ──")
    for s in rc.top_market_moves[:5]:
        flag = " ***" if s.threshold_flag else ""
        print(f"    {s.display_name}: {s.one_day_change:+.3f} {s.one_day_change_unit}{flag}")

    _bar("DONE")


if __name__ == "__main__":
    main()
