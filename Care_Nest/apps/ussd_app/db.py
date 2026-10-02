"""Database helpers for the Africa's Talking USSD menus."""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Avg
from django.utils import timezone

from apps.comms_layer.utils import format_phone_number
from apps.contracts.models import Contract
from apps.jobs.maps import haversine_km
from apps.jobs.models import Application, Job
from apps.jobs.scheduling import refresh_queryset
from apps.profiles.models import EmployerProfile, WorkerProfile

JOB_TYPES = {"1": "nanny", "2": "housekeeper", "3": "caregiver"}
DURATIONS = {"1": ("1 day", 1), "2": ("1 week", 7), "3": ("1 month", 30)}


def digits(phone: str | None) -> str:
    formatted = format_phone_number(phone or "") if phone else None
    raw = formatted or (phone or "")
    return "".join(ch for ch in raw if ch.isdigit())[-9:]


def user_for_phone(phone: str | None):
    tail = digits(phone)
    if not tail:
        return None
    worker = WorkerProfile.objects.select_related("user").filter(phone__endswith=tail).first()
    if worker:
        return worker.user
    employer = EmployerProfile.objects.select_related("user").filter(phone__endswith=tail).first()
    return employer.user if employer else None


def live_jobs():
    jobs = Job.objects.select_related("employer").order_by("-created_at")
    return [job for job in refresh_queryset(jobs) if job.is_live]


def nearby_jobs(worker, limit: int = 5):
    jobs = live_jobs()
    profile = getattr(worker, "workerprofile", None) if worker else None
    if not (profile and profile.approx_latitude and profile.approx_longitude):
        return [(job, None) for job in jobs[:limit]]
    ranked = []
    for job in jobs:
        km = haversine_km(profile.approx_latitude, profile.approx_longitude, job.latitude, job.longitude)
        ranked.append((job, km))
    ranked.sort(key=lambda item: item[1])
    return ranked[:limit]


def typical_pay(job_type: str) -> Decimal:
    avg = Job.objects.filter(job_type=job_type, status=Job.Status.ACTIVE).aggregate(avg=Avg("pay"))["avg"]
    return Decimal(avg).quantize(Decimal("1")) if avg else Decimal("25000")


def create_ussd_job(employer, job_type: str, location: str, duration_label: str):
    profile = EmployerProfile.objects.filter(user=employer).first()
    lat = (profile.last_latitude if profile and profile.last_latitude else -1.2864)
    lon = (profile.last_longitude if profile and profile.last_longitude else 36.8172)
    pay = typical_pay(job_type)
    job = Job.objects.create(
        employer=employer,
        title=f"{job_type.title()} in {location}",
        description=f"Posted via USSD. Duration: {duration_label}.",
        location=location,
        latitude=lat,
        longitude=lon,
        pay=pay,
        currency="KES",
        job_type=job_type,
        status=Job.Status.ACTIVE,
        published_at=timezone.now(),
        employer_terms=f"Duration {duration_label}. Location {location}.",
    )
    return job


def employer_applicants(employer, limit: int = 5):
    return (
        Application.objects.filter(job__employer=employer)
        .select_related("worker", "job")
        .order_by("-created_at")[:limit]
    )


def worker_active_contracts(worker, limit: int = 5):
    return (
        Contract.objects.filter(worker=worker)
        .exclude(approval_status=Contract.ApprovalStatus.REJECTED)
        .select_related("job")
        .order_by("-created_at")[:limit]
    )


def employer_open_contracts(employer, limit: int = 5):
    return (
        Contract.objects.filter(employer=employer)
        .exclude(approval_status=Contract.ApprovalStatus.REJECTED)
        .select_related("job", "worker")
        .order_by("-created_at")[:limit]
    )


def wallet_for(user):
    if not user:
        return None
    from apps.wallet.models import Wallet

    return Wallet.objects.filter(user=user).first()
