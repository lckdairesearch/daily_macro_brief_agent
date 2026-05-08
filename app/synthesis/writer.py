"""Brief writer. Calls LiteLLM once with grounded ranked context to produce BriefDraft.

The LLM writes only the synthesis sections (three_things, radar_items,
contrarian_corner, chart_caption). Dashboard rows and calendar events are
pipeline pass-throughs merged in _assemble_draft().
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.llm.prompt_registry import load_prompt
from app.llm.provider import LLMClient, LLMConfig, LLMResponseError
from app.models import (
    BriefDraft,
    BriefItem,
    BriefSection,
    BriefWriterOutput,
    ChartSpec,
    FreshnessStatus,
    RunMode,
    WriterItem,
)

# Pre-baked fixture brief for sample mode — avoids LLM call, no credentials needed.
_SAMPLE_FAKE_RESPONSE: dict = {
    "three_things": [
        {
            "headline": "Metals complex gains on central bank accumulation",
            "body": (
                "Gold and silver moved higher amid continued central bank buying. "
                "Reserve managers are diversifying away from dollar-denominated assets, "
                "keeping the bid elevated. Real yields softened, removing a key headwind "
                "for non-yielding metals and reinforcing the structural debasement narrative."
            ),
            "so_what": "Supports long metals complex position in the book.",
            "supporting_market_ids": ["SPY"],
            "supporting_evidence_ids": ["ev_003"],
        }
    ],
    "radar_items": [
        {
            "headline": "Fiscal Dominance Thesis — Research Note",
            "body": (
                "Author argues fiscal dominance is now the primary driver of long-end yields. "
                "Evidence includes persistently widening deficits and sustained Treasury issuance "
                "at elevated supply levels. Central banks are losing credibility as effective "
                "inflation anchors. Historical parallels drawn to the 1940s yield curve control "
                "era when fiscal needs overrode monetary independence. Non-mainstream source "
                "challenges the consensus soft-landing narrative. "
                "What this means for our book: reinforces short duration positioning."
            ),
            "so_what": "Reinforces short duration positioning in our book.",
            "supporting_evidence_ids": ["ev_004"],
        }
    ],
    "contrarian_corner": None,
    "chart_caption": None,
    "warnings": [],
}
from app.synthesis.ranker import RankedBriefContext

if TYPE_CHECKING:
    from app.models import LLMUsage
    from app.settings import Settings


def write_brief(
    ranked_context: RankedBriefContext,
    settings: "Settings",
    run_date: datetime,
    mode: RunMode = RunMode.LIVE,
) -> tuple[BriefDraft, "LLMUsage"]:
    """Call the LLM once and assemble a BriefDraft.

    In sample mode, uses a pre-baked fake response so no API key is needed.
    Returns (draft, usage). Raises LLMResponseError if the model output
    cannot be parsed into BriefWriterOutput.
    """
    if mode == RunMode.SAMPLE:
        client = LLMClient(LLMConfig(model="fake/sample", fake_response=_SAMPLE_FAKE_RESPONSE))
        result = client.generate_structured(
            system_prompt="",
            user_payload={},
            schema=BriefWriterOutput,
        )
        return _assemble_draft(result.output, ranked_context), result.usage

    _validate_credentials(settings)

    prompt = load_prompt("brief_writer")
    llm_cfg = settings.sources.get("llm", {})
    model = llm_cfg.get("synthesis_model", "openai/gpt-4o")
    temperature = float(llm_cfg.get("temperature", 0.2))

    client = LLMClient(LLMConfig(model=model, temperature=temperature, max_tokens=8000))
    payload = _build_payload(ranked_context, settings, run_date)

    result = client.generate_structured(
        system_prompt=prompt.text,
        user_payload=payload,
        schema=BriefWriterOutput,
    )

    draft = _assemble_draft(result.output, ranked_context)
    return draft, result.usage


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------

def _build_payload(
    ctx: RankedBriefContext,
    settings: "Settings",
    run_date: datetime,
) -> dict[str, Any]:
    stale_ids = [
        s.instrument_id for s in ctx.dashboard_rows
        if s.freshness_status == FreshnessStatus.STALE_CACHE
    ]
    return {
        "run_date": run_date.strftime("%Y-%m-%d"),
        "market_snapshots": [_snapshot_dict(s) for s in ctx.top_market_moves],
        "calendar_events": [_calendar_dict(e) for e in ctx.top_calendar_events],
        "proposed_three_things": [_evidence_dict(c) for c in ctx.proposed_three_things],
        "proposed_theme_radar": [_evidence_dict(c) for c in ctx.proposed_theme_radar],
        "proposed_contrarian_seed": (
            _evidence_dict(ctx.proposed_contrarian_corner_seed)
            if ctx.proposed_contrarian_corner_seed else None
        ),
        "portfolio_context": settings.portfolio,
        "theme_config": settings.themes.get("themes", []),
        "stale_instruments": stale_ids,
    }


# ---------------------------------------------------------------------------
# Draft assembly
# ---------------------------------------------------------------------------

def _assemble_draft(
    writer_out: BriefWriterOutput,
    ctx: RankedBriefContext,
) -> BriefDraft:
    three_things = [
        _to_brief_item(item, BriefSection.THREE_THINGS)
        for item in writer_out.three_things
    ]
    radar_items = [
        _to_brief_item(item, BriefSection.THEME_RADAR)
        for item in writer_out.radar_items
    ]
    contrarian = (
        _to_brief_item(writer_out.contrarian_corner, BriefSection.CONTRARIAN_CORNER)
        if writer_out.contrarian_corner else None
    )
    chart = None
    if writer_out.chart_caption:
        chart = ChartSpec(
            title="Chart — see caption",
            caption=writer_out.chart_caption,
            chart_type="line",
            data_source="pipeline",
        )
    return BriefDraft(
        run_metadata={},  # filled by pipeline after RunMetadata is built
        overnight_dashboard=ctx.dashboard_rows,
        three_things=three_things,
        todays_calendar=ctx.top_calendar_events,
        chart=chart,
        radar_items=radar_items,
        contrarian_corner=contrarian,
        warnings=list(writer_out.warnings),
    )


def _to_brief_item(item: WriterItem, section: BriefSection) -> BriefItem:
    return BriefItem(
        section=section,
        headline=item.headline,
        body=item.body,
        so_what=item.so_what,
        supporting_market_ids=item.supporting_market_ids,
        supporting_evidence_ids=item.supporting_evidence_ids,
        confidence=item.confidence,
    )


# ---------------------------------------------------------------------------
# Credential check
# ---------------------------------------------------------------------------

def _validate_credentials(settings: "Settings") -> None:
    if not settings.creds.openai_api_key:
        raise LLMResponseError(
            "OPENAI_API_KEY is required for brief synthesis (including sample mode). "
            "Add it to your .env file."
        )


# ---------------------------------------------------------------------------
# Serialisation helpers — only fields the LLM needs
# ---------------------------------------------------------------------------

def _snapshot_dict(s: Any) -> dict[str, Any]:
    return {
        "instrument_id": s.instrument_id,
        "display_name": s.display_name,
        "asset_class": s.asset_class.value,
        "one_day_change": s.one_day_change,
        "one_day_change_unit": s.one_day_change_unit,
        "one_day_zscore": s.one_day_zscore,
        "threshold_flag": s.threshold_flag,
        "freshness_status": s.freshness_status.value,
        "source": s.source,
    }


def _calendar_dict(e: Any) -> dict[str, Any]:
    return {
        "event_time_hkt": e.event_time_hkt.isoformat(),
        "session": e.session,
        "country_or_region": e.country_or_region,
        "event_name": e.event_name,
        "importance": e.importance,
        "consensus": e.consensus,
        "previous": e.previous,
        "missing_consensus": e.missing_consensus,
        "why_it_matters": e.why_it_matters,
    }


def _evidence_dict(c: Any) -> dict[str, Any]:
    return {
        "id": c.id,
        "title": c.title,
        "source_name": c.source_name,
        "source_type": c.source_type.value,
        "url": c.url,
        "thesis": c.thesis,
        "evidence": c.evidence,
        "macro_relevance": c.macro_relevance,
        "portfolio_relevance": c.portfolio_relevance,
        "tags": c.tags,
    }
