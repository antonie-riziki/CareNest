from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from apps.comms_layer.messaging import (
    employer_account_activation_message,
    worker_account_activation_message,
)
from apps.comms_layer.utils import format_phone_number
from apps.profiles.models import EmployerProfile, WorkerProfile
from apps.wallet.models import Wallet

from apps.accounts.google import google_authorize_url, google_configured, google_fetch_profile

User = get_user_model()
AUTH_BACKEND = "django.contrib.auth.backends.ModelBackend"


def _mask_phone(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) < 4:
        return ""
    return f"+{digits[:4]}****{digits[-3:]}"


def csrf_failure(request, reason=""):
    messages.error(request, "Your session expired. Please submit the form again.")
    referer = request.META.get("HTTP_REFERER") or request.path or "/"
    return redirect(referer)


def _split_name(full_name: str, email: str = "") -> tuple[str, str]:
    parts = (full_name or "").strip().split()
    if parts:
        return parts[0], " ".join(parts[1:])
    local = (email or "").split("@")[0].replace(".", " ").replace("_", " ").strip()
    return (local.title() or "Member"), ""


def _complete_login(request, user):
    login(request, user, backend=AUTH_BACKEND)
    Wallet.objects.get_or_create(user=user, defaults={"balance": 0})
    if getattr(user, "role", "") == "employer" or EmployerProfile.objects.filter(user=user).exists():
        return redirect("employer-dashboard")
    return redirect("worker-dashboard")


def _resolve_username(identifier: str) -> str:
    value = (identifier or "").strip()
    if not value:
        return ""
    if "@" in value:
        user = User.objects.filter(email__iexact=value).first() or User.objects.filter(username__iexact=value).first()
        return user.username if user else value
    phone = format_phone_number(value)
    if phone:
        worker = WorkerProfile.objects.filter(phone=phone).select_related("user").first()
        if worker:
            return worker.user.username
        employer = EmployerProfile.objects.filter(phone=phone).select_related("user").first()
        if employer:
            return employer.user.username
    return value


def _create_account(*, role: str, email: str, password: str, full_name: str, phone: str = "", photo_url: str = ""):
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError("Enter a valid email address.")
    if User.objects.filter(username__iexact=email).exists() or User.objects.filter(email__iexact=email).exists():
        raise ValueError("Email already registered. Sign in instead.")
    first_name, last_name = _split_name(full_name, email)
    with transaction.atomic():
        user = User.objects.create_user(
            username=email,
            email=email,
            first_name=first_name,
            last_name=last_name,
            role=role,
        )
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
        formatted = format_phone_number(phone) or ""
        if role == "employer":
            EmployerProfile.objects.create(user=user, phone=formatted, photo_url=photo_url or "")
        else:
            WorkerProfile.objects.create(
                user=user,
                skills="",
                phone=formatted,
                phone_masked=_mask_phone(formatted),
                photo_url=photo_url or "",
            )
        Wallet.objects.get_or_create(user=user, defaults={"balance": 0})
    try:
        from apps.core.supabase_client import sync_account

        sync_account(user)
    except Exception:
        pass
    return user


def worker_signup(request):
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip()
        password = request.POST.get("password") or ""
        confirm_password = request.POST.get("confirm_password") or ""
        phone = request.POST.get("phone") or ""
        if password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return redirect("worker-signup")
        if len(password) < 8:
            messages.error(request, "Use a password with at least 8 characters.")
            return redirect("worker-signup")
        if phone and not format_phone_number(phone):
            messages.error(request, "Enter a valid Kenyan phone number, e.g. 0712 345 678.")
            return redirect("worker-signup")
        try:
            user = _create_account(
                role="worker",
                email=email,
                password=password,
                full_name=request.POST.get("full_name") or "",
                phone=phone,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("worker-signup")
        worker_account_activation_message(phone)
        messages.success(request, "Worker account created successfully.")
        return _complete_login(request, user)
    return render(request, "worker_signup.html", {"google_oauth": google_configured()})


def worker_signin(request):
    if request.method == "POST":
        identifier = request.POST.get("identifier") or ""
        password = request.POST.get("password") or ""
        username = _resolve_username(identifier)
        user = authenticate(request, username=username, password=password)
        if user is None:
            messages.error(request, "Invalid email, phone, or password.")
            return redirect("worker-signin")
        if user.role != "worker" and not WorkerProfile.objects.filter(user=user).exists():
            messages.error(request, "Account found, but it's not a worker account.")
            return redirect("worker-signin")
        messages.success(request, f"Welcome back, {user.first_name or user.username}!")
        return _complete_login(request, user)
    return render(request, "worker_signin.html", {"google_oauth": google_configured()})


def employer_signup(request):
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip()
        password = request.POST.get("password") or ""
        confirm_password = request.POST.get("confirm_password") or ""
        phone = request.POST.get("phone") or ""
        if password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return redirect("employer-signup")
        if len(password) < 8:
            messages.error(request, "Use a password with at least 8 characters.")
            return redirect("employer-signup")
        if phone and not format_phone_number(phone):
            messages.error(request, "Enter a valid Kenyan phone number, e.g. 0712 345 678.")
            return redirect("employer-signup")
        try:
            user = _create_account(
                role="employer",
                email=email,
                password=password,
                full_name=request.POST.get("full_name") or "",
                phone=phone,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("employer-signup")
        employer_account_activation_message(phone)
        messages.success(request, "Employer account created successfully.")
        return _complete_login(request, user)
    return render(request, "employer_signup.html", {"google_oauth": google_configured()})


def employer_signin(request):
    if request.method == "POST":
        identifier = request.POST.get("identifier") or ""
        password = request.POST.get("password") or ""
        username = _resolve_username(identifier)
        user = authenticate(request, username=username, password=password)
        if user is None:
            messages.error(request, "Invalid email, phone, or password.")
            return redirect("employer-signin")
        if user.role != "employer" and not EmployerProfile.objects.filter(user=user).exists():
            messages.error(request, "Account found, but it's not an employer account.")
            return redirect("employer-signin")
        messages.success(request, f"Welcome back, {user.first_name or user.username}!")
        return _complete_login(request, user)
    return render(request, "employer_signin.html", {"google_oauth": google_configured()})


@require_GET
def google_oauth_start(request):
    role = (request.GET.get("role") or "worker").strip().lower()
    if role not in {"worker", "employer"}:
        role = "worker"
    login_name = "employer-signin" if role == "employer" else "worker-signin"
    if not google_configured():
        messages.error(request, "Google sign-in is not configured yet. Create an account with email instead.")
        return redirect(login_name)
    return redirect(google_authorize_url(request, role=role))


@require_GET
def google_oauth_callback(request):
    role = request.session.get("google_oauth_role") or "worker"
    login_name = "employer-signin" if role == "employer" else "worker-signin"
    signup_name = "employer-signup" if role == "employer" else "worker-signup"
    error = request.GET.get("error")
    if error:
        messages.error(request, "Google sign-in was cancelled.")
        return redirect(login_name)
    state = request.GET.get("state") or ""
    expected = request.session.get("google_oauth_state") or ""
    if not state or state != expected:
        messages.error(request, "Google sign-in expired. Try again.")
        return redirect(login_name)
    code = request.GET.get("code") or ""
    if not code:
        messages.error(request, "Google did not return an authorization code.")
        return redirect(login_name)
    try:
        profile = google_fetch_profile(request, code)
    except Exception:
        messages.error(request, "Google sign-in failed. Try email and password.")
        return redirect(login_name)
    email = profile["email"]
    user = User.objects.filter(email__iexact=email).first() or User.objects.filter(username__iexact=email).first()
    if user:
        if user.role and user.role != role:
            messages.error(request, f"That Google account is already a {user.role}. Sign in from that portal.")
            return redirect(login_name)
        if role == "worker":
            WorkerProfile.objects.get_or_create(user=user, defaults={"skills": "", "photo_url": profile.get("picture") or ""})
        else:
            EmployerProfile.objects.get_or_create(user=user, defaults={"photo_url": profile.get("picture") or ""})
        messages.success(request, f"Welcome back, {user.first_name or user.username}!")
        return _complete_login(request, user)
    try:
        user = _create_account(
            role=role,
            email=email,
            password=None,
            full_name=profile.get("full_name") or f"{profile.get('first_name')} {profile.get('last_name')}".strip(),
            photo_url=profile.get("picture") or "",
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(signup_name)
        messages.success(request, "Google account connected. You're in.")
        return _complete_login(request, user)


def worker_logout(request):
    logout(request)
    return redirect("worker-signin")


def employer_logout(request):
    logout(request)
    return redirect("employer-signin")
