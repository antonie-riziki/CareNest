"""Job scheduling validation and lifecycle transitions."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

JOB_STATUSES = (
    "DRAFT",
    "SCHEDULED",
    "ACTIVE",
    "PAUSED",
    "EXPIRED",
    "CANCELLED",
    "COMPLETED",
    "LOCKED",
)

RECURRENCE = ("", "daily", "weekly", "weekdays", "custom")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_TZ = "Africa/Nairobi"


class ScheduleError(ValueError):
    pass


def resolve_timezone(name: str):
    name = (name or DEFAULT_TZ).strip() or DEFAULT_TZ
    try:
        return ZoneInfo(name), name
    except ZoneInfoNotFoundError as exc:
        raise ScheduleError(f"Unknown timezone: {name}") from exc


def parse_time(value) -> time | None:
    if value in (None, ""):
        return None
    if isinstance(value, time):
        return value
    try:
        parts = str(value).split(":")
        return time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except (TypeError, ValueError) as exc:
        raise ScheduleError("Invalid time") from exc


def combine_local(date_value, time_value, tz):
    if not date_value:
        return None
    clock = parse_time(time_value) or time(8, 0)
    naive = datetime.combine(date_value, clock)
    return timezone.make_aware(naive, tz)


def validate_schedule(payload: dict) -> dict:
    tz, tz_name = resolve_timezone(payload.get("timezone") or DEFAULT_TZ)
    start_date = payload.get("start_date") or None
    end_date = payload.get("end_date") or None
    start_time = parse_time(payload.get("start_time"))
    end_time = parse_time(payload.get("end_time"))
    is_recurring = bool(payload.get("is_recurring"))
    frequency = (payload.get("recurrence_frequency") or "").lower()
    days = payload.get("recurrence_days") or []
    deadline = payload.get("application_deadline") or None
    status = (payload.get("status") or "DRAFT").upper()
    scheduled_publish_at = payload.get("scheduled_publish_at")

    if status not in JOB_STATUSES:
        raise ScheduleError("Invalid job status")
    if is_recurring and frequency not in RECURRENCE:
        raise ScheduleError("Invalid recurrence frequency")
    if is_recurring and frequency in ("weekly", "custom"):
        cleaned = [str(d).lower()[:3] for d in days]
        if not cleaned or any(d not in WEEKDAYS for d in cleaned):
            raise ScheduleError("Recurring jobs need valid weekdays (e.g. mon, wed, fri)")
        days = cleaned
    elif not is_recurring:
        frequency = ""
        days = []

    start_dt = combine_local(start_date, start_time, tz) if start_date else None
    end_dt = combine_local(end_date, end_time or start_time, tz) if end_date else None
    now = timezone.now()

    if start_dt and start_dt.date() < now.astimezone(tz).date() and status in ("ACTIVE", "SCHEDULED"):
        raise ScheduleError("Start date cannot be in the past")
    if start_dt and end_dt and end_dt < start_dt:
        raise ScheduleError("End date/time cannot be before the start")
    if deadline and start_dt and deadline > start_dt:
        raise ScheduleError("Application deadline cannot be after the job start")
    if is_recurring and not start_date:
        raise ScheduleError("Recurring jobs need a start date")
    if status == "SCHEDULED" and not (scheduled_publish_at or start_dt):
        raise ScheduleError("Scheduled jobs need a publish or start time")

    if status == "DRAFT":
        resolved = "DRAFT"
    elif status in ("PAUSED", "EXPIRED", "CANCELLED", "COMPLETED", "LOCKED"):
        resolved = status
    elif scheduled_publish_at and scheduled_publish_at > now:
        resolved = "SCHEDULED"
    elif start_dt and start_dt > now and not payload.get("publish_now"):
        resolved = "SCHEDULED"
    else:
        resolved = "ACTIVE"

    return {
        "timezone": tz_name,
        "start_date": start_date,
        "start_time": start_time,
        "end_date": end_date,
        "end_time": end_time,
        "is_recurring": is_recurring,
        "recurrence_frequency": frequency,
        "recurrence_days": days,
        "application_deadline": deadline,
        "scheduled_publish_at": scheduled_publish_at or start_dt,
        "status": resolved,
        "published_at": now if resolved == "ACTIVE" else None,
    }


def refresh_job_status(job, *, now=None):
    """Activate scheduled jobs and expire ended ones. Persistence, not a label."""
    now = now or timezone.now()
    if job.status in ("CANCELLED", "COMPLETED", "PAUSED", "DRAFT", "LOCKED") or getattr(job, "locked", False):
        return job
    publish_at = job.scheduled_publish_at
    if job.status == "SCHEDULED" and publish_at and publish_at <= now:
        job.status = "ACTIVE"
        job.published_at = job.published_at or now
        job.save(update_fields=["status", "published_at", "updated_at"])
        return job
    if job.end_date:
        tz, _ = resolve_timezone(job.timezone)
        end_dt = combine_local(job.end_date, job.end_time, tz)
        if end_dt and end_dt < now and job.status == "ACTIVE":
            job.status = "EXPIRED"
            job.save(update_fields=["status", "updated_at"])
    if job.application_deadline and job.application_deadline < now and job.status == "ACTIVE":
        # Deadline closing applications does not auto-expire an already-live job.
        pass
    return job


def refresh_queryset(qs):
    jobs = list(qs)
    for job in jobs:
        refresh_job_status(job)
    return jobs


def schedule_summary(job) -> str:
    if job.is_recurring and job.recurrence_frequency == "weekly" and job.recurrence_days:
        days = ", ".join(str(d).title() for d in job.recurrence_days)
        clock = job.start_time.strftime("%H:%M") if job.start_time else ""
        return f"Every {days} {clock}".strip()
    if job.is_recurring and job.recurrence_frequency == "daily":
        clock = job.start_time.strftime("%H:%M") if job.start_time else ""
        return f"Daily {clock}".strip()
    if job.start_date and job.start_time:
        return f"{job.start_date.strftime('%d %b %Y')} at {job.start_time.strftime('%H:%M')}"
    if job.start_date:
        return job.start_date.strftime("%d %b %Y")
    return "As agreed"
