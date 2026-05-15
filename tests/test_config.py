"""Tests for config loading (Step 3)."""

from __future__ import annotations

import pytest

from app.models import RunMode
from app.settings import (
    CONFIG_DIR,
    AppConfig,
    Credentials,
    Settings,
    _load_yaml,
)


def test_app_yaml_loads():
    """app.yaml parses into AppConfig with expected field types and values."""
    raw = _load_yaml(CONFIG_DIR / "app.yaml")
    cfg = AppConfig(**raw)
    assert cfg.timezone == "Asia/Hong_Kong"
    assert cfg.data_cutoff_hkt == "08:00"
    assert cfg.send_time_hkt == "08:00"
    assert cfg.email_enabled is False
    assert isinstance(cfg.max_discovery_items, int)
    assert isinstance(cfg.max_theme_radar_items, int)
    assert cfg.output_dir == "outputs"
    assert cfg.cache_dir == ".cache"
    assert cfg.dashboard_core_instruments == ["SPY", "US10Y", "USDJPY", "GOLD", "WTI", "VIX"]
    assert cfg.dashboard_max_extra_movers == 4


def test_portfolio_yaml_loads():
    """portfolio.yaml loads as a dict with expected structure."""
    raw = _load_yaml(CONFIG_DIR / "portfolio.yaml")
    assert isinstance(raw, dict)
    assert "core_positions" in raw
    positions = raw["core_positions"]
    assert len(positions) > 0
    for pos in positions:
        assert "id" in pos
        assert "brief_aliases" in pos
        assert "sensitivity_tags" in pos


def test_themes_yaml_loads():
    """themes.yaml loads with a list of theme dicts containing required keys."""
    raw = _load_yaml(CONFIG_DIR / "themes.yaml")
    themes = raw.get("themes", [])
    assert len(themes) > 0
    for theme in themes:
        assert "id" in theme
        assert "label" in theme
        assert "keywords" in theme


def test_sources_yaml_loads():
    """sources.yaml loads with expected provider sections."""
    raw = _load_yaml(CONFIG_DIR / "sources.yaml")
    assert "market" in raw
    assert "calendar" in raw
    assert "llm" in raw
    assert "scouts" in raw
    assert "delivery" in raw
    assert raw["llm"]["provider"] == "litellm"
    assert raw["llm"]["scout_model"] == "openai/gpt-5.4"
    assert raw["llm"]["synthesis_model"] == "openai/gpt-5.5"
    assert raw["llm"]["chart_model"] == "openai/gpt-5.4"
    assert raw["llm"]["chart_selector_model"] == "openai/gpt-5.4-mini"
    assert raw["llm"]["chart_codegen_model"] == "openai/gpt-5.4"
    assert raw["llm"]["synthesis_review_model"] == "openai/gpt-5.4"
    assert raw["llm"]["synthesis_timeout_seconds"] == 180
    assert raw["llm"]["synthesis_max_tokens"] == 6000
    assert raw["llm"]["synthesis_review_timeout_seconds"] == 180
    assert raw["llm"]["synthesis_review_max_tokens"] == 2500
    assert raw["llm"]["synthesis_context_card_count"] == 10
    assert raw["llm"]["chart_selector_max_tokens"] == 1200
    assert raw["llm"]["chart_codegen_max_tokens"] == 3000
    assert raw["llm"]["x_scout_model"] == "xai/grok-4-1-fast-reasoning"


def test_chart_templates_yaml_loads():
    """chart_templates.yaml loads with valid templates referencing known theme IDs."""
    themes_raw = _load_yaml(CONFIG_DIR / "themes.yaml")
    known_theme_ids = {t["id"] for t in themes_raw.get("themes", [])}

    raw = _load_yaml(CONFIG_DIR / "chart_templates.yaml")
    templates = raw.get("templates", [])
    assert len(templates) > 0
    for t in templates:
        assert "id" in t
        assert "instruments" in t
        assert "theme_tags" in t
        for tag in t["theme_tags"]:
            assert tag in known_theme_ids, (
                f"chart_templates.yaml: '{tag}' in template '{t['id']}' "
                f"is not a known theme ID in themes.yaml"
            )


def test_secrets_not_in_yaml():
    """No YAML config file contains credential environment variable names."""
    credential_keys = [
        "OPENAI_API_KEY", "GEMINI_API_KEY", "XAI_API_KEY",
        "ALPHA_VANTAGE_API_KEY", "DATABENTO_API_KEY", "FRED_API_KEY",
        "LISTEN_NOTES_API_KEY", "POSTMARK_API_KEY",
        "CLOUDFLARE_R2_ACCOUNT_ID", "CLOUDFLARE_R2_ACCESS_KEY_ID",
        "CLOUDFLARE_R2_SECRET_ACCESS_KEY",
    ]
    yaml_files = [
        CONFIG_DIR / "app.yaml",
        CONFIG_DIR / "portfolio.yaml",
        CONFIG_DIR / "themes.yaml",
        CONFIG_DIR / "sources.yaml",
        CONFIG_DIR / "chart_templates.yaml",
    ]
    for yaml_path in yaml_files:
        content = yaml_path.read_text(encoding="utf-8")
        for key in credential_keys:
            assert key not in content, (
                f"Secret key '{key}' found in {yaml_path.name}"
            )


def test_settings_load_sample_mode_no_keys():
    """Settings.load() is valid in sample mode with no API keys."""
    settings = Settings.load()
    errors = settings.validate_for_mode(RunMode.SAMPLE)
    assert errors == []


def test_settings_validate_live_mode_reports_missing():
    """validate_for_mode(LIVE) reports missing required secret names."""
    app = AppConfig()
    creds = Credentials.model_construct(
        openai_api_key=None,
        alpha_vantage_api_key=None,
        enable_email_delivery=False,
        llm_scout_model=None,
        llm_x_scout_model=None,
        llm_synthesis_model=None,
        llm_temperature=None,
        llm_synthesis_temperature=None,
        llm_synthesis_reasoning_effort=None,
        llm_synthesis_verbosity=None,
        llm_synthesis_timeout_seconds=None,
        llm_synthesis_max_tokens=None,
        llm_synthesis_review_enabled=None,
        llm_synthesis_review_model=None,
        llm_synthesis_review_temperature=None,
        llm_synthesis_review_reasoning_effort=None,
        llm_synthesis_review_verbosity=None,
        llm_synthesis_review_timeout_seconds=None,
        llm_synthesis_review_max_tokens=None,
        llm_synthesis_context_card_count=None,
        llm_chart_selector_model=None,
        llm_chart_selector_max_tokens=None,
        llm_chart_codegen_model=None,
        llm_chart_codegen_max_tokens=None,
    )
    settings = Settings(app, creds, {}, {}, {}, {})
    errors = settings.validate_for_mode(RunMode.LIVE)
    assert "OPENAI_API_KEY" in errors
    assert "ALPHA_VANTAGE_API_KEY" in errors


def test_settings_validate_live_email_enabled_reports_postmark():
    """validate_for_mode(LIVE) reports Postmark keys when email delivery is enabled."""
    app = AppConfig()
    creds = Credentials.model_construct(
        openai_api_key="sk-test",
        alpha_vantage_api_key="av-test",
        enable_email_delivery=True,
        postmark_api_key=None,
        postmark_from_email=None,
        postmark_maintainer_email=None,
        llm_scout_model=None,
        llm_x_scout_model=None,
        llm_synthesis_model=None,
        llm_temperature=None,
        llm_synthesis_temperature=None,
        llm_synthesis_reasoning_effort=None,
        llm_synthesis_verbosity=None,
        llm_synthesis_timeout_seconds=None,
        llm_synthesis_max_tokens=None,
        llm_synthesis_review_enabled=None,
        llm_synthesis_review_model=None,
        llm_synthesis_review_temperature=None,
        llm_synthesis_review_reasoning_effort=None,
        llm_synthesis_review_verbosity=None,
        llm_synthesis_review_timeout_seconds=None,
        llm_synthesis_review_max_tokens=None,
        llm_synthesis_context_card_count=None,
        llm_chart_selector_model=None,
        llm_chart_selector_max_tokens=None,
        llm_chart_codegen_model=None,
        llm_chart_codegen_max_tokens=None,
    )
    settings = Settings(app, creds, {}, {}, {}, {})
    errors = settings.validate_for_mode(RunMode.LIVE)
    assert "POSTMARK_API_KEY" in errors
    assert "POSTMARK_FROM_EMAIL" in errors
    assert "recipients.yaml" in errors


def test_settings_validate_dry_run_email_enabled_reports_maintainer_postmark():
    """validate_for_mode(DRY_RUN) reports maintainer and Postmark keys when delivery is enabled."""
    app = AppConfig()
    creds = Credentials.model_construct(
        openai_api_key="sk-test",
        alpha_vantage_api_key="av-test",
        enable_email_delivery=True,
        postmark_api_key=None,
        postmark_from_email=None,
        postmark_maintainer_email=None,
        llm_scout_model=None,
        llm_x_scout_model=None,
        llm_synthesis_model=None,
        llm_temperature=None,
        llm_synthesis_temperature=None,
        llm_synthesis_reasoning_effort=None,
        llm_synthesis_verbosity=None,
        llm_synthesis_timeout_seconds=None,
        llm_synthesis_max_tokens=None,
        llm_synthesis_review_enabled=None,
        llm_synthesis_review_model=None,
        llm_synthesis_review_temperature=None,
        llm_synthesis_review_reasoning_effort=None,
        llm_synthesis_review_verbosity=None,
        llm_synthesis_review_timeout_seconds=None,
        llm_synthesis_review_max_tokens=None,
        llm_synthesis_context_card_count=None,
        llm_chart_selector_model=None,
        llm_chart_selector_max_tokens=None,
        llm_chart_codegen_model=None,
        llm_chart_codegen_max_tokens=None,
    )
    settings = Settings(app, creds, {}, {}, {}, {})
    errors = settings.validate_for_mode(RunMode.DRY_RUN)
    assert "POSTMARK_API_KEY" in errors
    assert "POSTMARK_FROM_EMAIL" in errors
    assert "POSTMARK_MAINTAINER_EMAIL" in errors
    assert "recipients.yaml" not in errors


def test_settings_load_resolves_all_yaml():
    """Settings.load() loads all five YAML files into the expected attributes."""
    settings = Settings.load()
    assert settings.app.timezone == "Asia/Hong_Kong"
    assert settings.app.data_cutoff_hkt == "08:00"
    assert settings.app.send_time_hkt == "08:00"
    assert isinstance(settings.portfolio, dict)
    assert isinstance(settings.themes, dict)
    assert isinstance(settings.sources, dict)
    assert isinstance(settings.chart_templates, dict)


def test_settings_applies_llm_env_overrides_to_sources():
    """Environment model overrides are merged into the source-of-truth LLM config."""
    app = AppConfig()
    creds = Credentials.model_construct(
        llm_scout_model="openai/gpt-5.5",
        llm_x_scout_model="xai/grok-4",
        llm_synthesis_model="openai/gpt-5.5",
        llm_temperature=0.1,
        llm_synthesis_temperature=0.05,
        llm_synthesis_reasoning_effort="high",
        llm_synthesis_verbosity="low",
        llm_synthesis_timeout_seconds=240,
        llm_synthesis_max_tokens=6200,
        llm_synthesis_review_enabled=True,
        llm_synthesis_review_model="openai/gpt-5.4",
        llm_synthesis_review_temperature=0.0,
        llm_synthesis_review_reasoning_effort="xhigh",
        llm_synthesis_review_verbosity="medium",
        llm_synthesis_review_timeout_seconds=300,
        llm_synthesis_review_max_tokens=2600,
        llm_synthesis_context_card_count=12,
        llm_chart_selector_model="openai/gpt-5.4-mini",
        llm_chart_selector_max_tokens=900,
        llm_chart_codegen_model="openai/gpt-5.4",
        llm_chart_codegen_max_tokens=3500,
    )
    settings = Settings(app, creds, {}, {}, {"llm": {}}, {})
    assert settings.sources["llm"]["scout_model"] == "openai/gpt-5.5"
    assert settings.sources["llm"]["x_scout_model"] == "xai/grok-4"
    assert settings.sources["llm"]["synthesis_model"] == "openai/gpt-5.5"
    assert settings.sources["llm"]["temperature"] == 0.1
    assert settings.sources["llm"]["synthesis_temperature"] == 0.05
    assert settings.sources["llm"]["synthesis_reasoning_effort"] == "high"
    assert settings.sources["llm"]["synthesis_verbosity"] == "low"
    assert settings.sources["llm"]["synthesis_timeout_seconds"] == 240
    assert settings.sources["llm"]["synthesis_max_tokens"] == 6200
    assert settings.sources["llm"]["synthesis_review_enabled"] is True
    assert settings.sources["llm"]["synthesis_review_model"] == "openai/gpt-5.4"
    assert settings.sources["llm"]["synthesis_review_temperature"] == 0.0
    assert settings.sources["llm"]["synthesis_review_reasoning_effort"] == "xhigh"
    assert settings.sources["llm"]["synthesis_review_verbosity"] == "medium"
    assert settings.sources["llm"]["synthesis_review_timeout_seconds"] == 300
    assert settings.sources["llm"]["synthesis_review_max_tokens"] == 2600
    assert settings.sources["llm"]["synthesis_context_card_count"] == 12
    assert settings.sources["llm"]["chart_selector_model"] == "openai/gpt-5.4-mini"
    assert settings.sources["llm"]["chart_selector_max_tokens"] == 900
    assert settings.sources["llm"]["chart_codegen_model"] == "openai/gpt-5.4"
    assert settings.sources["llm"]["chart_codegen_max_tokens"] == 3500


def test_settings_accepts_cloudflare_r2_credentials():
    """Cloudflare R2 hosting fields load through the Credentials model."""
    creds = Credentials.model_construct(
        cloudflare_r2_account_id="acct123",
        cloudflare_r2_access_key_id="ak",
        cloudflare_r2_secret_access_key="sk",
        cloudflare_r2_bucket="brief-charts",
        cloudflare_r2_public_base_url="https://pub.example.com",
        cloudflare_r2_prefix="charts",
    )
    assert creds.cloudflare_r2_account_id == "acct123"
    assert creds.cloudflare_r2_bucket == "brief-charts"
    assert creds.cloudflare_r2_public_base_url == "https://pub.example.com"
