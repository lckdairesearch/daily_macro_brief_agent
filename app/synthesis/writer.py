"""Brief writer. Calls LiteLLM once with grounded ranked context to produce BriefDraft.

The LLM writes only the synthesis sections (three_things, radar_items,
contrarian_corner). Dashboard rows and calendar events are pipeline pass-throughs
merged in _assemble_draft().
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
    FreshnessStatus,
    RunMode,
    SourceType,
    WriterItem,
)

# Pre-baked fixture brief for sample mode — avoids LLM call, no credentials needed.
_SAMPLE_FAKE_RESPONSE: dict = {
    "book_impact": (
        "Overnight price action leans toward a benign liquidity backdrop for risk "
        "assets while keeping the structural bear case for long-end Treasuries "
        "intact. That mix supports the short long-term US duration position and "
        "keeps the metals complex thesis alive rather than invalidated."
    ),
    "three_things": [
        {
            "headline": "Liquidity eased, but duration pressure remains",
            "body": (
                "QQQ rose 2.3441%, SPY gained 0.8256%, MOVE fell 6.9075%, and US 10Y "
                "and US 2Y yields both rose 5 bps. Fed H.4.1 helps reconcile that mix: "
                "reserve balances rose by 113,989 million dollars as the TGA fell "
                "104,168 million, easing near-term liquidity even as MBS runoff "
                "continued. That is supportive for risk without removing supply and "
                "term-premium pressure on rates."
            ),
            "so_what": (
                "Keeps the short long-term US duration position aligned with today's "
                "tape while reducing immediate stress on the broader risk backdrop."
            ),
            "supporting_market_ids": ["QQQ", "SPY", "MOVE", "US10Y", "US2Y"],
            "supporting_evidence_ids": ["ev_dfb73667"],
        },
        {
            "headline": "Market is trading a higher long-end regime",
            "body": (
                "Nasdaq strength alongside higher Treasury yields and lower rate vol "
                "fits Keyrock's argument that markets are shifting from a Fed-centric "
                "regime to one driven by supply, sticky inflation, and rebuilding term "
                "premium. The note explicitly frames higher long-end yields as a "
                "supply-and-inflation repricing rather than a simple policy-rate story."
            ),
            "so_what": (
                "Adds near-term conviction to the short long-term US duration position "
                "into the next rates repricing while keeping the metals complex bid intact."
            ),
            "supporting_market_ids": ["QQQ", "US10Y", "MOVE"],
            "supporting_evidence_ids": ["ev_211cb61b"],
        },
        {
            "headline": "Payrolls set today's real macro test",
            "body": (
                "The key event is 20:30 HKT US payrolls: Nonfarm Payrolls consensus is "
                "65K versus 178K prior, unemployment is seen at 4.3%, and Average "
                "Hourly Earnings at 0.3% month on month versus 0.2% prior. After "
                "yields rose and rate vol fell overnight, this release is the clearest "
                "near-term test of whether growth is slowing fast enough to challenge "
                "the book's structural duration view."
            ),
            "so_what": (
                "Leaves the short long-term US duration position exposed to today's "
                "payrolls surprise and puts the currency diversification overlay in play."
            ),
            "supporting_market_ids": ["US10Y", "US2Y", "MOVE", "EURUSD", "UUP"],
            "supporting_evidence_ids": [],
        }
    ],
    "radar_items": [
        {
            "headline": "Balance-sheet plumbing is easing conditions at the margin",
            "body": (
                "The Fed's H.4.1 is more useful than the headline balance-sheet shrink "
                "suggests. Securities held outright fell only 1,624 million dollars on "
                "the week, but the important move was in liabilities: reserve balances "
                "rose 113,989 million while the Treasury General Account fell 104,168 "
                "million. At the same time, MBS holdings declined 6,981 million, so QT "
                "is still running through mortgage runoff. The author's point is that "
                "this mix can support near-term risk appetite and lower rate vol without "
                "changing the medium-term supply burden facing duration."
            ),
            "so_what": (
                "Argues for staying with the structural short long-term US duration "
                "view even if the next few sessions still look risk-friendly."
            ),
            "supporting_evidence_ids": ["ev_dfb73667"],
        },
        {
            "headline": "The regime shift is from policy path to term premium",
            "body": (
                "Keyrock's thesis is that the market is no longer primarily trading the "
                "next Fed move; it is repricing the long end around supply, sticky "
                "inflation, and capital-intensive AI buildout. The note backs that with "
                "March PCE at 0.7% month on month and 3.5% year on year headline, core "
                "at 0.3% month on month and 3.2% year on year, and a Fed hold described "
                "as unusually split. The author argues gold weakness is tactical and "
                "that higher long yields now reflect rebuilding term premium more than "
                "changing cut expectations."
            ),
            "so_what": (
                "Adds medium-term conviction to the fiscal-dominance case behind the "
                "short long-term US duration position and keeps the long metals complex theme intact."
            ),
            "supporting_evidence_ids": ["ev_211cb61b"],
        },
        {
            "headline": "Grains are not delivering a fresh inflation impulse",
            "body": (
                "The agriculture note is useful mainly as a rejection signal. Soybeans "
                "were reported down 10 cents to $11.84 3/4, corn export sales at 1.362 "
                "million metric tons were within trade guesses and slightly below the "
                "four-week average, and wheat export sales of 78,800 metric tons were "
                "down 45% from the four-week average. That is not evidence of a new "
                "food-inflation shock. The author's underlying message is that current "
                "grain data look mixed to soft rather than supply-constrained."
            ),
            "so_what": (
                "Tempers the medium-term inflation case for the agriculture_long "
                "position without overturning the longer-term food security theme."
            ),
            "supporting_evidence_ids": ["ev_2685ad3d"],
        }
    ],
    "contrarian_corner": {
        "headline": "Lower rate vol may be masking structural duration risk",
        "body": (
            "The market is enjoying easier liquidity signals now, but that may be "
            "obscuring the more important balance-sheet composition story. The Fed "
            "data show bills up 242.439 billion dollars year over year, Treasury "
            "holdings up 218.058 billion, and MBS down 191.885 billion, while "
            "reverse repos are also down sharply. Worth watching because falling "
            "MOVE and firmer equities can coexist with a still-unfriendly backdrop "
            "for long-end Treasuries if supply absorption and term premium keep "
            "doing the heavy lifting."
        ),
        "so_what": None,
        "supporting_evidence_ids": ["ev_e568f062"],
    },
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
        "available_x_evidence": [
            _evidence_dict(c)
            for c in ctx.ranked_evidence_cards
            if c.source_type == SourceType.SOCIAL
        ],
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
    evidence_by_id = {card.id: card for card in ctx.ranked_evidence_cards}
    three_things = [
        _to_brief_item(item, BriefSection.THREE_THINGS, evidence_by_id)
        for item in writer_out.three_things
    ]
    radar_items = [
        _to_brief_item(item, BriefSection.THEME_RADAR, evidence_by_id)
        for item in writer_out.radar_items
    ]
    contrarian = (
        _to_brief_item(
            writer_out.contrarian_corner,
            BriefSection.CONTRARIAN_CORNER,
            evidence_by_id,
        )
        if writer_out.contrarian_corner else None
    )
    return BriefDraft(
        run_metadata={},  # filled by pipeline after RunMetadata is built
        book_impact=writer_out.book_impact,
        overnight_dashboard=ctx.dashboard_rows,
        three_things=three_things,
        todays_calendar=ctx.top_calendar_events,
        chart=None,
        radar_items=radar_items,
        contrarian_corner=contrarian,
        warnings=list(writer_out.warnings),
    )


def _to_brief_item(
    item: WriterItem,
    section: BriefSection,
    evidence_by_id: dict[str, Any] | None = None,
) -> BriefItem:
    source = None
    if evidence_by_id:
        for evidence_id in item.supporting_evidence_ids:
            source = evidence_by_id.get(evidence_id)
            if source is not None:
                break
    return BriefItem(
        section=section,
        headline=item.headline,
        body=item.body,
        so_what=item.so_what,
        supporting_market_ids=item.supporting_market_ids,
        supporting_evidence_ids=item.supporting_evidence_ids,
        source_name=getattr(source, "source_name", None),
        source_url=getattr(source, "url", None),
        source_type=getattr(source, "source_type", None),
        topic_label=_derive_topic_label(item, source),
        confidence=item.confidence,
    )


def _derive_topic_label(item: WriterItem, source: Any | None) -> str | None:
    text_parts = [
        item.headline,
        item.body,
        " ".join(item.supporting_market_ids),
    ]
    if source is not None:
        text_parts.extend([
            getattr(source, "title", "") or "",
            getattr(source, "thesis", "") or "",
            " ".join(getattr(source, "tags", []) or []),
        ])
    text = " ".join(text_parts).lower()

    topic_rules = [
        ("Metals", ("gold", "silver", "copper", "metals", "metal")),
        ("Rates", ("treasury", "duration", "yield", "yields", "term premium", "fiscal")),
        ("Monetary Policy", ("fed", "fomc", "ecb", "central bank", "monetary policy")),
        ("Energy", ("oil", "crude", "brent", "wti", "hormuz", "energy")),
        ("Agriculture", ("food", "fertilizer", "agriculture", "grain", "grains", "crop")),
        ("FX", ("usd", "dollar", "currency", "yen", "euro", "reserve")),
        ("Credit", ("credit", "oas", "spread", "spreads", "default")),
    ]
    for label, tokens in topic_rules:
        if any(token in text for token in tokens):
            return label
    return None


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
