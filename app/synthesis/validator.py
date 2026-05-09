"""Brief validator. Deterministic quality gate — runs after the LLM writer.

Failure severity:
  Critical  → hard-fails the run, suppresses delivery.
  Warning   → attaches a banner to the brief, delivery proceeds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models import BriefDraft, FreshnessStatus

# Matches numeric market-change patterns: -1.5%, +0.8%, 12 bps, 4.5bp
_MARKET_NUM_RE = re.compile(
    r"[+\-]?\d+\.?\d*\s*(?:%|bps?|basis\s+points?)\b", re.IGNORECASE
)
_URL_RE = re.compile(r"https?://\S+")


@dataclass
class ValidationResult:
    critical_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.critical_failures) == 0


def validate_brief(draft: BriefDraft) -> ValidationResult:
    """Run all checks and return a ValidationResult."""
    result = ValidationResult()
    valid_market_ids = {s.instrument_id for s in draft.overnight_dashboard}

    _check_required_sections(draft, result)
    _check_supporting_ids(draft, valid_market_ids, result)
    _check_unsupported_urls(draft, result)
    _check_unsupported_numbers(draft, result)
    _check_book_impact_numbers(draft, result)
    _check_word_limits(draft, result)
    _check_so_what_present(draft, result)
    _check_stale_disclosed(draft, result)
    _check_contrarian_present(draft, result)

    return result


# ---------------------------------------------------------------------------
# Critical checks
# ---------------------------------------------------------------------------

def _check_required_sections(draft: BriefDraft, result: ValidationResult) -> None:
    if not draft.overnight_dashboard:
        result.critical_failures.append("overnight_dashboard is empty")
    if not draft.three_things:
        result.critical_failures.append("three_things section is empty")
    if not draft.radar_items:
        result.critical_failures.append("theme_radar section is empty")


def _check_supporting_ids(
    draft: BriefDraft,
    valid_market_ids: set[str],
    result: ValidationResult,
) -> None:
    """All referenced market IDs must exist in the dashboard."""
    all_items = draft.three_things + draft.radar_items
    if draft.contrarian_corner:
        all_items = all_items + [draft.contrarian_corner]

    for item in all_items:
        for mid in item.supporting_market_ids:
            if mid not in valid_market_ids:
                result.critical_failures.append(
                    f"item '{item.headline[:50]}' references unknown market_id '{mid}'"
                )


def _check_unsupported_urls(draft: BriefDraft, result: ValidationResult) -> None:
    """URLs embedded in LLM-written body text must have backing evidence IDs."""
    all_items = draft.three_things + draft.radar_items
    if draft.contrarian_corner:
        all_items = all_items + [draft.contrarian_corner]

    for item in all_items:
        if _URL_RE.search(item.body) and not item.supporting_evidence_ids:
            result.critical_failures.append(
                f"item '{item.headline[:50]}' contains a URL but has no supporting_evidence_ids"
            )


def _check_unsupported_numbers(draft: BriefDraft, result: ValidationResult) -> None:
    """Market-number patterns in body text must be backed by supporting IDs."""
    all_items = draft.three_things + draft.radar_items
    if draft.contrarian_corner:
        all_items = all_items + [draft.contrarian_corner]

    for item in all_items:
        if (
            _MARKET_NUM_RE.search(item.body)
            and not item.supporting_market_ids
            and not item.supporting_evidence_ids
        ):
            result.critical_failures.append(
                f"item '{item.headline[:50]}' contains market numbers "
                "but has no supporting_market_ids or supporting_evidence_ids"
            )


def _check_book_impact_numbers(draft: BriefDraft, result: ValidationResult) -> None:
    """Book impact is narrative-only in V1 because no exposure model exists."""
    if not draft.book_impact:
        return
    if _MARKET_NUM_RE.search(draft.book_impact) or "$" in draft.book_impact:
        result.critical_failures.append(
            "book_impact contains numeric impact language, but V1 has no exposure model"
        )


# ---------------------------------------------------------------------------
# Minor checks (warnings)
# ---------------------------------------------------------------------------

def _check_word_limits(draft: BriefDraft, result: ValidationResult) -> None:
    for item in draft.three_things:
        wc = _word_count(item.body)
        if wc > 80:
            result.warnings.append(
                f"three_things '{item.headline[:40]}' body is {wc} words (limit 80)"
            )

    for item in draft.radar_items:
        wc = _word_count(item.body)
        if wc < 60 or wc > 100:
            result.warnings.append(
                f"radar_items '{item.headline[:40]}' body is {wc} words (expected 60–100)"
            )

    if draft.contrarian_corner:
        wc = _word_count(draft.contrarian_corner.body)
        if wc < 50 or wc > 100:
            result.warnings.append(
                f"contrarian_corner body is {wc} words (expected 50–100)"
            )

    if draft.chart and draft.chart.caption:
        wc = _word_count(draft.chart.caption)
        if wc < 10:
            result.warnings.append(
                f"chart caption is {wc} words (minimum 10)"
            )
        if wc > 30:
            result.warnings.append(
                f"chart caption is {wc} words (limit 30)"
            )


def _check_so_what_present(draft: BriefDraft, result: ValidationResult) -> None:
    for item in draft.three_things:
        if not item.so_what:
            result.warnings.append(
                f"three_things '{item.headline[:40]}' is missing a so_what"
            )
    for item in draft.radar_items:
        if not item.so_what:
            result.warnings.append(
                f"radar_items '{item.headline[:40]}' is missing a so_what"
            )


def _check_stale_disclosed(draft: BriefDraft, result: ValidationResult) -> None:
    stale_ids = [
        s.instrument_id for s in draft.overnight_dashboard
        if s.freshness_status == FreshnessStatus.STALE_CACHE
    ]
    if not stale_ids:
        return

    # Check that draft.warnings mentions at least one stale instrument
    warnings_text = " ".join(draft.warnings).lower()
    for sid in stale_ids:
        if sid.lower() not in warnings_text and "stale" not in warnings_text:
            result.warnings.append(
                f"stale instrument '{sid}' present but not disclosed in brief warnings"
            )


def _check_contrarian_present(draft: BriefDraft, result: ValidationResult) -> None:
    if draft.contrarian_corner is None:
        result.warnings.append("contrarian_corner is absent")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _word_count(text: str) -> int:
    return len(text.split())
