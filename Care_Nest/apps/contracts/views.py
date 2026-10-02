from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse
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
from apps.contracts.models import CompletionReport, Contract, Dispute, DisputeEvidence, Notification, ServiceInvoice, ShiftAttendance
from apps.contracts.notify import notify
from apps.contracts.reports import ensure_completion_report
from apps.contracts.uploads import EvidenceUploadError, store_evidence
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
    report = ensure_completion_report(engagement)
    return render(
        request,
        "employer_engagement_review.html",
        {
            "engagement": engagement,
            "breakdown": employer_breakdown,
            "fx": fx,
            "shifts": shifts,
            "audience": "employer",
            "report": report,
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
            "report": ensure_completion_report(engagement),
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
        verify_shift(
            shift=shift,
            employer=request.user,
            note=request.POST.get("note") or "",
            rating=request.POST.get("rating") or None,
        )
        messages.success(request, "Work recorded and approved.")
    except AgreementError as exc:
        messages.error(request, str(exc))
    return redirect("employer-engagement-review", pk=shift.engagement_id)


def _dispute_queryset(user):
    return Dispute.objects.filter(
        Q(reporter=user) | Q(against=user) | Q(engagement__employer=user) | Q(engagement__worker=user)
    ).distinct()


def _party_contracts(user, *, query: str = ""):
    qs = Contract.objects.filter(
        Q(employer=user) | Q(worker=user),
        approval_status=Contract.ApprovalStatus.APPROVED,
    ).select_related("job", "employer", "worker")
    query = (query or "").strip()
    if query:
        filters = (
            Q(job__title__icontains=query)
            | Q(job__location__icontains=query)
            | Q(employer__first_name__icontains=query)
            | Q(employer__last_name__icontains=query)
            | Q(employer__username__icontains=query)
            | Q(worker__first_name__icontains=query)
            | Q(worker__last_name__icontains=query)
            | Q(worker__username__icontains=query)
        )
        raw_id = query.lstrip("#")
        if raw_id.isdigit():
            filters |= Q(pk=int(raw_id))
        qs = qs.filter(filters)
    return qs.order_by("-updated_at")


def _user_can_see_dispute(user, dispute: Dispute) -> bool:
    return user.pk in {
        dispute.reporter_id,
        dispute.against_id,
        dispute.engagement.employer_id,
        dispute.engagement.worker_id,
    }


def _save_evidence(request, dispute: Dispute) -> int:
    saved = 0
    uploads = request.FILES.getlist("evidence") if hasattr(request.FILES, "getlist") else []
    single = request.FILES.get("evidence")
    if single and single not in uploads:
        uploads.append(single)
    for uploaded in uploads:
        url, kind, filename = store_evidence(uploaded)
        DisputeEvidence.objects.create(
            dispute=dispute,
            uploaded_by=request.user,
            kind=kind,
            file_url=url,
            file_name=filename[:255],
            content_type=getattr(uploaded, "content_type", "") or "",
        )
        saved += 1
    return saved


@login_required
def disputes_portal(request):
    query = request.GET.get("q") or ""
    selected_id = request.GET.get("engagement") or ""
    engagements = _party_contracts(request.user, query=query)
    selected = None
    if selected_id:
        selected = _party_contracts(request.user).filter(pk=selected_id).first()
        if selected and not engagements.filter(pk=selected.pk).exists():
            engagements = [selected, *list(engagements)]
    disputes = (
        _dispute_queryset(request.user)
        .select_related("engagement", "job", "reporter", "against")
        .prefetch_related("evidence")
    )
    grouped = {
        "ongoing": disputes.filter(status=Dispute.Status.ONGOING),
        "unresolved": disputes.filter(status=Dispute.Status.UNRESOLVED),
        "settled": disputes.filter(status=Dispute.Status.SETTLED),
    }
    template = "employer_disputes.html" if getattr(request.user, "role", "") == "employer" else "worker_disputes.html"
    return render(
        request,
        template,
        {
            "grouped": grouped,
            "engagements": engagements,
            "selected": selected,
            "query": query,
            "categories": Dispute.Category.choices,
        },
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
    against = engagement.worker if engagement.employer_id == request.user.pk else engagement.employer
    title = (request.POST.get("title") or "").strip()
    description = (request.POST.get("description") or "").strip()
    if len(title) < 4 or len(description) < 12:
        messages.error(request, "Add a short title and a description of the dispute.")
        return redirect(f"/disputes/?engagement={engagement.pk}")
    category = request.POST.get("category") or Dispute.Category.OTHER
    if category not in Dispute.Category.values:
        category = Dispute.Category.OTHER
    uploads = request.FILES.getlist("evidence")
    if not uploads:
        messages.error(request, "Upload at least one image, document, or video as proof.")
        return redirect(f"/disputes/?engagement={engagement.pk}")
    dispute = Dispute.objects.create(
        engagement=engagement,
        job=engagement.job,
        reporter=request.user,
        against=against,
        category=category,
        title=title[:160],
        description=description,
    )
    try:
        _save_evidence(request, dispute)
    except EvidenceUploadError as exc:
        messages.error(request, str(exc))
        return redirect("dispute-detail", pk=dispute.pk)
    notify(
        recipient=against,
        actor=request.user,
        engagement=engagement,
        kind=Notification.Kind.DISPUTE,
        title="A dispute was opened",
        body=title,
        url=f"/disputes/{dispute.pk}/",
    )
    messages.success(request, f"Dispute #{dispute.pk} opened on contract #{engagement.pk}.")
    return redirect("dispute-detail", pk=dispute.pk)


@login_required
def dispute_detail(request, pk):
    dispute = get_object_or_404(
        Dispute.objects.select_related("engagement", "job", "reporter", "against").prefetch_related("evidence"),
        pk=pk,
    )
    if not _user_can_see_dispute(request.user, dispute):
        messages.error(request, "Not your dispute.")
        return redirect("disputes")
    template = "employer_dispute_detail.html" if getattr(request.user, "role", "") == "employer" else "worker_dispute_detail.html"
    return render(request, template, {"dispute": dispute, "engagement": dispute.engagement})


@login_required
@require_POST
def add_dispute_evidence(request, pk):
    dispute = get_object_or_404(Dispute, pk=pk)
    if not _user_can_see_dispute(request.user, dispute):
        messages.error(request, "Not your dispute.")
        return redirect("disputes")
    try:
        saved = _save_evidence(request, dispute)
        if not saved:
            raise EvidenceUploadError("Choose at least one image, document, or video.")
        messages.success(request, "Proof uploaded to this contract dispute.")
    except EvidenceUploadError as exc:
        messages.error(request, str(exc))
    return redirect("dispute-detail", pk=pk)


@login_required
@require_POST
def update_dispute(request, pk):
    dispute = get_object_or_404(Dispute, pk=pk)
    if not _user_can_see_dispute(request.user, dispute):
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
    return redirect("dispute-detail", pk=pk)


def _report_or_404(token):
    return get_object_or_404(CompletionReport, share_token=token)


def completion_report_share(request, token):
    report = _report_or_404(token)
    snapshot = report.snapshot or {}
    share_url = request.build_absolute_uri(report.share_path)
    return render(
        request,
        "completion_report.html",
        {"report": report, "snapshot": snapshot, "share_url": share_url, "pdf_url": f"{report.share_path}pdf/"},
    )


def completion_report_pdf(request, token):
    report = _report_or_404(token)
    from apps.contracts.pdf import render_completion_pdf

    share_url = request.build_absolute_uri(report.share_path)
    payload = render_completion_pdf(report.snapshot or {}, share_url=share_url)
    response = HttpResponse(payload, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="carenest-contract-{report.engagement_id}.pdf"'
    return response


@login_required
def engagement_report(request, pk):
    engagement = get_object_or_404(Contract, pk=pk)
    if request.user.pk not in {engagement.employer_id, engagement.worker_id}:
        messages.error(request, "Not your contract.")
        return redirect("home")
    report = ensure_completion_report(engagement)
    if not report:
        messages.info(request, "The completion report is available after the job is done and invoices are cleared.")
        if getattr(request.user, "role", "") == "employer":
            return redirect("employer-engagement-review", pk=pk)
        return redirect("worker-engagement-review", pk=pk)
    return redirect("completion-report", token=report.share_token)
