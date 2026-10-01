"""
Map provider abstraction.

The existing worker map already uses Leaflet + OpenStreetMap. Google Maps 403s
in this app were coming from blocked googleusercontent images and would also
appear if a restricted Maps JS key was used without billing/referrers.

Default: Leaflet/OSM (no browser API key).
Optional: Google Maps JS when GOOGLE_MAPS_API_KEY is configured.
On tile/script failure the UI falls back to OSM and never stays blank.
Worker coordinates are jittered so exact home location is not published.
"""

from __future__ import annotations

import hashlib
from typing import Any

from django.conf import settings


def approximate_coordinates(lat: float, lon: float, *, salt: str = "") -> tuple[float, float]:
    """Offset a point by ~250–400m using a stable hash so the same user stays consistent."""
    digest = hashlib.sha256(f"{lat}:{lon}:{salt}".encode()).hexdigest()
    dx = (int(digest[:8], 16) / 0xFFFFFFFF) - 0.5
    dy = (int(digest[8:16], 16) / 0xFFFFFFFF) - 0.5
    # ~0.003 degrees ≈ 330m at Nairobi latitudes
    return round(lat + dy * 0.006, 5), round(lon + dx * 0.006, 5)


def public_config() -> dict[str, Any]:
    google_key = (getattr(settings, "GOOGLE_MAPS_API_KEY", "") or "").strip()
    provider = (getattr(settings, "MAP_PROVIDER", "") or "").strip().lower()
    if provider == "google" and google_key:
        primary = "google"
    else:
        primary = "leaflet"
        google_key = ""  # never send an unused/private key
    return {
        "provider": primary,
        "fallback": "leaflet",
        "google_maps_api_key": google_key,
        "tile_url": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        "tile_attribution": "© OpenStreetMap contributors",
        "privacy": "Worker location is approximate. Exact residential coordinates are never shown publicly.",
        "error_hint": "If a map provider returns 403, CareNest falls back to OpenStreetMap.",
    }
