from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.jobs.models import Job
from apps.profiles.models import EmployerProfile, WorkerProfile
from apps.wallet.models import Wallet

User = get_user_model()


class DashboardDataTests(TestCase):
    def setUp(self):
        self.employer = User.objects.create_user(username="dash-e@test.com", password="x", role="employer", first_name="Sarah")
        self.worker = User.objects.create_user(username="dash-w@test.com", password="x", role="worker", first_name="Mary")
        EmployerProfile.objects.create(user=self.employer)
        WorkerProfile.objects.create(user=self.worker)
        Wallet.objects.create(user=self.employer, balance=0)
        Wallet.objects.create(user=self.worker, balance=0)
        Job.objects.create(
            employer=self.employer,
            title="Live dashboard job",
            description="d",
            location="Nairobi",
            latitude=-1.26,
            longitude=36.80,
            pay=Decimal("20000"),
            job_type="nanny",
            status=Job.Status.ACTIVE,
            published_at=timezone.now(),
        )

    def test_employer_dashboard_shows_live_counts(self):
        self.client.force_login(self.employer)
        response = self.client.get("/employer-dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Live dashboard job")
        self.assertContains(response, "1 live")

    def test_worker_dashboard_shows_live_job_count(self):
        self.client.force_login(self.worker)
        response = self.client.get("/worker-dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1 live jobs")
        self.assertContains(response, "Confirmed earnings")
