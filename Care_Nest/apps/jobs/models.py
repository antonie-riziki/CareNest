from django.db import models
from apps.accounts.models import User


# Create your models here.
class Job(models.Model):
    employer = models.ForeignKey(User, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    description = models.TextField()
    location = models.CharField(max_length=255)
    latitude = models.FloatField()
    longitude = models.FloatField()
    pay = models.DecimalField(max_digits=10, decimal_places=2)
    job_type = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)


class Application(models.Model):
    worker = models.ForeignKey(User, on_delete=models.CASCADE)
    job = models.ForeignKey(Job, on_delete=models.CASCADE)
    status = models.CharField(max_length=50)
