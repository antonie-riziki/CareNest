from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.utils import timezone

from apps.contracts.models import Contract
from apps.jobs.models import Job
from apps.agentic_core.models import AgentApproval, WorkCredential
from apps.agentic_core.state import EngagementStatus
from apps.agentic_core import stellar


def _chain():
    cfg = stellar.get_config()
    return {"is_live": cfg.configured, "mode_label": cfg.mode_label, "network": cfg.network, "contract_id": cfg.contract_id, "contract_url": stellar.explorer_contract_url(cfg.contract_id) if cfg.contract_id else ""}


@login_required(login_url="worker-signin")
def worker_dashboard(request):
    engagements = Contract.objects.filter(worker=request.user).select_related("job", "employer")
    active = engagements.exclude(chain_status__in=EngagementStatus.TERMINAL | {EngagementStatus.DRAFT})
    credentials = WorkCredential.objects.filter(worker=request.user)
    earned = credentials.aggregate(total=Sum("amount"))["total"] or 0
    latest = active.order_by("-updated_at").first()
    open_jobs = Job.objects.exclude(employer=request.user).order_by("-created_at")[:5]
    return render(
        request,
        "worker_dashboard.html",
        {
            "active_count": active.count(),
            "earned": earned,
            "latest_engagement": latest,
            "open_jobs": open_jobs,
            "credential_count": credentials.count(),
            "chain": _chain(),
        },
    )


@login_required(login_url="employer-signin")
def employer_dashboard(request):
    engagements = Contract.objects.filter(employer=request.user).select_related("job", "worker")
    active = engagements.filter(chain_status__in=EngagementStatus.FUNDS_IN_ESCROW | {EngagementStatus.CREATED, EngagementStatus.DRAFT})
    pending = AgentApproval.objects.filter(requested_from=request.user, status=AgentApproval.Status.PENDING)
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent = (
        engagements.filter(chain_status=EngagementStatus.RELEASED, updated_at__gte=month_start).aggregate(total=Sum("amount"))["total"]
        or 0
    )
    jobs = Job.objects.filter(employer=request.user).order_by("-created_at")[:5]
    return render(
        request,
        "employer_dashboard.html",
        {
            "active_engagements": active.order_by("-updated_at")[:8],
            "active_count": active.exclude(chain_status=EngagementStatus.DRAFT).count(),
            "pending_count": pending.count(),
            "spent_this_month": spent,
            "recent_jobs": jobs,
            "chain": _chain(),
        },
    )
