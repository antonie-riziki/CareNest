"""WSGI config for Care_Nest project."""

import os
from pathlib import Path

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Care_Nest.settings")

application = get_wsgi_application()
app = application

_BOOT_FLAG = Path("/tmp/carenest-migrated")


def _ensure_schema():
    if not os.getenv("VERCEL") or _BOOT_FLAG.exists():
        return
    from django.core.management import call_command
    from django.contrib.auth import get_user_model

    call_command("migrate", interactive=False, run_syncdb=True)
    User = get_user_model()
    if not User.objects.exists():
        try:
            call_command("seed_workos")
        except Exception:
            pass
    try:
        _BOOT_FLAG.write_text("ok")
    except OSError:
        pass


_ensure_schema()
