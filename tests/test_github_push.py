"""Tests for app/render/github_push.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from app.render.github_push import push_chart_to_github, REPO_ROOT


def _chart(tmp_path: Path) -> Path:
    # Must be inside REPO_ROOT so relative_to() succeeds in push_chart_to_github.
    # Use a test-specific name so the real sample_chart.png is not overwritten.
    p = REPO_ROOT / "outputs" / "_test_chart.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x89PNG fake")
    return p


# ---------------------------------------------------------------------------
# _is_changed helpers via push_chart_to_github behaviour
# ---------------------------------------------------------------------------

class TestPushChartToGithub:
    def _run_with_mocks(self, chart_path, changed=True, push_ok=True, token=None):
        """Helper: mock subprocess.run so no real git calls happen."""
        def fake_run(cmd, **kwargs):
            result = MagicMock()
            result.stdout = "https://github.com/owner/repo.git\n"
            result.returncode = 0
            # diff-index: returncode 1 = changed, 0 = unchanged
            if "diff-index" in cmd:
                result.returncode = 1 if changed else 0
            # ls-files: returncode 0 = tracked
            if "ls-files" in cmd:
                result.returncode = 0
            if not push_ok and "push" in cmd:
                import subprocess
                raise subprocess.CalledProcessError(1, cmd)
            return result

        with patch("subprocess.run", side_effect=fake_run) as mock_run:
            url = push_chart_to_github(chart_path, repo="owner/repo", branch="master", token=token)
        return url, mock_run

    def test_changed_file_runs_add_commit_push(self, tmp_path):
        chart = _chart(tmp_path)
        url, mock_run = self._run_with_mocks(chart, changed=True)
        cmds = [c.args[0] for c in mock_run.call_args_list]
        assert any("add" in c for c in cmds)
        assert any("commit" in c for c in cmds)
        assert any("push" in c for c in cmds)

    def test_unchanged_file_skips_commit(self, tmp_path):
        chart = _chart(tmp_path)
        url, mock_run = self._run_with_mocks(chart, changed=False)
        cmds = [c.args[0] for c in mock_run.call_args_list]
        assert not any("commit" in c for c in cmds)
        assert not any("add" in c for c in cmds)

    def test_returns_raw_githubusercontent_url(self, tmp_path):
        chart = _chart(tmp_path)
        url, _ = self._run_with_mocks(chart, changed=True)
        assert url is not None
        assert url.startswith("https://raw.githubusercontent.com/owner/repo/master/")
        assert "_test_chart.png" in url

    def test_url_has_cache_bust_timestamp(self, tmp_path):
        chart = _chart(tmp_path)
        url, _ = self._run_with_mocks(chart, changed=True)
        assert "?ts=" in url

    def test_push_failure_returns_none(self, tmp_path):
        chart = _chart(tmp_path)
        url, _ = self._run_with_mocks(chart, changed=True, push_ok=False)
        assert url is None

    def test_chart_outside_repo_returns_none(self, tmp_path):
        # tmp_path is outside REPO_ROOT
        chart = tmp_path / "chart.png"
        chart.write_bytes(b"fake")
        # Make sure tmp_path is not a sub-path of REPO_ROOT
        try:
            chart.resolve().relative_to(REPO_ROOT)
            pytest.skip("tmp_path happens to be inside repo root")
        except ValueError:
            pass
        url = push_chart_to_github(chart, repo="owner/repo")
        assert url is None

    def test_token_rewrites_remote_url(self, tmp_path):
        chart = _chart(tmp_path)
        calls_seen = []

        def fake_run(cmd, **kwargs):
            calls_seen.append(list(cmd))
            result = MagicMock()
            result.stdout = "https://github.com/owner/repo.git\n"
            result.returncode = 1  # changed
            return result

        with patch("subprocess.run", side_effect=fake_run):
            push_chart_to_github(chart, repo="owner/repo", branch="master", token="mytoken")

        # Should have set-url with token and then restored it
        set_url_calls = [c for c in calls_seen if "set-url" in c]
        assert len(set_url_calls) == 2
        assert any("x-access-token:mytoken@" in " ".join(c) for c in set_url_calls)
        # Last set-url restores the original
        assert "x-access-token" not in " ".join(set_url_calls[-1])

    def test_commit_message_contains_skip_ci(self, tmp_path):
        chart = _chart(tmp_path)
        commits = []

        def fake_run(cmd, **kwargs):
            result = MagicMock()
            result.stdout = ""
            result.returncode = 1
            if "commit" in cmd:
                commits.append(cmd)
            return result

        with patch("subprocess.run", side_effect=fake_run):
            push_chart_to_github(chart, repo="owner/repo")

        assert commits, "commit was not called"
        assert any("[skip ci]" in str(c) for c in commits)
