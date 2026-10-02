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


def _post_json(url: str, payload: dict) -> dict | None:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "CareNest/1.0 (carenest.app; hello@carenest.app)",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=4) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return None


def fetch_evm_snapshot(address: str, network: str = "sepolia") -> dict:
    empty = {
        "available": False,
        "address": address or "",
        "native_balance": None,
        "symbol": "ETH",
        "network": network or "sepolia",
        "payments": [],
        "balances": [],
    }
    if not address:
        return empty
    net = (network or "sepolia").lower()
    rpc = "https://ethereum.publicnode.com" if net in {"mainnet", "0x1", "1"} else "https://rpc.sepolia.org"
    payload = _post_json(
        rpc, {"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [address, "latest"]}
    )
    hex_value = (payload or {}).get("result")
    if not hex_value:
        return empty
    try:
        wei = int(hex_value, 16)
        eth = Decimal(wei) / Decimal(10**18)
    except (TypeError, ValueError):
        return empty
    empty.update(
        {
            "available": True,
            "native_balance": eth,
            "balances": [{"asset": "ETH", "balance": eth}],
        }
    )
    return empty


def fetch_eth_kes(amount) -> dict | None:
    if amount is None:
        return None
    prices = _get_json("https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=kes,usd")
    kes = ((prices or {}).get("ethereum") or {}).get("kes")
    if kes is None:
        return None
    converted = (Decimal(str(amount)) * Decimal(str(kes))).quantize(Decimal("0.01"))
    return {"converted": converted, "rate": kes, "base": "ETH", "quote": "KES"}


def sync_wallet_ledger(wallet) -> dict:
    """Copy the connected chain balance onto the CareNest wallet record."""
    from django.utils import timezone

    from apps.wallet.pricing import get_price_service

    snapshot: dict = {"available": False}
    kes_amount = None
    if wallet.stellar_connected:
        snapshot = fetch_stellar_snapshot(wallet.stellar_address)
        wallet.onchain_symbol = "XLM"
        native = snapshot.get("native_balance")
        if native is not None:
            wallet.onchain_balance = Decimal(str(native))
            try:
                quote = get_price_service().convert(native, "XLM", "KES")
                if quote.get("converted") not in (None, ""):
                    kes_amount = Decimal(str(quote["converted"]))
            except Exception:
                kes_amount = None
    elif wallet.evm_connected:
        snapshot = fetch_evm_snapshot(wallet.evm_address, wallet.network or "sepolia")
        wallet.onchain_symbol = "ETH"
        native = snapshot.get("native_balance")
        if native is not None:
            wallet.onchain_balance = Decimal(str(native))
            quote = fetch_eth_kes(native)
            if quote:
                kes_amount = Decimal(str(quote["converted"]))
                snapshot["kes"] = quote
    if kes_amount is not None:
        wallet.balance = kes_amount
    wallet.onchain_synced_at = timezone.now()
    wallet.save(update_fields=["balance", "onchain_balance", "onchain_symbol", "onchain_synced_at"])
    return snapshot
