from __future__ import annotations

import os
import secrets
from urllib.parse import urlencode

import httpx
from django.urls import reverse


def google_configured() -> bool:
    return bool(os.getenv("GOOGLE_OAUTH_CLIENT_ID") and os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"))


def google_redirect_uri(request) -> str:
    configured = (os.getenv("GOOGLE_OAUTH_REDIRECT_URI") or "").strip()
    if configured:
        return configured
    return request.build_absolute_uri(reverse("google-oauth-callback"))


def google_authorize_url(request, *, role: str) -> str:
    state = secrets.token_urlsafe(24)
    request.session["google_oauth_state"] = state
    request.session["google_oauth_role"] = role if role in {"worker", "employer"} else "worker"
    request.session.modified = True
    params = {
        "client_id": os.getenv("GOOGLE_OAUTH_CLIENT_ID", ""),
        "redirect_uri": google_redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


def google_fetch_profile(request, code: str) -> dict:
    token_resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": os.getenv("GOOGLE_OAUTH_CLIENT_ID", ""),
            "client_secret": os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", ""),
            "redirect_uri": google_redirect_uri(request),
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    token_resp.raise_for_status()
    access_token = token_resp.json().get("access_token")
    if not access_token:
        raise ValueError("Google did not return an access token.")
    user_resp = httpx.get(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    user_resp.raise_for_status()
    payload = user_resp.json()
    email = (payload.get("email") or "").strip().lower()
    if not email:
        raise ValueError("Google did not share an email address.")
    return {
        "email": email,
        "first_name": (payload.get("given_name") or "").strip(),
        "last_name": (payload.get("family_name") or "").strip(),
        "full_name": (payload.get("name") or "").strip(),
        "picture": (payload.get("picture") or "").strip(),
    }
