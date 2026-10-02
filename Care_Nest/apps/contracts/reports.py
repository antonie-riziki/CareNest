from __future__ import annotations

import uuid
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from apps.contracts.models import CompletionReport, Contract


def _name(user) -> str:
    return (user.get_full_name() or user.username or "User").strip()


def _money(amount, currency="KES") -> str:
    if amount is None or amount == "":
        return f"{currency} 0"
    try:
        value = Decimal(str(amount))
    except Exception:
        return f"{currency} {amount}"
    if value == value.to_integral_value():
        return f"{currency} {int(value):,}"
    return f"{currency} {value.quantize(Decimal('0.01')):,.2f}"


def work_rating(contract: Contract) -> dict:
    ratings = [
        int(shift.employer_rating)
        for shift in contract.shifts.all()
        if shift.employer_verified and shift.employer_rating
    ]
    if not ratings:
        return {"average": None, "count": 0, "label": "Not rated"}
    average = round(sum(ratings) / len(ratings), 1)
    return {"average": average, "count": len(ratings), "label": f"{average} / 5 from {len(ratings)} verified shift(s)"}


def build_snapshot(contract: Contract) -> dict:
    job = contract.job
    try:
        settlement = contract.settlement
    except ObjectDoesNotExist:
        settlement = None
    rating = work_rating(contract)
    shifts = []
    activities = []
    total_minutes = 0
    for shift in contract.shifts.order_by("checked_in_at"):
        minutes = shift.duration_minutes or 0
        total_minutes += minutes
        row = {
            "checked_in_at": shift.checked_in_at.isoformat() if shift.checked_in_at else "",
            "checked_out_at": shift.checked_out_at.isoformat() if shift.checked_out_at else "",
            "minutes": minutes,
            "work_summary": shift.work_summary or "",
            "check_in_note": shift.check_in_note or "",
            "check_out_note": shift.check_out_note or "",
            "employer_verified": bool(shift.employer_verified),
            "employer_note": shift.employer_verification_note or "",
            "employer_rating": shift.employer_rating,
        }
        shifts.append(row)
        if shift.work_summary:
            activities.append(shift.work_summary)
    invoices = [
        {
            "number": inv.invoice_number,
            "status": inv.get_status_display(),
            "amount": str(inv.recovery_amount),
            "currency": inv.currency,
            "settled_at": inv.settled_at.isoformat() if inv.settled_at else "",
        }
        for inv in contract.service_invoices.all()
    ]
    return {
        "engagement_id": contract.pk,
        "generated_at": timezone.now().isoformat(),
        "status": contract.display_status,
        "job": {
            "title": job.title,
            "type": job.job_type,
            "location": job.location,
            "schedule": job.schedule_label,
            "description": job.description or "",
        },
        "parties": {
            "employer": _name(contract.employer),
            "worker": _name(contract.worker),
        },
        "pay": {
            "amount": str(contract.amount),
            "currency": contract.currency,
            "display": _money(contract.amount, contract.currency),
            "platform_fee": _money(contract.platform_fee_amount, contract.currency),
            "worker_receives": _money(contract.worker_net_amount, contract.currency),
        },
        "terms": {
            "employer": contract.employer_terms or job.employer_terms or "",
            "worker": contract.worker_terms or "",
            "hours": contract.worker_hours_note or "",
            "bound_at": contract.bound_at.isoformat() if contract.bound_at else "",
        },
        "settlement": {
            "status": settlement.get_status_display() if settlement else "No settlement",
            "reference": getattr(settlement, "settlement_reference", "") or "",
            "completed_at": settlement.completed_at.isoformat() if settlement and settlement.completed_at else "",
        },
        "invoices": invoices,
        "invoices_cleared": contract.invoices_cleared,
        "shifts": shifts,
        "activities": activities,
        "total_minutes": total_minutes,
        "rating": rating,
        "chain_status": contract.chain_status,
    }


def ensure_completion_report(contract: Contract) -> CompletionReport | None:
    if not contract.is_complete:
        return CompletionReport.objects.filter(engagement=contract).first()
    snapshot = build_snapshot(contract)
    report = CompletionReport.objects.filter(engagement=contract).first()
    if report:
        report.snapshot = snapshot
        report.save(update_fields=["snapshot", "updated_at"])
        return report
    return CompletionReport.objects.create(
        engagement=contract,
        share_token=uuid.uuid4(),
        snapshot=snapshot,
    )
