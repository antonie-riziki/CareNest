from django.db import models
from apps.accounts.models import User


# Create your models here.
class WorkerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    skills = models.TextField()
    rating = models.FloatField(default=0)
    verified = models.BooleanField(default=False)


class EmployerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    rating = models.FloatField(default=0)
