from __future__ import annotations

import uuid
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from apps.contracts.models import DisputeEvidence

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
DOC_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
}
DOC_EXT = {".pdf", ".doc", ".docx", ".txt"}
VIDEO_TYPES = {"video/mp4", "video/webm", "video/quicktime"}
VIDEO_EXT = {".mp4", ".webm", ".mov"}

MAX_IMAGE = 8 * 1024 * 1024
MAX_DOC = 12 * 1024 * 1024
MAX_VIDEO = 40 * 1024 * 1024


class EvidenceUploadError(ValueError):
    pass


def classify_upload(uploaded) -> str:
    name = getattr(uploaded, "name", "") or ""
    ext = Path(name).suffix.lower()
    content_type = (getattr(uploaded, "content_type", "") or "").lower()
    if ext in IMAGE_EXT or content_type in IMAGE_TYPES:
        return DisputeEvidence.Kind.IMAGE
    if ext in DOC_EXT or content_type in DOC_TYPES:
        return DisputeEvidence.Kind.DOCUMENT
    if ext in VIDEO_EXT or content_type in VIDEO_TYPES:
        return DisputeEvidence.Kind.VIDEO
    raise EvidenceUploadError("Upload an image, PDF/Word document, or MP4/WebM/MOV video.")


def store_evidence(uploaded, *, folder: str = "disputes") -> tuple[str, str, str]:
    if uploaded is None:
        raise EvidenceUploadError("Choose a proof file.")
    kind = classify_upload(uploaded)
    size = getattr(uploaded, "size", 0) or 0
    limits = {
        DisputeEvidence.Kind.IMAGE: MAX_IMAGE,
        DisputeEvidence.Kind.DOCUMENT: MAX_DOC,
        DisputeEvidence.Kind.VIDEO: MAX_VIDEO,
    }
    if size > limits[kind]:
        raise EvidenceUploadError("That file is too large. Use a smaller image, document, or video.")
    ext = Path(uploaded.name).suffix.lower() or ".bin"
    name = f"{folder}/{uuid.uuid4().hex}{ext}"
    content_type = getattr(uploaded, "content_type", "") or "application/octet-stream"
    supabase_url = _try_supabase(uploaded, name, content_type)
    if supabase_url:
        return supabase_url, kind, Path(uploaded.name).name
    uploaded.seek(0)
    path = default_storage.save(name, ContentFile(uploaded.read()))
    return _public_url(default_storage.url(path)), kind, Path(uploaded.name).name


def _public_url(url: str) -> str:
    if not url:
        return url
    if url.startswith("http://") or url.startswith("https://") or url.startswith("/"):
        return url
    return "/" + url.lstrip("/")


def _try_supabase(uploaded, name: str, content_type: str) -> str:
    try:
        from apps.core.supabase_client import upload_bytes
    except Exception:
        return ""
    try:
        uploaded.seek(0)
        data = uploaded.read()
        uploaded.seek(0)
        return upload_bytes("job-images", name, data, content_type=content_type)
    except Exception:
        return ""
