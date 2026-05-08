"""CLI entry point. Parses --mode and calls run_pipeline()."""

import argparse
import sys

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

    settings = Settings.load()
    result = run_pipeline(mode=args.mode, settings=settings)

    if not result.success:
        sys.exit(1)


if __name__ == "__main__":
    main()
