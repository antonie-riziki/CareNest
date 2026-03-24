from django.shortcuts import render


# Create your views here.
def worker_jobs(request):
    return render(request, "worker_jobs.html")


def employer_jobs(request):
    return render(request, "employer_jobs.html")
