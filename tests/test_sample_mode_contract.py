"""Contract tests for credential-free deterministic sample mode."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app import settings as settings_module
from app.llm import provider
from app.models import RunMode
from app.pipeline import run_pipeline
from app.settings import AppConfig, CONFIG_DIR, Credentials, Settings, _load_yaml
from app.synthesis.ranker import rank
from app.synthesis.validator import validate_brief
from app.synthesis.writer import write_brief

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _settings_without_credentials() -> Settings:
    """Build settings without reading .env so sample-mode tests are deterministic."""
    creds = Credentials.model_construct(
        openai_api_key=None,
        gemini_api_key=None,
        xai_api_key=None,
        llm_scout_model=None,
        llm_x_scout_model=None,
        llm_synthesis_model=None,
        llm_temperature=None,
        alpha_vantage_api_key=None,
        databento_api_key=None,
        fred_api_key=None,
        listen_notes_api_key=None,
        taddy_user_id=None,
        taddy_api_key=None,
        sendgrid_api_key=None,
        sendgrid_from_email=None,
        sendgrid_to_email=None,
        enable_email_delivery=False,
        app_mode=None,
        cache_dir=None,
        output_dir=None,
    )
    return Settings(
        app=AppConfig(**_load_yaml(CONFIG_DIR / "app.yaml")),
        creds=creds,
        portfolio=_load_yaml(CONFIG_DIR / "portfolio.yaml"),
        themes=_load_yaml(CONFIG_DIR / "themes.yaml"),
        sources=_load_yaml(CONFIG_DIR / "sources.yaml"),
        chart_templates=_load_yaml(CONFIG_DIR / "chart_templates.yaml"),
        config_dir=CONFIG_DIR,
    )


def test_sample_mode_validation_does_not_require_openai_key():
    settings = _settings_without_credentials()

    assert settings.validate_for_mode(RunMode.SAMPLE) == []


def _sample_ranked_context(settings: Settings):
    from app.data.market import FixtureMarketProvider
    from app.discovery.orchestrator import build_scouts, run_discovery
    from app.discovery.scouts.base import DiscoveryContext
    from app.pipeline import _data_cutoff, _load_fixture_calendar
    from app.synthesis.deduper import deduplicate
    from zoneinfo import ZoneInfo

    data_cutoff = _data_cutoff(settings, ZoneInfo(settings.app.timezone))
    market_snapshots = FixtureMarketProvider().fetch_watchlist([], data_cutoff)
    calendar_events = _load_fixture_calendar()
    scouts = build_scouts(settings, RunMode.SAMPLE)
    evidence_cards = run_discovery(
        scouts,
        DiscoveryContext(
            market_snapshots=market_snapshots,
            calendar_events=calendar_events,
            themes=settings.themes.get("themes", []),
            portfolio=settings.portfolio,
            lookback_hours=24,
            data_cutoff=data_cutoff,
            mode=RunMode.SAMPLE,
        ),
        failed_sources=[],
    )
    return rank(
        market_snapshots=market_snapshots,
        calendar_events=calendar_events,
        evidence_cards=deduplicate(evidence_cards),
        themes=settings.themes.get("themes", []),
        portfolio=settings.portfolio,
    ), data_cutoff


def test_sample_writer_uses_fake_response_without_openai_key(monkeypatch):
    calls = 0

    def fail_litellm_completion(**kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("sample writer must not call live LiteLLM completion")

    monkeypatch.setattr(provider.litellm, "completion", fail_litellm_completion)
    settings = _settings_without_credentials()
    ranked_context, data_cutoff = _sample_ranked_context(settings)

    draft, usage = write_brief(ranked_context, settings, data_cutoff, RunMode.SAMPLE)

    assert calls == 0
    assert usage.model == "fake/sample"
    assert validate_brief(draft).is_valid


def test_sample_pipeline_returns_validator_passing_draft_without_openai_key(monkeypatch):
    def fail_litellm_completion(**kwargs):
        raise AssertionError("sample mode must not call live LiteLLM completion")

    monkeypatch.setattr(provider.litellm, "completion", fail_litellm_completion)
    settings = _settings_without_credentials()

    result = run_pipeline(RunMode.SAMPLE, settings)

    assert result.success is True, result.error_message
    assert result.brief_draft is not None
    validation = validate_brief(result.brief_draft)
    assert validation.is_valid, validation.critical_failures


def test_sample_cli_runs_without_openai_key_or_live_llm():
    env = os.environ.copy()
    for name in settings_module.Credentials.model_fields:
        if name.endswith("_api_key") or name in {
            "openai_api_key",
            "gemini_api_key",
            "xai_api_key",
            "sendgrid_from_email",
            "sendgrid_to_email",
        }:
            env[name.upper()] = ""
    env["ENABLE_EMAIL_DELIVERY"] = "false"

    result = subprocess.run(
        [sys.executable, "-m", "app.main", "--mode", "sample"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "[OK]" in result.stdout
    assert "run_id=" in result.stdout
