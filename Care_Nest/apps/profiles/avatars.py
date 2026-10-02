from __future__ import annotations

from urllib.parse import quote


def avatar_url(user, profile=None) -> str:
    photo = ""
    if profile is not None:
        photo = getattr(profile, "photo_url", "") or ""
    if not photo:
        from apps.profiles.models import EmployerProfile, WorkerProfile

        profile = (
            WorkerProfile.objects.filter(user=user).first()
            or EmployerProfile.objects.filter(user=user).first()
        )
        photo = getattr(profile, "photo_url", "") or "" if profile else ""
    if photo:
        return photo
    name = ((user.get_full_name() or "") or getattr(user, "username", "") or "User").strip()
    return f"https://ui-avatars.com/api/?name={quote(name)}&background=002045&color=fff&size=256"
