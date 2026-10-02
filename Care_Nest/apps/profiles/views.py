from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from apps.accounts.models import User
from apps.jobs.models import Job
from apps.profiles.avatars import avatar_url
from apps.profiles.models import EmployerProfile


def worker_profile(request):
    return render(request, "worker_profile.html")


def employer_profile(request):
    return render(request, "employer_profile.html")


@login_required
def employer_public_profile(request, pk):
    employer = get_object_or_404(User, pk=pk, role="employer")
    profile, _ = EmployerProfile.objects.get_or_create(user=employer)
    jobs = Job.objects.filter(employer=employer).exclude(status=Job.Status.DRAFT).order_by("-created_at")[:8]
    return render(
        request,
        "employer_public_profile.html",
        {
            "host": employer,
            "profile": profile,
            "photo": avatar_url(employer, profile),
            "jobs": jobs,
        },
    )
