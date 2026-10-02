from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.accounts.models import User
from apps.jobs.images import category_meta, placeholder_url
from apps.jobs.scheduling import schedule_summary


class Job(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SCHEDULED = "SCHEDULED", "Scheduled"
        ACTIVE = "ACTIVE", "Active"
        PAUSED = "PAUSED", "Paused"
        EXPIRED = "EXPIRED", "Expired"
        CANCELLED = "CANCELLED", "Cancelled"
        COMPLETED = "COMPLETED", "Completed"
        LOCKED = "LOCKED", "Locked"

    employer = models.ForeignKey(User, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    description = models.TextField()
    location = models.CharField(max_length=255)
    latitude = models.FloatField()
    longitude = models.FloatField()
    pay = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=8, default="KES")
    job_type = models.CharField(max_length=100)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    image_url = models.URLField(max_length=500, blank=True, default="")
    gallery_urls = models.JSONField(default=list, blank=True)
    start_date = models.DateField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    is_recurring = models.BooleanField(default=False)
    recurrence_frequency = models.CharField(max_length=16, blank=True, default="")
    recurrence_days = models.JSONField(default=list, blank=True)
    timezone = models.CharField(max_length=64, default="Africa/Nairobi")
    application_deadline = models.DateTimeField(null=True, blank=True)
    scheduled_publish_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    is_verified = models.BooleanField(default=False)
    employer_terms = models.TextField(blank=True, default="")
    locked = models.BooleanField(default=False, db_index=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_contract = models.ForeignKey(
        "contracts.Contract",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    workers_needed = models.PositiveSmallIntegerField(default=1)
    slots_filled = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "scheduled_publish_at"]),
            models.Index(fields=["employer", "status"]),
        ]

    def __str__(self):
        return self.title

    @property
    def category_key(self) -> str:
        from apps.jobs.images import normalize_category

        return normalize_category(self.job_type)

    @property
    def category_label(self) -> str:
        return category_meta(self.job_type)["label"]

    @property
    def category_icon(self) -> str:
        return category_meta(self.job_type)["icon"]

    @property
    def cover_image(self) -> str:
        if self.image_url:
            return self.image_url
        return placeholder_url(self.job_type)

    @property
    def has_custom_image(self) -> bool:
        return bool(self.image_url)

    @property
    def schedule_label(self) -> str:
        return schedule_summary(self)

    @property
    def slots_remaining(self) -> int:
        return max(0, int(self.workers_needed or 1) - int(self.slots_filled or 0))

    @property
    def is_live(self) -> bool:
        return self.status == self.Status.ACTIVE and not self.is_filled

    @property
    def is_listed(self) -> bool:
        return self.status in {self.Status.ACTIVE, self.Status.LOCKED}

    @property
    def is_filled(self) -> bool:
        return self.locked or self.status == self.Status.LOCKED or self.slots_remaining <= 0

    def public_point(self) -> tuple[float, float]:
        from apps.jobs.maps import approximate_coordinates

        return approximate_coordinates(self.latitude, self.longitude, salt=f"job-{self.pk}")


class JobImage(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="images")
    image_url = models.URLField(max_length=500)
    is_primary = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["sort_order", "id"]


class Application(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        REVIEWING = "reviewing", "Reviewing"
        SHORTLISTED = "shortlisted", "Shortlisted"
        REJECTED = "rejected", "Rejected"
        ENGAGED = "engaged", "Engaged"

    worker = models.ForeignKey(User, on_delete=models.CASCADE)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="applications")
    status = models.CharField(max_length=50, default=Status.SUBMITTED)
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ("worker", "job")
