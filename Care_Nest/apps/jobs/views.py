from django.shortcuts import render


# Create your views here.
def worker_jobs(request):
    return render(request, "worker_jobs.html")


def employer_jobs(request):
    return render(request, "employer_jobs.html")


def worker_job_details(request):
    return render(request, "worker_job_details.html")


def employer_job_details(request):
    return render(request, "employer_job_details.html")
