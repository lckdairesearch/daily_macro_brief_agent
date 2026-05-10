"""Upload run artifacts to Cloudflare R2 for email rendering and backup."""
from __future__ import annotations

import mimetypes
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.settings import REPO_ROOT, Settings

_PUBLIC_CACHE_CONTROL = "public, max-age=300"
_NO_CACHE_CONTROL = "no-cache"


def publish_chart(chart_path: Path, settings: Settings) -> str | None:
    """Return a public URL for chart_path, or None if no hosting path succeeds."""
    return upload_chart_to_r2(
        chart_path=chart_path,
        account_id=settings.creds.cloudflare_r2_account_id,
        access_key_id=settings.creds.cloudflare_r2_access_key_id,
        secret_access_key=settings.creds.cloudflare_r2_secret_access_key,
        bucket=settings.creds.cloudflare_r2_bucket,
        public_base_url=settings.creds.cloudflare_r2_public_base_url,
        object_prefix=settings.creds.cloudflare_r2_prefix or "",
    )


def upload_chart_to_r2(
    chart_path: Path,
    account_id: str | None,
    access_key_id: str | None,
    secret_access_key: str | None,
    bucket: str | None,
    public_base_url: str | None,
    object_prefix: str = "",
) -> str | None:
    """Upload chart_path to Cloudflare R2 and return a public URL."""
    if not chart_path.exists():
        return None

    key = _object_key(chart_path, object_prefix=object_prefix)
    content_type = mimetypes.guess_type(chart_path.name)[0] or "application/octet-stream"
    return upload_file_to_r2(
        file_path=chart_path,
        account_id=account_id,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        bucket=bucket,
        public_base_url=public_base_url,
        object_key=key,
        content_type=content_type,
    )


def publish_run_artifacts(run_output_dir: Path, settings: Settings) -> list[str]:
    """Upload every file in run_output_dir and return the public URLs."""
    urls: list[str] = []
    for path in sorted(p for p in run_output_dir.rglob("*") if p.is_file()):
        url = upload_file_to_r2(
            file_path=path,
            account_id=settings.creds.cloudflare_r2_account_id,
            access_key_id=settings.creds.cloudflare_r2_access_key_id,
            secret_access_key=settings.creds.cloudflare_r2_secret_access_key,
            bucket=settings.creds.cloudflare_r2_bucket,
            public_base_url=settings.creds.cloudflare_r2_public_base_url,
            object_key=_object_key(path, object_prefix=settings.creds.cloudflare_r2_prefix or ""),
        )
        if url is not None:
            urls.append(url)
    return urls


def publish_latest_artifact_alias(
    artifact_path: Path,
    alias_key: str,
    settings: Settings,
) -> str | None:
    """Upload artifact_path to a stable alias key with no-cache headers."""
    if not artifact_path.exists():
        return None

    content_type = mimetypes.guess_type(artifact_path.name)[0] or "application/octet-stream"
    return upload_file_to_r2(
        file_path=artifact_path,
        account_id=settings.creds.cloudflare_r2_account_id,
        access_key_id=settings.creds.cloudflare_r2_access_key_id,
        secret_access_key=settings.creds.cloudflare_r2_secret_access_key,
        bucket=settings.creds.cloudflare_r2_bucket,
        public_base_url=settings.creds.cloudflare_r2_public_base_url,
        object_key=alias_key,
        content_type=content_type,
        cache_control=_NO_CACHE_CONTROL,
    )


def upload_file_to_r2(
    file_path: Path,
    account_id: str | None,
    access_key_id: str | None,
    secret_access_key: str | None,
    bucket: str | None,
    public_base_url: str | None,
    object_key: str,
    content_type: str | None = None,
    cache_control: str = _PUBLIC_CACHE_CONTROL,
) -> str | None:
    """Upload file_path to R2 at object_key and return its public URL."""
    if not file_path.exists():
        return None

    return upload_bytes_to_r2(
        payload=file_path.read_bytes(),
        account_id=account_id,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        bucket=bucket,
        public_base_url=public_base_url,
        object_key=object_key,
        content_type=content_type or mimetypes.guess_type(file_path.name)[0] or "application/octet-stream",
        cache_control=cache_control,
    )


def upload_bytes_to_r2(
    payload: bytes,
    account_id: str | None,
    access_key_id: str | None,
    secret_access_key: str | None,
    bucket: str | None,
    public_base_url: str | None,
    object_key: str,
    content_type: str,
    cache_control: str = _PUBLIC_CACHE_CONTROL,
) -> str | None:
    """Upload bytes to R2 at object_key and return the public URL."""
    if not all([account_id, access_key_id, secret_access_key, bucket, public_base_url]):
        return None

    client = _build_r2_client(
        account_id=account_id,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )
    if client is None:
        return None

    client.put_object(
        Bucket=bucket,
        Key=object_key,
        Body=payload,
        ContentType=content_type,
        CacheControl=cache_control,
    )
    ts = int(time.time())
    return f"{public_base_url.rstrip('/')}/{quote(object_key, safe='/')}?ts={ts}"


def _build_r2_client(
    account_id: str,
    access_key_id: str,
    secret_access_key: str,
) -> Any | None:
    try:
        import boto3
    except ImportError:
        return None

    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",
    )


def _object_key(chart_path: Path, object_prefix: str = "") -> str:
    """Build a stable object key that preserves per-run directory structure."""
    resolved = chart_path.resolve()
    try:
        rel_path = resolved.relative_to(REPO_ROOT)
    except ValueError:
        rel_path = Path(chart_path.name)
    key_path = rel_path if not object_prefix else Path(object_prefix) / rel_path
    return str(key_path).replace("\\", "/")
