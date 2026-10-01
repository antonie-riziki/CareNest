"""
Payout provider abstraction (Stellar + optional M-Pesa).

Workers choose a payout method. M-Pesa is optional. Payment credentials are
never stored; only a masked phone identifier and verification status.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

from django.utils import timezone

logger = logging.getLogger("carenest.payouts")

KENYAN_PHONE = re.compile(r"^2547\d{8}$")


class PayoutError(ValueError):
    pass


def normalize_kenyan_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    if digits.startswith("7") and len(digits) == 9:
        digits = "254" + digits
    if digits.startswith("2540"):
        digits = "254" + digits[4:]
    if not KENYAN_PHONE.match(digits):
        raise PayoutError("Enter a valid Kenyan mobile number (07… or 2547…)")
    return digits


def mask_phone(e164: str) -> str:
    if len(e164) < 8:
        return "****"
    return f"+{e164[:5]}****{e164[-3:]}"


def phone_hash(e164: str) -> str:
    return hashlib.sha256(e164.encode("utf-8")).hexdigest()


@dataclass
class PayoutResult:
    ok: bool
    status: str
    reference: str = ""
    error: str = ""
    provider: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "reference": self.reference,
            "error": self.error,
            "provider": self.provider,
        }


class PayoutProvider(Protocol):
    name: str

    def validate(self, payload: dict[str, Any]) -> None: ...
    def create_payout(self, payload: dict[str, Any]) -> PayoutResult: ...
    def get_status(self, reference: str) -> PayoutResult: ...
    def handle_callback(self, payload: dict[str, Any]) -> PayoutResult: ...


class StellarPayoutProvider:
    name = "stellar"

    def validate(self, payload: dict[str, Any]) -> None:
        address = payload.get("address") or ""
        if not str(address).startswith("G") or len(str(address)) != 56:
            raise PayoutError("Stellar payouts require a connected G… address")

    def create_payout(self, payload: dict[str, Any]) -> PayoutResult:
        self.validate(payload)
        reference = payload.get("reference") or payload.get("tx_hash") or ""
        status = payload.get("status") or "PENDING"
        if payload.get("confirmed"):
            status = "CONFIRMED"
        return PayoutResult(ok=True, status=status, reference=reference, provider=self.name)

    def get_status(self, reference: str) -> PayoutResult:
        return PayoutResult(ok=True, status="PENDING", reference=reference, provider=self.name)

    def handle_callback(self, payload: dict[str, Any]) -> PayoutResult:
        return self.create_payout(payload)


class MpesaPayoutProvider:
    name = "mpesa"

    def validate(self, payload: dict[str, Any]) -> None:
        normalize_kenyan_phone(payload.get("phone") or payload.get("phone_number") or "")

    def create_payout(self, payload: dict[str, Any]) -> PayoutResult:
        self.validate(payload)
        # Live B2C is only attempted when PayHero/Daraja credentials exist.
        from apps.comms_layer.payment_gateway import initiate_payout

        phone = normalize_kenyan_phone(payload.get("phone") or "")
        amount = payload.get("amount")
        result = initiate_payout(phone, amount, reference=payload.get("reference") or "")
        if result is None:
            return PayoutResult(
                ok=False,
                status="FAILED",
                error="M-Pesa payout provider unavailable. The payout was recorded as failed, not completed.",
                provider=self.name,
            )
        return PayoutResult(
            ok=True,
            status=result.get("status") or "PROCESSING",
            reference=result.get("reference") or payload.get("reference") or "",
            provider=self.name,
        )

    def get_status(self, reference: str) -> PayoutResult:
        return PayoutResult(ok=True, status="PROCESSING", reference=reference, provider=self.name)

    def handle_callback(self, payload: dict[str, Any]) -> PayoutResult:
        status = (payload.get("status") or "").upper()
        mapped = {
            "SUCCESS": "COMPLETED",
            "COMPLETED": "COMPLETED",
            "FAILED": "FAILED",
            "PENDING": "PENDING",
            "PROCESSING": "PROCESSING",
        }.get(status, "PROCESSING")
        return PayoutResult(ok=mapped != "FAILED", status=mapped, reference=payload.get("reference") or "", provider=self.name)


PROVIDERS = {
    "stellar": StellarPayoutProvider(),
    "mpesa": MpesaPayoutProvider(),
}


def get_payout_provider(name: str) -> PayoutProvider:
    provider = PROVIDERS.get((name or "").lower())
    if not provider:
        raise PayoutError("Unsupported payout provider")
    return provider


def save_mpesa_method(user, phone: str, *, verified: bool = False):
    from apps.wallet.models import PayoutMethod

    e164 = normalize_kenyan_phone(phone)
    method, _ = PayoutMethod.objects.update_or_create(
        user=user,
        method_type=PayoutMethod.Type.MPESA,
        defaults={
            "provider": "mpesa",
            "masked_identifier": mask_phone(e164),
            "identifier_hash": phone_hash(e164),
            "verified": verified,
            "status": PayoutMethod.Status.VERIFIED if verified else PayoutMethod.Status.PENDING,
            "is_default": False,
        },
    )
    return method


def preferred_payout_method(user):
    from apps.wallet.models import PayoutMethod, Wallet

    method = PayoutMethod.objects.filter(user=user, is_default=True).first()
    if method:
        return method
    method = PayoutMethod.objects.filter(user=user).order_by("-updated_at").first()
    if method:
        return method
    wallet = Wallet.objects.filter(user=user).first()
    if wallet and wallet.stellar_address:
        return PayoutMethod(
            user=user,
            method_type=PayoutMethod.Type.STELLAR,
            provider=wallet.wallet_provider or "freighter",
            masked_identifier=wallet.short_address,
            status=PayoutMethod.Status.VERIFIED,
        )
    return None
