"""Bilateral job terms: employer offer → worker terms → bind, lock, invoice."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.contracts.models import Contract, ServiceInvoice, ShiftAttendance
from apps.jobs.models import Application, Job
from apps.wallet.settlement import (
    apply_breakdown_to_contract,
    compute_waterfall,
    ensure_settlement,
    remaining_training_for,
)


class AgreementError(ValueError):
    pass


def default_employer_terms(job: Job) -> str:
    pay = f"{job.currency} {job.pay}"
    return (
        f"Role: {job.title}\n"
        f"Location: {job.location}\n"
        f"Pay: {pay}\n"
        f"Schedule: {job.schedule_label}\n\n"
        f"Scope of work:\n{job.description or 'As discussed.'}\n"
    )


def ensure_employer_terms(job: Job, override: str = "") -> str:
    text = (override or job.employer_terms or "").strip()
    if not text:
        text = default_employer_terms(job)
    if job.employer_terms != text:
        job.employer_terms = text
        job.save(update_fields=["employer_terms", "updated_at"])
    return text


def hash_bound_terms(contract: Contract) -> str:
    from apps.agentic_core import stellar

    return stellar.sha256_hex(
        {
            "job_id": contract.job_id,
            "employer_id": contract.employer_id,
            "worker_id": contract.worker_id,
            "employer_terms": contract.employer_terms,
            "worker_terms": contract.worker_terms,
            "hours": contract.worker_hours_note,
            "pay": str(contract.amount),
            "currency": contract.currency,
            "scope": contract.scope,
        }
    )


def lock_job(job: Job, contract: Contract) -> Job:
    job.locked = True
    job.locked_at = timezone.now()
    job.locked_contract = contract
    job.status = Job.Status.LOCKED
    job.save(update_fields=["locked", "locked_at", "locked_contract", "status", "updated_at"])
    return job


def open_shift_for(contract: Contract) -> ShiftAttendance | None:
    return contract.shifts.filter(checked_out_at__isnull=True).first()


@transaction.atomic
def submit_worker_terms(*, job: Job, worker, worker_terms: str, hours_note: str = "") -> Contract:
    if job.is_filled:
        raise AgreementError("This job is already filled. A bound contract is in place.")
    if job.status != Job.Status.ACTIVE:
        raise AgreementError("This job is not open for applications.")
    if job.employer_id == worker.pk:
        raise AgreementError("You cannot apply to your own posting.")
    terms = (worker_terms or "").strip()
    if len(terms) < 12:
        raise AgreementError("Add your terms of service so the employer can review them.")

    Application.objects.get_or_create(
        worker=worker,
        job=job,
        defaults={"status": Application.Status.SUBMITTED, "note": hours_note[:500]},
    )
    employer_terms = ensure_employer_terms(job)
    existing = (
        Contract.objects.select_for_update()
        .filter(job=job, worker=worker)
        .exclude(approval_status=Contract.ApprovalStatus.REJECTED)
        .first()
    )
    if existing and existing.approval_status == Contract.ApprovalStatus.APPROVED:
        raise AgreementError("This engagement is already bound.")

    from apps.agentic_core import stellar
    from apps.agentic_core.tools import _escrow_units_for, _wallet_address
    from apps.agentic_core.state import EngagementStatus

    cfg = stellar.get_config()
    pay_amount = job.pay
    breakdown = compute_waterfall(
        pay_amount,
        remaining_training_balance=remaining_training_for(worker),
        currency=job.currency,
    )
    now = timezone.now()
    if existing:
        contract = existing
    else:
        contract = Contract(
            job=job,
            worker=worker,
            employer=job.employer,
            status="draft",
            chain_status=EngagementStatus.DRAFT,
            contract_address=cfg.contract_id,
            data_source=cfg.data_source,
            start_date=now,
        )
    contract.scope = job.description or job.title
    contract.duration_text = job.schedule_label
    contract.employer_terms = employer_terms
    contract.worker_terms = terms
    contract.worker_hours_note = (hours_note or "").strip()
    contract.worker_responded_at = now
    contract.approval_status = Contract.ApprovalStatus.PENDING_REVIEW
    contract.rejection_reason = ""
    contract.change_request = ""
    contract.amount = pay_amount
    contract.currency = job.currency
    contract.token_amount = _escrow_units_for(pay_amount, job.currency)
    contract.token_symbol = cfg.token_symbol
    contract.employer_wallet = _wallet_address(job.employer)
    contract.worker_wallet = _wallet_address(worker)
    apply_breakdown_to_contract(contract, breakdown)
    contract.save()
    return contract


@transaction.atomic
def bind_agreement(contract: Contract, *, employer, action: str, reason: str = "") -> Contract:
    if contract.employer_id != employer.pk:
        raise AgreementError("Not your engagement.")
    action = (action or "").strip()
    if action == "reject":
        contract.approval_status = Contract.ApprovalStatus.REJECTED
        contract.rejection_reason = reason or "Rejected by employer"
        contract.save(update_fields=["approval_status", "rejection_reason", "updated_at"])
        return contract
    if action == "request_changes":
        contract.approval_status = Contract.ApprovalStatus.CHANGES_REQUESTED
        contract.change_request = reason or "Please revise the terms."
        contract.save(update_fields=["approval_status", "change_request", "updated_at"])
        return contract
    if action != "approve":
        raise AgreementError("Unknown action.")
    if contract.approval_status != Contract.ApprovalStatus.PENDING_REVIEW:
        raise AgreementError("The worker must send terms before you can bind this contract.")
    if not (contract.employer_terms or "").strip() or not (contract.worker_terms or "").strip():
        raise AgreementError("Both parties must state their terms before the contract is bound.")
    if contract.job.is_filled and contract.job.locked_contract_id not in (None, contract.pk):
        raise AgreementError("This job is already locked to another worker.")

    remaining = remaining_training_for(contract.worker)
    breakdown = compute_waterfall(contract.amount, remaining_training_balance=remaining, currency=contract.currency)
    apply_breakdown_to_contract(contract, breakdown)
    contract.approval_status = Contract.ApprovalStatus.APPROVED
    contract.approved_by = employer
    contract.approved_at = timezone.now()
    contract.bound_at = contract.approved_at
    contract.rejection_reason = ""
    contract.change_request = ""
    contract.terms_hash = hash_bound_terms(contract)
    contract.save()
    lock_job(contract.job, contract)
    Application.objects.filter(job=contract.job, worker=contract.worker).update(status=Application.Status.ENGAGED)
    ensure_settlement(contract, status="PENDING")
    issue_worker_service_invoice(contract, breakdown)
    return contract


def issue_worker_service_invoice(contract: Contract, breakdown=None) -> ServiceInvoice | None:
    if breakdown is None:
        breakdown = compute_waterfall(
            contract.amount,
            remaining_training_balance=remaining_training_for(contract.worker),
            currency=contract.currency,
        )
    if breakdown.upskilling_recovery_amount <= 0:
        return None
    lines = [
        {
            "label": "Eligible worker earnings after CareNest 5% platform fee",
            "amount": str(breakdown.worker_net_amount),
        },
        {
            "label": f"Upskilling recovery ({breakdown.upskilling_recovery_percentage}% of eligible earnings, capped)",
            "amount": str(breakdown.upskilling_recovery_amount),
        },
        {
            "label": "Amount paid to worker after this invoice",
            "amount": str(breakdown.worker_payout_amount),
        },
    ]
    invoice, created = ServiceInvoice.objects.get_or_create(
        engagement=contract,
        defaults={
            "worker": contract.worker,
            "description": "CareNest upskilling service recovery",
            "eligible_earnings": breakdown.worker_net_amount,
            "recovery_percentage": breakdown.upskilling_recovery_percentage,
            "recovery_amount": breakdown.upskilling_recovery_amount,
            "remaining_training_after": breakdown.remaining_training_after,
            "currency": breakdown.currency,
            "line_items": lines,
            "status": ServiceInvoice.Status.ISSUED,
        },
    )
    if not created and invoice.status == ServiceInvoice.Status.ISSUED:
        invoice.eligible_earnings = breakdown.worker_net_amount
        invoice.recovery_percentage = breakdown.upskilling_recovery_percentage
        invoice.recovery_amount = breakdown.upskilling_recovery_amount
        invoice.remaining_training_after = breakdown.remaining_training_after
        invoice.line_items = lines
        invoice.save()
    return invoice


def mark_invoice_settled(contract: Contract) -> None:
    ServiceInvoice.objects.filter(engagement=contract, status=ServiceInvoice.Status.ISSUED).update(
        status=ServiceInvoice.Status.SETTLED, settled_at=timezone.now()
    )


@transaction.atomic
def check_in(*, contract: Contract, worker, note: str = "") -> ShiftAttendance:
    if contract.worker_id != worker.pk:
        raise AgreementError("Not your engagement.")
    if not contract.is_bound or not contract.job.locked:
        raise AgreementError("Check-in opens after both parties bind the contract.")
    if open_shift_for(contract):
        raise AgreementError("You are already checked in. Check out before starting a new shift.")
    return ShiftAttendance.objects.create(
        engagement=contract,
        worker=worker,
        check_in_note=(note or "")[:255],
    )


@transaction.atomic
def check_out(*, contract: Contract, worker, note: str = "") -> ShiftAttendance:
    if contract.worker_id != worker.pk:
        raise AgreementError("Not your engagement.")
    shift = open_shift_for(contract)
    if not shift:
        raise AgreementError("You are not checked in.")
    shift.checked_out_at = timezone.now()
    shift.check_out_note = (note or "")[:255]
    shift.save(update_fields=["checked_out_at", "check_out_note"])
    return shift
