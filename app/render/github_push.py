"""Push chart PNG to GitHub; return raw.githubusercontent.com URL on success."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_OUTPUTS_RUNS = REPO_ROOT / "outputs" / "runs"


def push_chart_to_github(
    chart_path: Path,
    repo: str,
    branch: str = "master",
    token: str | None = None,
) -> str | None:
    """Git-add, commit, and push chart_path; return raw.githubusercontent.com URL.

    Returns None on failure — caller falls back to CID attachment.
    Skips commit when chart has not changed (diff-index --quiet).
    """
    resolved = chart_path.resolve()
    try:
        rel_path = resolved.relative_to(REPO_ROOT)
    except ValueError:
        return None  # chart outside repo

    # outputs/runs/** is gitignored; copy to outputs/ root where chart_*.png is tracked.
    if resolved.is_relative_to(_OUTPUTS_RUNS):
        git_path = REPO_ROOT / "outputs" / chart_path.name
        shutil.copy2(resolved, git_path)
        rel_path = git_path.relative_to(REPO_ROOT)

    try:
        changed = subprocess.run(
            ["git", "diff-index", "--quiet", "HEAD", "--", str(rel_path)],
            capture_output=True,
            cwd=REPO_ROOT,
        ).returncode != 0

        if changed:
            subprocess.run(
                ["git", "add", str(rel_path)],
                check=True, capture_output=True, cwd=REPO_ROOT,
            )
            subprocess.run(
                ["git", "commit", "-m", "chore: update chart [skip ci]"],
                check=True, capture_output=True, cwd=REPO_ROOT,
            )

        original_url: str | None = None
        if token:
            original_url = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True, text=True, cwd=REPO_ROOT,
            ).stdout.strip()
            subprocess.run(
                ["git", "remote", "set-url", "origin",
                 f"https://x-access-token:{token}@github.com/{repo}.git"],
                check=True, capture_output=True, cwd=REPO_ROOT,
            )

        try:
            subprocess.run(
                ["git", "push", "origin", branch],
                check=True, capture_output=True, cwd=REPO_ROOT,
                stdin=subprocess.DEVNULL,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        finally:
            if original_url:
                subprocess.run(
                    ["git", "remote", "set-url", "origin", original_url],
                    check=True, capture_output=True, cwd=REPO_ROOT,
                )

    except subprocess.CalledProcessError:
        return None

    ts = int(time.time())
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{rel_path}?ts={ts}"
