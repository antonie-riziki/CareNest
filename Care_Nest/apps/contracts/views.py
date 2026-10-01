from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.agentic_core.state import EngagementStatus, explain
from apps.contracts.agreement import (
    AgreementError,
    bind_agreement,
    check_in,
    check_out,
    open_shift_for,
    submit_worker_terms,
)
from apps.contracts.models import Contract, ServiceInvoice
from apps.jobs.models import Application, Job
from apps.wallet.pricing import get_price_service
from apps.wallet.settlement import compute_waterfall


@login_required(login_url="worker-signin")
def worker_contracts(request):
    engagements = Contract.objects.filter(worker=request.user).select_related("job", "employer").order_by("-updated_at")
    cards = [{"engagement": c, "explanation": explain(c.chain_status), "active": c.chain_status not in EngagementStatus.TERMINAL} for c in engagements]
    return render(request, "worker_contracts.html", {"cards": cards})


@login_required(login_url="employer-signin")
def employer_contracts(request):
    engagements = Contract.objects.filter(employer=request.user).select_related("job", "worker").order_by("-updated_at")
    pending = engagements.filter(approval_status=Contract.ApprovalStatus.PENDING_REVIEW)
    return render(
        request,
        "employer_contracts.html",
        {"engagements": engagements, "pending": pending},
    )


@login_required(login_url="employer-signin")
def employer_engagement_review(request, pk):
    engagement = get_object_or_404(Contract, pk=pk, employer=request.user)
    fx = get_price_service().convert(engagement.amount, engagement.currency, "XLM")
    employer_breakdown = {
        "currency": engagement.currency,
        "gross_amount": engagement.amount,
        "platform_fee_percentage": engagement.platform_fee_percentage,
        "platform_fee_amount": engagement.platform_fee_amount,
        "worker_net_amount": engagement.worker_net_amount,
    }
    shifts = engagement.shifts.all()[:12]
    return render(
        request,
        "employer_engagement_review.html",
        {
            "engagement": engagement,
            "breakdown": employer_breakdown,
            "fx": fx,
            "shifts": shifts,
            "audience": "employer",
        },
    )


@login_required(login_url="employer-signin")
@require_POST
def employer_engagement_decide(request, pk):
    engagement = get_object_or_404(Contract, pk=pk, employer=request.user)
    action = request.POST.get("action")
    try:
        bind_agreement(engagement, employer=request.user, action=action, reason=request.POST.get("reason") or "")
    except AgreementError as exc:
        messages.error(request, str(exc))
        return redirect("employer-engagement-review", pk=pk)
    engagement.refresh_from_db()
    if action == "approve":
        messages.success(request, "Both parties agreed. The contract is bound, the job is locked, and the on-chain agreement can be created.")
        return redirect("agent-command-center", pk=engagement.pk)
    if action == "reject":
        messages.info(request, "Terms declined. No contract was bound and no funds will move.")
        return redirect("employer-contracts")
    messages.info(request, "Change request sent to the worker. They can revise their terms.")
    return redirect("employer-contracts")


@login_required(login_url="employer-signin")
@require_POST
def start_engagement_from_application(request, application_id):
    application = get_object_or_404(Application, pk=application_id, job__employer=request.user)
    job = application.job
    from apps.agentic_core.agent import CareNestAgent
    from apps.contracts.agreement import ensure_employer_terms

    ensure_employer_terms(job)
    agent = CareNestAgent.start(request.user)
    contract = agent.prepare_engagement(
        worker_id=application.worker_id,
        job_id=job.pk,
        requirement={
            "title": job.title,
            "job_type": job.job_type,
            "location": job.location,
            "pay_amount": str(job.pay),
            "pay_currency": job.currency,
            "schedule": job.schedule_label,
            "duration": job.schedule_label,
        },
    )
    agent.end()
    messages.success(request, "Draft offer prepared with your terms. The worker still adds theirs before you can bind the contract.")
    return redirect("employer-engagement-review", pk=contract.pk)


@login_required(login_url="worker-signin")
@require_POST
def submit_job_terms(request, pk):
    job = get_object_or_404(Job, pk=pk)
    try:
        contract = submit_worker_terms(
            job=job,
            worker=request.user,
            worker_terms=request.POST.get("worker_terms") or "",
            hours_note=request.POST.get("hours_note") or "",
        )
    except AgreementError as exc:
        messages.error(request, str(exc))
        return redirect(f"/worker-job-details/?id={job.pk}")
    messages.success(request, "Your terms were sent to the employer. The job stays open until they approve.")
    return redirect("worker-engagement-review", pk=contract.pk)


@login_required(login_url="worker-signin")
def worker_engagement_review(request, pk):
    engagement = get_object_or_404(Contract, pk=pk, worker=request.user)
    remaining = None
    worker_breakdown = None
    invoices = engagement.service_invoices.filter(worker=request.user)
    if engagement.is_bound:
        from apps.wallet.settlement import remaining_training_for

        remaining = remaining_training_for(request.user)
        worker_breakdown = compute_waterfall(engagement.amount, remaining_training_balance=remaining, currency=engagement.currency)
    open_shift = open_shift_for(engagement)
    return render(
        request,
        "worker_engagement_review.html",
        {
            "engagement": engagement,
            "breakdown": worker_breakdown,
            "audience": "worker",
            "invoices": invoices,
            "open_shift": open_shift,
            "shifts": engagement.shifts.all()[:12],
            "explanation": explain(engagement.chain_status),
        },
    )


@login_required(login_url="worker-signin")
@require_POST
def check_in_shift(request, pk):
    engagement = get_object_or_404(Contract, pk=pk, worker=request.user)
    try:
        check_in(contract=engagement, worker=request.user, note=request.POST.get("note") or "")
        messages.success(request, "Checked in. Your shift is open.")
    except AgreementError as exc:
        messages.error(request, str(exc))
    return redirect("worker-engagement-review", pk=pk)


@login_required(login_url="worker-signin")
@require_POST
def check_out_shift(request, pk):
    engagement = get_object_or_404(Contract, pk=pk, worker=request.user)
    try:
        shift = check_out(contract=engagement, worker=request.user, note=request.POST.get("note") or "")
        minutes = shift.duration_minutes or 0
        messages.success(request, f"Checked out. Shift logged ({minutes} min).")
    except AgreementError as exc:
        messages.error(request, str(exc))
    return redirect("worker-engagement-review", pk=pk)


@login_required(login_url="worker-signin")
def worker_service_invoice(request, pk):
    invoice = get_object_or_404(ServiceInvoice, pk=pk, worker=request.user)
    return render(request, "worker_service_invoice.html", {"invoice": invoice})
