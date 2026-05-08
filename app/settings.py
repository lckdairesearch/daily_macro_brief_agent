"""Application settings loaded from YAML configs and environment variables."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models import RunMode

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "app" / "config"


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


class AppConfig(BaseModel):
    """Non-secret runtime behavior from app.yaml."""

    model_config = ConfigDict(extra="ignore")

    timezone: str = "Asia/Hong_Kong"
    data_cutoff_hkt: str = "06:45"
    send_time_hkt: str = "07:15"
    default_mode: RunMode = RunMode.SAMPLE
    output_dir: str = "outputs"
    cache_dir: str = ".cache"
    email_enabled: bool = False
    max_discovery_items: int = 30
    max_theme_radar_items: int = 3


class Credentials(BaseSettings):
    """Secrets and deployment overrides from environment variables only."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM — required for live/dry-run
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    xai_api_key: str | None = None

    # LLM optional overrides (env vars override sources.yaml defaults)
    llm_scout_model: str | None = None
    llm_x_scout_model: str | None = None
    llm_synthesis_model: str | None = None
    llm_temperature: float | None = None

    # Market data — required for live/dry-run
    alpha_vantage_api_key: str | None = None
    databento_api_key: str | None = None
    fred_api_key: str | None = None

    # Discovery — required for live/dry-run
    listen_notes_api_key: str | None = None  # kept for env compat; no longer used by podcast scout
    taddy_user_id: str | None = None
    taddy_api_key: str | None = None

    # Delivery — required for live email send
    sendgrid_api_key: str | None = None
    sendgrid_from_email: str | None = None
    sendgrid_to_email: str | None = None
    enable_email_delivery: bool = False

    # Deployment-specific overrides
    app_mode: str | None = None
    cache_dir: str | None = None
    output_dir: str | None = None


class Settings:
    """Combined runtime settings: YAML config merged with environment variables."""

    def __init__(
        self,
        app: AppConfig,
        creds: Credentials,
        portfolio: dict[str, Any],
        themes: dict[str, Any],
        sources: dict[str, Any],
        chart_templates: dict[str, Any],
        config_dir: Path = CONFIG_DIR,
    ) -> None:
        self.app = app
        self.creds = creds
        self.portfolio = portfolio
        self.themes = themes
        self.sources = sources
        self.chart_templates = chart_templates
        self.config_dir = config_dir

        llm = self.sources.setdefault("llm", {})
        if creds.llm_scout_model:
            llm["scout_model"] = creds.llm_scout_model
        if creds.llm_x_scout_model:
            llm["x_scout_model"] = creds.llm_x_scout_model
        if creds.llm_synthesis_model:
            llm["synthesis_model"] = creds.llm_synthesis_model
        if creds.llm_temperature is not None:
            llm["temperature"] = creds.llm_temperature

        # Env var overrides for deployment-specific fields
        if creds.cache_dir:
            object.__setattr__(self.app, "cache_dir", creds.cache_dir)
        if creds.output_dir:
            object.__setattr__(self.app, "output_dir", creds.output_dir)

    @classmethod
    def load(cls, config_dir: Path = CONFIG_DIR) -> "Settings":
        """Load all YAML configs and merge with environment variables."""
        app = AppConfig(**_load_yaml(config_dir / "app.yaml"))
        creds = Credentials()
        portfolio = _load_yaml(config_dir / "portfolio.yaml")
        themes = _load_yaml(config_dir / "themes.yaml")
        sources = _load_yaml(config_dir / "sources.yaml")
        chart_templates = _load_yaml(config_dir / "chart_templates.yaml")
        return cls(app, creds, portfolio, themes, sources, chart_templates, config_dir)

    def validate_for_mode(self, mode: RunMode) -> list[str]:
        """Return names of missing required secrets for the given mode.

        Empty list means the settings are valid for the mode.
        Sample mode uses fixture data and a pre-baked LLM response — no credentials needed.
        """
        missing: list[str] = []

        if mode == RunMode.SAMPLE:
            return missing

        if not self.creds.openai_api_key:
            missing.append("OPENAI_API_KEY")
        if not self.creds.alpha_vantage_api_key:
            missing.append("ALPHA_VANTAGE_API_KEY")

        if mode == RunMode.LIVE and self.creds.enable_email_delivery:
            for name, val in [
                ("SENDGRID_API_KEY", self.creds.sendgrid_api_key),
                ("SENDGRID_FROM_EMAIL", self.creds.sendgrid_from_email),
                ("SENDGRID_TO_EMAIL", self.creds.sendgrid_to_email),
            ]:
                if not val:
                    missing.append(name)

        return missing
