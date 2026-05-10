"""Email delivery via Postmark. No-op when email delivery is disabled."""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import requests

from app.models import DeliveryResult, RunMode

if TYPE_CHECKING:
    from app.settings import Settings

_log = logging.getLogger(__name__)

_POSTMARK_API = "https://api.postmarkapp.com/email"


class NoopDeliveryProvider:
    def send(
        self,
        subject: str,
        html_body: str,
        text_body: str,
        inline_images: list[dict[str, Any]] | None = None,
    ) -> DeliveryResult:
        _log.info("Noop delivery — email not sent (subject: %s)", subject)
        return DeliveryResult(success=True)


class PostmarkDeliveryProvider:
    def __init__(self, settings: "Settings", recipient_override: str | None = None) -> None:
        self._settings = settings
        self._recipient_override = recipient_override

    def send(
        self,
        subject: str,
        html_body: str,
        text_body: str,
        inline_images: list[dict[str, Any]] | None = None,
    ) -> DeliveryResult:
        api_key = self._settings.creds.postmark_api_key
        from_email = self._settings.creds.postmark_from_email

        if not api_key:
            return DeliveryResult(success=False, error_message="POSTMARK_API_KEY not set")
        if not from_email:
            return DeliveryResult(success=False, error_message="POSTMARK_FROM_EMAIL not set")

        if self._recipient_override:
            recipients = [e.strip() for e in self._recipient_override.split(",") if e.strip()]
        else:
            recipients = self._settings.recipients
        if not recipients:
            return DeliveryResult(success=False, error_message="Postmark recipient not set or empty")

        payload: dict[str, Any] = {
            "Subject": subject,
            "HtmlBody": html_body,
            "TextBody": text_body,
            "From": from_email,
            "To": ", ".join(recipients),
            "MessageStream": "broadcast",
        }

        if inline_images:
            payload["Attachments"] = [
                {
                    "Name": img["name"],
                    "Content": base64.b64encode(img["data"]).decode("ascii"),
                    "ContentType": "image/png",
                    "ContentID": f"cid:{img['cid']}",
                }
                for img in inline_images
            ]

        try:
            resp = requests.post(
                _POSTMARK_API,
                headers={
                    "X-Postmark-Server-Token": api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            message_id = resp.json().get("MessageID")
            return DeliveryResult(
                success=True,
                delivery_id=message_id,
                sent_at=datetime.now(timezone.utc),
            )
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else "?"
            return DeliveryResult(
                success=False,
                error_message=f"HTTP {code}: {exc.response.text}",
            )
        except requests.RequestException as exc:
            return DeliveryResult(success=False, error_message=str(exc))


def get_provider(
    mode: RunMode, settings: "Settings"
) -> NoopDeliveryProvider | PostmarkDeliveryProvider:
    """Return PostmarkDeliveryProvider when delivery should run; Noop otherwise.

    Sample and dry-run never send email.
    Live mode delivers only when ENABLE_EMAIL_DELIVERY=true.
    """
    if mode in {RunMode.SAMPLE, RunMode.DRY_RUN}:
        return NoopDeliveryProvider()
    if mode == RunMode.LIVE and settings.creds.enable_email_delivery:
        return PostmarkDeliveryProvider(settings)
    return NoopDeliveryProvider()
