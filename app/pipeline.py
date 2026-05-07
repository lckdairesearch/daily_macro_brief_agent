"""Pipeline orchestrator. Owns the run sequence; no provider-specific logic here."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.settings import Settings


@dataclass
class PipelineResult:
    success: bool
    artifacts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def run_pipeline(mode: str, settings: "Settings") -> PipelineResult:
    """
    Main pipeline. Steps:
      1. Load settings and config
      2. Determine run window and data cutoff
      3. Fetch market data
      4. Fetch today's calendar
      5. Enrich missing consensus values
      6. Discover source evidence
      7. Normalize to EvidenceCards
      8. Deduplicate and rank
      9. Generate/synthesize brief sections
      10. Validate
      11. Render HTML/text/chart
      12. Save artifacts
      13. Deliver email if enabled
      14. Record cost/run metadata
    """
    # Stub — to be implemented in later steps
    raise NotImplementedError("Pipeline not yet implemented — see plan.md Steps 2–7")
