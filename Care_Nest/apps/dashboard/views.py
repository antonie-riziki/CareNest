from django.shortcuts import render


# Create your views here.
def worker_dashboard(request):
    return render(request, "worker_dashboard.html")


def employer_dashboard(request):
    return render(request, "employer_dashboard.html")
