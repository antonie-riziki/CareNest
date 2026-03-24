from django.shortcuts import render


# Create your views here.
def worker_signup(request):
    return render(request, "worker_signup.html")


def worker_login(request):
    return render(request, "worker_login.html")


def employer_signup(request):
    return render(request, "employer_signup.html")


def employer_login(request):
    return render(request, "employer_login.html")
