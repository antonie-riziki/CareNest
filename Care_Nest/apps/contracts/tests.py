from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.contracts.agreement import AgreementError, bind_agreement, check_in, check_out, submit_worker_terms
from apps.contracts.models import Contract, ServiceInvoice
from apps.courses.models import Course, Enrollment
from apps.jobs.models import Job

User = get_user_model()


class BilateralAgreementTests(TestCase):
    def setUp(self):
        self.employer = User.objects.create_user(username="emp@test.com", password="x", role="employer")
        self.worker = User.objects.create_user(username="wrk@test.com", password="x", role="worker")
        self.job = Job.objects.create(
            employer=self.employer,
            title="Weekday nanny",
            description="School run and meals",
            location="Westlands",
            latitude=-1.26,
            longitude=36.8,
            pay=Decimal("45000"),
            job_type="nanny",
            status=Job.Status.ACTIVE,
            employer_terms="Weekdays 08:00-17:00. Two children. No overnight.",
        )
        Course.objects.create(
            title="Professional Childcare",
            description="x",
            fee=Decimal("8000"),
            slug="childcare-fundamentals",
            thumbnail="https://thebesanamail.com/wp-content/uploads/2025/03/Kenya.Lucy-plays-with-child.Kate-Holt.2016.cropped.jpg",
        )

    def test_worker_terms_then_employer_bind_locks_job(self):
        contract = submit_worker_terms(
            job=self.job,
            worker=self.worker,
            worker_terms="I can work 08:00-16:00 weekdays. Sundays off.",
            hours_note="08:00-16:00",
        )
        self.assertEqual(contract.approval_status, Contract.ApprovalStatus.PENDING_REVIEW)
        self.assertFalse(self.job.locked)
        bind_agreement(contract, employer=self.employer, action="approve")
        self.job.refresh_from_db()
        contract.refresh_from_db()
        self.assertTrue(self.job.locked)
        self.assertEqual(self.job.status, Job.Status.LOCKED)
        self.assertEqual(contract.approval_status, Contract.ApprovalStatus.APPROVED)
        self.assertTrue(contract.terms_hash)
        self.client.force_login(self.worker)
        listing = self.client.get("/worker-jobs/")
        self.assertNotContains(listing, self.job.title)

    def test_employer_review_hides_upskilling(self):
        Enrollment.objects.create(
            course=Course.objects.get(slug="childcare-fundamentals"),
            worker=self.worker,
            total_fee=Decimal("8000"),
            amount_remaining=Decimal("8000"),
            commission_percentage=Decimal("20"),
        )
        contract = submit_worker_terms(
            job=self.job,
            worker=self.worker,
            worker_terms="I accept the posted hours with a 16:00 finish.",
            hours_note="finish 16:00",
        )
        bind_agreement(contract, employer=self.employer, action="approve")
        self.client.force_login(self.employer)
        response = self.client.get(f"/employer-engagements/{contract.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Upskilling")
        self.assertNotContains(response, "upskilling")
        self.assertContains(response, "Worker receives")
        invoice = ServiceInvoice.objects.get(engagement=contract, worker=self.worker)
        self.client.force_login(self.worker)
        invoice_page = self.client.get(f"/worker-invoices/{invoice.pk}/")
        self.assertContains(invoice_page, "CareNest")
        self.assertContains(invoice_page, str(invoice.recovery_amount))

    def test_check_in_and_out_after_bind(self):
        contract = submit_worker_terms(
            job=self.job,
            worker=self.worker,
            worker_terms="Agreed to weekday hours as posted.",
        )
        with self.assertRaises(AgreementError):
            check_in(contract=contract, worker=self.worker)
        bind_agreement(contract, employer=self.employer, action="approve")
        shift = check_in(contract=contract, worker=self.worker)
        self.assertTrue(shift.is_open)
        closed = check_out(contract=contract, worker=self.worker)
        self.assertFalse(closed.is_open)

    def test_course_cards_keep_images_and_show_fee(self):
        Course.objects.create(
            title="Advanced Housekeeping",
            description="x",
            fee=Decimal("5000"),
            slug="housekeeping-professional",
            thumbnail="https://lodgingmagazine.com/wp-content/uploads/2025/01/BoH-Winter_Housekeeping1.jpg",
        )
        self.client.force_login(self.worker)
        response = self.client.get("/worker-courses/")
        self.assertContains(response, "lodgingmagazine.com")
        self.assertContains(response, "thebesanamail.com")
        self.assertContains(response, "Fee:")
