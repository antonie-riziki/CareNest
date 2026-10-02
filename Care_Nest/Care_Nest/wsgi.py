"""WSGI config for Care_Nest project."""

import os
from pathlib import Path

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Care_Nest.settings")

application = get_wsgi_application()
app = application

_BOOT_FLAG = Path("/tmp/carenest-migrated")


def _ensure_schema():
    hosted = os.getenv("VERCEL") or os.getenv("RENDER")
    if not hosted:
        return
    from django.contrib.auth import get_user_model
    from django.core.management import call_command

    if os.getenv("VERCEL") and not _BOOT_FLAG.exists():
        call_command("migrate", interactive=False, run_syncdb=True)
    User = get_user_model()
    if not User.objects.filter(username="sarah@carenest.demo").exists():
        try:
            call_command("seed_workos")
        except Exception:
            pass
    if os.getenv("VERCEL"):
        try:
            _BOOT_FLAG.write_text("ok")
        except OSError:
            pass


_ensure_schema()
