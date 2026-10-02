from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from apps.comms_layer.utils import format_phone_number
from apps.profiles.models import EmployerProfile, WorkerProfile
from apps.wallet.models import Wallet


@login_required(login_url="worker-signin")
def worker_settings(request):
    profile, _ = WorkerProfile.objects.get_or_create(user=request.user)
    if request.method == "POST":
        user = request.user
        user.first_name = (request.POST.get("first_name") or user.first_name).strip()
        user.last_name = (request.POST.get("last_name") or user.last_name).strip()
        user.save(update_fields=["first_name", "last_name"])
        phone = format_phone_number(request.POST.get("phone") or "") or profile.phone
        profile.phone = phone
        profile.skills = request.POST.get("skills") or profile.skills
        profile.preferred_payout = request.POST.get("preferred_payout") or profile.preferred_payout
        profile.save()
        messages.success(request, "Settings saved.")
        return redirect("worker-settings")
    wallet = Wallet.objects.filter(user=request.user).first()
    skills = [s.strip() for s in (profile.skills or "").split(",") if s.strip()]
    return render(
        request,
        "worker_settings.html",
        {"profile": profile, "wallet": wallet, "skills": skills},
    )


@login_required(login_url="employer-signin")
def employer_settings(request):
    profile, _ = EmployerProfile.objects.get_or_create(user=request.user)
    if request.method == "POST":
        user = request.user
        user.first_name = (request.POST.get("first_name") or user.first_name).strip()
        user.last_name = (request.POST.get("last_name") or user.last_name).strip()
        user.save(update_fields=["first_name", "last_name"])
        phone = format_phone_number(request.POST.get("phone") or "") or profile.phone
        profile.phone = phone
        profile.organisation = (request.POST.get("organisation") or profile.organisation).strip()
        profile.location_label = (request.POST.get("location_label") or profile.location_label).strip()
        profile.save()
        messages.success(request, "Household settings saved.")
        return redirect("employer-settings")
    wallet = Wallet.objects.filter(user=request.user).first()
    return render(request, "employer_settings.html", {"profile": profile, "wallet": wallet})
