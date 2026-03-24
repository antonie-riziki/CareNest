from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.models import User
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt

from apps.comms_layer.messaging import *


# Create your views here.
def worker_signup(request):
    if request.method == "POST":
        full_name = request.POST.get("full_name")
        email = request.POST.get("email")
        phone = request.POST.get("phone")
        password = request.POST.get("password")
        confirm_password = request.POST.get("confirm_password")

        # Check if passwords match
        if password != confirm_password:
            messages.error(request, "Passwords do not match")
            return redirect("register")

        # Check if email already exists
        if User.objects.filter(username=email).exists():
            messages.error(request, "Email already registered")
            return redirect("register")

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
        )

        user.save()

        account_activation_message(phone)
        messages.success(request, "Account created successfully")
        return redirect("signin")
    return render(request, "worker_signup.html")


def worker_signin(request):
    return render(request, "worker_signin.html")


def employer_signup(request):
    return render(request, "employer_signup.html")


def employer_signin(request):
    return render(request, "employer_signin.html")
