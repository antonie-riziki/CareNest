from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.jobs.models import Job
from apps.jobs.scheduling import refresh_job_status
from apps.profiles.models import EmployerProfile, WorkerProfile

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


class LiveLocationTests(TestCase):
    def setUp(self):
        self.employer = User.objects.create_user(username="emp-loc@test.com", password="x", role="employer")
        self.worker = User.objects.create_user(username="wrk-loc@test.com", password="x", role="worker")
        EmployerProfile.objects.create(user=self.employer)
        WorkerProfile.objects.create(user=self.worker)

    def test_worker_can_pin_live_location(self):
        self.client.force_login(self.worker)
        response = self.client.post(
            "/api/location/worker/",
            data={"latitude": -1.2921, "longitude": 36.8219},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        profile = WorkerProfile.objects.get(user=self.worker)
        self.assertAlmostEqual(profile.last_latitude, -1.2921, places=4)
        self.assertIsNotNone(profile.approx_latitude)

    def test_employer_can_pin_live_location(self):
        self.client.force_login(self.employer)
        response = self.client.post(
            "/api/location/employer/",
            data={"latitude": -1.2634, "longitude": 36.8036, "label": "Westlands"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        profile = EmployerProfile.objects.get(user=self.employer)
        self.assertAlmostEqual(profile.last_latitude, -1.2634, places=4)
        self.assertEqual(profile.location_label, "Westlands")

    def test_job_form_includes_live_map(self):
        self.client.force_login(self.employer)
        response = self.client.get("/employer-jobs/new/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "employer-map")
        self.assertContains(response, "Use my live location")
        self.assertContains(response, "form-field")

    def test_distant_jobs_still_listed_for_workers(self):
        near = Job.objects.create(
            employer=self.employer,
            title="Nearby nanny",
            description="d",
            location="Westlands",
            latitude=-1.26,
            longitude=36.80,
            pay=10000,
            job_type="nanny",
            status=Job.Status.ACTIVE,
            published_at=timezone.now(),
        )
        far = Job.objects.create(
            employer=self.employer,
            title="Mombasa caregiver",
            description="d",
            location="Mombasa",
            latitude=-4.04,
            longitude=39.66,
            pay=18000,
            job_type="caregiver",
            status=Job.Status.ACTIVE,
            published_at=timezone.now(),
        )
        WorkerProfile.objects.filter(user=self.worker).update(last_latitude=-1.26, last_longitude=36.80)
        self.client.force_login(self.worker)
        response = self.client.get("/worker-jobs/")
        self.assertContains(response, near.title)
        self.assertContains(response, far.title)
        self.assertContains(response, "All live jobs are listed")

    def test_employer_jobs_use_compact_rows(self):
        Job.objects.create(
            employer=self.employer,
            title="Compact row job",
            description="d",
            location="Nairobi",
            latitude=-1.26,
            longitude=36.80,
            pay=12000,
            job_type="nanny",
            status=Job.Status.ACTIVE,
            published_at=timezone.now(),
        )
        self.client.force_login(self.employer)
        response = self.client.get("/employer-jobs/")
        self.assertContains(response, "Compact row job")
        self.assertContains(response, "applicant")
        self.assertNotContains(response, "grid-cols-1 md:grid-cols-2")
