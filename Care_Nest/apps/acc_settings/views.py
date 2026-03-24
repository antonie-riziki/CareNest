from django.shortcuts import render


# Create your views here.
def worker_settings(request):
    return render(request, "worker_settings.html")


def employer_settings(request):
    return render(request, "employer_settings.html")
