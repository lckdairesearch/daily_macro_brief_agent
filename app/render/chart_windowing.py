"""Shared helpers for calendar-date chart slicing, shared windows, and ticks."""

from __future__ import annotations

from datetime import date, timedelta


def sort_rows_by_date(rows: list[dict]) -> list[dict]:
    """Return rows sorted by their observation date."""
    return sorted(rows, key=lambda row: row["date"])


def slice_rows_by_calendar_window(rows: list[dict], calendar_days: int) -> list[dict]:
    """Clip rows to the trailing calendar-day window using observation dates."""
    if not rows:
        return []
    ordered = sort_rows_by_date(rows)
    last_day = date.fromisoformat(ordered[-1]["date"])
    start_day = last_day - timedelta(days=calendar_days - 1)
    return [row for row in ordered if date.fromisoformat(row["date"]) >= start_day]


def resolve_shared_window(series_rows: dict[str, list[dict]], calendar_days: int) -> tuple[date, date]:
    """Return one shared chart window anchored to the latest observation date."""
    ordered_days = [
        date.fromisoformat(sort_rows_by_date(rows)[-1]["date"])
        for rows in series_rows.values()
        if rows
    ]
    if not ordered_days:
        raise ValueError("resolve_shared_window requires at least one populated series")
    window_end = max(ordered_days)
    window_start = window_end - timedelta(days=calendar_days - 1)
    return window_start, window_end


def clip_rows_to_window(rows: list[dict], window_start: date, window_end: date) -> list[dict]:
    """Clip rows to an explicit shared calendar window."""
    ordered = sort_rows_by_date(rows)
    return [
        row
        for row in ordered
        if window_start <= date.fromisoformat(row["date"]) <= window_end
    ]


def first_shared_observation_date(series_rows: dict[str, list[dict]]) -> date | None:
    """Return the first date where every selected series has an observation."""
    date_sets = [
        {date.fromisoformat(row["date"]) for row in rows}
        for rows in series_rows.values()
        if rows
    ]
    if not date_sets:
        return None
    shared = set.intersection(*date_sets)
    if not shared:
        return None
    return min(shared)


def build_window_ticks(window_start: date, window_end: date, daily: bool) -> list[date]:
    """Return calendar ticks for either daily or 1m-style Monday spacing."""
    if daily:
        days = (window_end - window_start).days
        return [window_start + timedelta(days=offset) for offset in range(days + 1)]

    first_monday = window_start
    while first_monday.weekday() != 0 and first_monday <= window_end:
        first_monday += timedelta(days=1)

    ticks: list[date] = []
    if first_monday <= window_end:
        if (first_monday - window_start).days >= 2:
            ticks.append(window_start)
        current = first_monday
        while current <= window_end:
            ticks.append(current)
            current += timedelta(days=7)
    else:
        ticks.append(window_start)
    return ticks


def split_contiguous_segments(rows: list[dict]) -> list[list[dict]]:
    """Split rows into contiguous date segments without bridging missing days."""
    ordered = sort_rows_by_date(rows)
    if not ordered:
        return []
    segments: list[list[dict]] = [[ordered[0]]]
    previous_day = date.fromisoformat(ordered[0]["date"])
    for row in ordered[1:]:
        current_day = date.fromisoformat(row["date"])
        if (current_day - previous_day).days > 1:
            segments.append([])
        segments[-1].append(row)
        previous_day = current_day
    return segments
