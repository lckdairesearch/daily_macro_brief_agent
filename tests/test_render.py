"""Tests for chart generation (Step 11.1)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.data.market import FixtureChartSeriesProvider, fetch_chart_series
from app.models import (
    AssetClass,
    BriefDraft,
    BriefItem,
    BriefSection,
    CalendarEvent,
    ChartPlan,
    ChartSelectionMethod,
    ChartSpec,
    ChartWindow,
    FreshnessStatus,
    MarketSnapshot,
)
from app.render.chart_codegen import (
    build_data_preamble,
    execute_chart_code,
    select_instruments,
)
from app.render.chart_selector import build_chart_candidates, select_chart_plan, shortlist_chart_candidates
from app.render.charts import _hardcoded_fallback

_AS_OF = datetime(2026, 5, 9, 6, 45, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _snapshot(iid: str, display: str, asset_class: AssetClass, change: float, flagged: bool = False) -> MarketSnapshot:
    return MarketSnapshot(
        as_of=_AS_OF,
        instrument_id=iid,
        display_name=display,
        asset_class=asset_class,
        region="US",
        last_price_or_level=100.0,
        one_day_change=change,
        one_day_change_unit="%",
        one_day_zscore=change,
        threshold_flag=flagged,
        source="fixture",
        freshness_status=FreshnessStatus.FRESH,
    )


def _make_draft(supporting_market_ids: list[str] | None = None, with_calendar: bool = False) -> BriefDraft:
    dashboard = [
        _snapshot("SPY", "S&P 500 (proxy)", AssetClass.EQUITY, -1.2),
        _snapshot("VIX", "VIX", AssetClass.VOLATILITY, 8.0, flagged=True),
        _snapshot("MOVE", "MOVE Index", AssetClass.VOLATILITY, 3.0, flagged=True),
        _snapshot("US10Y", "US 10Y Yield", AssetClass.RATES, 5.0, flagged=True),
        _snapshot("GOLD", "Gold (spot)", AssetClass.COMMODITY, 0.5),
    ]
    three_things = []
    if supporting_market_ids:
        three_things = [
            BriefItem(
                section=BriefSection.THREE_THINGS,
                headline="Test headline",
                body="Test body",
                so_what="Test so_what",
                supporting_market_ids=supporting_market_ids,
            )
        ]
    calendar: list[CalendarEvent] = []
    if with_calendar:
        calendar = [
            CalendarEvent(
                event_time_local=datetime(2026, 5, 9, 14, 0, tzinfo=timezone.utc),
                event_time_hkt=datetime(2026, 5, 9, 14, 0, tzinfo=timezone.utc),
                session="US",
                country_or_region="US",
                event_name="FOMC Minutes",
                importance=3,
                brief_importance=3,
                source="fixture",
            )
        ]
    return BriefDraft(
        run_metadata={},
        overnight_dashboard=dashboard,
        three_things=three_things,
        todays_calendar=calendar,
        chart=ChartSpec(
            title="VIX / MOVE stress comparison",
            caption="Volatility spike signals stress",
            chart_type="line",
            data_source="fixture",
        ),
    )


def _make_chart_plan(
    *,
    window: ChartWindow = ChartWindow.ONE_WEEK,
    chart_type: str = "line",
    instrument_ids: list[str] | None = None,
    title: str = "VIX / MOVE stress comparison",
    caption: str = "Volatility spike signals stress",
) -> ChartPlan:
    return ChartPlan(
        window=window,
        chart_type=chart_type,
        instrument_ids=instrument_ids or ["VIX", "MOVE"],
        title=title,
        caption=caption,
        selection_reason="Linked to the lead risk-off theme.",
        candidate_family="divergence",
        linked_three_things_index=0,
        selection_method=ChartSelectionMethod.DETERMINISTIC_FALLBACK,
    )


# ---------------------------------------------------------------------------
# select_instruments
# ---------------------------------------------------------------------------

def test_select_instruments_matches_vix_move_template():
    draft = _make_draft(supporting_market_ids=["VIX", "MOVE"])
    label, instruments = select_instruments(draft)
    assert "VIX" in instruments
    assert "MOVE" in instruments
    assert "stress" in label.lower() or "VIX" in label or "MOVE" in label


def test_select_instruments_uses_threshold_flagged_items():
    draft = _make_draft()  # VIX, MOVE, US10Y are flagged
    label, instruments = select_instruments(draft)
    # vix_move_stress or rates_fx_divergence should match
    assert len(instruments) > 0


def test_select_instruments_fallback_returns_all_dashboard_ids():
    draft = _make_draft(supporting_market_ids=[])
    # Remove flagged status from all dashboard items so nothing scores
    dashboard = [s.model_copy(update={"threshold_flag": False}) for s in draft.overnight_dashboard]
    draft = draft.model_copy(update={"overnight_dashboard": dashboard, "three_things": []})
    label, instruments = select_instruments(draft)
    assert label == "Market overview"
    assert set(instruments) == {"SPY", "VIX", "MOVE", "US10Y", "GOLD"}


# ---------------------------------------------------------------------------
# FixtureChartSeriesProvider
# ---------------------------------------------------------------------------

def test_fixture_provider_returns_rows_for_all_instruments():
    provider = FixtureChartSeriesProvider()
    result = provider.fetch(["SPY", "VIX", "US10Y"], lookback_days=30, as_of=_AS_OF)
    assert set(result.keys()) == {"SPY", "VIX", "US10Y"}


def test_fixture_provider_row_count():
    provider = FixtureChartSeriesProvider()
    result = provider.fetch(["SPY"], lookback_days=30, as_of=_AS_OF)
    assert len(result["SPY"]) == 31  # inclusive of both endpoints


def test_fixture_provider_row_structure():
    provider = FixtureChartSeriesProvider()
    result = provider.fetch(["GOLD"], lookback_days=5, as_of=_AS_OF)
    for row in result["GOLD"]:
        assert "date" in row and "close" in row
        assert isinstance(row["close"], float)
        assert row["close"] > 0


def test_fixture_provider_is_deterministic():
    provider = FixtureChartSeriesProvider()
    r1 = provider.fetch(["SPY"], lookback_days=10, as_of=_AS_OF)
    r2 = provider.fetch(["SPY"], lookback_days=10, as_of=_AS_OF)
    assert r1["SPY"] == r2["SPY"]


def test_fetch_chart_series_sample_mode():
    result = fetch_chart_series(["SPY", "VIX"], lookback_days=30, as_of=_AS_OF, sample_mode=True)
    assert "SPY" in result
    assert "VIX" in result
    assert len(result["SPY"]) == 31


# ---------------------------------------------------------------------------
# build_data_preamble
# ---------------------------------------------------------------------------

def test_build_data_preamble_contains_all_variables():
    series_data = {"SPY": [{"date": "2026-05-01", "close": 510.0}]}
    display_names = {"SPY": "S&P 500 (proxy)"}
    asset_classes = {"SPY": "equity"}
    units = {"SPY": "price"}
    preamble = build_data_preamble("Test chart", series_data, display_names, asset_classes, units, [], "out.png")
    for var in ("dates", "series", "units", "asset_classes", "events", "title", "output_path"):
        assert var in preamble
    assert "matplotlib.use" in preamble
    assert "out.png" in preamble


# ---------------------------------------------------------------------------
# execute_chart_code
# ---------------------------------------------------------------------------

def test_execute_chart_code_success(tmp_path):
    output = str(tmp_path / "test.png")
    code = f"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, ax = plt.subplots()
ax.plot([1, 2, 3], [4, 5, 6])
plt.savefig({output!r}, dpi=72)
plt.close()
"""
    execute_chart_code(code, output)
    assert Path(output).exists()
    assert Path(output).stat().st_size > 0


def test_execute_chart_code_syntax_error_raises():
    with pytest.raises(RuntimeError, match="exited"):
        execute_chart_code("this is not python !!!{}", "/tmp/never.png")


def test_execute_chart_code_missing_savefig_raises(tmp_path):
    output = str(tmp_path / "missing.png")
    code = """
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, ax = plt.subplots()
# intentionally no savefig
plt.close()
"""
    with pytest.raises(RuntimeError, match="PNG not created"):
        execute_chart_code(code, output)


def test_execute_chart_code_timeout_raises(tmp_path):
    output = str(tmp_path / "timeout.png")
    code = "import time; time.sleep(60)"
    with pytest.raises(RuntimeError, match="timed out"):
        execute_chart_code(code, output, timeout=1)


# ---------------------------------------------------------------------------
# _hardcoded_fallback
# ---------------------------------------------------------------------------

def test_hardcoded_fallback_creates_png(tmp_path):
    draft = _make_draft()
    output = str(tmp_path / "fallback.png")
    spec = _hardcoded_fallback(draft, output, _make_chart_plan(window=ChartWindow.ONE_DAY, chart_type="bar"))
    assert Path(output).exists()
    assert Path(output).stat().st_size > 0
    assert spec.file_path == output
    assert spec.code_generated is False
    assert spec.chart_type == "bar"


def test_hardcoded_fallback_uses_draft_caption(tmp_path):
    draft = _make_draft()
    output = str(tmp_path / "cap.png")
    plan = _make_chart_plan(window=ChartWindow.ONE_DAY, chart_type="bar", caption="Volatility spike signals stress")
    spec = _hardcoded_fallback(draft, output, plan)
    assert spec.caption == "Volatility spike signals stress"


# ---------------------------------------------------------------------------
# build_chart (with mocked LLM)
# ---------------------------------------------------------------------------

def _make_fake_viz_code(output_path_var: str = "output_path") -> str:
    return f"""
fig, ax = plt.subplots(figsize=(10, 3.5), facecolor="#FFFFFF")
ax.set_facecolor("#FAFAFA")
date_objs = [datetime.date.fromisoformat(d) for d in dates]
for name, values in series.items():
    ax.plot(date_objs, values[:len(date_objs)], label=name, linewidth=2.0)
ax.legend()
ax.set_title(title, fontsize=12, fontweight="bold", loc="left")
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
plt.tight_layout(pad=1.5)
plt.savefig({output_path_var}, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
plt.close()
"""


def test_build_chart_sample_mode_produces_png(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import app.llm.provider as provider_mod
    from app.render.charts import build_chart

    viz_code = _make_fake_viz_code()

    def _fake_completion(**kwargs):
        return SimpleNamespace(
            model="openai/gpt-4o",
            choices=[SimpleNamespace(message=SimpleNamespace(content=viz_code))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=100, total_tokens=110),
            _hidden_params={},
        )

    monkeypatch.setattr(provider_mod.litellm_compat, "completion", _fake_completion)

    settings = _make_settings()
    draft = _make_draft(supporting_market_ids=["VIX", "MOVE"])
    chart_plan = _make_chart_plan()
    output = str(tmp_path / "chart.png")

    spec = build_chart(
        draft,
        chart_plan=chart_plan,
        settings=settings,
        output_path=output,
        sample_mode=True,
        as_of=_AS_OF,
    )
    assert Path(output).exists()
    assert spec.file_path == output
    assert spec.code_generated is True


def test_build_chart_prefers_chart_plan_caption(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import app.llm.provider as provider_mod
    from app.render.charts import build_chart

    viz_code = _make_fake_viz_code()

    def _fake_completion(**kwargs):
        return SimpleNamespace(
            model="openai/gpt-4o",
            choices=[SimpleNamespace(message=SimpleNamespace(content=viz_code))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=100, total_tokens=110),
            _hidden_params={},
        )

    monkeypatch.setattr(provider_mod.litellm_compat, "completion", _fake_completion)

    settings = _make_settings()
    draft = _make_draft(supporting_market_ids=["VIX", "MOVE"])
    chart_plan = _make_chart_plan(caption="This caption comes from the selected chart plan and should win.")
    output = str(tmp_path / "chart.png")

    spec = build_chart(
        draft,
        chart_plan=chart_plan,
        settings=settings,
        output_path=output,
        sample_mode=True,
        as_of=_AS_OF,
    )
    assert spec.caption == chart_plan.caption


def test_build_chart_falls_back_when_llm_fails(tmp_path, monkeypatch):
    import app.llm.provider as provider_mod
    from app.render.charts import build_chart

    def _bad_completion(**kwargs):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(provider_mod.litellm_compat, "completion", _bad_completion)

    settings = _make_settings()
    draft = _make_draft(supporting_market_ids=["VIX", "MOVE"])
    chart_plan = _make_chart_plan()
    output = str(tmp_path / "fallback.png")

    spec = build_chart(
        draft,
        chart_plan=chart_plan,
        settings=settings,
        output_path=output,
        sample_mode=True,
        as_of=_AS_OF,
    )
    assert Path(output).exists()
    assert spec.code_generated is False


# ---------------------------------------------------------------------------
# Prompt registry — chart_codegen.md is registered and has anti-hallucination text
# ---------------------------------------------------------------------------

def test_chart_codegen_prompt_exists_and_has_no_invention_constraint():
    from app.llm.prompt_registry import clear_prompt_cache, load_prompt
    clear_prompt_cache()
    prompt = load_prompt("chart_codegen")
    assert "Never invent" in prompt.text


def test_chart_codegen_prompt_aligns_one_day_bar_chart_dates():
    from app.llm.prompt_registry import clear_prompt_cache, load_prompt
    clear_prompt_cache()
    prompt = load_prompt("chart_codegen")
    assert "chart_window == \"1w\"" in prompt.text
    assert "chart_window == \"1m\"" in prompt.text
    assert "Do not choose a different window or a different series" in prompt.text
    assert "missing dates across series are acceptable" in prompt.text
    assert "Ignore the `events` variable entirely" in prompt.text


def test_chart_selector_candidates_include_daily_and_reinforcing_pair():
    draft = _make_draft(supporting_market_ids=["VIX", "MOVE"])
    series_data = fetch_chart_series(["SPY", "VIX", "MOVE", "US10Y", "GOLD"], lookback_days=30, as_of=_AS_OF, sample_mode=True)
    candidates = build_chart_candidates(
        snapshots=draft.overnight_dashboard,
        series_data=series_data,
        brief_draft=draft,
        portfolio={
            "core_positions": [
                {"label": "Long Metals", "instruments": ["Gold"], "sensitivity_tags": ["real_rates"]},
            ],
            "tactical_overlays": [],
            "macro_sensitivities": ["real_rates"],
        },
    )
    shortlist = shortlist_chart_candidates(candidates)
    assert any(candidate.window == ChartWindow.ONE_DAY for candidate in shortlist)
    assert any(candidate.linked_three_things_index == 0 for candidate in shortlist if candidate.window != ChartWindow.ONE_DAY)


def test_chart_codegen_prompt_uses_transparent_background_with_faint_line_grid():
    from app.llm.prompt_registry import clear_prompt_cache, load_prompt
    clear_prompt_cache()
    prompt = load_prompt("chart_codegen")
    assert 'facecolor="none"' in prompt.text
    assert "fig.patch.set_alpha(0)" in prompt.text
    assert 'ax.set_facecolor("none")' in prompt.text
    assert "For line charts, keep faint gridlines" in prompt.text
    assert "transparent=True" in prompt.text


def test_chart_codegen_prompt_includes_dual_axis_overlap_guidance():
    from app.llm.prompt_registry import clear_prompt_cache, load_prompt
    clear_prompt_cache()
    prompt = load_prompt("chart_codegen")
    assert "For exactly 2 selected series on dual axes" in prompt.text
    assert "shared dates" in prompt.text
    assert "abs(yl - yr) < 0.04" in prompt.text
    assert "never change the data values" in prompt.text
    assert "Adjust only `ax2` limits asymmetrically" in prompt.text
    assert "at least `0.08` apart" in prompt.text
    assert "padding reaches `15%`" in prompt.text
    assert "np.median" in prompt.text


def test_chart_selector_prompt_exists_and_restricts_choice_to_shortlist():
    from app.llm.prompt_registry import clear_prompt_cache, load_prompt
    clear_prompt_cache()
    prompt = load_prompt("chart_selector")
    assert "Pick exactly one candidate from the supplied shortlist" in prompt.text
    assert "`candidate_id` must match one of the supplied `chart_candidates`" in prompt.text


def test_chart_selector_falls_back_when_llm_selection_errors(monkeypatch):
    draft = _make_draft(supporting_market_ids=["VIX", "MOVE"])
    settings = _make_settings()
    settings.portfolio = {
        "core_positions": [],
        "tactical_overlays": [],
        "macro_sensitivities": [],
    }
    ranked_context = SimpleNamespace(dashboard_rows=draft.overnight_dashboard)

    def fake_fetch_chart_series(*, instruments, lookback_days, as_of, settings, sample_mode):
        return fetch_chart_series(
            instruments,
            lookback_days=lookback_days,
            as_of=as_of,
            sample_mode=True,
        )

    def fake_generate_structured(self, *, system_prompt, user_payload, schema):
        raise RuntimeError("insufficient_quota")

    monkeypatch.setattr("app.render.chart_selector.fetch_chart_series", fake_fetch_chart_series)
    monkeypatch.setattr("app.render.chart_selector.LLMClient.generate_structured", fake_generate_structured)

    plan, shortlist, usage = select_chart_plan(
        ranked_context=ranked_context,
        brief_draft=draft,
        settings=settings,
        as_of=_AS_OF,
        sample_mode=False,
    )

    assert usage is None
    assert len(shortlist) > 1
    assert plan.selection_method == ChartSelectionMethod.DETERMINISTIC_FALLBACK
    assert plan.title == shortlist[0].title


# ---------------------------------------------------------------------------
# Email render context
# ---------------------------------------------------------------------------

def test_dashboard_render_row_uses_significance_and_five_day_arrow():
    from app.render.email import build_render_context

    settings = _make_settings()
    draft = _make_draft().model_copy(
        update={
            "overnight_dashboard": [
                _snapshot("US10Y", "US 10Y Yield", AssetClass.RATES, 5.0, flagged=True).model_copy(
                    update={
                        "last_price_or_level": 4.45,
                        "one_day_change_unit": "bps",
                        "five_day_change": 12.0,
                        "five_day_change_unit": "bps",
                    }
                )
            ]
        }
    )
    ctx = build_render_context(draft, settings, vol_params={"US10Y": {"sd_1d": 4.0, "unit": "bps"}})
    row = ctx["dashboard_rows"][0]
    assert row["change_class"] == "up sig-move"
    assert row["trend_arrow"] == "↗"
    assert row["trend_class"] == "up"


def test_dashboard_render_row_flattens_small_five_day_move():
    from app.render.email import build_render_context

    settings = _make_settings()
    draft = _make_draft().model_copy(
        update={
            "overnight_dashboard": [
                _snapshot("SPY", "S&P 500", AssetClass.EQUITY, 0.1).model_copy(
                    update={"five_day_change": 0.2, "five_day_change_unit": "%"}
                )
            ]
        }
    )
    ctx = build_render_context(draft, settings, vol_params={"SPY": {"sd_1d": 1.0, "unit": "%"}})
    row = ctx["dashboard_rows"][0]
    assert row["change_class"] == "up"
    assert row["trend_arrow"] == "→"
    assert row["trend_class"] == "flat"


def test_build_render_context_filters_dashboard_to_core_plus_significant_extras():
    from app.render.email import build_render_context

    settings = _make_settings()
    dashboard = [
        _snapshot("SPY", "S&P 500 (proxy)", AssetClass.EQUITY, -1.2),
        _snapshot("US10Y", "US 10Y Yield", AssetClass.RATES, 5.0, flagged=True),
        _snapshot("USDJPY", "USD/JPY", AssetClass.FX, 0.3),
        _snapshot("GOLD", "Gold (spot)", AssetClass.COMMODITY, 0.5),
        _snapshot("WTI", "WTI (front-month)", AssetClass.COMMODITY, -0.2),
        _snapshot("VIX", "VIX", AssetClass.VOLATILITY, 8.0, flagged=True),
        _snapshot("QQQ", "Nasdaq 100 (proxy)", AssetClass.EQUITY, -2.1, flagged=True),
        _snapshot("US2Y", "US 2Y Yield", AssetClass.RATES, 3.5, flagged=True),
        _snapshot("MOVE", "MOVE Index", AssetClass.VOLATILITY, 3.0, flagged=True),
        _snapshot("BRENT", "Brent (front-month)", AssetClass.COMMODITY, -2.5, flagged=True),
        _snapshot("EURUSD", "EUR/USD", AssetClass.FX, 0.1),
    ]
    draft = _make_draft().model_copy(update={"overnight_dashboard": dashboard})

    ctx = build_render_context(draft, settings, vol_params={})

    assert [row["asset"] for row in ctx["dashboard_rows"]] == [
        "S&P 500 (proxy)",
        "US 10Y Yield",
        "USD/JPY",
        "Gold (spot)",
        "WTI (front-month)",
        "VIX",
        "US 2Y Yield",
        "MOVE Index",
        "Brent (front-month)",
        "Nasdaq 100 (proxy)",
    ]


def test_build_render_context_does_not_duplicate_significant_core_assets():
    from app.render.email import build_render_context

    settings = _make_settings()
    draft = _make_draft()
    ctx = build_render_context(draft, settings, vol_params={})
    assets = [row["asset"] for row in ctx["dashboard_rows"]]

    assert assets.count("VIX") == 1
    assert assets.count("US 10Y Yield") == 1


def test_build_render_context_pairs_calendar_events_for_two_column_layout():
    from app.render.email import build_render_context

    settings = _make_settings()
    second = CalendarEvent(
        event_time_local=datetime(2026, 5, 9, 15, 0, tzinfo=timezone.utc),
        event_time_hkt=datetime(2026, 5, 9, 15, 0, tzinfo=timezone.utc),
        session="US",
        country_or_region="US",
        event_name="Jobless Claims",
        importance=2,
        brief_importance=2,
        source="fixture",
    )
    third = CalendarEvent(
        event_time_local=datetime(2026, 5, 9, 16, 0, tzinfo=timezone.utc),
        event_time_hkt=datetime(2026, 5, 9, 16, 0, tzinfo=timezone.utc),
        session="US",
        country_or_region="US",
        event_name="Retail Sales",
        importance=3,
        brief_importance=3,
        source="fixture",
    )
    draft = _make_draft(with_calendar=True).model_copy(
        update={"todays_calendar": _make_draft(with_calendar=True).todays_calendar + [second, third]}
    )

    ctx = build_render_context(draft, settings, vol_params={})
    assert len(ctx["calendar_event_rows"]) == 2
    assert ctx["calendar_event_rows"][0][0]["event_name"] == "FOMC Minutes"
    assert ctx["calendar_event_rows"][1][0]["event_name"] == "Jobless Claims"
    assert ctx["calendar_event_rows"][0][1]["event_name"] == "Retail Sales"
    assert ctx["calendar_event_rows"][1][1] is None


def test_render_brief_excludes_filtered_dashboard_assets(tmp_path):
    from app.render.email import render_brief

    settings = _make_settings()
    dashboard = [
        _snapshot("SPY", "S&P 500 (proxy)", AssetClass.EQUITY, -1.2),
        _snapshot("US10Y", "US 10Y Yield", AssetClass.RATES, 5.0, flagged=True),
        _snapshot("USDJPY", "USD/JPY", AssetClass.FX, 0.3),
        _snapshot("GOLD", "Gold (spot)", AssetClass.COMMODITY, 0.5),
        _snapshot("WTI", "WTI (front-month)", AssetClass.COMMODITY, -0.2),
        _snapshot("VIX", "VIX", AssetClass.VOLATILITY, 8.0, flagged=True),
        _snapshot("QQQ", "Nasdaq 100 (proxy)", AssetClass.EQUITY, -2.1, flagged=True),
        _snapshot("EURUSD", "EUR/USD", AssetClass.FX, 0.1),
    ]
    draft = _make_draft().model_copy(update={"overnight_dashboard": dashboard})

    paths = render_brief(draft, settings, output_dir=tmp_path, vol_params={})
    html = Path(paths["html"]).read_text(encoding="utf-8")
    text = Path(paths["text"]).read_text(encoding="utf-8")

    assert "Nasdaq 100 (proxy)" in html
    assert "Nasdaq 100 (proxy)" in text
    assert "EUR/USD" not in html
    assert "EUR/USD" not in text


def test_fixture_chart_series_provider_supports_fez():
    provider = FixtureChartSeriesProvider()
    result = provider.fetch(["FEZ"], lookback_days=30, as_of=_AS_OF)

    assert "FEZ" in result
    assert len(result["FEZ"]) == 31


def test_render_brief_writes_html_and_text(tmp_path):
    from app.render.email import render_brief

    settings = _make_settings()
    chart_path = tmp_path / "sample_chart.png"
    chart_path.write_bytes(b"fake png")
    draft = _make_draft().model_copy(
        update={
            "book_impact": "Higher yields support the short-duration sleeve.",
            "chart": ChartSpec(
                title="Test chart",
                caption="Chart caption.",
                chart_type="line",
                data_source="fixture",
                file_path=str(chart_path),
            ),
        }
    )
    paths = render_brief(draft, settings, output_dir=tmp_path, vol_params={})
    html = Path(paths["html"]).read_text(encoding="utf-8")
    text = Path(paths["text"]).read_text(encoding="utf-8")
    assert "Overnight Book Impact" in html
    assert "Higher yields support" in html
    assert "cid:chart@brief" in html
    assert str(chart_path) in text
    assert "OVERNIGHT BOOK IMPACT" in text


def test_render_brief_calendar_uses_two_column_table_and_inline_marker(tmp_path):
    from app.render.email import render_brief

    settings = _make_settings()
    extra_event = CalendarEvent(
        event_time_local=datetime(2026, 5, 9, 15, 0, tzinfo=timezone.utc),
        event_time_hkt=datetime(2026, 5, 9, 15, 0, tzinfo=timezone.utc),
        session="US",
        country_or_region="US",
        event_name="Jobless Claims",
        importance=2,
        brief_importance=2,
        source="fixture",
    )
    draft = _make_draft(with_calendar=True).model_copy(
        update={"todays_calendar": _make_draft(with_calendar=True).todays_calendar + [extra_event]}
    )

    paths = render_brief(draft, settings, output_dir=tmp_path, vol_params={})
    html = Path(paths["html"]).read_text(encoding="utf-8")

    assert 'class="calendar-table"' in html
    assert "All times indicated are in HKT." in html
    assert '<span class="cal-marker">●</span>' in html
    assert "::before" not in html


def test_runtime_template_sets_explicit_calendar_event_font_size():
    template = Path("app/render/templates/brief_template.html").read_text(encoding="utf-8")
    assert ".cal-event" in template
    assert "font-size: 13px" in template


def test_warnings_do_not_render_in_html_or_text(tmp_path):
    from app.render.email import render_brief

    settings = _make_settings()
    draft = _make_draft().model_copy(
        update={
            "warnings": ["first warning", "second warning"],
            "run_metadata": {"warnings": ["third warning"]},
        }
    )
    paths = render_brief(draft, settings, output_dir=tmp_path, vol_params={})
    html = Path(paths["html"]).read_text(encoding="utf-8")
    text = Path(paths["text"]).read_text(encoding="utf-8")

    assert "Warnings" not in html
    assert "first warning" not in html
    assert "third warning" not in html
    assert "WARNINGS" not in text
    assert "first warning" not in text
    assert "third warning" not in text


def test_theme_radar_topic_renders_before_title(tmp_path):
    from app.render.email import render_brief

    settings = _make_settings()
    radar = BriefItem(
        section=BriefSection.THEME_RADAR,
        headline="Research headline",
        body="Author argues that fiscal dominance is now the primary driver of long yields. "
             "Evidence includes widening deficits and sustained Treasury issuance. "
             "Central banks are losing credibility as inflation anchors.",
        so_what="What this means for our book: reinforces short duration thesis.",
        supporting_evidence_ids=["ev_001"],
        source_name="Research Source",
        source_url="https://example.com/research",
        topic_label="Rates",
    )
    draft = _make_draft().model_copy(update={"radar_items": [radar]})
    paths = render_brief(draft, settings, output_dir=tmp_path, vol_params={})
    html = Path(paths["html"]).read_text(encoding="utf-8")

    assert "radar-source" not in html
    assert "radar-topic" in html
    assert html.index("Rates") < html.index("Research headline")


def test_theme_radar_uses_consistent_so_what_label(tmp_path):
    from app.render.email import render_brief

    settings = _make_settings()
    radar = BriefItem(
        section=BriefSection.THEME_RADAR,
        headline="Research headline",
        body="Author argues that fiscal dominance is now the primary driver of long yields. "
             "Evidence includes widening deficits and sustained Treasury issuance. "
             "Central banks are losing credibility as inflation anchors.",
        so_what="What this means for our book: reinforces short duration thesis.",
        supporting_evidence_ids=["ev_001"],
        topic_label="Rates",
    )
    draft = _make_draft().model_copy(update={"radar_items": [radar]})
    paths = render_brief(draft, settings, output_dir=tmp_path, vol_params={})
    html = Path(paths["html"]).read_text(encoding="utf-8")
    text = Path(paths["text"]).read_text(encoding="utf-8")

    assert "So what: reinforces short duration thesis." in html
    assert "What this means for our book:" not in html
    assert "So what: reinforces short duration thesis." in text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings():
    from types import SimpleNamespace
    return SimpleNamespace(
        sources={"llm": {"synthesis_model": "openai/gpt-4o"}},
        creds=SimpleNamespace(
            openai_api_key="test-key",
            alpha_vantage_api_key=None,
            databento_api_key=None,
            fred_api_key=None,
        ),
        app=SimpleNamespace(
            output_dir="outputs",
            dashboard_core_instruments=["SPY", "US10Y", "USDJPY", "GOLD", "WTI", "VIX"],
            dashboard_max_extra_movers=4,
        ),
    )
