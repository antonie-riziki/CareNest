"""Read public chain balances and recent payments. Private keys are never requested."""

from __future__ import annotations

import json
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


def _get_json(url: str) -> dict | None:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "CareNest/1.0 (carenest.app; hello@carenest.app)",
            "Referer": "https://carenest.app/",
        },
    )
    try:
        with urlopen(request, timeout=3) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return None


def fetch_stellar_snapshot(address: str) -> dict:
    empty = {
        "available": False,
        "address": address or "",
        "balances": [],
        "native_balance": None,
        "payments": [],
        "network": getattr(settings, "STELLAR_NETWORK", "testnet"),
        "horizon": getattr(settings, "STELLAR_HORIZON_URL", ""),
    }
    if not address:
        return empty
    base = (getattr(settings, "STELLAR_HORIZON_URL", "") or "https://horizon-testnet.stellar.org").rstrip("/")
    account = _get_json(f"{base}/accounts/{address}")
    if not account:
        return empty
    balances = []
    native = None
    for row in account.get("balances") or []:
        code = "XLM" if row.get("asset_type") == "native" else (row.get("asset_code") or "ASSET")
        amount = Decimal(str(row.get("balance") or "0"))
        item = {"asset": code, "balance": amount}
        balances.append(item)
        if code == "XLM":
            native = amount
    payments_payload = _get_json(f"{base}/accounts/{address}/payments?order=desc&limit=8")
    payments = []
    for row in (payments_payload or {}).get("_embedded", {}).get("records", []):
        if row.get("type") not in {"payment", "create_account", "path_payment_strict_send", "path_payment_strict_receive"}:
            continue
        amount = row.get("amount") or row.get("starting_balance") or "0"
        asset = "XLM" if row.get("asset_type") in (None, "native") else row.get("asset_code") or "ASSET"
        payments.append(
            {
                "id": row.get("id"),
                "type": row.get("type"),
                "amount": amount,
                "asset": asset,
                "from": row.get("from") or row.get("funder") or "",
                "to": row.get("to") or row.get("account") or "",
                "created_at": row.get("created_at") or "",
                "hash": (row.get("transaction_hash") or "")[:12],
            }
        )
    empty.update(
        {
            "available": True,
            "balances": balances,
            "native_balance": native,
            "payments": payments,
        }
    )
    return empty
