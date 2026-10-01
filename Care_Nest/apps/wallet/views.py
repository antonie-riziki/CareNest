from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.agentic_core.models import WorkCredential
from apps.agentic_core.state import EngagementStatus
from apps.contracts.models import Contract
from apps.courses.models import Enrollment
from apps.wallet.models import PayoutMethod, Transaction, Wallet
from apps.wallet.payouts import PayoutError, preferred_payout_method, save_mpesa_method
from apps.wallet.pricing import get_price_service
from apps.wallet.providers import WalletError, disconnect_wallet, persist_connection


@login_required(login_url="worker-signin")
def worker_wallet(request):
    wallet, _ = Wallet.objects.get_or_create(user=request.user, defaults={"balance": 0})
    earned = WorkCredential.objects.filter(worker=request.user).aggregate(total=Sum("amount"))["total"] or 0
    escrowed = (
        Contract.objects.filter(worker=request.user, chain_status__in=EngagementStatus.FUNDS_IN_ESCROW).aggregate(total=Sum("amount"))["total"]
        or 0
    )
    txs = Transaction.objects.filter(wallet=wallet).select_related("settlement").order_by("-created_at")[:12]
    enrollments = Enrollment.objects.filter(worker=request.user).select_related("course")
    remaining = sum((e.amount_remaining for e in enrollments), start=0)
    payouts = PayoutMethod.objects.filter(user=request.user)
    prices = get_price_service().dashboard()
    from apps.contracts.models import ServiceInvoice

    invoices = ServiceInvoice.objects.filter(worker=request.user).select_related("engagement", "engagement__job")[:8]
    return render(
        request,
        "workers_wallet.html",
        {
            "wallet": wallet,
            "earned": earned,
            "escrowed": escrowed,
            "transactions": txs,
            "enrollments": enrollments,
            "training_remaining": remaining,
            "payout_methods": payouts,
            "preferred_payout": preferred_payout_method(request.user),
            "prices": prices,
            "invoices": invoices,
        },
    )


@login_required(login_url="employer-signin")
def employer_wallet(request):
    return redirect("wallet-connect")


@login_required
@require_POST
def connect_wallet_view(request):
    provider = request.POST.get("provider") or "manual"
    chain = request.POST.get("chain") or ""
    network = request.POST.get("network") or ""
    address = request.POST.get("public_key") or request.POST.get("address") or ""
    try:
        persist_connection(request.user, address=address, provider_name=provider, chain=chain or None, network=network)
        messages.success(request, "Wallet connected.")
    except WalletError as exc:
        messages.error(request, str(exc))
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect("wallet-connect")


@login_required
@require_POST
def disconnect_chain(request):
    chain = request.POST.get("chain") or None
    disconnect_wallet(request.user, chain=chain)
    messages.info(request, "Wallet disconnected.")
    return redirect("wallet-connect")


@login_required(login_url="worker-signin")
@require_POST
def save_mpesa(request):
    try:
        save_mpesa_method(request.user, request.POST.get("phone") or "")
        if request.POST.get("make_default"):
            PayoutMethod.objects.filter(user=request.user).update(is_default=False)
            PayoutMethod.objects.filter(user=request.user, method_type=PayoutMethod.Type.MPESA).update(is_default=True)
        messages.success(request, "M-Pesa payout number saved. Only a masked identifier is stored.")
    except PayoutError as exc:
        messages.error(request, str(exc))
    return redirect("worker-wallet")
