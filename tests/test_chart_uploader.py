"""Tests for Cloudflare R2 chart uploads."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.render.chart_uploader import (
    _object_key,
    publish_chart,
    publish_latest_artifact_alias,
    publish_run_artifacts,
    upload_chart_to_r2,
)
from app.settings import REPO_ROOT


def _settings(**overrides):
    creds = SimpleNamespace(
        cloudflare_r2_account_id=None,
        cloudflare_r2_access_key_id=None,
        cloudflare_r2_secret_access_key=None,
        cloudflare_r2_bucket=None,
        cloudflare_r2_public_base_url=None,
        cloudflare_r2_prefix=None,
    )
    for key, value in overrides.items():
        setattr(creds, key, value)
    return SimpleNamespace(creds=creds)


def test_object_key_preserves_repo_relative_path(tmp_path):
    chart = REPO_ROOT / "outputs" / "runs" / "2026-05-09" / "test-run" / "chart.png"
    chart.parent.mkdir(parents=True, exist_ok=True)
    chart.write_bytes(b"png")
    try:
        assert _object_key(chart) == "outputs/runs/2026-05-09/test-run/chart.png"
    finally:
        chart.unlink(missing_ok=True)


def test_upload_chart_to_r2_returns_none_when_config_missing(tmp_path):
    chart = tmp_path / "chart.png"
    chart.write_bytes(b"png")
    assert upload_chart_to_r2(chart, None, "ak", "sk", "bucket", "https://example.com") is None


def test_upload_chart_to_r2_puts_object_and_returns_public_url(tmp_path):
    chart = tmp_path / "chart.png"
    chart.write_bytes(b"png")

    client_calls = {}

    class FakeClient:
        def put_object(self, **kwargs):
            client_calls.update(kwargs)

    class FakeBoto3:
        @staticmethod
        def client(service_name, **kwargs):
            assert service_name == "s3"
            client_calls["client_kwargs"] = kwargs
            return FakeClient()

    with patch.dict("sys.modules", {"boto3": FakeBoto3}):
        url = upload_chart_to_r2(
            chart,
            account_id="acct123",
            access_key_id="ak",
            secret_access_key="sk",
            bucket="brief-charts",
            public_base_url="https://pub.example.com",
            object_prefix="",
        )

    assert url is not None
    assert url.startswith("https://pub.example.com/chart.png?ts=")
    assert client_calls["Bucket"] == "brief-charts"
    assert client_calls["Key"] == "chart.png"
    assert client_calls["ContentType"] == "image/png"
    assert client_calls["CacheControl"] == "public, max-age=300"
    assert client_calls["Body"] == b"png"
    assert client_calls["client_kwargs"]["endpoint_url"] == "https://acct123.r2.cloudflarestorage.com"
    assert client_calls["client_kwargs"]["region_name"] == "auto"


def test_publish_chart_uses_r2(tmp_path):
    chart = tmp_path / "chart.png"
    chart.write_bytes(b"png")
    settings = _settings(
        cloudflare_r2_account_id="acct123",
        cloudflare_r2_access_key_id="ak",
        cloudflare_r2_secret_access_key="sk",
        cloudflare_r2_bucket="brief-charts",
        cloudflare_r2_public_base_url="https://pub.example.com",
    )

    with patch("app.render.chart_uploader.upload_chart_to_r2", return_value="https://pub.example.com/chart.png") as mock_r2:
        url = publish_chart(chart, settings)

    assert url == "https://pub.example.com/chart.png"
    mock_r2.assert_called_once()


def test_publish_run_artifacts_uploads_every_file_in_run_directory(tmp_path):
    run_dir = REPO_ROOT / "outputs" / "dry-runs" / "2099-01-01" / "test-publish-run-artifacts"
    first = run_dir / "brief_rendered.html"
    second = run_dir / "run_metadata.json"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("<html></html>", encoding="utf-8")
    second.write_text("{}", encoding="utf-8")
    settings = _settings(
        cloudflare_r2_account_id="acct123",
        cloudflare_r2_access_key_id="ak",
        cloudflare_r2_secret_access_key="sk",
        cloudflare_r2_bucket="brief-charts",
        cloudflare_r2_public_base_url="https://pub.example.com",
    )

    uploaded_keys: list[str] = []

    def _fake_upload(*args, **kwargs):
        uploaded_keys.append(kwargs["object_key"])
        return f"https://pub.example.com/{kwargs['object_key']}"

    with patch("app.render.chart_uploader.upload_file_to_r2", side_effect=_fake_upload):
        urls = publish_run_artifacts(run_dir, settings)

    assert urls == [
        "https://pub.example.com/outputs/dry-runs/2099-01-01/test-publish-run-artifacts/brief_rendered.html",
        "https://pub.example.com/outputs/dry-runs/2099-01-01/test-publish-run-artifacts/run_metadata.json",
    ]
    assert uploaded_keys == [
        "outputs/dry-runs/2099-01-01/test-publish-run-artifacts/brief_rendered.html",
        "outputs/dry-runs/2099-01-01/test-publish-run-artifacts/run_metadata.json",
    ]
    first.unlink(missing_ok=True)
    second.unlink(missing_ok=True)
    run_dir.rmdir()


def test_publish_latest_artifact_alias_uses_no_cache_headers(tmp_path):
    artifact = tmp_path / "brief_rendered.html"
    artifact.write_text("<html></html>", encoding="utf-8")
    settings = _settings(
        cloudflare_r2_account_id="acct123",
        cloudflare_r2_access_key_id="ak",
        cloudflare_r2_secret_access_key="sk",
        cloudflare_r2_bucket="brief-charts",
        cloudflare_r2_public_base_url="https://pub.example.com",
    )

    with patch("app.render.chart_uploader.upload_file_to_r2", return_value="https://pub.example.com/outputs/runs/latest/brief_rendered.html") as mock_upload:
        url = publish_latest_artifact_alias(artifact, "outputs/runs/latest/brief_rendered.html", settings)

    assert url == "https://pub.example.com/outputs/runs/latest/brief_rendered.html"
    mock_upload.assert_called_once()
    assert mock_upload.call_args.kwargs["cache_control"] == "no-cache"
