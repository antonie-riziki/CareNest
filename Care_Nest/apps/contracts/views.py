from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.agentic_core.state import EngagementStatus, explain
from apps.contracts.models import Contract
from apps.jobs.models import Application, Job
from apps.wallet.pricing import get_price_service
from apps.wallet.settlement import compute_waterfall, ensure_settlement, remaining_training_for


@login_required(login_url="worker-signin")
def worker_contracts(request):
    engagements = Contract.objects.filter(worker=request.user).select_related("job", "employer").order_by("-updated_at")
    cards = [{"engagement": c, "explanation": explain(c.chain_status), "active": c.chain_status not in EngagementStatus.TERMINAL} for c in engagements]
    return render(request, "worker_contracts.html", {"cards": cards})


@login_required(login_url="employer-signin")
def employer_contracts(request):
    engagements = Contract.objects.filter(employer=request.user).select_related("job", "worker").order_by("-updated_at")
    pending = engagements.filter(approval_status__in=[Contract.ApprovalStatus.PENDING_REVIEW, Contract.ApprovalStatus.CHANGES_REQUESTED, Contract.ApprovalStatus.DRAFT])
    return render(
        request,
        "employer_contracts.html",
        {"engagements": engagements, "pending": pending},
    )


@login_required(login_url="employer-signin")
def employer_engagement_review(request, pk):
    engagement = get_object_or_404(Contract, pk=pk, employer=request.user)
    remaining = remaining_training_for(engagement.worker)
    breakdown = compute_waterfall(engagement.amount, remaining_training_balance=remaining, currency=engagement.currency)
    fx = get_price_service().convert(engagement.amount, engagement.currency, "XLM")
    return render(
        request,
        "employer_engagement_review.html",
        {
            "engagement": engagement,
            "breakdown": breakdown,
            "fx": fx,
            "remaining_training": remaining,
        },
    )


@login_required(login_url="employer-signin")
@require_POST
def employer_engagement_decide(request, pk):
    engagement = get_object_or_404(Contract, pk=pk, employer=request.user)
    action = request.POST.get("action")
    if action == "approve":
        remaining = remaining_training_for(engagement.worker)
        breakdown = compute_waterfall(engagement.amount, remaining_training_balance=remaining, currency=engagement.currency)
        engagement.approval_status = Contract.ApprovalStatus.APPROVED
        engagement.approved_by = request.user
        engagement.approved_at = timezone.now()
        engagement.rejection_reason = ""
        engagement.change_request = ""
        engagement.platform_fee_percentage = breakdown.platform_fee_percentage
        engagement.platform_fee_amount = breakdown.platform_fee_amount
        engagement.worker_net_amount = breakdown.worker_net_amount
        engagement.upskilling_recovery_amount = breakdown.upskilling_recovery_amount
        engagement.worker_payout_amount = breakdown.worker_payout_amount
        engagement.save()
        ensure_settlement(engagement, status="PENDING")
        messages.success(request, "Engagement approved. You can now create the on-chain contract from the WorkOS command center.")
        return redirect("agent-command-center", pk=engagement.pk)
    if action == "reject":
        engagement.approval_status = Contract.ApprovalStatus.REJECTED
        engagement.rejection_reason = request.POST.get("reason") or "Rejected by employer"
        engagement.save(update_fields=["approval_status", "rejection_reason", "updated_at"])
        messages.info(request, "Engagement rejected. No funds will move.")
        return redirect("employer-contracts")
    if action == "request_changes":
        engagement.approval_status = Contract.ApprovalStatus.CHANGES_REQUESTED
        engagement.change_request = request.POST.get("reason") or "Please revise the terms."
        engagement.save(update_fields=["approval_status", "change_request", "updated_at"])
        messages.info(request, "Change request sent. The agent can prepare a revision; it cannot approve it.")
        return redirect("employer-contracts")
    messages.error(request, "Unknown action")
    return redirect("employer-engagement-review", pk=pk)


@login_required(login_url="employer-signin")
@require_POST
def start_engagement_from_application(request, application_id):
    application = get_object_or_404(Application, pk=application_id, job__employer=request.user)
    job = application.job
    from apps.agentic_core.agent import CareNestAgent

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
    application.status = Application.Status.ENGAGED
    application.save(update_fields=["status"])
    messages.success(request, "Engagement draft prepared. Review and approve it before funding.")
    return redirect("employer-engagement-review", pk=contract.pk)
