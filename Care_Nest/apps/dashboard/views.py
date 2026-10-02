from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.utils import timezone

from apps.contracts.models import Contract
from apps.jobs.models import Application, Job
from apps.agentic_core.models import AgentApproval, WorkCredential
from apps.agentic_core.state import EngagementStatus
from apps.agentic_core import stellar
from apps.courses.models import Course, Enrollment
from apps.jobs.scheduling import refresh_queryset
from apps.wallet.models import Wallet
from apps.wallet.pricing import get_price_service


def _chain():
    cfg = stellar.get_config()
    return {
        "is_live": cfg.configured,
        "mode_label": cfg.mode_label,
        "network": cfg.network,
        "contract_id": cfg.contract_id,
        "contract_url": stellar.explorer_contract_url(cfg.contract_id) if cfg.contract_id else "",
    }


@login_required(login_url="worker-signin")
def worker_dashboard(request):
    engagements = Contract.objects.filter(worker=request.user).select_related("job", "employer")
    active = engagements.exclude(chain_status__in=EngagementStatus.TERMINAL | {EngagementStatus.DRAFT})
    credentials = WorkCredential.objects.filter(worker=request.user)
    earned = credentials.aggregate(total=Sum("amount"))["total"] or 0
    latest = active.order_by("-updated_at").first()
    live_jobs = [job for job in refresh_queryset(Job.objects.order_by("-created_at")[:40]) if job.is_live]
    open_jobs = live_jobs[:5]
    enrollments = Enrollment.objects.filter(worker=request.user).select_related("course")
    training_remaining = sum((e.amount_remaining for e in enrollments), start=0)
    wallet = Wallet.objects.filter(user=request.user).first()
    recommended = enrollments.first()
    featured_course = recommended.course if recommended else Course.objects.filter(is_active=True).order_by("title").first()
    applications = Application.objects.filter(worker=request.user).count()
    return render(
        request,
        "worker_dashboard.html",
        {
            "active_count": active.count(),
            "earned": earned,
            "latest_engagement": latest,
            "open_jobs": open_jobs,
            "live_jobs_count": len(live_jobs),
            "application_count": applications,
            "credential_count": credentials.count(),
            "enrollments": enrollments,
            "training_remaining": training_remaining,
            "featured_course": featured_course,
            "wallet": wallet,
            "prices": get_price_service().dashboard(),
            "chain": _chain(),
        },
    )


@login_required(login_url="employer-signin")
def employer_dashboard(request):
    engagements = Contract.objects.filter(employer=request.user).select_related("job", "worker")
    active = engagements.filter(
        chain_status__in=EngagementStatus.FUNDS_IN_ESCROW | {EngagementStatus.CREATED, EngagementStatus.DRAFT}
    )
    pending = AgentApproval.objects.filter(requested_from=request.user, status=AgentApproval.Status.PENDING)
    pending_engagements = engagements.filter(approval_status=Contract.ApprovalStatus.PENDING_REVIEW)
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent = (
        engagements.filter(chain_status=EngagementStatus.RELEASED, updated_at__gte=month_start).aggregate(total=Sum("amount"))["total"]
        or 0
    )
    jobs = refresh_queryset(Job.objects.filter(employer=request.user).order_by("-created_at")[:8])
    live_jobs = [job for job in jobs if job.is_live]
    applicants = Application.objects.filter(job__employer=request.user).count()
    escrowed = (
        engagements.filter(chain_status__in=EngagementStatus.FUNDS_IN_ESCROW).aggregate(total=Sum("amount"))["total"] or 0
    )
    wallet = Wallet.objects.filter(user=request.user).first()
    return render(
        request,
        "employer_dashboard.html",
        {
            "active_engagements": active.order_by("-updated_at")[:8],
            "active_count": active.exclude(chain_status=EngagementStatus.DRAFT).count(),
            "pending_count": pending.count() + pending_engagements.count(),
            "pending_engagements": pending_engagements[:6],
            "spent_this_month": spent,
            "recent_jobs": jobs,
            "live_jobs_count": len(live_jobs),
            "applicant_count": applicants,
            "escrowed": escrowed,
            "wallet": wallet,
            "prices": get_price_service().dashboard(),
            "chain": _chain(),
        },
    )
