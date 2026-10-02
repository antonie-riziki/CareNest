from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.contracts.models import Contract
from apps.jobs.models import Application, Job
from apps.profiles.models import EmployerProfile, WorkerProfile

User = get_user_model()


class UssdDatabaseMenuTests(TestCase):
    def setUp(self):
        self.employer = User.objects.create_user(
            username="sarah@test.com", password="x", role="employer", first_name="Sarah"
        )
        self.worker = User.objects.create_user(
            username="mary@test.com", password="x", role="worker", first_name="Mary", last_name="Wanjiku"
        )
        EmployerProfile.objects.create(
            user=self.employer,
            phone="+254712000001",
            last_latitude=-1.2634,
            last_longitude=36.8036,
        )
        WorkerProfile.objects.create(
            user=self.worker,
            phone="+254722000001",
            last_latitude=-1.2648,
            last_longitude=36.8049,
            approx_latitude=-1.2648,
            approx_longitude=36.8049,
        )
        self.job = Job.objects.create(
            employer=self.employer,
            title="Full-time nanny in Westlands",
            description="Childcare",
            location="Westlands",
            latitude=-1.2634,
            longitude=36.8036,
            pay=Decimal("45000"),
            job_type="nanny",
            status=Job.Status.ACTIVE,
            published_at=timezone.now(),
        )
        self.contract = Contract.objects.create(
            job=self.job,
            worker=self.worker,
            employer=self.employer,
            scope="Childcare",
            status="draft",
            amount=Decimal("45000"),
            currency="KES",
            employer_terms="Weekdays",
        )

    def _ussd(self, text, phone="+254722000001"):
        return self.client.post("/ussd/callback/", {"text": text, "phoneNumber": phone, "sessionId": "sess-1"})

    def test_welcome_uses_con_format(self):
        response = self._ussd("")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.decode().startswith("CON "))
        self.assertIn("Employer", response.content.decode())

    def test_worker_nearby_jobs_come_from_database(self):
        response = self._ussd("2*1")
        body = response.content.decode()
        self.assertTrue(body.startswith("CON "))
        self.assertIn("Full-time nanny in Westlands", body)
        self.assertIn("45000", body)

    def test_worker_can_apply_from_ussd(self):
        response = self._ussd("2*1*1*1")
        self.assertTrue(response.content.decode().startswith("END "))
        self.assertTrue(Application.objects.filter(worker=self.worker, job=self.job).exists())

    def test_employer_post_creates_job(self):
        before = Job.objects.count()
        response = self._ussd("1*1*2*Kilimani*2*1", phone="+254712000001")
        self.assertTrue(response.content.decode().startswith("END "))
        self.assertEqual(Job.objects.count(), before + 1)
        job = Job.objects.exclude(pk=self.job.pk).get()
        self.assertEqual(job.job_type, "housekeeper")
        self.assertEqual(job.location, "Kilimani")
        self.assertEqual(job.status, Job.Status.ACTIVE)

    def test_employer_sees_real_applicants(self):
        Application.objects.create(worker=self.worker, job=self.job)
        response = self._ussd("1*2", phone="+254712000001")
        body = response.content.decode()
        self.assertTrue(body.startswith("END "))
        self.assertIn("Mary", body)

    def test_contract_lookup_uses_database(self):
        response = self._ussd(f"3*{self.contract.pk}")
        body = response.content.decode()
        self.assertTrue(body.startswith("END "))
        self.assertIn(str(self.contract.pk), body)
        self.assertIn("Full-time nanny in Westlands", body)
