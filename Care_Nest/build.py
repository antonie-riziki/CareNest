"""Run database migrations during the Vercel build."""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Care_Nest.settings")

import django
from django.core.management import call_command

django.setup()
call_command("migrate", interactive=False, run_syncdb=True)
