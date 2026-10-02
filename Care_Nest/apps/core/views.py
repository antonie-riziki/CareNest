from pathlib import Path

from django.http import FileResponse, Http404
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

BASE_STATIC = Path(__file__).resolve().parents[2] / "static" / "pwa"


def index(request):
    return render(request, "index.html")


def role_selection(request):
    return render(request, "role_selection.html")


def pwa_offline(request):
    return render(request, "offline.html")


@require_GET
@never_cache
def pwa_manifest(request):
    path = BASE_STATIC / "manifest.webmanifest"
    if not path.exists():
        raise Http404("Manifest missing")
    return FileResponse(path.open("rb"), content_type="application/manifest+json")


@require_GET
@never_cache
def pwa_service_worker(request):
    path = BASE_STATIC / "sw.js"
    if not path.exists():
        raise Http404("Service worker missing")
    response = FileResponse(path.open("rb"), content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-store, max-age=0"
    return response
