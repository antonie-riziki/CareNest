"""
Map provider abstraction.

Primary: Leaflet + OpenStreetMap.
Fallbacks (in order): Carto Voyager, Esri World Street Map, then Google Maps JS if a key is set.
Worker coordinates are jittered so exact home location is not published.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from django.conf import settings


def approximate_coordinates(lat: float, lon: float, *, salt: str = "") -> tuple[float, float]:
    """Offset a point by ~250–400m using a stable hash so the same user stays consistent."""
    digest = hashlib.sha256(f"{lat}:{lon}:{salt}".encode()).hexdigest()
    dx = (int(digest[:8], 16) / 0xFFFFFFFF) - 0.5
    dy = (int(digest[8:16], 16) / 0xFFFFFFFF) - 0.5
    return round(lat + dy * 0.006, 5), round(lon + dx * 0.006, 5)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return round(2 * radius * math.asin(min(1.0, math.sqrt(a))), 1)


def public_config() -> dict[str, Any]:
    google_key = (getattr(settings, "GOOGLE_MAPS_API_KEY", "") or "").strip()
    provider = (getattr(settings, "MAP_PROVIDER", "") or "").strip().lower()
    if provider == "google" and google_key:
        primary = "google"
    else:
        primary = "leaflet"
        google_key = ""
    return {
        "provider": primary,
        "fallback": "leaflet",
        "google_maps_api_key": google_key,
        "tile_url": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        "tile_attribution": "© OpenStreetMap contributors",
        "tile_fallbacks": [
            {
                "url": "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
                "attribution": "© OpenStreetMap, © CARTO",
            },
            {
                "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
                "attribution": "Tiles © Esri",
            },
        ],
        "privacy": "Worker location is approximate. Exact residential coordinates are never shown publicly.",
        "error_hint": "If OpenStreetMap tiles fail, CareNest switches to Carto, then Esri.",
    }
