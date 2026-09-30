from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import redirect, render

from apps.wallet.models import Wallet, Transaction
from apps.contracts.models import Contract
from apps.agentic_core.models import WorkCredential
from apps.agentic_core.state import EngagementStatus


@login_required(login_url="worker-signin")
def worker_wallet(request):
    wallet, _ = Wallet.objects.get_or_create(user=request.user, defaults={"balance": 0})
    earned = WorkCredential.objects.filter(worker=request.user).aggregate(total=Sum("amount"))["total"] or 0
    escrowed = (
        Contract.objects.filter(worker=request.user, chain_status__in=EngagementStatus.FUNDS_IN_ESCROW).aggregate(total=Sum("amount"))["total"]
        or 0
    )
    txs = Transaction.objects.filter(wallet=wallet).order_by("-created_at")[:12]
    return render(
        request,
        "workers_wallet.html",
        {"wallet": wallet, "earned": earned, "escrowed": escrowed, "transactions": txs},
    )


@login_required(login_url="employer-signin")
def employer_wallet(request):
    return redirect("wallet-connect")
