"""Job image helpers: validation, storage, category placeholders."""

from __future__ import annotations

import uuid
from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_BYTES = 5 * 1024 * 1024

CATEGORY_META = {
    "nanny": {"label": "Childcare / Nanny", "icon": "child_care", "from": "#dbeafe", "to": "#99f6e4"},
    "childcare": {"label": "Childcare / Nanny", "icon": "child_care", "from": "#dbeafe", "to": "#99f6e4"},
    "caregiver": {"label": "Elder Care", "icon": "health_and_safety", "from": "#fce7f3", "to": "#ddd6fe"},
    "elderly": {"label": "Elder Care", "icon": "health_and_safety", "from": "#fce7f3", "to": "#ddd6fe"},
    "housekeeper": {"label": "Housekeeping", "icon": "cleaning_services", "from": "#ffedd5", "to": "#fef9c3"},
    "cleaner": {"label": "Housekeeping", "icon": "cleaning_services", "from": "#ffedd5", "to": "#fef9c3"},
    "chef": {"label": "Cooking", "icon": "skillet", "from": "#fee2e2", "to": "#ffedd5"},
    "cooking": {"label": "Cooking", "icon": "skillet", "from": "#fee2e2", "to": "#ffedd5"},
    "gardener": {"label": "Gardening", "icon": "garden_cart", "from": "#dcfce7", "to": "#bbf7d0"},
    "gardening": {"label": "Gardening", "icon": "garden_cart", "from": "#dcfce7", "to": "#bbf7d0"},
    "companion": {"label": "Companion Care", "icon": "favorite", "from": "#e0e7ff", "to": "#fce7f3"},
    "driver": {"label": "Driver", "icon": "directions_car", "from": "#e2e8f0", "to": "#c7d2fe"},
    "security": {"label": "Security", "icon": "security", "from": "#e2e8f0", "to": "#cbd5e1"},
}


class ImageUploadError(ValueError):
    pass


def normalize_category(job_type: str) -> str:
    key = (job_type or "").strip().lower()
    aliases = {
        "nanny": "nanny",
        "childcare": "nanny",
        "child care": "nanny",
        "caregiver": "caregiver",
        "elder care": "caregiver",
        "elderly": "caregiver",
        "housekeeper": "housekeeper",
        "housekeeping": "housekeeper",
        "cleaner": "housekeeper",
        "chef": "chef",
        "cook": "chef",
        "cooking": "chef",
        "gardener": "gardener",
        "gardening": "gardener",
        "companion": "companion",
        "companion care": "companion",
        "driver": "driver",
        "security": "security",
    }
    return aliases.get(key, key if key in CATEGORY_META else "housekeeper")


def category_meta(job_type: str) -> dict:
    return CATEGORY_META.get(normalize_category(job_type), CATEGORY_META["housekeeper"])


def placeholder_url(job_type: str) -> str:
    slug = normalize_category(job_type)
    return f"{settings.STATIC_URL}jobs/placeholders/{slug}.svg"


def validate_upload(uploaded) -> None:
    if uploaded is None:
        raise ImageUploadError("No image selected")
    name = getattr(uploaded, "name", "") or ""
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise ImageUploadError("Use a JPEG, PNG, WEBP, or GIF image")
    content_type = getattr(uploaded, "content_type", "") or ""
    if content_type and content_type not in ALLOWED_TYPES:
        raise ImageUploadError("Unsupported image type")
    size = getattr(uploaded, "size", 0) or 0
    if size > MAX_BYTES:
        raise ImageUploadError("Images must be 5 MB or smaller")


def store_upload(uploaded, *, folder: str = "jobs") -> str:
    validate_upload(uploaded)
    ext = Path(uploaded.name).suffix.lower() or ".jpg"
    name = f"{folder}/{uuid.uuid4().hex}{ext}"
    supabase_url = _try_supabase(uploaded, name)
    if supabase_url:
        return supabase_url
    path = default_storage.save(name, ContentFile(uploaded.read()))
    return default_storage.url(path)


def _try_supabase(uploaded, name: str) -> str:
    try:
        from apps.core.supabase_client import upload_bytes
    except Exception:  # noqa: BLE001
        return ""
    try:
        uploaded.seek(0)
        data = uploaded.read()
        uploaded.seek(0)
        return upload_bytes("job-images", name, data, content_type=getattr(uploaded, "content_type", "image/jpeg"))
    except Exception:  # noqa: BLE001
        return ""
