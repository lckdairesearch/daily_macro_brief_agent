"""Tests for the live-eval diagnostic runner."""

from __future__ import annotations

from datetime import datetime, timezone

from scripts.live_eval_run import _markdown_to_rendered_html


def test_markdown_to_rendered_html_has_print_friendly_shell() -> None:
    html = _markdown_to_rendered_html(
        "Morning Macro Brief",
        "# Morning Macro Brief\n\n## Section One\n\n- first\n- second\n",
        datetime(2026, 5, 9, 6, 45, tzinfo=timezone.utc),
    )

    assert "<title>Morning Macro Brief</title>" in html
    assert "Data cutoff: 2026-05-09 06:45 UTC" in html
    assert "<h2>Section One</h2>" in html
    assert "<li>first</li>" in html
    assert "Rendered live-eval artifact for review and later print/PDF use." in html
