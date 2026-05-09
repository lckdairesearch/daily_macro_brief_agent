"""Portfolio-aware chart planning for the Daily Macro Brief."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from math import sqrt
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from app.data.market import fetch_chart_series
from app.llm.prompt_registry import load_prompt
from app.llm.provider import LLMClient, LLMConfig
from app.models import (
    AssetClass,
    ChartPlan,
    ChartSelectionMethod,
    ChartWindow,
    LLMUsage,
    MarketSnapshot,
)

if TYPE_CHECKING:
    from app.models import BriefDraft
    from app.settings import Settings
    from app.synthesis.ranker import RankedBriefContext


_WINDOW_OBS = {
    ChartWindow.ONE_WEEK: 5,
    ChartWindow.ONE_MONTH: 21,
}
_WINDOW_LABEL = {
    ChartWindow.ONE_DAY: "1-day",
    ChartWindow.ONE_WEEK: "1-week",
    ChartWindow.ONE_MONTH: "1-month",
}


class ChartCandidate(BaseModel):
    """Serializable scored candidate used for shortlist and debugging."""

    candidate_id: str
    window: ChartWindow
    chart_type: str
    instrument_ids: list[str] = Field(default_factory=list)
    title: str
    caption: str
    candidate_family: str
    linked_three_things_index: int | None = None
    score: float
    pattern_strength: float
    portfolio_relevance: float
    brief_linkage: float
    visual_clarity: float
    selection_reason: str


class ChartSelectorOutput(BaseModel):
    """LLM arbitration over a deterministic shortlist."""

    candidate_id: str
    title: str
    caption: str
    selection_reason: str


@dataclass(frozen=True)
class PortfolioHints:
    instrument_ids: set[str]
    position_tokens: set[str]
    sensitivity_tags: set[str]


def select_chart_plan(
    ranked_context: "RankedBriefContext",
    brief_draft: "BriefDraft",
    settings: "Settings",
    as_of: datetime,
    sample_mode: bool = False,
) -> tuple[ChartPlan, list[ChartCandidate], LLMUsage | None]:
    """Select one chart plan from a scored candidate shortlist."""
    snapshots = ranked_context.dashboard_rows
    instrument_ids = _candidate_universe_ids(snapshots)
    series_data = fetch_chart_series(
        instruments=instrument_ids,
        lookback_days=30,
        as_of=as_of,
        settings=settings,
        sample_mode=sample_mode,
    )

    candidates = build_chart_candidates(
        snapshots=snapshots,
        series_data=series_data,
        brief_draft=brief_draft,
        portfolio=settings.portfolio,
    )
    shortlist = shortlist_chart_candidates(candidates)
    usage: LLMUsage | None = None

    can_use_llm = (
        not sample_mode
        and bool(settings.creds.openai_api_key)
        and len(shortlist) > 1
    )
    if can_use_llm:
        plan, usage = _select_with_llm(shortlist, brief_draft, settings)
        return plan, shortlist, usage

    best = shortlist[0]
    return _plan_from_candidate(best, ChartSelectionMethod.DETERMINISTIC_FALLBACK), shortlist, usage


def build_chart_candidates(
    *,
    snapshots: list[MarketSnapshot],
    series_data: dict[str, list[dict]],
    brief_draft: "BriefDraft",
    portfolio: dict,
) -> list[ChartCandidate]:
    """Build deterministic candidate charts from market, brief, and portfolio context."""
    if not snapshots:
        return [
            ChartCandidate(
                candidate_id="fallback_1d",
                window=ChartWindow.ONE_DAY,
                chart_type="bar",
                instrument_ids=[],
                title="Cross-asset daily moves",
                caption="One-day changes across key macro assets.",
                candidate_family="daily_snapshot",
                score=0.0,
                pattern_strength=0.0,
                portfolio_relevance=0.0,
                brief_linkage=0.0,
                visual_clarity=0.0,
                selection_reason="Fallback snapshot because no market snapshots were available.",
            )
        ]

    snapshot_by_id = {snap.instrument_id: snap for snap in snapshots}
    portfolio_hints = _build_portfolio_hints(portfolio, snapshots)
    brief_support = _brief_support_ids(brief_draft)
    candidates: list[ChartCandidate] = [
        _build_one_day_candidate(snapshots, brief_support),
    ]

    available_ids = [iid for iid in _candidate_universe_ids(snapshots) if iid in series_data]
    for window in (ChartWindow.ONE_WEEK, ChartWindow.ONE_MONTH):
        min_obs = _WINDOW_OBS[window]
        for left_id, right_id in combinations(available_ids, 2):
            left_rows = series_data.get(left_id, [])
            right_rows = series_data.get(right_id, [])
            left_slice = _slice_window(left_rows, min_obs)
            right_slice = _slice_window(right_rows, min_obs)
            if len(left_slice) < min_obs or len(right_slice) < min_obs:
                continue
            candidate = _build_pair_candidate(
                left=snapshot_by_id[left_id],
                right=snapshot_by_id[right_id],
                left_rows=left_slice,
                right_rows=right_slice,
                window=window,
                portfolio_hints=portfolio_hints,
                brief_support=brief_support,
            )
            candidates.append(candidate)

    candidates.sort(key=lambda cand: cand.score, reverse=True)
    return candidates


def shortlist_chart_candidates(candidates: list[ChartCandidate]) -> list[ChartCandidate]:
    """Return the final shortlist, preserving top candidates and key fallback paths."""
    if not candidates:
        raise ValueError("shortlist_chart_candidates requires at least one candidate")

    ranked = sorted(candidates, key=lambda cand: cand.score, reverse=True)
    chosen: list[ChartCandidate] = []
    seen: set[str] = set()

    def _push(candidate: ChartCandidate | None) -> None:
        if candidate is None or candidate.candidate_id in seen:
            return
        chosen.append(candidate)
        seen.add(candidate.candidate_id)

    _push(next((cand for cand in ranked if cand.window == ChartWindow.ONE_DAY), None))
    reinforcing = next((cand for cand in ranked if cand.linked_three_things_index is not None), None)
    _push(reinforcing)
    for candidate in ranked:
        if len(chosen) >= 5:
            break
        _push(candidate)
    return chosen


def _select_with_llm(
    shortlist: list[ChartCandidate],
    brief_draft: "BriefDraft",
    settings: "Settings",
) -> tuple[ChartPlan, LLMUsage | None]:
    prompt = load_prompt("chart_selector")
    llm_cfg = settings.sources.get("llm", {})
    model = llm_cfg.get("chart_model") or llm_cfg.get("synthesis_model", "openai/gpt-4o")
    client = LLMClient(LLMConfig(model=model, temperature=0.1, max_tokens=1200))
    payload = {
        "portfolio_context": settings.portfolio,
        "three_things": [
            {
                "headline": item.headline,
                "supporting_market_ids": item.supporting_market_ids,
                "body": item.body,
            }
            for item in brief_draft.three_things
        ],
        "chart_candidates": [candidate.model_dump(mode="json") for candidate in shortlist],
    }
    try:
        result = client.generate_structured(
            system_prompt=prompt.text,
            user_payload=payload,
            schema=ChartSelectorOutput,
        )
    except Exception:
        return _plan_from_candidate(shortlist[0], ChartSelectionMethod.DETERMINISTIC_FALLBACK), None
    by_id = {candidate.candidate_id: candidate for candidate in shortlist}
    selected = by_id.get(result.output.candidate_id)
    if selected is None:
        selected = shortlist[0]
        return _plan_from_candidate(selected, ChartSelectionMethod.DETERMINISTIC_FALLBACK), result.usage

    return ChartPlan(
        window=selected.window,
        chart_type=selected.chart_type,
        instrument_ids=selected.instrument_ids,
        title=(result.output.title or selected.title)[:90],
        caption=(result.output.caption or selected.caption)[:180],
        selection_reason=result.output.selection_reason or selected.selection_reason,
        candidate_family=selected.candidate_family,
        linked_three_things_index=selected.linked_three_things_index,
        selection_method=ChartSelectionMethod.HYBRID_LLM_SHORTLIST,
    ), result.usage


def _plan_from_candidate(candidate: ChartCandidate, method: ChartSelectionMethod) -> ChartPlan:
    return ChartPlan(
        window=candidate.window,
        chart_type=candidate.chart_type,
        instrument_ids=candidate.instrument_ids,
        title=candidate.title,
        caption=candidate.caption,
        selection_reason=candidate.selection_reason,
        candidate_family=candidate.candidate_family,
        linked_three_things_index=candidate.linked_three_things_index,
        selection_method=method,
    )


def _build_one_day_candidate(
    snapshots: list[MarketSnapshot],
    brief_support: dict[str, int],
) -> ChartCandidate:
    diverse = _select_diverse_daily_ids(snapshots)
    linked_idx = _best_linked_index(diverse, brief_support)
    breadth = len({snap.asset_class for snap in snapshots if snap.instrument_id in diverse})
    sig_count = sum(
        1 for snap in snapshots if snap.instrument_id in diverse and _is_significant(snap)
    )
    pattern_strength = min((0.45 * breadth + 0.30 * sig_count) / 3.0, 1.0)
    brief_linkage = 1.0 if linked_idx is not None else 0.45
    visual_clarity = min(0.45 + 0.10 * breadth, 1.0)
    score = (
        0.40 * pattern_strength
        + 0.25 * 0.55
        + 0.20 * brief_linkage
        + 0.15 * visual_clarity
    )
    reason = "Breadth across asset classes is the story, so a one-day cross-asset snapshot is the clearest chart."
    return ChartCandidate(
        candidate_id="daily_snapshot_1d",
        window=ChartWindow.ONE_DAY,
        chart_type="bar",
        instrument_ids=diverse,
        title="Cross-asset daily moves",
        caption="One-day moves across the key assets shaping today’s macro tape.",
        candidate_family="daily_snapshot",
        linked_three_things_index=linked_idx,
        score=score,
        pattern_strength=pattern_strength,
        portfolio_relevance=0.55,
        brief_linkage=brief_linkage,
        visual_clarity=visual_clarity,
        selection_reason=reason,
    )


def _build_pair_candidate(
    *,
    left: MarketSnapshot,
    right: MarketSnapshot,
    left_rows: list[dict],
    right_rows: list[dict],
    window: ChartWindow,
    portfolio_hints: PortfolioHints,
    brief_support: dict[str, int],
) -> ChartCandidate:
    left_returns = _daily_returns(left_rows)
    right_returns = _daily_returns(right_rows)
    left_total = _pct_change(left_rows)
    right_total = _pct_change(right_rows)
    divergence = min(abs(left_total - right_total) / 0.06, 1.0)
    same_direction = left_total * right_total > 0
    opposite_direction = left_total * right_total < 0
    corr = _correlation(left_returns, right_returns)
    reversal = max(_reversal_score(left_returns), _reversal_score(right_returns))
    trend = min((abs(left_total) + abs(right_total)) / 0.08, 1.0)
    co_move = min(max(corr, 0.0) * min(abs(left_total), abs(right_total)) / 0.02, 1.0)

    if reversal >= max(divergence, co_move, trend) and reversal >= 0.45:
        family = "sharp_reversal"
        pattern_strength = reversal
    elif opposite_direction and divergence >= 0.35:
        family = "opposite_direction"
        pattern_strength = min(0.35 + divergence * 0.65, 1.0)
    elif same_direction and co_move >= 0.45:
        family = "co_move"
        pattern_strength = min(0.30 + co_move * 0.70, 1.0)
    elif divergence >= 0.35:
        family = "divergence"
        pattern_strength = min(0.30 + divergence * 0.70, 1.0)
    else:
        family = "trend"
        pattern_strength = trend

    brief_linkage = _pair_brief_linkage(left.instrument_id, right.instrument_id, brief_support)
    portfolio_relevance = _pair_portfolio_relevance(left, right, portfolio_hints)
    visual_clarity = _visual_clarity(left, right, family)
    score = (
        0.40 * pattern_strength
        + 0.25 * portfolio_relevance
        + 0.20 * brief_linkage
        + 0.15 * visual_clarity
    )
    linked_idx = _best_linked_index([left.instrument_id, right.instrument_id], brief_support)
    title = _pair_title(left.display_name, right.display_name, family, window)
    caption = _pair_caption(left.display_name, right.display_name, family, window)
    reason = _pair_reason(left.display_name, right.display_name, family, window, portfolio_relevance, brief_linkage)

    return ChartCandidate(
        candidate_id=f"{window.value}_{left.instrument_id}_{right.instrument_id}",
        window=window,
        chart_type="line",
        instrument_ids=[left.instrument_id, right.instrument_id],
        title=title,
        caption=caption,
        candidate_family=family,
        linked_three_things_index=linked_idx,
        score=score,
        pattern_strength=pattern_strength,
        portfolio_relevance=portfolio_relevance,
        brief_linkage=brief_linkage,
        visual_clarity=visual_clarity,
        selection_reason=reason,
    )


def _candidate_universe_ids(snapshots: list[MarketSnapshot]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for snapshot in snapshots:
        if snapshot.instrument_id in seen:
            continue
        seen.add(snapshot.instrument_id)
        ordered.append(snapshot.instrument_id)
    return ordered


def _slice_window(rows: list[dict], count: int) -> list[dict]:
    return rows[-count:] if len(rows) >= count else rows


def _daily_returns(rows: list[dict]) -> list[float]:
    returns: list[float] = []
    for prev, curr in zip(rows, rows[1:]):
        prev_close = float(prev["close"])
        curr_close = float(curr["close"])
        if prev_close == 0:
            returns.append(0.0)
        else:
            returns.append((curr_close / prev_close) - 1.0)
    return returns


def _pct_change(rows: list[dict]) -> float:
    if len(rows) < 2 or float(rows[0]["close"]) == 0:
        return 0.0
    return float(rows[-1]["close"]) / float(rows[0]["close"]) - 1.0


def _reversal_score(returns: list[float]) -> float:
    if len(returns) < 3:
        return 0.0
    last = returns[-1]
    prior = returns[:-1]
    prior_sum = sum(prior)
    if last == 0 or prior_sum == 0 or last * prior_sum >= 0:
        return 0.0
    return min(abs(last - prior_sum) / 0.03, 1.0)


def _correlation(left: list[float], right: list[float]) -> float:
    n = min(len(left), len(right))
    if n < 2:
        return 0.0
    left = left[-n:]
    right = right[-n:]
    left_mean = sum(left) / n
    right_mean = sum(right) / n
    cov = sum((lx - left_mean) * (rx - right_mean) for lx, rx in zip(left, right))
    left_var = sum((lx - left_mean) ** 2 for lx in left)
    right_var = sum((rx - right_mean) ** 2 for rx in right)
    denom = sqrt(left_var * right_var)
    if denom == 0:
        return 0.0
    return cov / denom


def _build_portfolio_hints(portfolio: dict, snapshots: list[MarketSnapshot]) -> PortfolioHints:
    alias_map = _instrument_alias_map(snapshots)
    instrument_ids: set[str] = set()
    position_tokens: set[str] = set()
    sensitivity_tags: set[str] = set()
    for position in portfolio.get("core_positions", []) + portfolio.get("tactical_overlays", []):
        for raw in position.get("instruments", []):
            norm = _normalize(raw)
            matched = alias_map.get(norm)
            if matched:
                instrument_ids.add(matched)
            position_tokens.update(token for token in norm.split() if len(token) > 2)
        label = _normalize(position.get("label", ""))
        position_tokens.update(token for token in label.split() if len(token) > 2)
        rationale = _normalize(position.get("rationale", ""))
        position_tokens.update(token for token in rationale.split() if len(token) > 3)
        for tag in position.get("sensitivity_tags", []):
            sensitivity_tags.add(_normalize(tag))
    for tag in portfolio.get("macro_sensitivities", []):
        sensitivity_tags.add(_normalize(tag))
    return PortfolioHints(instrument_ids=instrument_ids, position_tokens=position_tokens, sensitivity_tags=sensitivity_tags)


def _instrument_alias_map(snapshots: list[MarketSnapshot]) -> dict[str, str]:
    aliases: dict[str, str] = {
        "gold": "GOLD",
        "silver": "SILVER",
        "copper": "COPPER",
        "us 10y": "US10Y",
        "us 2y": "US2Y",
        "u s 10y": "US10Y",
        "u s 2y": "US2Y",
        "usd jpy": "USDJPY",
        "eur usd": "EURUSD",
        "dxy": "UUP",
        "oil": "WTI",
        "brent": "BRENT",
        "bitcoin": "BTC",
        "vix": "VIX",
        "move": "MOVE",
    }
    for snapshot in snapshots:
        aliases[_normalize(snapshot.instrument_id)] = snapshot.instrument_id
        aliases[_normalize(snapshot.display_name)] = snapshot.instrument_id
        for token in _normalize(snapshot.display_name).replace("(", " ").replace(")", " ").split():
            if len(token) > 2:
                aliases.setdefault(token, snapshot.instrument_id)
    return aliases


def _brief_support_ids(brief_draft: "BriefDraft") -> dict[str, int]:
    support: dict[str, int] = {}
    for idx, item in enumerate(brief_draft.three_things):
        for instrument_id in item.supporting_market_ids:
            support[instrument_id] = min(idx, support.get(instrument_id, idx))
    return support


def _best_linked_index(instrument_ids: list[str], brief_support: dict[str, int]) -> int | None:
    indexes = [brief_support[iid] for iid in instrument_ids if iid in brief_support]
    return min(indexes) if indexes else None


def _pair_brief_linkage(left_id: str, right_id: str, brief_support: dict[str, int]) -> float:
    matches = int(left_id in brief_support) + int(right_id in brief_support)
    if matches == 2:
        return 1.0
    if matches == 1:
        return 0.75
    return 0.35


def _pair_portfolio_relevance(
    left: MarketSnapshot,
    right: MarketSnapshot,
    hints: PortfolioHints,
) -> float:
    ids = {left.instrument_id, right.instrument_id}
    direct_hits = len(ids & hints.instrument_ids)
    if direct_hits == 2:
        return 1.0
    if direct_hits == 1:
        return 0.8

    text = " ".join(
        [
            _normalize(left.instrument_id),
            _normalize(left.display_name),
            _normalize(right.instrument_id),
            _normalize(right.display_name),
            _normalize(left.asset_class.value),
            _normalize(right.asset_class.value),
        ]
    )
    token_hits = sum(1 for token in hints.position_tokens if token in text)
    tag_hits = sum(1 for tag in hints.sensitivity_tags if tag and tag in text)
    if token_hits + tag_hits >= 2:
        return 0.75
    if token_hits + tag_hits == 1:
        return 0.6
    return 0.35


def _visual_clarity(left: MarketSnapshot, right: MarketSnapshot, family: str) -> float:
    score = 0.55
    if left.asset_class != right.asset_class:
        score += 0.20
    if family in {"divergence", "opposite_direction"}:
        score += 0.15
    if family == "co_move":
        score += 0.05
    return min(score, 1.0)


def _pair_title(left_name: str, right_name: str, family: str, window: ChartWindow) -> str:
    family_labels = {
        "sharp_reversal": "reversal",
        "opposite_direction": "push-pull",
        "co_move": "co-move",
        "divergence": "divergence",
        "trend": "trend",
    }
    return f"{left_name} vs {right_name} {_WINDOW_LABEL[window]} {family_labels.get(family, 'trend')}"


def _pair_caption(left_name: str, right_name: str, family: str, window: ChartWindow) -> str:
    if family == "sharp_reversal":
        return f"{_WINDOW_LABEL[window]} view of a developing reversal between {left_name} and {right_name}."
    if family == "opposite_direction":
        return f"{_WINDOW_LABEL[window]} push-pull between {left_name} and {right_name}."
    if family == "co_move":
        return f"{_WINDOW_LABEL[window]} co-move showing how {left_name} and {right_name} are trading together."
    if family == "divergence":
        return f"{_WINDOW_LABEL[window]} divergence between {left_name} and {right_name}."
    return f"{_WINDOW_LABEL[window]} trend comparison for {left_name} and {right_name}."


def _pair_reason(
    left_name: str,
    right_name: str,
    family: str,
    window: ChartWindow,
    portfolio_relevance: float,
    brief_linkage: float,
) -> str:
    notes = [f"{_WINDOW_LABEL[window]} {family.replace('_', ' ')} stands out"]
    if portfolio_relevance >= 0.8:
        notes.append("it is directly tied to the configured portfolio")
    elif portfolio_relevance >= 0.6:
        notes.append("it connects to portfolio themes")
    if brief_linkage >= 0.75:
        notes.append("it reinforces one of the lead written items")
    return f"{left_name} and {right_name}: " + ", ".join(notes) + "."


def _select_diverse_daily_ids(snapshots: list[MarketSnapshot], limit: int = 6) -> list[str]:
    ordered = sorted(
        snapshots,
        key=lambda snap: (
            1 if _is_significant(snap) else 0,
            abs(snap.one_day_zscore) if snap.one_day_zscore is not None else 0.0,
            abs(snap.one_day_change),
        ),
        reverse=True,
    )
    selected: list[str] = []
    seen_classes: set[AssetClass] = set()
    for snapshot in ordered:
        if snapshot.asset_class not in seen_classes:
            selected.append(snapshot.instrument_id)
            seen_classes.add(snapshot.asset_class)
        if len(selected) >= limit:
            return selected
    for snapshot in ordered:
        if snapshot.instrument_id in selected:
            continue
        selected.append(snapshot.instrument_id)
        if len(selected) >= limit:
            break
    return selected


def _is_significant(snapshot: MarketSnapshot) -> bool:
    if snapshot.threshold_flag:
        return True
    return bool(snapshot.one_day_zscore is not None and abs(snapshot.one_day_zscore) >= 1.0)


def _normalize(value: str) -> str:
    return " ".join(value.lower().replace("/", " ").replace("-", " ").replace("_", " ").split())
