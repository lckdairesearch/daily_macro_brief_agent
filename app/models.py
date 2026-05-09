"""Core Pydantic models (typed pipeline contracts).

Canonical field definitions per architecture.md §8:
  - MarketSnapshot
  - CalendarEvent
  - EvidenceCard
  - BriefItem
  - BriefDraft
  - RunMetadata
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RunMode(str, Enum):
    """Pipeline run modes."""
    SAMPLE = "sample"
    LIVE = "live"
    DRY_RUN = "dry-run"


class FreshnessStatus(str, Enum):
    """Data freshness status."""
    FRESH = "fresh"
    STALE_CACHE = "stale_cache"
    LOW_RELIABILITY = "low_reliability"


class AssetClass(str, Enum):
    """Asset class categories."""
    EQUITY = "equity"
    RATES = "rates"
    FX = "fx"
    COMMODITY = "commodity"
    CREDIT = "credit"
    CRYPTO = "crypto"
    VOLATILITY = "volatility"


class SourceType(str, Enum):
    """Source types for evidence cards."""
    NEWS = "news"
    CENTRAL_BANK = "central_bank"
    RESEARCH = "research"
    PODCAST = "podcast"
    SOCIAL = "social"
    CALENDAR = "calendar"


class ConsensusMethod(str, Enum):
    """Methods for consensus determination."""
    INVESTING_FORECAST = "investing_forecast"
    SOURCE_EXTRACTED = "source_extracted"
    COMPUTED_FROM_SOURCE = "computed_from_source"


class BriefSection(str, Enum):
    """Brief section identifiers."""
    OVERNIGHT_DASHBOARD = "overnight_dashboard"
    THREE_THINGS = "three_things"
    TODAYS_CALENDAR = "todays_calendar"
    CHART = "chart"
    THEME_RADAR = "theme_radar"
    CONTRARIAN_CORNER = "contrarian_corner"


class DeliveryStatus(str, Enum):
    """Delivery status."""
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DISABLED = "disabled"


class MarketSnapshot(BaseModel):
    """Market data observation for dashboard."""
    
    as_of: datetime
    observation_date: str | None = None
    instrument_id: str
    display_name: str
    asset_class: AssetClass
    region: str
    last_price_or_level: float
    one_day_change: float
    one_day_change_unit: str  # "%" or "bps" or "abs"
    one_day_zscore: float | None = None
    threshold_flag: bool | None = None
    five_day_change: float | None = None
    five_day_change_unit: str | None = None
    source: str
    source_url: str | None = None
    freshness_status: FreshnessStatus
    warning: str | None = None


class CalendarEvent(BaseModel):
    """Economic/policy calendar event."""
    
    event_time_local: datetime
    event_time_hkt: datetime
    session: str  # "Asia", "Europe", "US"
    country_or_region: str
    event_name: str
    importance: int  # 1=low, 2=medium, 3=high
    consensus: float | str | None = None
    consensus_source: str | None = None
    consensus_source_url: str | None = None
    consensus_confidence: float | None = None  # 0.0 to 1.0
    consensus_method: ConsensusMethod | None = None
    consensus_formula: str | None = None
    consensus_inputs: dict[str, Any] | None = None
    previous: float | str | None = None
    missing_consensus: bool = False
    source: str
    source_url: str | None = None
    why_it_matters: str | None = None


class EvidenceCard(BaseModel):
    """Normalized source item for ranking/synthesis."""
    
    id: str
    title: str
    source_name: str
    source_type: SourceType
    url: str
    published_at: datetime | None = None
    retrieved_at: datetime
    author: str | None = None
    summary_raw: str | None = None
    thesis: str
    evidence: str
    macro_relevance: str
    portfolio_relevance: str
    novelty_score: float | None = None  # 0.0 to 1.0
    confidence: float  # 0.0 to 1.0
    tags: list[str] = Field(default_factory=list)


class BriefItem(BaseModel):
    """Item included in the final brief."""

    section: BriefSection
    headline: str
    body: str
    so_what: str | None = None
    supporting_market_ids: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    source_name: str | None = None
    source_url: str | None = None
    source_type: SourceType | None = None
    topic_label: str | None = None
    confidence: float | None = None  # 0.0 to 1.0
    validation_flags: list[str] = Field(default_factory=list)


class WriterItem(BaseModel):
    """LLM-produced brief item before section assignment. No section field so the LLM
    cannot return an invalid enum value; the writer assigns section after parsing."""

    headline: str
    body: str
    so_what: str | None = None
    supporting_market_ids: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    confidence: float | None = None


class BriefWriterOutput(BaseModel):
    """LLM output: only the sections the writer generates.
    Dashboard and calendar are pipeline pass-throughs and are NOT reproduced here."""

    book_impact: str | None = None
    three_things: list[WriterItem] = Field(default_factory=list)
    radar_items: list[WriterItem] = Field(default_factory=list)
    contrarian_corner: WriterItem | None = None
    chart_caption: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ChartSpec(BaseModel):
    """Chart specification for the brief."""

    title: str
    caption: str
    chart_type: str  # "line", "bar", "scatter", etc.
    data_source: str
    file_path: str | None = None
    themes: list[str] = Field(default_factory=list)
    code_generated: bool = False


class BriefDraft(BaseModel):
    """Structured brief before rendering."""
    
    run_metadata: dict[str, Any]
    book_impact: str | None = None
    overnight_dashboard: list[MarketSnapshot]
    three_things: list[BriefItem]
    todays_calendar: list[CalendarEvent]
    chart: ChartSpec | None = None
    radar_items: list[BriefItem] = Field(default_factory=list)
    contrarian_corner: BriefItem | None = None
    warnings: list[str] = Field(default_factory=list)


class LLMUsage(BaseModel):
    """LLM usage metadata."""
    
    model: str
    latency_seconds: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None


class RunMetadata(BaseModel):
    """Audit information for each run."""
    
    run_id: str
    run_started_at: datetime
    data_cutoff_at: datetime
    timezone: str
    config_hash: str | None = None
    git_commit: str | None = None
    llm_provider: str
    llm_model: str
    prompt_versions: dict[str, str] | None = None
    token_usage: list[LLMUsage] = Field(default_factory=list)
    estimated_cost_usd: float | None = None
    warnings: list[str] = Field(default_factory=list)
    failed_sources: list[str] = Field(default_factory=list)
    delivery_status: DeliveryStatus
    output_paths: dict[str, str] = Field(default_factory=dict)
    timings: list[dict[str, Any]] = Field(default_factory=list)


class PipelineResult(BaseModel):
    """Result of pipeline execution."""
    
    success: bool
    run_metadata: RunMetadata
    brief_draft: BriefDraft | None = None
    error_message: str | None = None


class DeliveryResult(BaseModel):
    """Result of email delivery."""
    
    success: bool
    delivery_id: str | None = None
    error_message: str | None = None
    sent_at: datetime | None = None
