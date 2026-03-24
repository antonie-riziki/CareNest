from django.shortcuts import render


# Create your views here.
def worker_signup(request):
    return render(request, "worker_signup.html")


def worker_signin(request):
    return render(request, "worker_signin.html")


def employer_signup(request):
    return render(request, "employer_signup.html")


def employer_signin(request):
    return render(request, "employer_signin.html")
