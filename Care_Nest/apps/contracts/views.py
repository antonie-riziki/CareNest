from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.contracts.models import Contract
from apps.agentic_core.state import EngagementStatus, explain


@login_required(login_url="worker-signin")
def worker_contracts(request):
    engagements = Contract.objects.filter(worker=request.user).select_related("job", "employer").order_by("-updated_at")
    cards = [{"engagement": c, "explanation": explain(c.chain_status), "active": c.chain_status not in EngagementStatus.TERMINAL} for c in engagements]
    return render(request, "worker_contracts.html", {"cards": cards})


@login_required(login_url="employer-signin")
def employer_contracts(request):
    engagements = Contract.objects.filter(employer=request.user).select_related("job", "worker").order_by("-updated_at")
    return render(request, "employer_contracts.html", {"engagements": engagements})
