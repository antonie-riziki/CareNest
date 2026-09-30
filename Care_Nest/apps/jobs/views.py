import json

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.jobs.models import Application, Job


JOB_ICONS = {
    "nanny": "child_care",
    "housekeeper": "cleaning_services",
    "caregiver": "health_and_safety",
    "chef": "skillet",
    "gardener": "garden_cart",
    "driver": "directions_car",
    "cleaner": "cleaning_services",
    "security": "security",
}


@login_required(login_url="worker-signin")
def worker_jobs(request):
    jobs = Job.objects.select_related("employer").order_by("-created_at")[:40]
    payload = [
        {
            "id": j.pk,
            "title": j.title,
            "category": j.job_type,
            "job_type": j.job_type,
            "icon": JOB_ICONS.get(j.job_type.lower(), "work"),
            "lat": j.latitude,
            "lon": j.longitude,
            "pay": float(j.pay),
            "location": j.location,
            "duration": "as agreed",
            "rating": 4.8,
            "total_reviews": 0,
            "is_verified": True,
            "distance": "—",
            "description": j.description,
        }
        for j in jobs
    ]
    return render(request, "worker_jobs.html", {"jobs": jobs, "jobs_json": json.dumps(payload)})


@login_required(login_url="employer-signin")
def employer_jobs(request):
    jobs = Job.objects.filter(employer=request.user).order_by("-created_at")
    counts = {j.pk: Application.objects.filter(job=j).count() for j in jobs}
    return render(request, "employer_jobs.html", {"jobs": jobs, "applicant_counts": counts})


@login_required(login_url="worker-signin")
def worker_job_details(request):
    job = Job.objects.filter(pk=request.GET.get("id")).select_related("employer").first()
    return render(request, "worker_job_details.html", {"job": job})


@login_required(login_url="employer-signin")
def employer_job_details(request):
    job = Job.objects.filter(pk=request.GET.get("id"), employer=request.user).first()
    applicants = Application.objects.filter(job=job).select_related("worker") if job else []
    return render(request, "employer_job_details.html", {"job": job, "applicants": applicants})
