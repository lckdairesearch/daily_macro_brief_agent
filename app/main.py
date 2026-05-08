"""CLI entry point. Parses --mode and calls run_pipeline()."""

import argparse
import sys

from app.models import RunMode
from app.pipeline import run_pipeline
from app.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily Macro Brief Agent")
    parser.add_argument(
        "--mode",
        choices=["sample", "live", "dry-run"],
        default="sample",
        help="Run mode: sample (fixtures), live (APIs + delivery), dry-run (APIs, no delivery)",
    )
    args = parser.parse_args()
    mode = RunMode(args.mode)

    settings = Settings.load()

    missing = settings.validate_for_mode(mode)
    if missing:
        print(
            f"ERROR: Missing required credentials for {mode} mode: {', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        result = run_pipeline(mode=mode, settings=settings)
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)

    meta = result.run_metadata
    status = "OK" if result.success else "FAILED"
    print(f"[{status}] run_id={meta.run_id} mode={mode} warnings={len(meta.warnings)}")
    if meta.output_paths:
        for name, path in meta.output_paths.items():
            print(f"  {name}: {path}")

    if not result.success:
        if result.error_message:
            print(f"ERROR: {result.error_message}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
