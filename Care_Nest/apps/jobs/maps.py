"""
Map provider abstraction.

Primary tiles are Carto (OSM-derived) so the map works when tile.openstreetmap.org
returns 403. Fallbacks: Esri, OSM Germany, then OSM.org.
Worker coordinates are jittered so exact home location is not published.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from django.conf import settings


def approximate_coordinates(lat: float, lon: float, *, salt: str = "") -> tuple[float, float]:
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
        "tile_url": "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
        "tile_attribution": "© OpenStreetMap, © CARTO",
        "tile_fallbacks": [
            {
                "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
                "attribution": "Tiles © Esri",
            },
            {
                "url": "https://tile.openstreetmap.de/{z}/{x}/{y}.png",
                "attribution": "© OpenStreetMap contributors",
            },
            {
                "url": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
                "attribution": "© OpenStreetMap contributors",
            },
        ],
        "privacy": "Worker location is approximate. Exact residential coordinates are never shown publicly.",
        "error_hint": "CareNest loads Carto first. If those tiles fail it switches to Esri, then OSM Germany.",
    }
