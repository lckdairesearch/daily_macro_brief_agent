"""Tests for delivery providers (Step 12)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.delivery import NoopDeliveryProvider, PostmarkDeliveryProvider, get_provider
from app.models import DeliveryResult, RunMode


def _settings(
    postmark_api_key: str | None = "test-token",
    postmark_from_email: str | None = "brief@test.com",
    postmark_to_email: str | None = "pm@test.com",
    enable_email_delivery: bool = True,
) -> MagicMock:
    s = MagicMock()
    s.creds.postmark_api_key = postmark_api_key
    s.creds.postmark_from_email = postmark_from_email
    s.creds.postmark_to_email = postmark_to_email
    s.creds.enable_email_delivery = enable_email_delivery
    return s


def _ok_response(message_id: str = "msg-abc-123") -> MagicMock:
    r = MagicMock()
    r.json.return_value = {"MessageID": message_id}
    r.raise_for_status.return_value = None
    return r


# ---------------------------------------------------------------------------
# NoopDeliveryProvider
# ---------------------------------------------------------------------------

class TestNoopDeliveryProvider:
    def test_returns_success(self):
        result = NoopDeliveryProvider().send("Subject", "<p>html</p>", "plain")
        assert isinstance(result, DeliveryResult)
        assert result.success is True
        assert result.delivery_id is None
        assert result.error_message is None
        assert result.sent_at is None

    def test_no_http_request(self):
        with patch("requests.post") as mock_post:
            NoopDeliveryProvider().send("Subject", "html", "text")
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# PostmarkDeliveryProvider
# ---------------------------------------------------------------------------

class TestPostmarkDeliveryProvider:
    def test_success_returns_delivery_id(self):
        with patch("requests.post", return_value=_ok_response("id-001")):
            result = PostmarkDeliveryProvider(_settings()).send("S", "H", "T")
        assert result.success is True
        assert result.delivery_id == "id-001"
        assert result.sent_at is not None
        assert result.error_message is None

    def test_correct_payload(self):
        with patch("requests.post", return_value=_ok_response()) as mock_post:
            PostmarkDeliveryProvider(_settings()).send("Daily Brief", "<b>html</b>", "text body")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["Subject"] == "Daily Brief"
        assert payload["HtmlBody"] == "<b>html</b>"
        assert payload["TextBody"] == "text body"
        assert payload["To"] == "pm@test.com"
        assert payload["MessageStream"] == "broadcast"
        assert "Attachments" not in payload

    def test_inline_image_attachment(self):
        import base64
        png_bytes = b"\x89PNG\r\nfake"
        with patch("requests.post", return_value=_ok_response()) as mock_post:
            PostmarkDeliveryProvider(_settings()).send(
                "S", '<img src="cid:chart@brief">', "T",
                inline_images=[{"cid": "chart@brief", "name": "chart.png", "data": png_bytes}],
            )
        payload = mock_post.call_args.kwargs["json"]
        assert len(payload["Attachments"]) == 1
        att = payload["Attachments"][0]
        assert att["ContentID"] == "cid:chart@brief"
        assert att["Name"] == "chart.png"
        assert att["ContentType"] == "image/png"
        assert att["Content"] == base64.b64encode(png_bytes).decode("ascii")

    def test_multiple_recipients(self):
        s = _settings(postmark_to_email="a@x.com, b@x.com")
        with patch("requests.post", return_value=_ok_response()) as mock_post:
            result = PostmarkDeliveryProvider(s).send("S", "H", "T")
        assert result.success is True
        to_field = mock_post.call_args.kwargs["json"]["To"]
        assert "a@x.com" in to_field and "b@x.com" in to_field

    def test_missing_api_key_returns_failure(self):
        result = PostmarkDeliveryProvider(_settings(postmark_api_key=None)).send("S", "H", "T")
        assert result.success is False
        assert result.error_message is not None

    def test_missing_to_email_returns_failure(self):
        result = PostmarkDeliveryProvider(_settings(postmark_to_email=None)).send("S", "H", "T")
        assert result.success is False

    def test_empty_to_email_returns_failure(self):
        result = PostmarkDeliveryProvider(_settings(postmark_to_email="  ,  ")).send("S", "H", "T")
        assert result.success is False

    def test_http_error_returns_failure(self):
        bad_response = MagicMock()
        bad_response.status_code = 422
        bad_response.text = "Unprocessable Entity"
        bad_response.raise_for_status.side_effect = requests.HTTPError(response=bad_response)
        with patch("requests.post", return_value=bad_response):
            result = PostmarkDeliveryProvider(_settings()).send("S", "H", "T")
        assert result.success is False
        assert "422" in result.error_message

    def test_connection_error_returns_failure(self):
        with patch("requests.post", side_effect=requests.ConnectionError("timed out")):
            result = PostmarkDeliveryProvider(_settings()).send("S", "H", "T")
        assert result.success is False
        assert "timed out" in result.error_message

    def test_correct_auth_header(self):
        with patch("requests.post", return_value=_ok_response()) as mock_post:
            PostmarkDeliveryProvider(_settings(postmark_api_key="my-secret-token")).send("S", "H", "T")
        headers = mock_post.call_args.kwargs["headers"]
        assert headers["X-Postmark-Server-Token"] == "my-secret-token"


# ---------------------------------------------------------------------------
# get_provider factory
# ---------------------------------------------------------------------------

class TestGetProvider:
    def test_sample_mode_with_creds_returns_postmark(self):
        assert isinstance(get_provider(RunMode.SAMPLE, _settings(postmark_api_key="key")), PostmarkDeliveryProvider)

    def test_sample_mode_without_creds_returns_noop(self):
        assert isinstance(get_provider(RunMode.SAMPLE, _settings(postmark_api_key=None)), NoopDeliveryProvider)

    def test_dry_run_returns_noop(self):
        assert isinstance(get_provider(RunMode.DRY_RUN, _settings()), NoopDeliveryProvider)

    def test_live_delivery_disabled_returns_noop(self):
        assert isinstance(get_provider(RunMode.LIVE, _settings(enable_email_delivery=False)), NoopDeliveryProvider)

    def test_live_delivery_enabled_returns_postmark(self):
        assert isinstance(get_provider(RunMode.LIVE, _settings(enable_email_delivery=True)), PostmarkDeliveryProvider)


# ---------------------------------------------------------------------------
# Live integration test — skipped unless real credentials are present
# ---------------------------------------------------------------------------

def _postmark_creds_available() -> bool:
    """Check env vars OR the local .env file (pydantic-settings loads it but os.environ doesn't)."""
    if os.getenv("POSTMARK_API_KEY") and os.getenv("POSTMARK_TO_EMAIL"):
        return True
    try:
        from dotenv import dotenv_values
        env = dotenv_values()
        return bool(env.get("POSTMARK_API_KEY") and env.get("POSTMARK_TO_EMAIL"))
    except ImportError:
        return False


@pytest.mark.skipif(
    not _postmark_creds_available(),
    reason="Live Postmark credentials not set (POSTMARK_API_KEY, POSTMARK_TO_EMAIL)",
)
def test_postmark_live_send():
    """Actually sends an email via Postmark. Run with real credentials to verify end-to-end."""
    from app.settings import Settings
    settings = Settings.load()
    result = PostmarkDeliveryProvider(settings).send(
        subject="[TEST] Daily Macro Brief — delivery check",
        html_body="<p>This is a <strong>test</strong> from the test suite. You can ignore it.</p>",
        text_body="This is a test from the test suite. You can ignore it.",
    )
    assert result.success is True, f"Live send failed: {result.error_message}"
    assert result.delivery_id is not None
