from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.wallet.settlement import _money, ZERO


class Course(models.Model):
    title = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, blank=True, default="")
    description = models.TextField()
    thumbnail = models.CharField(max_length=500, blank=True, default="")
    category = models.CharField(max_length=64, blank=True, default="")
    level = models.CharField(
        max_length=50,
        choices=[
            ("beginner", "Beginner"),
            ("intermediate", "Intermediate"),
            ("advanced", "Advanced"),
        ],
        default="beginner",
    )
    duration_hours = models.FloatField(help_text="Estimated duration in hours", default=8)
    fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=8, default="KES")
    recovery_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=20)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

    @property
    def duration_label(self) -> str:
        hours = self.duration_hours or 8
        weeks = max(1, int(round(hours / 8)))
        return "1 Week" if weeks == 1 else f"{weeks} Weeks"

    @property
    def lessons(self) -> int:
        return max(8, int(self.duration_hours or 8))

    @property
    def image_url(self) -> str:
        return self.thumbnail

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify

            self.slug = slugify(self.title)[:50]
        super().save(*args, **kwargs)


class Enrollment(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        REPAYING = "repaying", "Repaying"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    OPEN_STATUSES = (Status.ACTIVE, Status.REPAYING)

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="enrollments")
    worker = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="course_enrollments")
    total_fee = models.DecimalField(max_digits=12, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount_remaining = models.DecimalField(max_digits=12, decimal_places=2)
    commission_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=20)
    commission_recovered = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    enrolled_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("course", "worker")

    def apply_recovery(self, amount) -> None:
        take = min(_money(amount), _money(self.amount_remaining))
        self.commission_recovered = _money(self.commission_recovered) + take
        self.amount_remaining = _money(self.amount_remaining) - take
        if self.amount_remaining <= ZERO:
            self.amount_remaining = ZERO
            self.status = self.Status.COMPLETED
            self.completed_at = timezone.now()
        else:
            self.status = self.Status.REPAYING
        self.save()

    def reverse_recovery(self, amount) -> None:
        give = min(_money(amount), _money(self.commission_recovered))
        self.commission_recovered = _money(self.commission_recovered) - give
        self.amount_remaining = _money(self.amount_remaining) + give
        if self.amount_remaining > ZERO:
            self.status = self.Status.REPAYING
            self.completed_at = None
        self.save()
