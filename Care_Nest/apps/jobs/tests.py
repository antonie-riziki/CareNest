from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.jobs.models import Job
from apps.jobs.scheduling import refresh_job_status

User = get_user_model()


class JobVisibilityTests(TestCase):
    def setUp(self):
        self.employer = User.objects.create_user(username="emp@test.com", password="x", role="employer")
        self.worker = User.objects.create_user(username="wrk@test.com", password="x", role="worker")

    def _job(self, **kwargs):
        defaults = dict(
            employer=self.employer,
            title="Role",
            description="d",
            location="Nairobi",
            latitude=-1.26,
            longitude=36.8,
            pay=Decimal("10000"),
            job_type="nanny",
            status=Job.Status.DRAFT,
        )
        defaults.update(kwargs)
        return Job.objects.create(**defaults)

    def test_scheduled_job_is_not_active(self):
        job = self._job(status=Job.Status.SCHEDULED, scheduled_publish_at=timezone.now() + timedelta(days=2))
        refresh_job_status(job)
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.SCHEDULED)
        self.client.force_login(self.worker)
        response = self.client.get("/worker-jobs/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, job.title)

    def test_locked_job_is_not_listed(self):
        job = self._job(status=Job.Status.ACTIVE, published_at=timezone.now(), locked=True)
        job.status = Job.Status.LOCKED
        job.save()
        self.client.force_login(self.worker)
        response = self.client.get("/worker-jobs/")
        self.assertNotContains(response, job.title)

    def test_active_job_is_listed(self):
        job = self._job(status=Job.Status.ACTIVE, published_at=timezone.now())
        self.client.force_login(self.worker)
        response = self.client.get("/worker-jobs/")
        self.assertContains(response, job.title)
