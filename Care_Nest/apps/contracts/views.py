from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
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
    verify_shift,
)
from apps.contracts.models import Contract, Dispute, Notification, ServiceInvoice, ShiftAttendance
from apps.contracts.notify import notify
from apps.jobs.models import Application, Job
from apps.wallet.pricing import get_price_service
from apps.wallet.settlement import compute_waterfall


@login_required(login_url="worker-signin")
def worker_contracts(request):
    engagements = Contract.objects.filter(worker=request.user).select_related("job", "employer").order_by("-updated_at")
    cards = [
        {
            "engagement": c,
            "explanation": explain(c.chain_status),
            "active": c.chain_status not in EngagementStatus.TERMINAL,
            "status_label": c.display_status,
        }
        for c in engagements
    ]
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
        messages.success(request, "Checked in. Your employer was notified.")
    except AgreementError as exc:
        messages.error(request, str(exc))
    return redirect("worker-engagement-review", pk=pk)


@login_required(login_url="worker-signin")
@require_POST
def check_out_shift(request, pk):
    engagement = get_object_or_404(Contract, pk=pk, worker=request.user)
    try:
        shift = check_out(
            contract=engagement,
            worker=request.user,
            note=request.POST.get("note") or "",
            work_summary=request.POST.get("work_summary") or "",
        )
        minutes = shift.duration_minutes or 0
        messages.success(request, f"Checked out. Work submitted for employer approval ({minutes} min).")
    except AgreementError as exc:
        messages.error(request, str(exc))
    return redirect("worker-engagement-review", pk=pk)


@login_required(login_url="worker-signin")
def worker_service_invoice(request, pk):
    invoice = get_object_or_404(ServiceInvoice, pk=pk, worker=request.user)
    return render(request, "worker_service_invoice.html", {"invoice": invoice})


@login_required(login_url="employer-signin")
@require_POST
def verify_shift_view(request, pk):
    shift = get_object_or_404(ShiftAttendance, pk=pk, engagement__employer=request.user)
    try:
        verify_shift(shift=shift, employer=request.user, note=request.POST.get("note") or "")
        messages.success(request, "Work recorded and approved.")
    except AgreementError as exc:
        messages.error(request, str(exc))
    return redirect("employer-engagement-review", pk=shift.engagement_id)


def _dispute_queryset(user):
    return (
        Dispute.objects.filter(Q(reporter=user) | Q(against=user) | Q(engagement__employer=user) | Q(engagement__worker=user))
        .select_related("engagement", "job", "reporter", "against")
        .distinct()
    )


@login_required
def disputes_portal(request):
    disputes = _dispute_queryset(request.user)
    grouped = {
        "ongoing": disputes.filter(status=Dispute.Status.ONGOING),
        "unresolved": disputes.filter(status=Dispute.Status.UNRESOLVED),
        "settled": disputes.filter(status=Dispute.Status.SETTLED),
    }
    engagements = Contract.objects.filter(Q(employer=request.user) | Q(worker=request.user), approval_status=Contract.ApprovalStatus.APPROVED)
    template = "employer_disputes.html" if getattr(request.user, "role", "") == "employer" else "worker_disputes.html"
    return render(
        request,
        template,
        {"grouped": grouped, "engagements": engagements, "categories": Dispute.Category.choices},
    )


@login_required
@require_POST
def report_dispute(request):
    engagement = get_object_or_404(
        Contract,
        pk=request.POST.get("engagement_id"),
        approval_status=Contract.ApprovalStatus.APPROVED,
    )
    if request.user.pk not in {engagement.employer_id, engagement.worker_id}:
        messages.error(request, "You can only report disputes on your own contracts.")
        return redirect("disputes")
    if engagement.employer_id == request.user.pk:
        against = engagement.worker
    else:
        against = engagement.employer
    title = (request.POST.get("title") or "").strip()
    description = (request.POST.get("description") or "").strip()
    if len(title) < 4 or len(description) < 12:
        messages.error(request, "Add a short title and a description of the dispute.")
        return redirect("disputes")
    category = request.POST.get("category") or Dispute.Category.OTHER
    if category not in Dispute.Category.values:
        category = Dispute.Category.OTHER
    dispute = Dispute.objects.create(
        engagement=engagement,
        job=engagement.job,
        reporter=request.user,
        against=against,
        category=category,
        title=title[:160],
        description=description,
    )
    notify(
        recipient=against,
        actor=request.user,
        engagement=engagement,
        kind=Notification.Kind.DISPUTE,
        title="A dispute was opened",
        body=title,
        url="/disputes/",
    )
    messages.success(request, f"Dispute #{dispute.pk} opened. Both parties can follow it in the dispute portal.")
    return redirect("disputes")


@login_required
@require_POST
def update_dispute(request, pk):
    dispute = get_object_or_404(Dispute, pk=pk)
    if request.user.pk not in {dispute.reporter_id, dispute.against_id, dispute.engagement.employer_id, dispute.engagement.worker_id}:
        messages.error(request, "Not your dispute.")
        return redirect("disputes")
    action = request.POST.get("action")
    if action == "unresolved":
        dispute.status = Dispute.Status.UNRESOLVED
        dispute.save(update_fields=["status", "updated_at"])
        messages.info(request, "Dispute marked unresolved.")
    elif action == "settle":
        dispute.status = Dispute.Status.SETTLED
        dispute.resolution = (request.POST.get("resolution") or "Settled by the parties.").strip()
        from django.utils import timezone

        dispute.resolved_at = timezone.now()
        dispute.save(update_fields=["status", "resolution", "resolved_at", "updated_at"])
        messages.success(request, "Dispute marked settled.")
    return redirect("disputes")
