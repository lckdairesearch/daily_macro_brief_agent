"""Project-local compatibility helpers for synchronous LiteLLM calls."""

from __future__ import annotations

import asyncio
from typing import Any

import litellm


def completion(**kwargs: Any) -> Any:
    """Call LiteLLM after ensuring sync code has a current event loop."""
    _ensure_current_event_loop()
    return litellm.completion(**kwargs)


def _ensure_current_event_loop() -> None:
    """Python 3.13 warns when sync code calls get_event_loop() with no current loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
