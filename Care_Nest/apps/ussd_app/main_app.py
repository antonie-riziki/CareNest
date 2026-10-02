"""
Flask sidecar for Africa's Talking.

Menus are the same CON/END strings as Django `/ussd/callback/`.
Jobs, applicants, contracts, and wallets come from the CareNest database
— not the old hardcoded Mary/Jane sample rows.

Run (from Care_Nest/):
    python3 apps/ussd_app/main_app.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, request

CARE_NEST_ROOT = Path(__file__).resolve().parents[2]
if str(CARE_NEST_ROOT) not in sys.path:
    sys.path.insert(0, str(CARE_NEST_ROOT))

load_dotenv(CARE_NEST_ROOT / ".env")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Care_Nest.settings")

import django

django.setup()

from apps.ussd_app.services import ussd_text  # noqa: E402

app = Flask(__name__)


@app.route("/", methods=["GET"])
@app.route("/ussd", methods=["GET"])
def health():
    return "CareNest USSD Flask is up. POST /ussd with text, phoneNumber, sessionId.\n", 200, {
        "Content-Type": "text/plain"
    }


@app.route("/ussd", methods=["POST"])
def ussd():
    body = ussd_text(
        request.values.get("text") or "",
        request.values.get("phoneNumber"),
        request.values.get("sessionId"),
    )
    return body, 200, {"Content-Type": "text/plain"}


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8002)
