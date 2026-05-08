"""Economic calendar fetching, normalization, and consensus guardrails."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from app.models import CalendarEvent, ConsensusMethod

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "calendar_sample.json"

INVESTING_ENDPOINT = (
    "https://endpoints.investing.com/pd-instruments/v1/calendars/"
    "economic/events/occurrences"
)
INVESTING_EVENT_URL_BASE = "https://www.investing.com/economic-calendar/"

IMPORTANCE_TO_INT = {
    "low": 1,
    "medium": 2,
    "high": 3,
}
INT_TO_IMPORTANCE = {value: key for key, value in IMPORTANCE_TO_INT.items()}

COUNTRY_ID_TO_REGION = {
    4: "GB",
    5: "US",
    6: "CA",
    10: "IT",
    11: "KR",
    12: "CH",
    14: "IN",
    17: "DE",
    22: "FR",
    25: "AU",
    26: "ES",
    32: "BR",
    35: "JP",
    36: "NZ",
    37: "CN",
    39: "HK",
    43: "MX",
    56: "SG",
    72: "EU",
    110: "ZA",
}
REGION_TO_COUNTRY_ID = {region: country_id for country_id, region in COUNTRY_ID_TO_REGION.items()}
REGION_TO_TIMEZONE = {
    "AU": "Australia/Sydney",
    "BR": "America/Sao_Paulo",
    "CA": "America/Toronto",
    "CH": "Europe/Zurich",
    "CN": "Asia/Shanghai",
    "DE": "Europe/Berlin",
    "ES": "Europe/Madrid",
    "EU": "Europe/Brussels",
    "FR": "Europe/Paris",
    "GB": "Europe/London",
    "HK": "Asia/Hong_Kong",
    "IN": "Asia/Kolkata",
    "IT": "Europe/Rome",
    "JP": "Asia/Tokyo",
    "KR": "Asia/Seoul",
    "MX": "America/Mexico_City",
    "NZ": "Pacific/Auckland",
    "SG": "Asia/Singapore",
    "US": "America/New_York",
    "ZA": "Africa/Johannesburg",
}
DEFAULT_COUNTRY_IDS = [
    72,
    17,
    35,
    37,
    5,
    4,
]


class CalendarIngestionError(RuntimeError):
    """Raised when a live calendar response cannot be fetched or parsed."""


@dataclass(frozen=True)
class ConsensusCandidate:
    """Candidate consensus value returned by a source-backed enrichment path."""

    value: str | float | int | None
    method: ConsensusMethod
    source: str | None = None
    source_url: str | None = None
    confidence: float | None = None
    formula: str | None = None
    inputs: dict[str, Any] | None = None


class InvestingCalendarProvider:
    """Small wrapper around the Investing.com economic calendar endpoint."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        cache_dir: str | Path | None = None,
        session: requests.Session | None = None,
        timezone_name: str = "Asia/Hong_Kong",
    ) -> None:
        self.config = config or {}
        self.cache_dir = Path(cache_dir) if cache_dir else REPO_ROOT / ".cache" / "calendar"
        self.session = session or requests.Session()
        self.timezone = ZoneInfo(timezone_name)

    def fetch_for_date(
        self,
        target_date: date | datetime | str | None = None,
    ) -> list[CalendarEvent]:
        """Fetch or load cached raw calendar data for one HKT date."""
        day = self._coerce_date(target_date)
        cache_path = self.cache_dir / f"calendar_{day.isoformat()}.json"

        if self.config.get("cache_daily", True) and cache_path.exists():
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            payload = self._request_payload(day)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

        return normalize_investing_payload(
            payload,
            timezone_name=str(self.timezone),
            filter_countries=self.config.get("filter_countries"),
            filter_importance=self.config.get("filter_importance"),
        )

    def _request_payload(self, day: date) -> dict[str, Any]:
        endpoint = self.config.get("endpoint_url", INVESTING_ENDPOINT)
        country_ids = self.config.get("country_ids") or _country_ids_for_regions(
            self.config.get("filter_countries")
        )
        if not country_ids:
            country_ids = DEFAULT_COUNTRY_IDS
        params = {
            "domain_id": self.config.get("domain_id", 1),
            "limit": self.config.get("limit", 200),
            "start_date": f"{day.isoformat()}T00:00:00.000+08:00",
            "end_date": f"{day.isoformat()}T23:59:59.999+08:00",
            "country_ids": ",".join(str(country_id) for country_id in country_ids),
        }
        headers = {
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        }

        try:
            response = self.session.get(endpoint, params=params, headers=headers, timeout=20)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # pragma: no cover - exact requests failures vary
            raise CalendarIngestionError("Failed to fetch Investing.com calendar payload") from exc

        if not isinstance(payload, dict):
            raise CalendarIngestionError("Investing.com calendar payload is not a JSON object")
        return payload

    def _coerce_date(self, target_date: date | datetime | str | None) -> date:
        if target_date is None:
            return datetime.now(self.timezone).date()
        if isinstance(target_date, datetime):
            dt = target_date if target_date.tzinfo else target_date.replace(tzinfo=self.timezone)
            return dt.astimezone(self.timezone).date()
        if isinstance(target_date, date):
            return target_date
        return date.fromisoformat(target_date)


def load_fixture_calendar(path: str | Path = FIXTURE_PATH) -> list[CalendarEvent]:
    """Load deterministic fixture calendar events for sample mode and tests."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [CalendarEvent.model_validate(event) for event in raw["events"]]


def normalize_investing_payload(
    payload: dict[str, Any],
    timezone_name: str = "Asia/Hong_Kong",
    filter_countries: list[str] | tuple[str, ...] | set[str] | None = None,
    filter_importance: list[str | int] | tuple[str | int, ...] | set[str | int] | None = None,
) -> list[CalendarEvent]:
    """Join Investing.com event metadata to occurrences and normalize models."""
    tz = ZoneInfo(timezone_name)
    metadata_by_id = {}
    for event in payload.get("events", []):
        if event.get("event_id"):
            metadata_by_id[str(event.get("event_id"))] = event
    allowed_countries = {country.upper() for country in filter_countries or []}
    allowed_importance = _importance_filter(filter_importance)

    normalized: list[CalendarEvent] = []
    for occurrence in payload.get("occurrences", []):
        event_meta = metadata_by_id.get(str(occurrence.get("event_id")), {})
        importance = _importance_to_int(event_meta.get("importance"))
        country_or_region = COUNTRY_ID_TO_REGION.get(event_meta.get("country_id"), "UNKNOWN")

        if allowed_countries and country_or_region.upper() not in allowed_countries:
            continue
        if allowed_importance and importance not in allowed_importance:
            continue

        event_time_utc = _parse_utc(occurrence.get("occurrence_time"))
        event_time_hkt = event_time_utc.astimezone(tz)
        forecast = occurrence.get("forecast")
        previous = occurrence.get("previous")
        unit = occurrence.get("unit")
        has_forecast = _has_value(forecast)
        name = _event_name(event_meta, occurrence)
        is_speech = _is_speech_event(name, event_meta)
        source_url = _source_url(event_meta)

        normalized.append(
            CalendarEvent(
                event_time_local=_local_time_for_region(event_time_utc, country_or_region),
                event_time_hkt=event_time_hkt,
                session=_session_for_hkt(event_time_hkt),
                country_or_region=country_or_region,
                event_name=name,
                importance=importance,
                consensus=_format_value(forecast, unit) if has_forecast else None,
                consensus_source="Investing.com" if has_forecast else None,
                consensus_source_url=source_url if has_forecast else None,
                consensus_method=ConsensusMethod.INVESTING_FORECAST if has_forecast else None,
                previous=_format_value(previous, unit) if _has_value(previous) else None,
                missing_consensus=importance == 3 and not has_forecast and not is_speech,
                source="Investing.com",
                source_url=source_url,
                why_it_matters=_why_it_matters(name, importance, is_speech),
            )
        )

    return sorted(normalized, key=lambda event: event.event_time_hkt)


def validate_consensus_candidate(candidate: ConsensusCandidate | None) -> bool:
    """Return True only when a consensus candidate is source-backed as required."""
    if candidate is None or not _has_value(candidate.value):
        return False

    method = ConsensusMethod(candidate.method)
    if method == ConsensusMethod.INVESTING_FORECAST:
        return True
    if method == ConsensusMethod.SOURCE_EXTRACTED:
        return (
            bool(candidate.source_url)
            and candidate.confidence is not None
            and 0 <= candidate.confidence <= 1
        )
    if method == ConsensusMethod.COMPUTED_FROM_SOURCE:
        return bool(candidate.formula) and _inputs_are_source_backed(candidate.inputs)
    return False


def apply_consensus_candidate(
    event: CalendarEvent,
    candidate: ConsensusCandidate | None,
) -> CalendarEvent:
    """Apply a valid consensus candidate or keep the event explicitly unresolved."""
    if not validate_consensus_candidate(candidate):
        return event.model_copy(
            update={
                "consensus": None,
                "consensus_source": None,
                "consensus_source_url": None,
                "consensus_confidence": None,
                "consensus_method": None,
                "consensus_formula": None,
                "consensus_inputs": None,
                "missing_consensus": True,
            }
        )

    assert candidate is not None
    method = ConsensusMethod(candidate.method)
    source = candidate.source
    if not source and method == ConsensusMethod.INVESTING_FORECAST:
        source = "Investing.com"
    return event.model_copy(
        update={
            "consensus": candidate.value,
            "consensus_source": source,
            "consensus_source_url": candidate.source_url,
            "consensus_confidence": candidate.confidence,
            "consensus_method": method,
            "consensus_formula": candidate.formula,
            "consensus_inputs": candidate.inputs,
            "missing_consensus": False,
        }
    )


def _importance_filter(
    raw_filter: list[str | int] | tuple[str | int, ...] | set[str | int] | None,
) -> set[int]:
    allowed: set[int] = set()
    for value in raw_filter or []:
        if isinstance(value, int):
            allowed.add(value)
        else:
            allowed.add(IMPORTANCE_TO_INT[value.lower()])
    return allowed


def _country_ids_for_regions(regions: list[str] | tuple[str, ...] | set[str] | None) -> list[int]:
    country_ids: list[int] = []
    for region in regions or []:
        country_id = REGION_TO_COUNTRY_ID.get(region.upper())
        if country_id is not None:
            country_ids.append(country_id)
    return country_ids


def _importance_to_int(raw_importance: Any) -> int:
    if isinstance(raw_importance, int):
        return raw_importance
    if isinstance(raw_importance, str):
        return IMPORTANCE_TO_INT.get(raw_importance.lower(), 1)
    return 1


def _parse_utc(raw_timestamp: str | None) -> datetime:
    if not raw_timestamp:
        raise CalendarIngestionError("Calendar occurrence is missing occurrence_time")
    parsed = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_value(value: Any, unit: Any = None) -> str:
    if not _has_value(value):
        return ""
    text = f"{value:g}" if isinstance(value, float) else str(value)
    if unit is None or unit == "":
        return text
    return f"{text}{unit}"


def _session_for_hkt(event_time_hkt: datetime) -> str:
    hour = event_time_hkt.hour
    if hour < 14:
        return "Asia"
    if hour < 20:
        return "Europe"
    return "US"


def _local_time_for_region(event_time_utc: datetime, country_or_region: str) -> datetime:
    timezone_name = REGION_TO_TIMEZONE.get(country_or_region, "Asia/Hong_Kong")
    return event_time_utc.astimezone(ZoneInfo(timezone_name))


def _event_name(event_meta: dict[str, Any], occurrence: dict[str, Any]) -> str:
    base = (
        event_meta.get("event_translated")
        or event_meta.get("long_name")
        or event_meta.get("short_name")
        or event_meta.get("event_meta_title")
        or "Unknown calendar event"
    )
    reference_period = occurrence.get("reference_period")
    if reference_period and f"({reference_period})" not in base:
        return f"{base} ({reference_period})"
    return str(base)


def _is_speech_event(name: str, event_meta: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(part).lower()
        for part in [
            name,
            event_meta.get("category"),
            event_meta.get("event_type"),
            event_meta.get("long_name"),
            event_meta.get("short_name"),
        ]
        if part
    )
    return any(token in haystack for token in ["speak", "speech", "testimony", "testifies"])


def _source_url(event_meta: dict[str, Any]) -> str | None:
    for field in ("source_url", "page_link"):
        value = event_meta.get(field)
        if not value:
            continue
        if str(value).startswith(("http://", "https://")):
            return str(value)
        return f"{INVESTING_EVENT_URL_BASE}{str(value).lstrip('/')}"
    return None


def _why_it_matters(name: str, importance: int, is_speech: bool) -> str | None:
    if is_speech:
        return "Policy communication can shift rate-path expectations and cross-asset risk pricing."
    if importance == 3:
        return (
            "High-importance macro release with potential to move rates, FX, equities, "
            "and policy expectations."
        )
    if importance == 2:
        return (
            "Medium-importance macro release that can refine the session's growth and "
            "inflation read."
        )
    return None


def _inputs_are_source_backed(inputs: dict[str, Any] | None) -> bool:
    if not inputs:
        return False
    for input_value in inputs.values():
        if not isinstance(input_value, dict):
            return False
        if not _has_value(input_value.get("value")) or not input_value.get("source_url"):
            return False
    return True


def _has_value(value: Any) -> bool:
    return value is not None and value != ""
