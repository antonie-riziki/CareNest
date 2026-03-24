from django.db import models
from apps.accounts.models import User
from apps.jobs.models import Job


# Create your models here.
class Contract(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE)
    worker = models.ForeignKey(User, on_delete=models.CASCADE)
    employer = models.ForeignKey(User, on_delete=models.CASCADE)
    scope = models.TextField()
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    status = models.CharField(max_length=50)
