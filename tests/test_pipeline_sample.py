"""Pipeline orchestration tests for ``run_pipeline``."""

from __future__ import annotations

import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.llm import provider
from app.delivery import PostmarkDeliveryProvider
from app.models import (
    BriefDraft,
    BriefItem,
    BriefSection,
    ChartBuildInfo,
    ChartSelectionMethod,
    ChartSpec,
    ChartWindow,
    DeliveryResult,
    DeliveryStatus,
    FreshnessStatus,
    LLMUsage,
    MarketSnapshot,
    PipelineResult,
    RunMode,
)
from app.pipeline import run_pipeline
from app.settings import AppConfig, CONFIG_DIR, Credentials, Settings, _load_yaml
from app.synthesis.validator import validate_brief


@pytest.fixture
def isolated_settings(tmp_path):
    settings = Settings.load()
    settings.app.output_dir = str(tmp_path / "outputs")
    settings.creds.cloudflare_r2_account_id = None
    settings.creds.cloudflare_r2_access_key_id = None
    settings.creds.cloudflare_r2_secret_access_key = None
    settings.creds.cloudflare_r2_bucket = None
    settings.creds.cloudflare_r2_public_base_url = None
    settings.creds.cloudflare_r2_prefix = None
    return settings


def _settings_without_credentials() -> Settings:
    creds = Credentials.model_construct(
        openai_api_key=None,
        gemini_api_key=None,
        xai_api_key=None,
        llm_scout_model=None,
        llm_x_scout_model=None,
        llm_synthesis_model=None,
        llm_temperature=None,
        llm_synthesis_temperature=None,
        llm_synthesis_reasoning_effort=None,
        llm_synthesis_verbosity=None,
        llm_synthesis_review_enabled=None,
        llm_synthesis_review_model=None,
        llm_synthesis_review_temperature=None,
        llm_synthesis_review_reasoning_effort=None,
        llm_synthesis_review_verbosity=None,
        llm_synthesis_context_card_count=None,
        alpha_vantage_api_key=None,
        databento_api_key=None,
        fred_api_key=None,
        listen_notes_api_key=None,
        taddy_user_id=None,
        taddy_api_key=None,
        postmark_api_key=None,
        postmark_from_email=None,
        postmark_maintainer_email=None,
        enable_email_delivery=False,
        app_mode=None,
        cache_dir=None,
        output_dir=None,
    )
    settings = Settings(
        app=AppConfig(**_load_yaml(CONFIG_DIR / "app.yaml")),
        creds=creds,
        portfolio=_load_yaml(CONFIG_DIR / "portfolio.yaml"),
        themes=_load_yaml(CONFIG_DIR / "themes.yaml"),
        sources=_load_yaml(CONFIG_DIR / "sources.yaml"),
        chart_templates=_load_yaml(CONFIG_DIR / "chart_templates.yaml"),
        config_dir=CONFIG_DIR,
    )
    settings.app.output_dir = tempfile.mkdtemp(prefix="daily-macro-sample-outputs-")
    return settings


def _mock_write_brief(ranked_context, settings, run_date, mode=None):
    """Return a minimal valid BriefDraft without making any LLM call."""
    snap = ranked_context.dashboard_rows[0] if ranked_context.dashboard_rows else None
    three_things = BriefItem(
        section=BriefSection.THREE_THINGS,
        headline="Metals complex gains",
        body="Gold moved higher as real yields softened. Supporting the monetary debasement thesis with central bank buying continuing at elevated pace.",
        so_what="Supports long metals complex position.",
        supporting_market_ids=[snap.instrument_id] if snap else [],
    )
    radar = BriefItem(
        section=BriefSection.THEME_RADAR,
        headline="Fiscal Dominance Thesis — Research Note",
        body=(
            "Author argues that fiscal dominance is now the primary driver of long yields. "
            "Evidence includes widening deficits and sustained Treasury issuance. "
            "Central banks are losing credibility as inflation anchors. "
            "Historical parallels to the 1940s yield curve control era are noted."
        ),
        so_what="What this means for our book: reinforces short duration positioning.",
        supporting_evidence_ids=["ev_001"],
    )
    draft = BriefDraft(
        run_metadata={},
        overnight_dashboard=ranked_context.dashboard_rows,
        three_things=[three_things],
        todays_calendar=ranked_context.top_calendar_events,
        radar_items=[radar],
        contrarian_corner=None,
        warnings=[],
    )
    usage = LLMUsage(model="mock", latency_seconds=0.0)
    return draft, [usage]


@pytest.fixture(autouse=False)
def mock_writer():
    with patch("app.synthesis.writer.write_brief", side_effect=_mock_write_brief):
        yield


@pytest.fixture(autouse=True)
def mock_delivery():
    from app.delivery import NoopDeliveryProvider

    with patch("app.delivery.get_provider", return_value=NoopDeliveryProvider()):
        yield


def test_sample_pipeline_returns_pipeline_result(mock_writer, isolated_settings):
    result = run_pipeline(RunMode.SAMPLE, isolated_settings)

    assert isinstance(result, PipelineResult)
    assert result.success is True


def test_sample_pipeline_run_metadata_populated(mock_writer, isolated_settings):
    result = run_pipeline(RunMode.SAMPLE, isolated_settings)

    meta = result.run_metadata
    assert re.fullmatch(r"\d{8}_\d{6}_sample", meta.run_id)
    assert meta.run_started_at is not None
    assert meta.run_started_at.tzinfo is not None
    assert meta.data_cutoff_at is not None
    assert meta.brief_date is not None
    assert meta.timezone == "Asia/Hong_Kong"
    assert meta.llm_provider == "litellm"
    assert meta.delivery_status == DeliveryStatus.DISABLED


def test_sample_pipeline_preserves_writer_warnings_in_run_metadata(isolated_settings):
    def _mock_writer_with_warning(ranked_context, settings, run_date, mode=None):
        draft, usages = _mock_write_brief(ranked_context, settings, run_date, mode)
        draft = draft.model_copy(update={"warnings": ["only 2 evidence cards available"]})
        return draft, usages

    with patch("app.synthesis.writer.write_brief", side_effect=_mock_writer_with_warning):
        result = run_pipeline(RunMode.SAMPLE, isolated_settings)

    assert "only 2 evidence cards available" in result.run_metadata.warnings


def test_sample_pipeline_persists_core_artifacts(mock_writer, isolated_settings):
    result = run_pipeline(RunMode.SAMPLE, isolated_settings)

    artifact_keys = {
        "html",
        "text",
        "chart",
        "evidence_cards",
        "ranked_context",
        "brief_rendered_html",
        "run_metadata",
        "market_snapshots",
        "calendar_events",
        "brief_draft",
        "chart_candidates",
        "chart_plan",
        "chart_build",
    }
    assert artifact_keys <= set(result.run_metadata.output_paths)
    for key in artifact_keys:
        assert Path(result.run_metadata.output_paths[key]).exists()

    cards = json.loads(Path(result.run_metadata.output_paths["evidence_cards"]).read_text(encoding="utf-8"))
    assert isinstance(cards, list)
    assert cards

    chart_build = json.loads(Path(result.run_metadata.output_paths["chart_build"]).read_text(encoding="utf-8"))
    assert chart_build["final_status"] in {"deterministic_render", "generated_code", "hardcoded_fallback"}
    assert "requested_instrument_ids" in chart_build
    assert "used_instrument_ids" in chart_build
    assert "attempts" in chart_build


def test_pipeline_uses_data_cutoff_override(mock_writer, isolated_settings):
    override = datetime(2026, 5, 8, 6, 45, tzinfo=ZoneInfo(isolated_settings.app.timezone))

    result = run_pipeline(RunMode.SAMPLE, isolated_settings, data_cutoff=override)

    assert result.run_metadata.data_cutoff_at == override


def test_pipeline_progress_callback_reports_steps(mock_writer, isolated_settings):
    events: list[str] = []

    result = run_pipeline(RunMode.SAMPLE, isolated_settings, progress=events.append)

    assert result.success is True
    assert events[:3] == ["Determine run window", "Fetch market data", "Fetch calendar"]
    assert events[-1] == "Record metadata"


def test_pipeline_records_step_and_scout_timings(mock_writer, isolated_settings):
    result = run_pipeline(RunMode.SAMPLE, isolated_settings)

    timings = result.run_metadata.timings
    components = [timing["component"] for timing in timings]
    assert "Fetch market data" in components
    assert "scout:fixture" in components
    assert "Record metadata" in components
    assert all(isinstance(timing["seconds"], float) for timing in timings)


def test_sample_pipeline_is_credential_free(monkeypatch):
    def fail_litellm_completion(**kwargs):
        raise AssertionError("sample mode must not call live LiteLLM completion")

    monkeypatch.setattr(provider.litellm_compat, "completion", fail_litellm_completion)
    settings = _settings_without_credentials()

    result = run_pipeline(RunMode.SAMPLE, settings)

    assert result.success is True, result.error_message
    assert result.brief_draft is not None
    validation = validate_brief(result.brief_draft)
    assert validation.is_valid, validation.critical_failures


def test_dry_run_pipeline_passes_data_cutoff_to_live_market_fetch(mock_writer, isolated_settings):
    override = datetime(2026, 5, 8, 6, 45, tzinfo=ZoneInfo(isolated_settings.app.timezone))
    snapshot = MarketSnapshot(
        as_of=override,
        observation_date="2026-05-07",
        instrument_id="SPY",
        display_name="S&P 500 (proxy)",
        asset_class="equity",
        region="US",
        last_price_or_level=500.0,
        one_day_change=1.0,
        one_day_change_unit="%",
        source="test",
        freshness_status=FreshnessStatus.FRESH,
    )

    with patch("app.pipeline.load_vol_params", return_value={}), \
         patch("app.pipeline.fetch_live_market_with_cache", return_value=[snapshot]) as mock_fetch, \
         patch("app.data.calendar.InvestingCalendarProvider.fetch_for_date", return_value=[]), \
         patch("app.discovery.orchestrator.build_scouts", return_value=[]), \
         patch("app.discovery.orchestrator.run_discovery", return_value=[]), \
         patch("app.render.charts.build_chart", side_effect=RuntimeError("skip chart")):
        result = run_pipeline(RunMode.DRY_RUN, isolated_settings, data_cutoff=override)

    assert result.run_metadata.data_cutoff_at == override
    mock_fetch.assert_called_once_with(isolated_settings, {}, override)


def test_dry_run_pipeline_calls_calendar_provider_with_brief_date(mock_writer, isolated_settings):
    override = datetime(2026, 5, 9, 6, 45, tzinfo=ZoneInfo(isolated_settings.app.timezone))

    with patch("app.pipeline.load_vol_params", return_value={}), \
         patch("app.pipeline.fetch_live_market_with_cache", return_value=[]), \
         patch("app.data.calendar.InvestingCalendarProvider.fetch_for_date", return_value=[]) as mock_calendar, \
         patch("app.discovery.orchestrator.build_scouts", return_value=[]), \
         patch("app.discovery.orchestrator.run_discovery", return_value=[]), \
         patch("app.render.charts.build_chart", side_effect=RuntimeError("skip chart")):
        result = run_pipeline(RunMode.DRY_RUN, isolated_settings, data_cutoff=override)

    assert result.run_metadata.brief_date == datetime(
        2026, 5, 11, 0, 0, tzinfo=ZoneInfo(isolated_settings.app.timezone)
    )
    mock_calendar.assert_called_once_with(datetime(2026, 5, 11).date())


def test_sample_pipeline_keeps_r2_uploads_disabled(mock_writer, isolated_settings):
    with patch("app.render.chart_uploader.publish_chart") as mock_chart_upload, \
         patch("app.render.chart_uploader.publish_run_artifacts") as mock_run_upload, \
         patch("app.render.chart_uploader.publish_latest_artifact_alias") as mock_alias_upload:
        result = run_pipeline(RunMode.SAMPLE, isolated_settings)

    assert result.success is True
    mock_chart_upload.assert_not_called()
    mock_run_upload.assert_not_called()
    mock_alias_upload.assert_not_called()


def test_pipeline_marks_non_one_day_chart_fallback_as_degraded(mock_writer, isolated_settings):
    def _fake_build_chart(*args, output_path, **kwargs):
        Path(output_path).write_bytes(b"png")
        return (
            ChartSpec(
                title="Fallback chart",
                caption="Fallback caption",
                chart_type="bar",
                data_source="dashboard",
                file_path=output_path,
                code_generated=False,
                window=ChartWindow.ONE_WEEK,
                instrument_ids=["US10Y", "MOVE"],
                selection_reason="test",
                selection_method=ChartSelectionMethod.HYBRID_LLM_SHORTLIST,
            ),
            ChartBuildInfo(
                final_status="hardcoded_fallback",
                fallback_reason="codegen_failed",
                output_path=output_path,
                requested_window=ChartWindow.ONE_WEEK,
                requested_chart_type="line",
                requested_instrument_ids=["US10Y", "MOVE"],
                used_instrument_ids=["US10Y", "MOVE"],
                series_instrument_ids=["US10Y", "MOVE"],
                code_generated=False,
            ),
        )

    with patch("app.render.charts.build_chart", side_effect=_fake_build_chart):
        result = run_pipeline(RunMode.SAMPLE, isolated_settings)

    build_chart_timing = next(
        timing for timing in result.run_metadata.timings if timing["component"] == "Build chart"
    )
    assert build_chart_timing["status"] == "degraded"
    assert any(
        warning == "Chart generation degraded to hardcoded fallback (codegen_failed)"
        for warning in result.run_metadata.warnings
    )


def test_pipeline_keeps_one_day_fallback_as_success(mock_writer, isolated_settings):
    def _fake_build_chart(*args, output_path, **kwargs):
        Path(output_path).write_bytes(b"png")
        return (
            ChartSpec(
                title="One-day fallback",
                caption="Fallback caption",
                chart_type="bar",
                data_source="dashboard",
                file_path=output_path,
                code_generated=False,
                window=ChartWindow.ONE_DAY,
                instrument_ids=["SPY", "VIX"],
                selection_reason="test",
                selection_method=ChartSelectionMethod.HYBRID_LLM_SHORTLIST,
            ),
            ChartBuildInfo(
                final_status="hardcoded_fallback",
                fallback_reason="one_day_window",
                output_path=output_path,
                requested_window=ChartWindow.ONE_DAY,
                requested_chart_type="bar",
                requested_instrument_ids=["SPY", "VIX"],
                used_instrument_ids=["SPY", "VIX"],
                series_instrument_ids=[],
                code_generated=False,
            ),
        )

    with patch("app.render.charts.build_chart", side_effect=_fake_build_chart):
        result = run_pipeline(RunMode.SAMPLE, isolated_settings)

    build_chart_timing = next(
        timing for timing in result.run_metadata.timings if timing["component"] == "Build chart"
    )
    assert build_chart_timing["status"] == "success"
    assert all(
        "Chart generation degraded to hardcoded fallback" not in warning
        for warning in result.run_metadata.warnings
    )


def test_dry_run_pipeline_publishes_run_artifacts_and_latest_alias(mock_writer, isolated_settings):
    override = datetime(2026, 5, 8, 6, 45, tzinfo=ZoneInfo(isolated_settings.app.timezone))
    snapshot = MarketSnapshot(
        as_of=override,
        observation_date="2026-05-07",
        instrument_id="SPY",
        display_name="S&P 500 (proxy)",
        asset_class="equity",
        region="US",
        last_price_or_level=500.0,
        one_day_change=1.0,
        one_day_change_unit="%",
        source="test",
        freshness_status=FreshnessStatus.FRESH,
    )

    with patch("app.pipeline.load_vol_params", return_value={}), \
         patch("app.pipeline.fetch_live_market_with_cache", return_value=[snapshot]), \
         patch("app.data.calendar.InvestingCalendarProvider.fetch_for_date", return_value=[]), \
         patch("app.discovery.orchestrator.build_scouts", return_value=[]), \
         patch("app.discovery.orchestrator.run_discovery", return_value=[]), \
         patch("app.render.chart_uploader.publish_chart", return_value="https://pub.example.com/chart.png") as mock_chart_upload, \
         patch("app.render.chart_uploader.publish_run_artifacts", return_value=["https://pub.example.com/brief.html"]) as mock_run_upload, \
         patch("app.render.chart_uploader.publish_latest_artifact_alias", return_value="https://pub.example.com/latest.html") as mock_alias_upload:
        result = run_pipeline(RunMode.DRY_RUN, isolated_settings, data_cutoff=override)

    assert result.success is True
    assert result.run_metadata.delivery_status == DeliveryStatus.DISABLED
    mock_chart_upload.assert_called_once()
    mock_run_upload.assert_called_once()
    mock_alias_upload.assert_called_once()


def test_live_pipeline_publishes_run_artifacts_even_when_delivery_fails(mock_writer, isolated_settings):
    isolated_settings.creds.enable_email_delivery = True
    override = datetime(2026, 5, 8, 6, 45, tzinfo=ZoneInfo(isolated_settings.app.timezone))
    snapshot = MarketSnapshot(
        as_of=override,
        observation_date="2026-05-07",
        instrument_id="SPY",
        display_name="S&P 500 (proxy)",
        asset_class="equity",
        region="US",
        last_price_or_level=500.0,
        one_day_change=1.0,
        one_day_change_unit="%",
        source="test",
        freshness_status=FreshnessStatus.FRESH,
    )

    class FailingProvider:
        def send(self, subject, html_body, text_body, inline_images=None):
            return DeliveryResult(success=False, error_message="boom")

    with patch("app.pipeline.load_vol_params", return_value={}), \
         patch("app.pipeline.fetch_live_market_with_cache", return_value=[snapshot]), \
         patch("app.data.calendar.InvestingCalendarProvider.fetch_for_date", return_value=[]), \
         patch("app.discovery.orchestrator.build_scouts", return_value=[]), \
         patch("app.discovery.orchestrator.run_discovery", return_value=[]), \
         patch("app.delivery.get_provider", return_value=FailingProvider()), \
         patch("app.render.chart_uploader.publish_chart", return_value="https://pub.example.com/chart.png"), \
         patch("app.render.chart_uploader.publish_run_artifacts", return_value=["https://pub.example.com/brief.html"]) as mock_run_upload, \
         patch("app.render.chart_uploader.publish_latest_artifact_alias", return_value="https://pub.example.com/latest.html") as mock_alias_upload:
        result = run_pipeline(RunMode.LIVE, isolated_settings, data_cutoff=override)

    assert result.run_metadata.delivery_status == DeliveryStatus.FAILED
    assert "Email delivery failed: boom" in result.run_metadata.warnings
    mock_run_upload.assert_called_once()
    mock_alias_upload.assert_called_once()


def test_dry_run_pipeline_delivery_enabled_sends_to_maintainer_only(mock_writer, isolated_settings):
    isolated_settings.creds.enable_email_delivery = True
    isolated_settings.creds.postmark_api_key = "pm-key"
    isolated_settings.creds.postmark_from_email = "brief@test.com"
    isolated_settings.creds.postmark_maintainer_email = "maintainer@test.com"
    isolated_settings.recipients = ["prod@test.com"]
    override = datetime(2026, 5, 8, 6, 45, tzinfo=ZoneInfo(isolated_settings.app.timezone))
    snapshot = MarketSnapshot(
        as_of=override,
        observation_date="2026-05-07",
        instrument_id="SPY",
        display_name="S&P 500 (proxy)",
        asset_class="equity",
        region="US",
        last_price_or_level=500.0,
        one_day_change=1.0,
        one_day_change_unit="%",
        source="test",
        freshness_status=FreshnessStatus.FRESH,
    )

    with patch("app.pipeline.load_vol_params", return_value={}), \
         patch("app.pipeline.fetch_live_market_with_cache", return_value=[snapshot]), \
         patch("app.data.calendar.InvestingCalendarProvider.fetch_for_date", return_value=[]), \
         patch("app.discovery.orchestrator.build_scouts", return_value=[]), \
         patch("app.discovery.orchestrator.run_discovery", return_value=[]), \
         patch(
             "app.delivery.get_provider",
             return_value=PostmarkDeliveryProvider(
                 isolated_settings,
                 recipient_override=isolated_settings.creds.postmark_maintainer_email,
             ),
         ), \
         patch("requests.post", return_value=MagicMock(json=lambda: {"MessageID": "msg-123"}, raise_for_status=lambda: None)) as mock_post:
        result = run_pipeline(RunMode.DRY_RUN, isolated_settings, data_cutoff=override)

    assert result.run_metadata.delivery_status == DeliveryStatus.SUCCESS
    assert mock_post.call_args.kwargs["json"]["To"] == "maintainer@test.com"
