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
        self.job.refresh_from_db()
        self.assertTrue(self.job.locked)
        bind_agreement(contract, employer=self.employer, action="approve")
        self.job.refresh_from_db()
        contract.refresh_from_db()
        self.assertTrue(self.job.locked)
        self.assertEqual(self.job.status, Job.Status.LOCKED)
        self.assertEqual(contract.approval_status, Contract.ApprovalStatus.APPROVED)
        self.assertEqual(contract.status, "bound")
        self.assertEqual(contract.display_status, "BOUND")
        self.assertTrue(contract.terms_hash)
        self.client.force_login(self.worker)
        listing = self.client.get("/worker-jobs/")
        self.assertContains(listing, self.job.title)

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
        closed = check_out(contract=contract, worker=self.worker, work_summary="School run completed and lunch prepared.")
        self.assertFalse(closed.is_open)
        self.assertEqual(closed.work_summary, "School run completed and lunch prepared.")
        from apps.contracts.agreement import verify_shift
        from apps.contracts.models import Notification

        verified = verify_shift(shift=closed, employer=self.employer, note="Confirmed.", rating=5)
        self.assertTrue(verified.employer_verified)
        self.assertEqual(verified.employer_rating, 5)
        self.assertTrue(Notification.objects.filter(recipient=self.employer, kind="check_in").exists())
        self.assertTrue(Notification.objects.filter(recipient=self.employer, kind="check_out").exists())

    def test_workers_needed_locks_when_slots_fill_but_stays_listed(self):
        self.job.workers_needed = 2
        self.job.save(update_fields=["workers_needed"])
        second = User.objects.create_user(username="wrk2@test.com", password="x", role="worker")
        first = submit_worker_terms(job=self.job, worker=self.worker, worker_terms="I can cover weekdays as posted hours.")
        self.job.refresh_from_db()
        self.assertFalse(self.job.is_filled)
        submit_worker_terms(job=self.job, worker=second, worker_terms="I can cover the second slot this week.")
        self.job.refresh_from_db()
        self.assertTrue(self.job.is_filled)
        self.assertEqual(self.job.status, Job.Status.LOCKED)
        bind_agreement(first, employer=self.employer, action="approve")
        first.refresh_from_db()
        self.assertEqual(first.display_status, "BOUND")
        self.client.force_login(self.worker)
        listing = self.client.get("/worker-jobs/")
        self.assertContains(listing, self.job.title)

    def test_dispute_portal_opens_for_both_parties(self):
        contract = submit_worker_terms(job=self.job, worker=self.worker, worker_terms="Agreed to weekday hours as posted.")
        bind_agreement(contract, employer=self.employer, action="approve")
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_login(self.worker)
        response = self.client.post(
            "/disputes/report/",
            {
                "engagement_id": contract.pk,
                "category": "payment",
                "title": "Late payment",
                "description": "Escrow was not funded after check-in.",
                "evidence": SimpleUploadedFile("late.pdf", b"%PDF-1.4 proof", content_type="application/pdf"),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/disputes/", response["Location"])
        detail = self.client.get(response["Location"])
        self.assertContains(detail, "Late payment")
        self.assertContains(detail, f"#{contract.pk}")
        search = self.client.get("/disputes/", {"q": "nanny", "engagement": contract.pk})
        self.assertContains(search, self.job.title)
        self.assertContains(search, f'value="{contract.pk}"')
        self.client.force_login(self.employer)
        employer_portal = self.client.get("/disputes/")
        self.assertContains(employer_portal, "Late payment")

    def test_dispute_requires_contract_and_stores_proof(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.contracts.models import DisputeEvidence

        contract = submit_worker_terms(job=self.job, worker=self.worker, worker_terms="Agreed to weekday hours as posted.")
        bind_agreement(contract, employer=self.employer, action="approve")
        self.client.force_login(self.worker)
        proof = SimpleUploadedFile("photo.jpg", b"\xff\xd8\xff\xdbfakejpeg", content_type="image/jpeg")
        response = self.client.post(
            "/disputes/report/",
            {
                "engagement_id": contract.pk,
                "category": "attendance",
                "title": "Missed hours",
                "description": "The recorded check-in does not match the agreed weekday hours.",
                "evidence": proof,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(DisputeEvidence.objects.filter(dispute__engagement=contract).exists())
        linked = self.client.get(f"/disputes/?engagement={contract.pk}")
        self.assertContains(linked, f"#{contract.pk}")

    def test_completion_report_pdf_and_share_link_after_clearance(self):
        from apps.agentic_core.state import EngagementStatus
        from apps.contracts.reports import ensure_completion_report
        from apps.wallet.settlement import capture_settled_commission

        contract = submit_worker_terms(job=self.job, worker=self.worker, worker_terms="Agreed to weekday hours as posted.")
        bind_agreement(contract, employer=self.employer, action="approve")
        check_in(contract=contract, worker=self.worker)
        closed = check_out(contract=contract, worker=self.worker, work_summary="School run completed and lunch prepared.")
        from apps.contracts.agreement import verify_shift

        verify_shift(shift=closed, employer=self.employer, note="On time.", rating=4)
        capture_settled_commission(contract, reference="test-clear")
        contract.chain_status = EngagementStatus.RELEASED
        contract.save(update_fields=["chain_status", "updated_at"])
        contract.refresh_from_db()
        report = ensure_completion_report(contract)
        self.assertIsNotNone(report)
        self.assertEqual(report.snapshot["rating"]["average"], 4.0)
        page = self.client.get(f"/reports/{report.share_token}/")
        self.assertContains(page, self.job.title)
        self.assertContains(page, "School run completed")
        pdf = self.client.get(f"/reports/{report.share_token}/pdf/")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))

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
