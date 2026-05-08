"""Demo: generate a chart from the 2026-05-08 live eval data.

Usage:
    .venv/bin/python scripts/demo_chart.py

Output: outputs/live_eval/live_eval_2026-05-08_193808/demo_chart.png
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app.models import (
    AssetClass,
    BriefDraft,
    BriefItem,
    BriefSection,
    ChartSpec,
    FreshnessStatus,
    MarketSnapshot,
)
from app.render.charts import build_chart
from app.settings import Settings

LIVE_EVAL = REPO / "outputs" / "live_eval" / "live_eval_2026-05-08_193808"
OUTPUT = LIVE_EVAL / "demo_chart.png"


def load_snapshots() -> list[MarketSnapshot]:
    raw = json.loads((LIVE_EVAL / "market_snapshots.json").read_text())
    return [MarketSnapshot.model_validate(s) for s in raw]


def main() -> None:
    settings = Settings.load()

    snapshots = load_snapshots()

    # Recreate the ranked context's top movers as BriefItems so template matching works
    ranked = json.loads((LIVE_EVAL / "ranked_context.json").read_text())
    top_ids: list[str] = ranked["top_market_moves"]

    three_things = [
        BriefItem(
            section=BriefSection.THREE_THINGS,
            headline="Live eval context",
            body="Commodities and rates moved significantly.",
            so_what="Testing chart generation with real data.",
            supporting_market_ids=top_ids[:5],
        )
    ]

    draft = BriefDraft(
        run_metadata={},
        overnight_dashboard=snapshots,
        three_things=three_things,
        todays_calendar=[],
        chart=ChartSpec(
            title="Commodity basket — 30-day move",
            caption="WTI, Gold, and Copper all moved amid tariff uncertainty and dollar weakness.",
            chart_type="line",
            data_source="live",
        ),
    )

    as_of = datetime(2026, 5, 8, 6, 45, tzinfo=timezone.utc)

    print(f"Generating chart → {OUTPUT}")
    spec = build_chart(
        draft=draft,
        settings=settings,
        output_path=str(OUTPUT),
        sample_mode=False,
        as_of=as_of,
    )

    print(f"Done: {spec.file_path}")
    print(f"  title:          {spec.title}")
    print(f"  chart_type:     {spec.chart_type}")
    print(f"  code_generated: {spec.code_generated}")
    print(f"  caption:        {spec.caption}")


if __name__ == "__main__":
    main()
