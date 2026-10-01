"""
Supabase helper. Credentials come from the environment only.

Django remains the application source of truth. This module optionally mirrors
worker/employer rows into Supabase tables and stores job images in Storage.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from django.conf import settings

logger = logging.getLogger("carenest.supabase")


class SupabaseUnavailable(RuntimeError):
    pass


def _url() -> str:
    return (getattr(settings, "SUPABASE_URL", "") or "").rstrip("/")


def _secret() -> str:
    return getattr(settings, "SUPABASE_SECRET_KEY", "") or ""


def _publishable() -> str:
    return getattr(settings, "SUPABASE_PUBLISHABLE_KEY", "") or ""


def configured() -> bool:
    return bool(_url() and _secret())


@lru_cache(maxsize=1)
def rest_headers() -> dict[str, str]:
    key = _secret()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def _request(method: str, path: str, *, json: Any = None, params: dict | None = None, extra_headers: dict | None = None):
    if not configured():
        raise SupabaseUnavailable("Supabase is not configured")
    import httpx

    headers = dict(rest_headers())
    if extra_headers:
        headers.update(extra_headers)
    url = f"{_url()}{path}"
    try:
        response = httpx.request(method, url, headers=headers, json=json, params=params, timeout=20.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("supabase request failed: %s", type(exc).__name__)
        raise SupabaseUnavailable(str(type(exc).__name__)) from exc
    if response.status_code >= 400:
        logger.warning("supabase %s %s -> %s", method, path, response.status_code)
        raise SupabaseUnavailable(f"supabase HTTP {response.status_code}")
    if response.content:
        try:
            return response.json()
        except Exception:  # noqa: BLE001
            return {"ok": True}
    return {"ok": True}


def upsert(table: str, rows: list[dict[str, Any]], *, on_conflict: str = "id") -> None:
    if not rows:
        return
    _request(
        "POST",
        f"/rest/v1/{table}",
        json=rows,
        extra_headers={"Prefer": f"resolution=merge-duplicates,return=minimal"},
        params={"on_conflict": on_conflict} if on_conflict else None,
    )


def upload_bytes(bucket: str, path: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
    if not configured():
        return ""
    import httpx

    url = f"{_url()}/storage/v1/object/{bucket}/{path}"
    headers = {
        "apikey": _secret(),
        "Authorization": f"Bearer {_secret()}",
        "Content-Type": content_type,
        "x-upsert": "true",
    }
    try:
        response = httpx.post(url, headers=headers, content=data, timeout=30.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("supabase storage failed: %s", type(exc).__name__)
        return ""
    if response.status_code >= 400:
        logger.warning("supabase storage HTTP %s", response.status_code)
        return ""
    public = f"{_url()}/storage/v1/object/public/{bucket}/{path}"
    return public


def public_client_config() -> dict[str, str]:
    """Safe values for the browser. Never includes the secret key."""
    return {
        "url": _url(),
        "publishable_key": _publishable() if getattr(settings, "SUPABASE_EXPOSE_PUBLISHABLE", False) else "",
    }


def sync_account(user) -> None:
    if not configured():
        return
    role = getattr(user, "role", "")
    table = "carenest_employers" if role == "employer" else "carenest_workers"
    row = {
        "user_id": user.pk,
        "email": user.email or user.username,
        "full_name": user.get_full_name() or user.username,
        "role": role,
        "is_active": user.is_active,
    }
    try:
        upsert(table, [row], on_conflict="user_id")
    except SupabaseUnavailable:
        return
