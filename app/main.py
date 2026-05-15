"""CLI entry point. Parses --mode and calls run_pipeline()."""

import argparse
import sys
import warnings
from datetime import datetime
from zoneinfo import ZoneInfo

# litellm calls asyncio.get_event_loop() at import time — harmless noise in Python 3.10+.
warnings.filterwarnings("ignore", message="There is no current event loop")

from app.models import RunMode
from app.pipeline import run_pipeline
from app.settings import Settings

_PROGRESS_STEPS = 13


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily Macro Brief Agent")
    parser.add_argument(
        "--mode",
        choices=["sample", "live", "dry-run"],
        default="sample",
        help="Run mode: sample (fixtures), live (APIs + delivery), dry-run (APIs, no delivery)",
    )
    parser.add_argument(
        "--data-cutoff",
        help=(
            "Optional data cutoff datetime. Naive values are interpreted in the configured "
            "app timezone. A bare date uses the configured morning cutoff time. "
            "Examples: 2026-05-08, 2026-05-08T08:00, '2026-05-08 08:00', "
            "2026-05-08T00:00:00Z"
        ),
    )
    args = parser.parse_args()
    mode = RunMode(args.mode)

    settings = Settings.load()
    try:
        data_cutoff = _parse_data_cutoff(
            args.data_cutoff,
            ZoneInfo(settings.app.timezone),
            settings.app.data_cutoff_hkt,
        )
    except ValueError as exc:
        parser.error(str(exc))

    missing = settings.validate_for_mode(mode)
    if missing:
        print(
            f"ERROR: Missing required credentials for {mode} mode: {', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        result = run_pipeline(
            mode=mode,
            settings=settings,
            data_cutoff=data_cutoff,
            progress=_progress_printer(),
        )
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)

    meta = result.run_metadata
    status = "OK" if result.success else "FAILED"
    print(f"[{status}] run_id={meta.run_id} mode={mode} warnings={len(meta.warnings)}")
    if meta.output_paths:
        for name, path in meta.output_paths.items():
            print(f"  {name}: {path}")
    if meta.timings:
        _print_timing_table(meta.timings)

    if not result.success:
        if result.error_message:
            print(f"ERROR: {result.error_message}", file=sys.stderr)
        sys.exit(1)


def _parse_data_cutoff(
    value: str | None,
    tz: ZoneInfo,
    default_time_hhmm: str = "08:00",
) -> datetime | None:
    if not value:
        return None

    normalized = value.strip()
    if len(normalized) == 10:
        try:
            parsed_date = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError(
                "Invalid --data-cutoff. Use ISO format like 2026-05-08, "
                "2026-05-08T08:00, or '2026-05-08 08:00'."
            ) from exc
        hour, minute = (int(part) for part in default_time_hhmm.split(":"))
        return parsed_date.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=tz)

    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(
            "Invalid --data-cutoff. Use ISO format like 2026-05-08, "
            "2026-05-08T08:00, or '2026-05-08 08:00'."
        ) from exc

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)

    return parsed.astimezone(tz)


def _progress_printer():
    step = 0

    def _print(message: str) -> None:
        nonlocal step
        step += 1
        print(f"[{step}/{_PROGRESS_STEPS}] {message}", flush=True)

    return _print


def _print_timing_table(timings: list[dict]) -> None:
    print()
    print("Timing summary")
    print("Component                   Status      Seconds  Cards")
    print("--------------------------  ----------  -------  -----")
    for item in timings:
        component = str(item.get("component", ""))[:26]
        status = str(item.get("status", ""))
        seconds = float(item.get("seconds") or 0.0)
        cards = item.get("cards", "")
        print(f"{component:<26}  {status:<10}  {seconds:>7.3f}  {cards:>5}")


if __name__ == "__main__":
    main()
