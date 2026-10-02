from decimal import Decimal
from datetime import date, time, timedelta
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.contracts.models import Contract
from apps.courses.models import Course, Enrollment
from apps.jobs.images import ImageUploadError, normalize_category, placeholder_url, validate_upload
from apps.jobs.maps import approximate_coordinates, public_config
from apps.jobs.models import Job
from apps.jobs.scheduling import ScheduleError, refresh_job_status, validate_schedule
from apps.profiles.models import EmployerProfile, WorkerProfile
from apps.wallet.models import PayoutMethod, Settlement, Wallet
from apps.wallet.payouts import PayoutError, normalize_kenyan_phone, save_mpesa_method
from apps.wallet.pricing import PriceQuote, PriceService, reset_price_service
from apps.wallet.providers import WalletError, get_provider, persist_connection
from apps.wallet.settlement import (
    capture_settled_commission,
    compute_waterfall,
    refund_settlement,
    remaining_training_for,
)

User = get_user_model()


class WaterfallTests(SimpleTestCase):
    def test_five_percent_and_no_training(self):
        b = compute_waterfall(Decimal("50000"), remaining_training_balance=0)
        self.assertEqual(b.platform_fee_amount, Decimal("2500.00"))
        self.assertEqual(b.worker_net_amount, Decimal("47500.00"))
        self.assertEqual(b.upskilling_recovery_amount, Decimal("0.00"))
        self.assertEqual(b.worker_payout_amount, Decimal("47500.00"))
        self.assertTrue(b.invariants_ok())

    def test_recovery_capped_at_remaining_balance(self):
        b = compute_waterfall(Decimal("50000"), remaining_training_balance=Decimal("4000"))
        self.assertEqual(b.worker_net_amount, Decimal("47500.00"))
        self.assertEqual(b.upskilling_recovery_amount, Decimal("4000.00"))  # not 9500
        self.assertEqual(b.worker_payout_amount, Decimal("43500.00"))

    def test_small_outstanding_vs_large_payout(self):
        b = compute_waterfall(Decimal("20000") / Decimal("0.95"), remaining_training_balance=Decimal("1000"))
        # Use exact 20k worker-side example from spec: gross 50000 already covered.
        b = compute_waterfall(Decimal("21052.64"), remaining_training_balance=Decimal("1000"))
        self.assertLessEqual(b.upskilling_recovery_amount, Decimal("1000.00"))

    def test_spec_example_worker_net_20000(self):
        # Outstanding 1000, payout 20000 => 20% would be 4000, recover only 1000
        from apps.wallet.settlement import _money

        # invert: worker net = gross * 0.95 = 20000 => gross = 20000/0.95
        gross = (Decimal("20000") / Decimal("0.95")).quantize(Decimal("0.01"))
        b = compute_waterfall(gross, remaining_training_balance=Decimal("1000"))
        self.assertEqual(b.upskilling_recovery_amount, Decimal("1000.00"))
        self.assertEqual(b.worker_payout_amount, b.worker_net_amount - Decimal("1000.00"))


class SchedulingTests(SimpleTestCase):
    def test_end_before_start_rejected(self):
        with self.assertRaises(ScheduleError):
            validate_schedule(
                {
                    "start_date": date(2026, 10, 15),
                    "end_date": date(2026, 10, 1),
                    "start_time": "08:00",
                    "status": "ACTIVE",
                    "timezone": "Africa/Nairobi",
                }
            )

    def test_weekly_needs_days(self):
        with self.assertRaises(ScheduleError):
            validate_schedule({"is_recurring": True, "recurrence_frequency": "weekly", "start_date": date(2026, 10, 15), "status": "SCHEDULED"})

    def test_future_start_is_scheduled(self):
        future = timezone.now() + timedelta(days=5)
        result = validate_schedule(
            {
                "start_date": future.date(),
                "start_time": "08:00",
                "status": "SCHEDULED",
                "timezone": "Africa/Nairobi",
            }
        )
        self.assertEqual(result["status"], "SCHEDULED")

    def test_recurring_weekdays(self):
        future = timezone.now() + timedelta(days=10)
        result = validate_schedule(
            {
                "start_date": future.date(),
                "is_recurring": True,
                "recurrence_frequency": "weekly",
                "recurrence_days": ["mon", "wed", "fri"],
                "status": "DRAFT",
                "timezone": "Africa/Nairobi",
            }
        )
        self.assertEqual(result["status"], "DRAFT")
        self.assertEqual(result["recurrence_days"], ["mon", "wed", "fri"])


class ImageHelperTests(SimpleTestCase):
    def test_placeholder_by_category(self):
        self.assertEqual(normalize_category("nanny"), "nanny")
        self.assertIn("nanny.svg", placeholder_url("childcare"))

    def test_rejects_bad_type(self):
        bad = SimpleUploadedFile("notes.txt", b"hello", content_type="text/plain")
        with self.assertRaises(ImageUploadError):
            validate_upload(bad)

    def test_rejects_oversize(self):
        big = SimpleUploadedFile("pic.jpg", b"x" * (5 * 1024 * 1024 + 10), content_type="image/jpeg")
        with self.assertRaises(ImageUploadError):
            validate_upload(big)


class WalletProviderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="w@test.com", email="w@test.com", password="x", role="worker")

    def test_stellar_and_evm_are_not_interchangeable(self):
        stellar = get_provider("freighter", chain="stellar")
        with self.assertRaises(WalletError):
            stellar.sign_transaction({"chain": "evm"})
        evm = get_provider("metamask", chain="evm")
        with self.assertRaises(WalletError):
            evm.sign_transaction({"chain": "stellar", "xdr": "AAAA"})
        with self.assertRaises(WalletError):
            get_provider("metamask", chain="stellar")

    def test_connect_disconnect(self):
        wallet = persist_connection(self.user, address="G" + "A" * 55, provider_name="freighter", chain="stellar")
        self.assertTrue(wallet.stellar_connected)
        persist_connection(self.user, address="0x" + "a" * 40, provider_name="metamask", chain="evm")
        wallet.refresh_from_db()
        self.assertTrue(wallet.evm_connected)
        self.assertTrue(wallet.stellar_connected)

    def test_unsupported_provider(self):
        with self.assertRaises(WalletError):
            get_provider("phantom")


class PayoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="p@test.com", email="p@test.com", password="x", role="worker")

    def test_kenyan_phone_and_mask(self):
        self.assertEqual(normalize_kenyan_phone("0712345678"), "254712345678")
        with self.assertRaises(PayoutError):
            normalize_kenyan_phone("12345")
        method = save_mpesa_method(self.user, "0712345678")
        self.assertTrue(method.masked_identifier.startswith("+25471"))
        self.assertTrue(method.identifier_hash)
        self.assertNotIn("0712345678", method.masked_identifier)


class PricingTests(SimpleTestCase):
    def tearDown(self):
        reset_price_service(None)

    def test_successful_conversion(self):
        class Fake:
            name = "fake"

            def fetch_xlm_quotes(self):
                return {"XLM_USD": Decimal("0.12"), "XLM_KES": Decimal("15.50"), "USD_KES": Decimal("129.16"), "last_updated": timezone.now().timestamp(), "source": "fake"}

        svc = PriceService(provider=Fake())
        reset_price_service(svc)
        q = svc.get_token_price("XLM", "KES")
        self.assertEqual(q.rate, Decimal("15.500000"))
        self.assertFalse(q.stale)
        conv = svc.convert(2, "XLM", "KES")
        self.assertEqual(conv["converted"], "31.00")

    def test_provider_failure(self):
        class Boom:
            name = "boom"

            def fetch_xlm_quotes(self):
                raise RuntimeError("nope")

        svc = PriceService(provider=Boom())
        q = svc.get_token_price("XLM", "USD")
        self.assertTrue(q.error)
        self.assertTrue(q.stale)

    def test_unsupported_token(self):
        class Fake:
            name = "fake"

            def fetch_xlm_quotes(self):
                return {"XLM_USD": Decimal("0.12"), "XLM_KES": Decimal("15"), "USD_KES": Decimal("129"), "last_updated": timezone.now().timestamp()}

        svc = PriceService(provider=Fake())
        q = svc.get_token_price("DOGE", "USD")
        self.assertEqual(q.error, "unsupported token")


class MapTests(SimpleTestCase):
    def test_leaflet_default_without_google_key(self):
        cfg = public_config()
        self.assertEqual(cfg["provider"], "leaflet")
        self.assertEqual(cfg["google_maps_api_key"], "")
        self.assertEqual(cfg["fallback"], "leaflet")
        self.assertGreaterEqual(len(cfg["tile_fallbacks"]), 2)

    @override_settings(GOOGLE_MAPS_API_KEY="browser-restricted-key", MAP_PROVIDER="google")
    def test_google_only_when_configured(self):
        cfg = public_config()
        self.assertEqual(cfg["provider"], "google")
        self.assertEqual(cfg["fallback"], "leaflet")

    def test_approx_location_is_offset(self):
        lat, lon = approximate_coordinates(-1.2921, 36.8219, salt="worker-1")
        self.assertNotAlmostEqual(lat, -1.2921, places=5)
        self.assertLess(abs(lat + 1.2921), 0.02)


class EngagementAndRecoveryTests(TestCase):
    def setUp(self):
        self.employer = User.objects.create_user(username="e@test.com", email="e@test.com", password="x", role="employer")
        self.worker = User.objects.create_user(username="k@test.com", email="k@test.com", password="x", role="worker")
        EmployerProfile.objects.create(user=self.employer)
        WorkerProfile.objects.create(user=self.worker, skills="nanny")
        Wallet.objects.create(user=self.employer, balance=0)
        Wallet.objects.create(user=self.worker, balance=0)
        self.job = Job.objects.create(
            employer=self.employer,
            title="Nanny",
            description="care",
            location="Westlands",
            latitude=-1.26,
            longitude=36.8,
            pay=Decimal("50000"),
            job_type="nanny",
            status=Job.Status.ACTIVE,
        )
        self.course = Course.objects.create(title="Professional Childcare", description="x", fee=Decimal("10000"), slug="childcare")
        Enrollment.objects.create(
            course=self.course,
            worker=self.worker,
            total_fee=Decimal("10000"),
            amount_remaining=Decimal("4000"),
            commission_percentage=Decimal("20"),
        )

    def test_missing_image_fallback_and_expired_job(self):
        self.assertFalse(self.job.has_custom_image)
        self.assertIn("nanny.svg", self.job.cover_image)
        self.job.end_date = timezone.now().date() - timedelta(days=1)
        self.job.end_time = time(8, 0)
        self.job.status = Job.Status.ACTIVE
        self.job.save()
        refresh_job_status(self.job)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.Status.EXPIRED)

    def test_commission_capture_and_no_double_fee(self):
        contract = Contract.objects.create(
            job=self.job,
            worker=self.worker,
            employer=self.employer,
            scope="care",
            status="draft",
            chain_status="FUNDED",
            amount=Decimal("50000"),
            currency="KES",
            approval_status=Contract.ApprovalStatus.APPROVED,
        )
        first = capture_settled_commission(contract, reference="tx1")
        self.assertEqual(first.status, Settlement.Status.FEE_CAPTURED)
        self.assertEqual(first.platform_fee_amount, Decimal("2500.00"))
        self.assertEqual(first.upskilling_recovery_amount, Decimal("4000.00"))
        self.assertEqual(first.worker_payout_amount, Decimal("43500.00"))
        enrollment = Enrollment.objects.get(worker=self.worker)
        self.assertEqual(enrollment.amount_remaining, Decimal("0.00"))
        self.assertEqual(enrollment.status, Enrollment.Status.COMPLETED)
        capture_settled_commission(contract, reference="tx1")
        self.assertEqual(Settlement.objects.filter(engagement=contract).count(), 1)
        refund_settlement(contract, reason="test refund")
        contract.settlement.refresh_from_db()
        self.assertEqual(contract.settlement.status, Settlement.Status.REFUNDED)

    def test_full_repayment_stops_recovery(self):
        self.assertEqual(remaining_training_for(self.worker), Decimal("4000.00"))
        Enrollment.objects.filter(worker=self.worker).update(amount_remaining=0, status=Enrollment.Status.COMPLETED, commission_recovered=Decimal("10000"))
        self.assertEqual(remaining_training_for(self.worker), Decimal("0.00"))
        b = compute_waterfall(Decimal("50000"), remaining_training_balance=remaining_training_for(self.worker))
        self.assertEqual(b.upskilling_recovery_amount, Decimal("0.00"))
