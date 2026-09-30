from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt

from apps.comms_layer.messaging import (
    worker_account_activation_message,
    employer_account_activation_message,
)
from apps.comms_layer.utils import format_phone_number

from apps.profiles.models import WorkerProfile, EmployerProfile

from django.contrib.auth import get_user_model

User = get_user_model()


# Create your views here.
def worker_signup(request):
    if request.method == "POST":
        full_name = request.POST.get("full_name")
        email = request.POST.get("email")
        phone = request.POST.get("phone")
        password = request.POST.get("password")
        confirm_password = request.POST.get("confirm_password")

        formatted_phone = format_phone_number(phone)

        if not formatted_phone:
            return JsonResponse({"error": "Invalid phone number"}, status=400)

        # Check if passwords match
        if password != confirm_password:
            messages.error(request, "Passwords do not match")
            return redirect("worker-signup")

        # Check if email already exists
        if User.objects.filter(username=email).exists():
            messages.error(request, "Email already registered")
            return redirect("worker-signup")

        # Split full name
        name_parts = full_name.split(" ")
        first_name = name_parts[0]
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

        # Create user
        user = User.objects.create_user(
            username=email,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            role="worker",
        )

        user.save()

        # Create Worker Profile
        WorkerProfile.objects.create(user=user, skills="")

        # Log in the user automatically
        login(request, user)

        worker_account_activation_message(phone)
        messages.success(request, "Worker account created successfully")
        return redirect("worker-dashboard")
    return render(request, "worker_signup.html")


def worker_signin(request):
    if request.method == "POST":
        identifier = request.POST.get("identifier")
        password = request.POST.get("password")

        # In this implementation, identifier could be username (email)
        user = authenticate(request, username=identifier, password=password)

        if user is not None:
            # Check if user is a worker
            if WorkerProfile.objects.filter(user=user).exists():
                login(request, user)
                messages.success(request, f"Welcome back, {user.first_name}!")
                return redirect("worker-dashboard")
            else:
                messages.error(request, "Account found, but it's not a Worker account.")
                return redirect("worker-signin")
        else:
            messages.error(request, "Invalid email or password")
            return redirect("worker-signin")

    return render(request, "worker_signin.html")


def employer_signup(request):
    if request.method == "POST":
        full_name = request.POST.get("full_name")
        email = request.POST.get("email")
        phone = request.POST.get("phone")
        password = request.POST.get("password")
        confirm_password = request.POST.get("confirm_password")

        # Check if passwords match
        if password != confirm_password:
            messages.error(request, "Passwords do not match")
            return redirect("employer-signup")

        # Check if email already exists
        if User.objects.filter(username=email).exists():
            messages.error(request, "Email already registered")
            return redirect("employer-signup")

        # Split full name
        name_parts = full_name.split(" ")
        first_name = name_parts[0]
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

        # Create user
        user = User.objects.create_user(
            username=email,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            role="employer",
        )

        user.save()

        # Create Employer Profile
        EmployerProfile.objects.create(user=user)

        # Log in the user automatically
        login(request, user)

        employer_account_activation_message(phone)
        messages.success(request, "Employer account created successfully")
        return redirect("employer-dashboard")
    return render(request, "employer_signup.html")


def employer_signin(request):
    if request.method == "POST":
        identifier = request.POST.get("identifier")
        password = request.POST.get("password")

        # In this implementation, identifier could be username (email)
        user = authenticate(request, username=identifier, password=password)

        if user is not None:
            # Check if user is an employer
            if EmployerProfile.objects.filter(user=user).exists():
                login(request, user)
                messages.success(request, f"Welcome back, {user.first_name}!")
                return redirect("employer-dashboard")
            else:
                messages.error(
                    request, "Account found, but it's not an Employer account."
                )
                return redirect("employer-signin")
        else:
            messages.error(request, "Invalid email or password")
            return redirect("employer-signin")

    return render(request, "employer_signin.html")
