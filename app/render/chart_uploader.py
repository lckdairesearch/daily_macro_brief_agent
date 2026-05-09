"""Upload chart PNGs to public hosting for email rendering."""
from __future__ import annotations

import mimetypes
import time
from pathlib import Path
from urllib.parse import quote

from app.settings import REPO_ROOT, Settings


def publish_chart(chart_path: Path, settings: Settings) -> str | None:
    """Return a public URL for chart_path, or None if no hosting path succeeds."""
    return upload_chart_to_r2(
        chart_path=chart_path,
        account_id=settings.creds.cloudflare_r2_account_id,
        access_key_id=settings.creds.cloudflare_r2_access_key_id,
        secret_access_key=settings.creds.cloudflare_r2_secret_access_key,
        bucket=settings.creds.cloudflare_r2_bucket,
        public_base_url=settings.creds.cloudflare_r2_public_base_url,
        object_prefix=settings.creds.cloudflare_r2_prefix or "charts",
    )


def upload_chart_to_r2(
    chart_path: Path,
    account_id: str | None,
    access_key_id: str | None,
    secret_access_key: str | None,
    bucket: str | None,
    public_base_url: str | None,
    object_prefix: str = "charts",
) -> str | None:
    """Upload chart_path to Cloudflare R2 and return a public URL."""
    if not all([account_id, access_key_id, secret_access_key, bucket, public_base_url]):
        return None
    if not chart_path.exists():
        return None

    try:
        import boto3
    except ImportError:
        return None

    key = _object_key(chart_path, object_prefix=object_prefix)
    content_type = mimetypes.guess_type(chart_path.name)[0] or "application/octet-stream"
    client = boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",
    )
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=chart_path.read_bytes(),
        ContentType=content_type,
        CacheControl="public, max-age=300",
    )
    ts = int(time.time())
    return f"{public_base_url.rstrip('/')}/{quote(key, safe='/')}?ts={ts}"


def _object_key(chart_path: Path, object_prefix: str = "charts") -> str:
    """Build a stable object key that preserves per-run directory structure."""
    resolved = chart_path.resolve()
    try:
        rel_path = resolved.relative_to(REPO_ROOT)
    except ValueError:
        rel_path = Path(chart_path.name)
    return str(Path(object_prefix) / rel_path).replace("\\", "/")
