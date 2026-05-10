"""Tests for chart generation (Step 11.1)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.data.market import FixtureChartSeriesProvider, fetch_chart_series, get_instrument_meta
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
    SourceType,
)
from app.render.chart_codegen import (
    _build_plotting_context,
    build_data_preamble,
    execute_chart_code,
    select_instruments,
)
from app.render.chart_windowing import (
    build_window_ticks,
    clip_rows_to_window,
    resolve_shared_window,
    slice_rows_by_calendar_window,
    split_contiguous_segments,
)
from app.render.chart_selector import build_chart_candidates, select_chart_plan, shortlist_chart_candidates
from app.render.charts import _axis_policy_for_pair, _axis_unit_label, _hardcoded_fallback

_AS_OF = datetime(2026, 5, 9, 6, 45, tzinfo=timezone.utc)
_REFERENCE_RUN_DIR = Path("outputs/dry-runs/2026-05-08/20260510_113122")
_REFERENCE_SERIES_FIXTURE = Path("tests/fixtures/chart_series_reference_20260510_113122_us10y_copper.json")


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
    render_family: str | None = None,
) -> ChartPlan:
    return ChartPlan(
        window=window,
        chart_type=chart_type,
        instrument_ids=instrument_ids or ["VIX", "MOVE"],
        title=title,
        caption=caption,
        selection_reason="Linked to the lead risk-off theme.",
        candidate_family="divergence",
        render_family=render_family,
        linked_three_things_index=0,
        selection_method=ChartSelectionMethod.DETERMINISTIC_FALLBACK,
    )


def _load_reference_chart_plan() -> dict:
    return json.loads((_REFERENCE_RUN_DIR / "chart_plan.json").read_text(encoding="utf-8"))


def _load_reference_series_fixture() -> dict[str, list[dict]]:
    return json.loads(_REFERENCE_SERIES_FIXTURE.read_text(encoding="utf-8"))


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
    for var in (
        "dates",
        "series",
        "plot_rows",
        "series_segments",
        "prefer_dual_axis",
        "units",
        "asset_classes",
        "events",
        "title",
        "output_path",
    ):
        assert var in preamble
    assert "matplotlib.use" in preamble
    assert "out.png" in preamble


def test_reference_run_fixture_matches_selected_chart_plan():
    chart_plan = _load_reference_chart_plan()

    assert chart_plan["window"] == "1w"
    assert chart_plan["chart_type"] == "line"
    assert chart_plan["instrument_ids"] == ["US10Y", "COPPER"]
    assert chart_plan["title"] == "Copper rallies as 10Y yields rise"


def test_calendar_window_uses_observation_dates_not_arrival_order():
    rows = [
        {"date": "2026-05-07", "close": 4.41},
        {"date": "2026-05-01", "close": 4.39},
        {"date": "2026-05-06", "close": 4.36},
        {"date": "2026-05-04", "close": 4.45},
        {"date": "2026-05-05", "close": 4.43},
    ]

    clipped = slice_rows_by_calendar_window(rows, calendar_days=7)

    assert [row["date"] for row in clipped] == [
        "2026-05-01",
        "2026-05-04",
        "2026-05-05",
        "2026-05-06",
        "2026-05-07",
    ]


def test_reference_fixture_clips_to_exact_one_week_calendar_span():
    series_data = _load_reference_series_fixture()

    clipped = slice_rows_by_calendar_window(series_data["US10Y"], calendar_days=7)

    assert [row["date"] for row in clipped] == [
        "2026-05-01",
        "2026-05-04",
        "2026-05-05",
        "2026-05-06",
        "2026-05-07",
    ]


def test_shared_window_anchors_to_latest_observation_across_selected_pair():
    series_data = {
        "US10Y": [
            {"date": "2026-04-30", "close": 4.39},
            {"date": "2026-05-07", "close": 4.41},
        ],
        "COPPER": [
            {"date": "2026-04-29", "close": 5.92},
            {"date": "2026-05-06", "close": 6.08},
        ],
    }

    window_start, window_end = resolve_shared_window(series_data, calendar_days=30)
    clipped = clip_rows_to_window(series_data["COPPER"], window_start, window_end)

    assert window_start.isoformat() == "2026-04-08"
    assert window_end.isoformat() == "2026-05-07"
    assert [row["date"] for row in clipped] == ["2026-04-29", "2026-05-06"]


def test_plotting_context_splits_weekday_gap_into_two_segments():
    series_data = {
        "US10Y": [
            {"date": "2026-05-01", "close": 4.4},
            {"date": "2026-05-04", "close": 4.45},
        ]
    }
    display_names = {"US10Y": "US 10Y Yield"}

    ctx = _build_plotting_context(series_data, display_names, window_days=7)

    assert ctx["plot_rows"]["US 10Y Yield"] == series_data["US10Y"]
    assert ctx["series_segments"]["US 10Y Yield"] == [
        [{"date": "2026-05-01", "close": 4.4}],
        [{"date": "2026-05-04", "close": 4.45}],
    ]


def test_segment_rows_preserves_calendar_gap_between_business_days():
    rows = [
        {"date": "2026-05-01", "close": 4.39},
        {"date": "2026-05-04", "close": 4.45},
        {"date": "2026-05-05", "close": 4.43},
    ]

    segments = split_contiguous_segments(rows)

    assert segments == [
        [{"date": "2026-05-01", "close": 4.39}],
        [
            {"date": "2026-05-04", "close": 4.45},
            {"date": "2026-05-05", "close": 4.43},
        ],
    ]


def test_month_window_ticks_use_mondays_and_optional_first_day():
    ticks = build_window_ticks(date(2026, 4, 8), date(2026, 5, 7), daily=False)

    assert [tick.isoformat() for tick in ticks] == [
        "2026-04-08",
        "2026-04-13",
        "2026-04-20",
        "2026-04-27",
        "2026-05-04",
    ]


def test_month_window_ticks_skip_first_day_when_monday_is_next_day():
    ticks = build_window_ticks(date(2026, 4, 12), date(2026, 5, 12), daily=False)

    assert [tick.isoformat() for tick in ticks][:2] == [
        "2026-04-13",
        "2026-04-20",
    ]


def test_axis_policy_for_mixed_units_uses_dual_axis():
    series_data = _load_reference_series_fixture()
    plot_rows = {
        "US10Y": slice_rows_by_calendar_window(series_data["US10Y"], calendar_days=7),
        "COPPER": slice_rows_by_calendar_window(series_data["COPPER"], calendar_days=7),
    }

    policy = _axis_policy_for_pair(
        plot_rows,
        units={"US10Y": "yield", "COPPER": "price"},
        instrument_ids=["US10Y", "COPPER"],
    )

    assert policy["prefer_dual_axis"] is True
    assert policy["reason"] == "different_units"


def test_axis_unit_label_prefers_display_units_over_generic_price():
    assert _axis_unit_label("US10Y", AssetClass.RATES, "yield") == "%"
    assert _axis_unit_label("HY_OAS", AssetClass.CREDIT, "spread") == "bps"
    assert _axis_unit_label("COPPER", AssetClass.COMMODITY, "price") == "USD"
    assert _axis_unit_label("USDJPY", AssetClass.FX, "price") == "JPY per USD"


def test_fixed_instrument_catalog_has_level_units_for_chartable_assets():
    for instrument_id in ("SPY", "QQQ", "FEZ", "UUP", "USDJPY", "EURUSD", "USDCNH", "US10Y", "DE10Y", "COPPER", "HY_OAS", "VIX"):
        meta = get_instrument_meta(instrument_id)
        assert meta is not None
        assert meta["level_unit_label"]


def test_fixed_instrument_catalog_marks_proxy_instruments():
    assert get_instrument_meta("SPY")["is_proxy"] is True
    assert get_instrument_meta("QQQ")["is_proxy"] is True
    assert get_instrument_meta("FEZ")["is_proxy"] is True
    assert get_instrument_meta("UUP")["is_proxy"] is True
    assert get_instrument_meta("US10Y")["is_proxy"] is False


def test_axis_policy_allows_single_axis_for_close_same_unit_pair():
    plot_rows = {
        "LEFT": [
            {"date": "2026-05-01", "close": 100.0},
            {"date": "2026-05-02", "close": 101.0},
            {"date": "2026-05-03", "close": 102.0},
        ],
        "RIGHT": [
            {"date": "2026-05-01", "close": 100.05},
            {"date": "2026-05-02", "close": 101.05},
            {"date": "2026-05-03", "close": 102.05},
        ],
    }

    policy = _axis_policy_for_pair(
        plot_rows,
        units={"LEFT": "price", "RIGHT": "price"},
        instrument_ids=["LEFT", "RIGHT"],
    )

    assert policy["prefer_dual_axis"] is False


def test_axis_policy_uses_dual_axis_when_same_unit_pair_is_visually_squashed():
    plot_rows = {
        "LEFT": [
            {"date": "2026-05-01", "close": 50.0},
            {"date": "2026-05-02", "close": 51.0},
            {"date": "2026-05-03", "close": 52.0},
        ],
        "RIGHT": [
            {"date": "2026-05-01", "close": 100.0},
            {"date": "2026-05-02", "close": 100.2},
            {"date": "2026-05-03", "close": 100.4},
        ],
    }

    policy = _axis_policy_for_pair(
        plot_rows,
        units={"LEFT": "price", "RIGHT": "price"},
        instrument_ids=["LEFT", "RIGHT"],
    )

    assert policy["prefer_dual_axis"] is True


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
# build_chart
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
    from app.render.charts import build_chart
    monkeypatch.setattr("app.render.charts.generate_chart_code", lambda **kwargs: (_ for _ in ()).throw(AssertionError("LLM path should not be used for standard pair line charts")))

    settings = _make_settings()
    draft = _make_draft(supporting_market_ids=["VIX", "MOVE"])
    chart_plan = _make_chart_plan()
    output = str(tmp_path / "chart.png")

    spec, build_info = build_chart(
        draft,
        chart_plan=chart_plan,
        settings=settings,
        output_path=output,
        sample_mode=True,
        as_of=_AS_OF,
    )
    assert Path(output).exists()
    assert spec.file_path == output
    assert spec.code_generated is False
    assert build_info.final_status == "deterministic_render"
    assert build_info.code_generated is False
    assert spec.render_family == "pair_line_single_axis_raw"
    assert build_info.attempts == []


def test_build_chart_prefers_chart_plan_caption(tmp_path, monkeypatch):
    from app.render.charts import build_chart
    monkeypatch.setattr("app.render.charts.generate_chart_code", lambda **kwargs: (_ for _ in ()).throw(AssertionError("LLM path should not be used for standard pair line charts")))

    settings = _make_settings()
    draft = _make_draft(supporting_market_ids=["VIX", "MOVE"])
    chart_plan = _make_chart_plan(caption="This caption comes from the selected chart plan and should win.")
    output = str(tmp_path / "chart.png")

    spec, _build_info = build_chart(
        draft,
        chart_plan=chart_plan,
        settings=settings,
        output_path=output,
        sample_mode=True,
        as_of=_AS_OF,
    )
    assert spec.caption == chart_plan.caption


def test_build_chart_uses_reference_fixture_for_deterministic_pair_render(tmp_path, monkeypatch):
    from app.render.charts import build_chart

    settings = _make_settings()
    draft = _make_draft().model_copy(
        update={
            "overnight_dashboard": [
                _snapshot("US10Y", "US 10Y Yield", AssetClass.RATES, 5.0, flagged=True),
                _snapshot("COPPER", "Copper (front-month)", AssetClass.COMMODITY, 1.2, flagged=True),
            ]
        }
    )
    chart_plan = ChartPlan.model_validate(_load_reference_chart_plan())
    series_data = _load_reference_series_fixture()
    output = str(tmp_path / "reference_chart.png")
    monkeypatch.setattr(
        "app.render.charts.fetch_chart_series",
        lambda **kwargs: series_data,
    )
    monkeypatch.setattr("app.render.charts.generate_chart_code", lambda **kwargs: (_ for _ in ()).throw(AssertionError("LLM path should not be used for standard pair line charts")))

    spec, build_info = build_chart(
        draft,
        chart_plan=chart_plan,
        settings=settings,
        output_path=output,
        sample_mode=True,
        as_of=_AS_OF,
    )

    assert Path(output).exists()
    assert Path(output).stat().st_size > 0
    assert spec.instrument_ids == ["US10Y", "COPPER"]
    assert spec.code_generated is False
    assert spec.render_family == "pair_line_dual_axis_raw"
    assert build_info.final_status == "deterministic_render"
    assert build_info.series_instrument_ids == ["US10Y", "COPPER"]


def test_build_chart_supports_explicit_rebased_render_family(tmp_path, monkeypatch):
    from app.render.charts import build_chart

    settings = _make_settings()
    draft = _make_draft().model_copy(
        update={
            "overnight_dashboard": [
                _snapshot("US10Y", "US 10Y Yield", AssetClass.RATES, 5.0, flagged=True),
                _snapshot("COPPER", "Copper (front-month)", AssetClass.COMMODITY, 1.2, flagged=True),
            ]
        }
    )
    series_data = _load_reference_series_fixture()
    output = str(tmp_path / "rebased_chart.png")
    monkeypatch.setattr("app.render.charts.fetch_chart_series", lambda **kwargs: series_data)
    monkeypatch.setattr("app.render.charts.generate_chart_code", lambda **kwargs: (_ for _ in ()).throw(AssertionError("LLM path should not be used for standard pair line charts")))

    spec, build_info = build_chart(
        draft,
        chart_plan=_make_chart_plan(
            instrument_ids=["US10Y", "COPPER"],
            render_family="pair_line_rebased",
            title="Copper vs 10Y, rebased",
            caption="Both series are rebased to 100 at the start of the chart window.",
        ),
        settings=settings,
        output_path=output,
        sample_mode=True,
        as_of=_AS_OF,
    )

    assert Path(output).exists()
    assert Path(output).stat().st_size > 0
    assert spec.render_family == "pair_line_rebased"
    assert build_info.final_status == "deterministic_render"


def test_build_chart_uses_codegen_for_nonstandard_multi_series_case(tmp_path, monkeypatch):
    from app.render.charts import build_chart

    monkeypatch.setattr("app.render.charts.generate_chart_code", lambda **kwargs: "placeholder")
    monkeypatch.setattr(
        "app.render.charts.execute_chart_code",
        lambda code, output_path: Path(output_path).write_bytes(b"png"),
    )

    settings = _make_settings()
    draft = _make_draft(supporting_market_ids=["VIX", "MOVE"])
    chart_plan = _make_chart_plan(instrument_ids=["VIX", "MOVE", "US10Y"])
    output = str(tmp_path / "chart.png")

    spec, build_info = build_chart(
        draft,
        chart_plan=chart_plan,
        settings=settings,
        output_path=output,
        sample_mode=True,
        as_of=_AS_OF,
    )
    assert Path(output).exists()
    assert spec.code_generated is True
    assert build_info.final_status == "generated_code"
    assert build_info.code_generated is True
    assert build_info.attempts[-1].status == "success"


def test_build_chart_falls_back_when_codegen_fails_for_nonstandard_case(tmp_path, monkeypatch):
    from app.render.charts import build_chart

    def _bad_generate_chart_code(**kwargs):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr("app.render.charts.generate_chart_code", _bad_generate_chart_code)

    settings = _make_settings()
    draft = _make_draft(supporting_market_ids=["VIX", "MOVE"])
    chart_plan = _make_chart_plan(instrument_ids=["VIX", "MOVE", "US10Y"])
    output = str(tmp_path / "fallback.png")

    spec, build_info = build_chart(
        draft,
        chart_plan=chart_plan,
        settings=settings,
        output_path=output,
        sample_mode=True,
        as_of=_AS_OF,
    )
    assert Path(output).exists()
    assert spec.code_generated is False
    assert build_info.final_status == "hardcoded_fallback"
    assert build_info.fallback_reason == "codegen_failed"
    assert [attempt.status for attempt in build_info.attempts] == ["failed", "failed"]


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
    assert "last 7 calendar days" in prompt.text
    assert "last 30 calendar days" in prompt.text
    assert "Do not choose a different window or a different series" in prompt.text
    assert "missing dates across series are acceptable" in prompt.text
    assert "Ignore the `events` variable entirely" in prompt.text
    assert "Do not draw dotted weekend bridges" in prompt.text
    assert "Plot each contiguous segment separately" in prompt.text


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


def test_chart_selector_builds_raw_and_rebased_variants_for_mixed_unit_pair():
    draft = _make_draft().model_copy(
        update={
            "overnight_dashboard": [
                _snapshot("US10Y", "US 10Y Yield", AssetClass.RATES, 5.0, flagged=True),
                _snapshot("COPPER", "Copper (front-month)", AssetClass.COMMODITY, 1.2, flagged=True),
            ],
            "three_things": [
                BriefItem(
                    section=BriefSection.THREE_THINGS,
                    headline="Metals lead",
                    body="Test",
                    supporting_market_ids=["US10Y", "COPPER"],
                )
            ],
        }
    )
    series_data = _load_reference_series_fixture()
    candidates = build_chart_candidates(
        snapshots=draft.overnight_dashboard,
        series_data=series_data,
        brief_draft=draft,
        portfolio={"core_positions": [], "tactical_overlays": [], "macro_sensitivities": []},
    )

    pair_candidates = [candidate for candidate in candidates if candidate.instrument_ids == ["US10Y", "COPPER"]]

    assert {candidate.render_family for candidate in pair_candidates} >= {
        "pair_line_dual_axis_raw",
        "pair_line_rebased",
    }


def test_chart_selector_prefers_rebased_for_proxy_pairs():
    draft = _make_draft().model_copy(
        update={
            "overnight_dashboard": [
                _snapshot("SPY", "S&P 500 (proxy)", AssetClass.EQUITY, -1.2, flagged=True),
                _snapshot("QQQ", "Nasdaq 100 (proxy)", AssetClass.EQUITY, -1.1, flagged=True),
            ],
            "three_things": [
                BriefItem(
                    section=BriefSection.THREE_THINGS,
                    headline="Proxy risk tone",
                    body="Test",
                    supporting_market_ids=["SPY", "QQQ"],
                )
            ],
        }
    )
    series_data = fetch_chart_series(["SPY", "QQQ"], lookback_days=30, as_of=_AS_OF, sample_mode=True)
    candidates = build_chart_candidates(
        snapshots=draft.overnight_dashboard,
        series_data=series_data,
        brief_draft=draft,
        portfolio={"core_positions": [], "tactical_overlays": [], "macro_sensitivities": []},
    )

    proxy_candidates = [candidate for candidate in candidates if candidate.instrument_ids == ["SPY", "QQQ"]]
    by_family = {candidate.render_family: candidate.score for candidate in proxy_candidates}

    assert by_family["pair_line_rebased"] >= by_family["pair_line_single_axis_raw"]


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
    assert "keep it simple" in prompt.text
    assert "If the larger range is more than 5x the smaller range" in prompt.text
    assert "Otherwise use a single left axis" in prompt.text
    assert "Do not add custom overlap-detection loops" in prompt.text
    assert "Keep the chart code straightforward and reliable" in prompt.text


def test_chart_codegen_uses_chart_specific_llm_overrides(monkeypatch):
    from app.render.chart_codegen import generate_chart_code

    captured = {}

    class FakeClient:
        def __init__(self, config):
            captured["config"] = config

        def generate_text(self, *, system_prompt, user_message):
            return "plt.tight_layout(pad=1.5)\nplt.savefig(output_path, dpi=150, bbox_inches=\"tight\", transparent=True)"

    monkeypatch.setattr("app.render.chart_codegen.LLMClient", FakeClient)

    settings = _make_settings()
    settings.sources["llm"]["chart_model"] = "openai/gpt-5.5"
    settings.sources["llm"]["chart_reasoning_effort"] = "high"
    settings.sources["llm"]["chart_verbosity"] = "medium"
    settings.sources["llm"]["chart_timeout_seconds"] = 300
    settings.sources["llm"]["chart_max_tokens"] = 5000

    generate_chart_code(
        chart_plan=_make_chart_plan(),
        series_data=fetch_chart_series(["VIX", "MOVE"], lookback_days=30, as_of=_AS_OF, sample_mode=True),
        display_names={"VIX": "VIX", "MOVE": "MOVE Index"},
        asset_classes={"VIX": "volatility", "MOVE": "volatility"},
        units={"VIX": "index", "MOVE": "index"},
        events=[],
        output_path="out.png",
        settings=settings,
    )

    assert captured["config"].model == "openai/gpt-5.5"
    assert captured["config"].reasoning_effort == "high"
    assert captured["config"].verbosity == "medium"
    assert captured["config"].timeout_seconds == 300
    assert captured["config"].max_tokens == 5000


def test_chart_selector_prompt_exists_and_restricts_choice_to_shortlist():
    from app.llm.prompt_registry import clear_prompt_cache, load_prompt
    clear_prompt_cache()
    prompt = load_prompt("chart_selector")
    assert "Pick exactly one candidate from the supplied shortlist" in prompt.text
    assert "`candidate_id` must match one of the supplied `chart_candidates`" in prompt.text
    assert "render families" in prompt.text


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
    assert plan.render_family == shortlist[0].render_family


# ---------------------------------------------------------------------------
# Email render context
# ---------------------------------------------------------------------------

def test_calendar_title_is_mondays_for_weekend_run():
    from app.render.email import build_render_context

    settings = _make_settings()
    draft = _make_draft().model_copy(
        update={
            "run_metadata": {
                "brief_date": "2026-05-11T00:00:00+08:00",
                "data_cutoff_at": "2026-05-10T06:45:00+08:00",  # Sunday
            }
        }
    )
    ctx = build_render_context(draft, settings)
    assert ctx["calendar_title"] == "Monday's Calendar"


def test_calendar_title_is_todays_for_weekday_run():
    from app.render.email import build_render_context

    settings = _make_settings()
    draft = _make_draft().model_copy(
        update={
            "run_metadata": {
                "brief_date": "2026-05-08T00:00:00+08:00",
                "data_cutoff_at": "2026-05-08T06:45:00+08:00",  # Friday
            }
        }
    )
    ctx = build_render_context(draft, settings)
    assert ctx["calendar_title"] == "Today's Calendar"


def test_header_line_includes_weekend_suffix_for_saturday_run():
    from app.render.email import build_render_context

    settings = _make_settings()
    # May 10 2026 is a Saturday
    draft = _make_draft().model_copy(
        update={
            "run_metadata": {
                "brief_date": "2026-05-11T00:00:00+08:00",
                "data_cutoff_at": "2026-05-10T06:45:00+08:00",
            }
        }
    )
    ctx = build_render_context(draft, settings)
    assert ctx["header_line"].endswith("(For Upcoming Monday)")
    assert "Monday, May 11, 2026" in ctx["header_line"]


def test_header_line_has_no_suffix_for_weekday_run():
    from app.render.email import build_render_context

    settings = _make_settings()
    # May 8 2026 is a Friday
    draft = _make_draft().model_copy(
        update={
            "run_metadata": {
                "brief_date": "2026-05-08T00:00:00+08:00",
                "data_cutoff_at": "2026-05-08T06:45:00+08:00",
            }
        }
    )
    ctx = build_render_context(draft, settings)
    assert "(For Upcoming Monday)" not in ctx["header_line"]
    assert "Friday, May 8, 2026" in ctx["header_line"]


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
        "Nasdaq 100 (proxy)",
        "USD/JPY",
        "US 10Y Yield",
        "US 2Y Yield",
        "Gold (spot)",
        "WTI (front-month)",
        "Brent (front-month)",
        "VIX",
        "MOVE Index",
    ]


def test_build_render_context_does_not_duplicate_significant_core_assets():
    from app.render.email import build_render_context

    settings = _make_settings()
    draft = _make_draft()
    ctx = build_render_context(draft, settings, vol_params={})
    assets = [row["asset"] for row in ctx["dashboard_rows"]]

    assert assets.count("VIX") == 1
    assert assets.count("US 10Y Yield") == 1


def test_build_render_context_orders_extras_by_bucket_after_selection():
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
        _snapshot("BTC", "Bitcoin", AssetClass.CRYPTO, 4.2, flagged=True),
        _snapshot("HY_OAS", "ICE BofA US High Yield OAS", AssetClass.CREDIT, 12.0, flagged=True),
        _snapshot("MOVE", "MOVE Index", AssetClass.VOLATILITY, 3.0, flagged=True),
        _snapshot("BRENT", "Brent (front-month)", AssetClass.COMMODITY, -2.5, flagged=True),
        _snapshot("EURUSD", "EUR/USD", AssetClass.FX, 0.1),
    ]
    draft = _make_draft().model_copy(update={"overnight_dashboard": dashboard})

    ctx = build_render_context(draft, settings, vol_params={})

    assert [row["asset"] for row in ctx["dashboard_rows"]] == [
        "S&P 500 (proxy)",
        "USD/JPY",
        "US 10Y Yield",
        "US 2Y Yield",
        "Gold (spot)",
        "WTI (front-month)",
        "Bitcoin",
        "VIX",
        "ICE BofA US High Yield OAS",
        "MOVE Index",
    ]


def test_build_render_context_splits_metals_from_commodities():
    from app.render.email import build_render_context

    settings = _make_settings()
    settings.app.dashboard_core_instruments = []
    dashboard = [
        _snapshot("WTI", "WTI (front-month)", AssetClass.COMMODITY, 1.2, flagged=True),
        _snapshot("COPPER", "Copper (front-month)", AssetClass.COMMODITY, 2.0, flagged=True),
        _snapshot("GOLD", "Gold (spot)", AssetClass.COMMODITY, 0.8, flagged=True),
        _snapshot("SILVER", "Silver (spot)", AssetClass.COMMODITY, 0.7, flagged=True),
    ]
    draft = _make_draft().model_copy(update={"overnight_dashboard": dashboard})

    ctx = build_render_context(draft, settings, vol_params={})

    assert [row["asset"] for row in ctx["dashboard_rows"]] == [
        "Copper (front-month)",
        "Gold (spot)",
        "Silver (spot)",
        "WTI (front-month)",
    ]


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
        source_type=SourceType.RESEARCH,
        topic_label="Rates",
    )
    draft = _make_draft().model_copy(update={"radar_items": [radar]})
    paths = render_brief(draft, settings, output_dir=tmp_path, vol_params={})
    html = Path(paths["html"]).read_text(encoding="utf-8")

    assert "radar-source" in html
    assert "radar-topic" in html
    assert html.index("Rates") < html.index("Research headline")
    assert html.index("Research - Research Source") < html.index("Research headline")
    assert 'href="https://example.com/research"' in html
    assert "Research headline<span class=\"radar-title-icon\">↗</span>" in html


def test_theme_radar_renders_plain_book_impact_text(tmp_path):
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

    assert "Reinforces short duration thesis." in html
    assert "What this means for our book:" not in html
    assert "Reinforces short duration thesis." in text
    assert "What this means for our book:" not in text


def test_book_impact_sentence_is_capitalized(tmp_path):
    from app.render.email import render_brief

    settings = _make_settings()
    radar = BriefItem(
        section=BriefSection.THEME_RADAR,
        headline="Research headline",
        body="Rates stay under pressure as issuance remains elevated.",
        so_what="what this means for our book: reinforces short duration thesis.",
        supporting_evidence_ids=["ev_001"],
        topic_label="Rates",
    )
    draft = _make_draft().model_copy(update={"radar_items": [radar]})
    paths = render_brief(draft, settings, output_dir=tmp_path, vol_params={})
    html = Path(paths["html"]).read_text(encoding="utf-8")
    text = Path(paths["text"]).read_text(encoding="utf-8")

    assert "Reinforces short duration thesis." in html
    assert "Reinforces short duration thesis." in text


def test_theme_radar_without_source_url_renders_plain_title(tmp_path):
    from app.render.email import render_brief

    settings = _make_settings()
    radar = BriefItem(
        section=BriefSection.THEME_RADAR,
        headline="Research headline",
        body="Author argues that fiscal dominance is now the primary driver of long yields. "
             "Evidence includes widening deficits and sustained Treasury issuance. "
             "Central banks are losing credibility as inflation anchors.",
        so_what="What this means for our book: reinforces duration_short thesis.",
        supporting_evidence_ids=["ev_001"],
        topic_label="Rates",
    )
    draft = _make_draft().model_copy(update={"radar_items": [radar]})
    paths = render_brief(draft, settings, output_dir=tmp_path, vol_params={})
    html = Path(paths["html"]).read_text(encoding="utf-8")

    assert 'href="#"' not in html
    assert "<span class=\"radar-title-icon\">" not in html
    assert "<span class=\"radar-title\">Research headline</span>" in html


@pytest.mark.parametrize(
    ("source_type", "source_name", "expected"),
    [
        (SourceType.SOCIAL, "Elon Musk", "X Post - Elon Musk"),
        (SourceType.PODCAST, "Bloomberg Asia", "Podcast - Bloomberg Asia"),
        (SourceType.PODCAST, None, "Podcast"),
    ],
)
def test_theme_radar_renders_source_credit_in_html_and_text(tmp_path, source_type, source_name, expected):
    from app.render.email import render_brief

    settings = _make_settings()
    radar = BriefItem(
        section=BriefSection.THEME_RADAR,
        headline="Alternative source headline",
        body="Source-specific evidence drives a medium-term portfolio implication.",
        so_what="Supports a differentiated theme expression.",
        supporting_evidence_ids=["ev_001"],
        source_name=source_name,
        source_url="https://example.com/source",
        source_type=source_type,
        topic_label="Rates",
    )
    draft = _make_draft().model_copy(update={"radar_items": [radar]})
    paths = render_brief(draft, settings, output_dir=tmp_path, vol_params={})
    html = Path(paths["html"]).read_text(encoding="utf-8")
    text = Path(paths["text"]).read_text(encoding="utf-8")

    assert expected in html
    assert f"Alternative source headline - {expected}" in text


def test_theme_radar_source_credit_falls_back_to_name_only(tmp_path):
    from app.render.email import render_brief

    settings = _make_settings()
    radar = BriefItem(
        section=BriefSection.THEME_RADAR,
        headline="Alternative source headline",
        body="Source-specific evidence drives a medium-term portfolio implication.",
        so_what="Supports a differentiated theme expression.",
        supporting_evidence_ids=["ev_001"],
        source_name="Asia Bloomberg",
        topic_label="Rates",
    )
    draft = _make_draft().model_copy(update={"radar_items": [radar]})
    paths = render_brief(draft, settings, output_dir=tmp_path, vol_params={})
    html = Path(paths["html"]).read_text(encoding="utf-8")
    text = Path(paths["text"]).read_text(encoding="utf-8")

    assert "Asia Bloomberg" in html
    assert "Alternative source headline - Asia Bloomberg" in text


def test_render_normalizes_known_position_ids_to_labels(tmp_path):
    from app.render.email import render_brief

    settings = _make_settings()
    draft = _make_draft().model_copy(
        update={
            "book_impact": "duration_short still anchors the hedge.",
            "three_things": [
                BriefItem(
                    section=BriefSection.THREE_THINGS,
                    headline="Policy pressure",
                    body="duration_short remains the cleanest expression.",
                    so_what="Supports metals_complex_long and duration_short.",
                )
            ],
        }
    )
    paths = render_brief(draft, settings, output_dir=tmp_path, vol_params={})
    html = Path(paths["html"]).read_text(encoding="utf-8")
    text = Path(paths["text"]).read_text(encoding="utf-8")

    assert "Short Long-term US Duration still anchors the hedge." in html
    assert "Short Long-term US Duration remains the cleanest expression." in html
    assert "Supports Long Metals Complex (Gold/Silver/Copper) and Short Long-term US Duration." in html
    assert "duration_short" not in html
    assert "metals_complex_long" not in html
    assert "Short Long-term US Duration still anchors the hedge." in text
    assert "Supports Long Metals Complex (Gold/Silver/Copper) and Short Long-term US Duration." in text


def test_render_normalizes_unknown_snake_case_tokens(tmp_path):
    from app.render.email import render_brief

    settings = _make_settings()
    draft = _make_draft().model_copy(
        update={
            "three_things": [
                BriefItem(
                    section=BriefSection.THREE_THINGS,
                    headline="Flow watch",
                    body="long_dollar remains crowded.",
                    so_what="Supports long_dollar cleanup.",
                )
            ],
        }
    )
    paths = render_brief(draft, settings, output_dir=tmp_path, vol_params={})
    html = Path(paths["html"]).read_text(encoding="utf-8")
    text = Path(paths["text"]).read_text(encoding="utf-8")

    assert "long dollar remains crowded." in html
    assert "Supports long dollar cleanup." in html
    assert "long_dollar" not in html
    assert "long dollar remains crowded." in text
    assert "Supports long dollar cleanup." in text


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
        portfolio={
            "core_positions": [
                {
                    "id": "metals_complex_long",
                    "label": "Long Metals Complex (Gold/Silver/Copper)",
                },
                {
                    "id": "duration_short",
                    "label": "Short Long-term US Duration",
                },
            ],
            "tactical_overlays": [],
        },
        themes={"themes": []},
        app=SimpleNamespace(
            output_dir="outputs",
            dashboard_core_instruments=["SPY", "US10Y", "USDJPY", "GOLD", "WTI", "VIX"],
            dashboard_max_extra_movers=4,
        ),
    )
