import json
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.jobs.images import ImageUploadError, attach_primary_image, store_upload
from apps.jobs.maps import public_config as map_config
from apps.jobs.models import Application, Job, JobImage
from apps.jobs.scheduling import ScheduleError, refresh_job_status, refresh_queryset, validate_schedule
from apps.wallet.pricing import get_price_service


JOB_ICONS = {
    "nanny": "child_care",
    "housekeeper": "cleaning_services",
    "caregiver": "health_and_safety",
    "chef": "skillet",
    "gardener": "garden_cart",
    "driver": "directions_car",
    "cleaner": "cleaning_services",
    "security": "security",
    "companion": "favorite",
}


def _job_payload(job: Job) -> dict:
    lat, lon = job.public_point()
    return {
        "id": job.pk,
        "title": job.title,
        "category": job.job_type,
        "job_type": job.job_type,
        "icon": job.category_icon,
        "lat": lat,
        "lon": lon,
        "pay": float(job.pay),
        "currency": job.currency,
        "location": job.location,
        "duration": job.schedule_label,
        "schedule": job.schedule_label,
        "status": job.status,
        "image": job.cover_image,
        "has_custom_image": job.has_custom_image,
        "is_verified": job.is_verified,
        "locked": job.locked,
        "description": job.description,
        "directions_url": f"https://www.openstreetmap.org/directions?from=&to={lat}%2C{lon}",
    }


def _parse_job_form(request) -> dict:
    data = request.POST
    start_date = data.get("start_date") or None
    end_date = data.get("end_date") or None
    deadline = data.get("application_deadline") or None
    publish_at = data.get("scheduled_publish_at") or None

    def _date(value):
        if not value:
            return None
        return datetime.strptime(value, "%Y-%m-%d").date()

    def _dt(value):
        if not value:
            return None
        raw = value.replace("T", " ")
        if len(raw) == 16:
            raw += ":00"
        parsed = datetime.fromisoformat(raw)
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed)
        return parsed

    days = data.getlist("recurrence_days") if hasattr(data, "getlist") else data.get("recurrence_days") or []
    if isinstance(days, str):
        days = [d.strip() for d in days.split(",") if d.strip()]
    return validate_schedule(
        {
            "start_date": _date(start_date),
            "end_date": _date(end_date),
            "start_time": data.get("start_time") or None,
            "end_time": data.get("end_time") or None,
            "is_recurring": data.get("is_recurring") in {"on", "true", "1", "yes"},
            "recurrence_frequency": data.get("recurrence_frequency") or "",
            "recurrence_days": days,
            "timezone": data.get("timezone") or "Africa/Nairobi",
            "application_deadline": _dt(deadline),
            "scheduled_publish_at": _dt(publish_at),
            "status": data.get("status") or "DRAFT",
            "publish_now": data.get("publish_now") in {"on", "true", "1", "yes"},
        }
    )


@login_required(login_url="worker-signin")
def worker_jobs(request):
    jobs = Job.objects.select_related("employer").order_by("-created_at")
    jobs = [j for j in refresh_queryset(jobs) if j.is_live]
    payload = [_job_payload(j) for j in jobs]
    return render(
        request,
        "worker_jobs.html",
        {
            "jobs": jobs,
            "jobs_json": json.dumps(payload),
            "map_config": map_config(),
            "map_config_json": json.dumps(map_config()),
        },
    )


@login_required(login_url="employer-signin")
def employer_jobs(request):
    if request.method == "POST":
        return save_employer_job(request)
    jobs = Job.objects.filter(employer=request.user).order_by("-created_at")
    jobs = refresh_queryset(jobs)
    counts = {j.pk: Application.objects.filter(job=j).count() for j in jobs}
    return render(request, "employer_jobs.html", {"jobs": jobs, "applicant_counts": counts})


@login_required(login_url="employer-signin")
def employer_job_form(request, pk=None):
    job = None
    if pk:
        job = get_object_or_404(Job, pk=pk, employer=request.user)
    return render(request, "employer_job_form.html", {"job": job, "gallery": job.images.all() if job else []})


@login_required(login_url="employer-signin")
def save_employer_job(request, pk=None):
    if request.method != "POST":
        return redirect("employer-jobs")
    try:
        schedule = _parse_job_form(request)
    except (ScheduleError, ValueError) as exc:
        messages.error(request, str(exc))
        if pk:
            return redirect("employer-job-edit", pk=pk)
        return redirect("employer-job-new")

    lat = request.POST.get("latitude") or "-1.2634"
    lon = request.POST.get("longitude") or "36.8036"
    fields = {
        "title": request.POST.get("title") or "Untitled role",
        "description": request.POST.get("description") or "",
        "location": request.POST.get("location") or "Nairobi",
        "latitude": float(lat),
        "longitude": float(lon),
        "pay": request.POST.get("pay") or 0,
        "currency": request.POST.get("currency") or "KES",
        "job_type": request.POST.get("job_type") or "housekeeper",
        "is_verified": request.POST.get("is_verified") in {"on", "true", "1"},
        "employer_terms": (request.POST.get("employer_terms") or "").strip(),
        **schedule,
    }
    if pk:
        job = get_object_or_404(Job, pk=pk, employer=request.user)
        for key, value in fields.items():
            setattr(job, key, value)
        job.save()
    else:
        job = Job.objects.create(employer=request.user, **fields)
    if not job.employer_terms:
        from apps.contracts.agreement import ensure_employer_terms

        ensure_employer_terms(job)

    upload = request.FILES.get("image")
    if upload:
        try:
            attach_primary_image(job, upload)
        except ImageUploadError as exc:
            messages.error(request, str(exc))

    extras = request.FILES.getlist("gallery") if hasattr(request.FILES, "getlist") else []
    for extra in extras:
        try:
            url = store_upload(extra)
            gallery = list(job.gallery_urls or [])
            gallery.append(url)
            job.gallery_urls = gallery
            JobImage.objects.create(job=job, image_url=url, is_primary=False, sort_order=len(gallery))
        except ImageUploadError as exc:
            messages.error(request, str(exc))
    if extras:
        job.save(update_fields=["gallery_urls", "updated_at"])

    if request.POST.get("remove_image"):
        job.image_url = ""
        job.save(update_fields=["image_url", "updated_at"])
        JobImage.objects.filter(job=job, is_primary=True).delete()

    messages.success(request, f"Job saved as {job.status}.")
    return redirect(f"/employer-job-details/?id={job.pk}")


@login_required(login_url="worker-signin")
def worker_job_details(request):
    job = Job.objects.filter(pk=request.GET.get("id")).select_related("employer").first()
    if job:
        refresh_job_status(job)
    applied = bool(job and Application.objects.filter(job=job, worker=request.user).exists())
    quote = get_price_service().convert(job.pay, job.currency, "XLM") if job else None
    from apps.contracts.models import Contract

    contract = None
    if job:
        contract = (
            Contract.objects.filter(job=job, worker=request.user)
            .exclude(approval_status=Contract.ApprovalStatus.REJECTED)
            .first()
        )
    return render(
        request,
        "worker_job_details.html",
        {"job": job, "applied": applied, "fx": quote, "contract": contract},
    )


@login_required(login_url="employer-signin")
def employer_job_details(request):
    job = Job.objects.filter(pk=request.GET.get("id"), employer=request.user).first()
    if job:
        refresh_job_status(job)
    applicants = Application.objects.filter(job=job).select_related("worker") if job else []
    return render(request, "employer_job_details.html", {"job": job, "applicants": applicants})


@login_required(login_url="worker-signin")
@require_POST
def apply_to_job(request, pk):
    job = get_object_or_404(Job, pk=pk, status=Job.Status.ACTIVE)
    if job.locked:
        messages.error(request, "This job is locked. A contract is already bound.")
        return redirect(f"/worker-job-details/?id={job.pk}")
    Application.objects.get_or_create(worker=request.user, job=job, defaults={"status": Application.Status.SUBMITTED})
    messages.success(request, "Application sent. Add your terms on this page so the employer can review them.")
    return redirect(f"/worker-job-details/?id={job.pk}")


@login_required
@require_POST
def remove_job_image(request, pk):
    job = get_object_or_404(Job, pk=pk, employer=request.user)
    image_id = request.POST.get("image_id")
    if image_id:
        JobImage.objects.filter(pk=image_id, job=job).delete()
        job.gallery_urls = [img.image_url for img in job.images.filter(is_primary=False)]
        job.save(update_fields=["gallery_urls", "updated_at"])
    else:
        job.image_url = ""
        job.save(update_fields=["image_url", "updated_at"])
        JobImage.objects.filter(job=job, is_primary=True).delete()
    return redirect(f"/employer-job-details/?id={job.pk}")


def prices_api(request):
    service = get_price_service()
    amount = request.GET.get("amount")
    source = request.GET.get("from", "XLM")
    target = request.GET.get("to", "KES")
    if amount is not None:
        return JsonResponse(service.convert(amount, source, target))
    return JsonResponse(service.dashboard())


def maps_config_api(request):
    return JsonResponse(map_config())
