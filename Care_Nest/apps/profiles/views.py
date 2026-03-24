from django.shortcuts import render


# Create your views here.
def worker_profile(request):
    return render(request, "worker_profile.html")


def employer_profile(request):
    return render(request, "employer_profile.html")
