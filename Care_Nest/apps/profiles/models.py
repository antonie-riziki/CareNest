from django.db import models
from apps.accounts.models import User


class WorkerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    skills = models.TextField(blank=True, default="")
    rating = models.FloatField(default=0)
    verified = models.BooleanField(default=False)
    phone = models.CharField(max_length=20, blank=True, default="", db_index=True)
    phone_masked = models.CharField(max_length=32, blank=True, default="")
    last_latitude = models.FloatField(null=True, blank=True)
    last_longitude = models.FloatField(null=True, blank=True)
    approx_latitude = models.FloatField(null=True, blank=True)
    approx_longitude = models.FloatField(null=True, blank=True)
    location_updated_at = models.DateTimeField(null=True, blank=True)
    preferred_payout = models.CharField(max_length=16, blank=True, default="")


class EmployerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    rating = models.FloatField(default=0)
    organisation = models.CharField(max_length=120, blank=True, default="")
    phone = models.CharField(max_length=20, blank=True, default="", db_index=True)
    last_latitude = models.FloatField(null=True, blank=True)
    last_longitude = models.FloatField(null=True, blank=True)
    location_label = models.CharField(max_length=255, blank=True, default="")
    location_updated_at = models.DateTimeField(null=True, blank=True)
