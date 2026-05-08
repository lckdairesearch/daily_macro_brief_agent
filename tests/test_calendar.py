"""Tests for calendar parsing, filtering, caching, and consensus handling."""

from __future__ import annotations

import json
from datetime import date

from app.data.calendar import (
    ConsensusCandidate,
    InvestingCalendarProvider,
    apply_consensus_candidate,
    load_fixture_calendar,
    normalize_investing_payload,
    validate_consensus_candidate,
)
from app.models import CalendarEvent, ConsensusMethod


def _payload() -> dict:
    return {
        "events": [
            {
                "event_id": 227,
                "country_id": 5,
                "event_translated": "Nonfarm Payrolls",
                "importance": "high",
                "page_link": "nonfarm-payrolls-227",
            },
            {
                "event_id": 901,
                "country_id": 5,
                "event_translated": "Durable Goods Orders",
                "importance": "high",
                "page_link": "durable-goods-orders-901",
            },
            {
                "event_id": 902,
                "country_id": 72,
                "event_translated": "ECB President Lagarde Speaks",
                "importance": "medium",
                "page_link": "ecb-president-lagarde-speaks-902",
            },
            {
                "event_id": 903,
                "country_id": 25,
                "event_translated": "Australia Retail Sales",
                "importance": "medium",
                "page_link": "australia-retail-sales-903",
            },
        ],
        "occurrences": [
            {
                "event_id": 227,
                "occurrence_id": 1,
                "occurrence_time": "2026-05-08T12:30:00Z",
                "forecast": 65,
                "previous": 178,
                "unit": "K",
                "reference_period": "Apr",
            },
            {
                "event_id": 901,
                "occurrence_id": 2,
                "occurrence_time": "2026-05-08T14:00:00Z",
                "previous": 1.2,
                "unit": "%",
                "reference_period": "Apr",
            },
            {
                "event_id": 902,
                "occurrence_id": 3,
                "occurrence_time": "2026-05-08T07:00:00Z",
            },
            {
                "event_id": 903,
                "occurrence_id": 4,
                "occurrence_time": "2026-05-08T01:30:00Z",
                "forecast": 0.3,
                "previous": 0.2,
                "unit": "%",
            },
        ],
        "next_page_cursor": None,
    }


def test_fixture_calendar_loader_returns_calendar_events():
    events = load_fixture_calendar()

    assert events
    assert all(isinstance(event, CalendarEvent) for event in events)
    assert any(event.missing_consensus is False for event in events)


def test_investing_payload_normalization_joins_metadata_and_occurrences():
    events = normalize_investing_payload(
        _payload(),
        filter_countries=["US"],
        filter_importance=["high"],
    )

    nfp = next(event for event in events if event.event_name == "Nonfarm Payrolls (Apr)")
    assert nfp.event_time_local.isoformat() == "2026-05-08T08:30:00-04:00"
    assert nfp.event_time_hkt.isoformat() == "2026-05-08T20:30:00+08:00"
    assert nfp.session == "US"
    assert nfp.country_or_region == "US"
    assert nfp.importance == 3


def test_forecast_maps_to_consensus_with_investing_metadata():
    events = normalize_investing_payload(
        _payload(),
        filter_countries=["US"],
        filter_importance=["high"],
    )

    nfp = next(event for event in events if event.event_name == "Nonfarm Payrolls (Apr)")
    assert nfp.consensus == "65K"
    assert nfp.previous == "178K"
    assert nfp.consensus_source == "Investing.com"
    assert nfp.consensus_method == ConsensusMethod.INVESTING_FORECAST
    assert nfp.missing_consensus is False


def test_missing_high_importance_non_speech_forecast_is_flagged():
    events = normalize_investing_payload(
        _payload(),
        filter_countries=["US"],
        filter_importance=["high"],
    )

    durable_goods = next(
        event for event in events if event.event_name == "Durable Goods Orders (Apr)"
    )
    assert durable_goods.consensus is None
    assert durable_goods.missing_consensus is True


def test_speech_event_without_forecast_is_not_flagged_missing_consensus():
    events = normalize_investing_payload(
        _payload(),
        filter_countries=["EU"],
        filter_importance=["medium"],
    )

    speech = next(event for event in events if event.event_name == "ECB President Lagarde Speaks")
    assert speech.consensus is None
    assert speech.missing_consensus is False


def test_country_and_importance_filters_apply_after_ingestion():
    events = normalize_investing_payload(
        _payload(),
        filter_countries=["US"],
        filter_importance=["medium"],
    )

    assert events == []


def test_cache_is_used_and_network_is_not_called(tmp_path):
    cache_file = tmp_path / "calendar_2026-05-08.json"
    cache_file.write_text(json.dumps(_payload()), encoding="utf-8")

    class NoNetworkSession:
        def get(self, *args, **kwargs):  # pragma: no cover - should never run
            raise AssertionError("network should not be called when same-day cache exists")

    provider = InvestingCalendarProvider(
        config={"cache_daily": True, "filter_countries": ["US"], "filter_importance": ["high"]},
        cache_dir=tmp_path,
        session=NoNetworkSession(),
    )

    events = provider.fetch_for_date(date(2026, 5, 8))
    assert len(events) == 2


def test_request_country_ids_derive_from_configured_filter_when_not_explicit(tmp_path):
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return _payload()

    class CapturingSession:
        def __init__(self) -> None:
            self.params = None

        def get(self, _endpoint, params, headers, timeout):
            self.params = params
            return FakeResponse()

    session = CapturingSession()
    provider = InvestingCalendarProvider(
        config={
            "cache_daily": False,
            "filter_countries": ["US", "EU"],
            "filter_importance": ["high"],
        },
        cache_dir=tmp_path,
        session=session,
    )

    provider.fetch_for_date(date(2026, 5, 8))
    assert session.params["country_ids"] == "5,72"


def test_source_extracted_candidate_rejected_without_source_url_and_confidence():
    event = _unresolved_event()
    candidate = ConsensusCandidate(
        value="0.4%",
        method=ConsensusMethod.SOURCE_EXTRACTED,
        source="Example",
    )

    assert validate_consensus_candidate(candidate) is False
    enriched = apply_consensus_candidate(event, candidate)
    assert enriched.consensus is None
    assert enriched.missing_consensus is True


def test_computed_candidate_rejected_without_formula_or_source_backed_inputs():
    candidate = ConsensusCandidate(
        value="2.1%",
        method=ConsensusMethod.COMPUTED_FROM_SOURCE,
        inputs={"forecast": {"value": 2.1}},
    )

    assert validate_consensus_candidate(candidate) is False


def test_valid_enrichment_candidate_applies_and_clears_missing_flag():
    event = _unresolved_event()
    candidate = ConsensusCandidate(
        value="0.4%",
        method=ConsensusMethod.SOURCE_EXTRACTED,
        source="Example Data",
        source_url="https://example.test/calendar",
        confidence=0.8,
    )

    enriched = apply_consensus_candidate(event, candidate)
    assert enriched.consensus == "0.4%"
    assert enriched.consensus_source == "Example Data"
    assert enriched.consensus_source_url == "https://example.test/calendar"
    assert enriched.consensus_confidence == 0.8
    assert enriched.consensus_method == ConsensusMethod.SOURCE_EXTRACTED
    assert enriched.missing_consensus is False


def _unresolved_event() -> CalendarEvent:
    return CalendarEvent.model_validate(
        {
            "event_time_local": "2026-05-08T20:30:00+08:00",
            "event_time_hkt": "2026-05-08T20:30:00+08:00",
            "session": "US",
            "country_or_region": "US",
            "event_name": "Durable Goods Orders (Apr)",
            "importance": 3,
            "missing_consensus": True,
            "source": "Investing.com",
        }
    )
