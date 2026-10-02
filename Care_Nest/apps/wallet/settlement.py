"""
Authoritative CareNest payout waterfall.

Employer gross
    → CareNest 5% transaction fee
    → worker net
    → 20% upskilling recovery, capped at remaining training balance
    → worker final payout

All money math happens here (or in the trusted settlement record). Clients
never submit commission amounts.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

TWOPLACES = Decimal("0.01")
ZERO = Decimal("0.00")


def _money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def platform_fee_percent() -> Decimal:
    return _money(getattr(settings, "CARENEST_PLATFORM_FEE_PERCENT", Decimal("5")))


def recovery_percent() -> Decimal:
    return _money(getattr(settings, "CARENEST_UPSKILLING_RECOVERY_PERCENT", Decimal("20")))


@dataclass(frozen=True)
class WaterfallBreakdown:
    gross_amount: Decimal
    platform_fee_percentage: Decimal
    platform_fee_amount: Decimal
    worker_net_amount: Decimal
    upskilling_recovery_percentage: Decimal
    upskilling_recovery_amount: Decimal
    remaining_training_before: Decimal
    remaining_training_after: Decimal
    worker_payout_amount: Decimal
    currency: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "gross_amount": str(self.gross_amount),
            "platform_fee_percentage": str(self.platform_fee_percentage),
            "platform_fee_amount": str(self.platform_fee_amount),
            "worker_net_amount": str(self.worker_net_amount),
            "upskilling_recovery_percentage": str(self.upskilling_recovery_percentage),
            "upskilling_recovery_amount": str(self.upskilling_recovery_amount),
            "remaining_training_before": str(self.remaining_training_before),
            "remaining_training_after": str(self.remaining_training_after),
            "worker_payout_amount": str(self.worker_payout_amount),
            "currency": self.currency,
            "invariants_ok": self.invariants_ok(),
        }

    def invariants_ok(self) -> bool:
        gross_ok = self.gross_amount == self.platform_fee_amount + self.worker_net_amount
        net_ok = self.worker_net_amount == self.worker_payout_amount + self.upskilling_recovery_amount
        cap_ok = self.upskilling_recovery_amount <= self.remaining_training_before
        return gross_ok and net_ok and cap_ok


def compute_waterfall(
    gross_amount,
    *,
    remaining_training_balance=0,
    currency: str = "KES",
    fee_percent: Decimal | None = None,
    recovery_rate: Decimal | None = None,
) -> WaterfallBreakdown:
    """Pure function. Never deducts more than the outstanding training balance."""
    gross = _money(gross_amount)
    remaining = max(_money(remaining_training_balance), ZERO)
    fee_pct = _money(fee_percent if fee_percent is not None else platform_fee_percent())
    rec_pct = _money(recovery_rate if recovery_rate is not None else recovery_percent())

    if gross < ZERO:
        raise ValueError("gross amount cannot be negative")
    if fee_pct < ZERO or rec_pct < ZERO:
        raise ValueError("percentages cannot be negative")

    fee = (gross * fee_pct / Decimal("100")).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    worker_net = (gross - fee).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    uncapped = (worker_net * rec_pct / Decimal("100")).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    recovery = min(uncapped, remaining)
    payout = (worker_net - recovery).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    remaining_after = (remaining - recovery).quantize(TWOPLACES, rounding=ROUND_HALF_UP)

    breakdown = WaterfallBreakdown(
        gross_amount=gross,
        platform_fee_percentage=fee_pct,
        platform_fee_amount=fee,
        worker_net_amount=worker_net,
        upskilling_recovery_percentage=rec_pct,
        upskilling_recovery_amount=recovery,
        remaining_training_before=remaining,
        remaining_training_after=remaining_after,
        worker_payout_amount=payout,
        currency=(currency or "KES").upper(),
    )
    if not breakdown.invariants_ok():
        raise ValueError("waterfall invariants failed")
    return breakdown


def remaining_training_for(worker) -> Decimal:
    from apps.courses.models import Enrollment

    if worker is None:
        return ZERO
    total = ZERO
    for row in Enrollment.objects.filter(worker=worker, status__in=Enrollment.OPEN_STATUSES):
        total += _money(row.amount_remaining)
    return total


def apply_breakdown_to_contract(contract, breakdown: WaterfallBreakdown) -> None:
    contract.amount = breakdown.gross_amount
    contract.currency = breakdown.currency
    contract.platform_fee_percentage = breakdown.platform_fee_percentage
    contract.platform_fee_amount = breakdown.platform_fee_amount
    contract.worker_net_amount = breakdown.worker_net_amount
    contract.upskilling_recovery_amount = breakdown.upskilling_recovery_amount
    contract.worker_payout_amount = breakdown.worker_payout_amount


@transaction.atomic
def ensure_settlement(contract, *, status: str | None = None):
    """Create or refresh the accounting row from current contract amounts."""
    from apps.wallet.models import Settlement

    remaining = remaining_training_for(contract.worker)
    breakdown = compute_waterfall(
        contract.amount or contract.job.pay,
        remaining_training_balance=remaining,
        currency=contract.currency or "KES",
    )
    apply_breakdown_to_contract(contract, breakdown)
    contract.save(
        update_fields=[
            "amount",
            "currency",
            "platform_fee_percentage",
            "platform_fee_amount",
            "worker_net_amount",
            "upskilling_recovery_amount",
            "worker_payout_amount",
            "updated_at",
        ]
    )
    defaults = {
        "employer": contract.employer,
        "worker": contract.worker,
        "gross_amount": breakdown.gross_amount,
        "platform_fee_percentage": breakdown.platform_fee_percentage,
        "platform_fee_amount": breakdown.platform_fee_amount,
        "worker_net_amount": breakdown.worker_net_amount,
        "upskilling_recovery_amount": breakdown.upskilling_recovery_amount,
        "worker_payout_amount": breakdown.worker_payout_amount,
        "currency": breakdown.currency,
        "status": status or Settlement.Status.PENDING,
        "breakdown": breakdown.to_dict(),
    }
    settlement, created = Settlement.objects.get_or_create(engagement=contract, defaults=defaults)
    if not created and settlement.status in (
        Settlement.Status.PENDING,
        Settlement.Status.FUNDED,
    ):
        for key, value in defaults.items():
            if key == "status" and status is None:
                continue
            setattr(settlement, key, value)
        if status:
            settlement.status = status
        settlement.save()
    return settlement, breakdown


@transaction.atomic
def mark_settlement_funded(contract, *, reference: str = "") -> None:
    from apps.wallet.models import Settlement

    settlement, _ = ensure_settlement(contract, status=Settlement.Status.FUNDED)
    if reference:
        settlement.settlement_reference = reference
        settlement.save(update_fields=["settlement_reference", "updated_at"])


@transaction.atomic
def capture_settled_commission(contract, *, reference: str = "") -> None:
    """
    Finalize accounting after escrow release is confirmed.

    Commission becomes FINAL only here. Upskilling recovery is applied to
    open enrollments, never exceeding remaining balance.
    """
    from apps.courses.models import Enrollment
    from apps.wallet.models import Settlement

    settlement, breakdown = ensure_settlement(contract)
    if settlement.status in (Settlement.Status.SETTLED, Settlement.Status.FEE_CAPTURED):
        from apps.contracts.reports import ensure_completion_report

        ensure_completion_report(contract)
        return settlement
    if settlement.status == Settlement.Status.FAILED:
        raise ValueError("cannot capture commission on a failed settlement")
    if settlement.status == Settlement.Status.REFUNDED:
        raise ValueError("cannot capture commission on a refunded settlement")

    remaining_to_recover = breakdown.upskilling_recovery_amount
    if remaining_to_recover > ZERO:
        enrollments = Enrollment.objects.select_for_update().filter(
            worker=contract.worker, status__in=Enrollment.OPEN_STATUSES
        ).order_by("enrolled_at")
        for enrollment in enrollments:
            if remaining_to_recover <= ZERO:
                break
            take = min(_money(enrollment.amount_remaining), remaining_to_recover)
            enrollment.apply_recovery(take)
            remaining_to_recover -= take

    settlement.status = Settlement.Status.FEE_CAPTURED
    settlement.settlement_reference = reference or settlement.settlement_reference
    settlement.completed_at = timezone.now()
    settlement.breakdown = breakdown.to_dict()
    settlement.save()

    from apps.contracts.agreement import mark_invoice_settled

    mark_invoice_settled(contract)

    from apps.wallet.models import Transaction
    from apps.wallet.models import Wallet

    employer_wallet, _ = Wallet.objects.get_or_create(user=contract.employer, defaults={"balance": 0})
    worker_wallet, _ = Wallet.objects.get_or_create(user=contract.worker, defaults={"balance": 0})
    Transaction.objects.create(
        wallet=employer_wallet,
        amount=breakdown.gross_amount,
        type="debit",
        status=Transaction.Status.SETTLED,
        memo="Employer funded domestic-work engagement",
        tx_hash=reference,
        settlement=settlement,
        gross_amount=breakdown.gross_amount,
        platform_fee_amount=breakdown.platform_fee_amount,
        worker_net_amount=breakdown.worker_net_amount,
        currency=breakdown.currency,
    )
    Transaction.objects.create(
        wallet=worker_wallet,
        amount=breakdown.worker_payout_amount,
        type="credit",
        status=Transaction.Status.SETTLED,
        memo="Worker payout after CareNest fee and training recovery",
        tx_hash=reference,
        settlement=settlement,
        gross_amount=breakdown.gross_amount,
        platform_fee_amount=breakdown.platform_fee_amount,
        worker_net_amount=breakdown.worker_net_amount,
        currency=breakdown.currency,
    )
    from apps.contracts.reports import ensure_completion_report

    ensure_completion_report(contract)
    return settlement


@transaction.atomic
def mark_settlement_failed(contract, *, reason: str = "") -> None:
    from apps.wallet.models import Settlement

    settlement = Settlement.objects.filter(engagement=contract).first()
    if not settlement:
        return None
    if settlement.status in (Settlement.Status.SETTLED, Settlement.Status.FEE_CAPTURED):
        raise ValueError("cannot fail a captured commission")
    settlement.status = Settlement.Status.FAILED
    notes = dict(settlement.breakdown or {})
    notes["failure_reason"] = reason
    settlement.breakdown = notes
    settlement.save(update_fields=["status", "breakdown", "updated_at"])
    return settlement


@transaction.atomic
def refund_settlement(contract, *, reason: str = "") -> None:
    """Reverse a captured settlement. Does not silently keep the platform fee."""
    from apps.courses.models import Enrollment
    from apps.wallet.models import Settlement, Transaction, Wallet

    settlement = Settlement.objects.select_for_update().filter(engagement=contract).first()
    if not settlement:
        return None
    if settlement.status == Settlement.Status.REFUNDED:
        return settlement
    if settlement.status in (Settlement.Status.SETTLED, Settlement.Status.FEE_CAPTURED):
        recovered = _money(settlement.upskilling_recovery_amount)
        if recovered > ZERO:
            enrollment = (
                Enrollment.objects.select_for_update()
                .filter(worker=contract.worker)
                .order_by("-updated_at")
                .first()
            )
            if enrollment:
                enrollment.reverse_recovery(recovered)
        employer_wallet, _ = Wallet.objects.get_or_create(user=contract.employer, defaults={"balance": 0})
        Transaction.objects.create(
            wallet=employer_wallet,
            amount=settlement.gross_amount,
            type="credit",
            status=Transaction.Status.REFUNDED,
            memo=f"Refund of engagement settlement ({reason})",
            settlement=settlement,
            gross_amount=settlement.gross_amount,
            platform_fee_amount=ZERO,
            worker_net_amount=ZERO,
            currency=settlement.currency,
        )
    settlement.status = Settlement.Status.REFUNDED
    notes = dict(settlement.breakdown or {})
    notes["refund_reason"] = reason
    settlement.breakdown = notes
    settlement.completed_at = timezone.now()
    settlement.save()
    return settlement
