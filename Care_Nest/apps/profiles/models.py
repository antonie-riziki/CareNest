from django.db import models
from apps.accounts.models import User


class WorkerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    skills = models.TextField(blank=True, default="")
    rating = models.FloatField(default=0)
    verified = models.BooleanField(default=False)
    phone_masked = models.CharField(max_length=32, blank=True, default="")
    approx_latitude = models.FloatField(null=True, blank=True)
    approx_longitude = models.FloatField(null=True, blank=True)
    preferred_payout = models.CharField(max_length=16, blank=True, default="")


class EmployerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    rating = models.FloatField(default=0)
    organisation = models.CharField(max_length=120, blank=True, default="")
