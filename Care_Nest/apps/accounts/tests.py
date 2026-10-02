from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.profiles.models import EmployerProfile, WorkerProfile

User = get_user_model()


class AccountAuthTests(TestCase):
    def test_worker_signup_creates_account_and_lands_on_dashboard(self):
        response = self.client.post(
            "/worker-signup/",
            {
                "full_name": "Mary Wanjiku",
                "email": "mary.live@test.com",
                "phone": "0712 345 678",
                "password": "CareNestDemo1!",
                "confirm_password": "CareNestDemo1!",
            },
        )
        self.assertRedirects(response, "/worker-dashboard/")
        user = User.objects.get(email="mary.live@test.com")
        self.assertEqual(user.role, "worker")
        self.assertTrue(WorkerProfile.objects.filter(user=user, phone="+254712345678").exists())
        follow = self.client.get("/worker-dashboard/")
        self.assertEqual(follow.status_code, 200)

    def test_employer_signup_creates_account(self):
        response = self.client.post(
            "/employer-signup/",
            {
                "full_name": "Sarah Otieno",
                "email": "sarah.live@test.com",
                "phone": "0712000001",
                "password": "CareNestDemo1!",
                "confirm_password": "CareNestDemo1!",
            },
        )
        self.assertRedirects(response, "/employer-dashboard/")
        self.assertTrue(EmployerProfile.objects.filter(user__email="sarah.live@test.com").exists())

    def test_worker_signin_after_signup(self):
        self.client.post(
            "/worker-signup/",
            {
                "full_name": "Jane Achieng",
                "email": "jane.live@test.com",
                "phone": "0722000002",
                "password": "CareNestDemo1!",
                "confirm_password": "CareNestDemo1!",
            },
        )
        self.client.logout()
        response = self.client.post(
            "/worker-signin/",
            {"identifier": "jane.live@test.com", "password": "CareNestDemo1!"},
        )
        self.assertRedirects(response, "/worker-dashboard/")

    def test_mismatched_passwords_stay_on_signup_with_error(self):
        response = self.client.post(
            "/worker-signup/",
            {
                "full_name": "Test User",
                "email": "mismatch@test.com",
                "phone": "0712345678",
                "password": "CareNestDemo1!",
                "confirm_password": "other-password",
            },
            follow=True,
        )
        self.assertContains(response, "Passwords do not match")
        self.assertFalse(User.objects.filter(email="mismatch@test.com").exists())

    def test_google_start_redirects_when_configured(self):
        with patch.dict(
            "os.environ",
            {"GOOGLE_OAUTH_CLIENT_ID": "cid.apps.googleusercontent.com", "GOOGLE_OAUTH_CLIENT_SECRET": "secret"},
            clear=False,
        ):
            response = self.client.get("/auth/google/?role=employer")
        self.assertEqual(response.status_code, 302)
        self.assertIn("accounts.google.com", response["Location"])

    def test_google_callback_creates_worker(self):
        session = self.client.session
        session["google_oauth_state"] = "abc123"
        session["google_oauth_role"] = "worker"
        session.save()
        with patch("apps.accounts.views.google_fetch_profile") as mocked:
            mocked.return_value = {
                "email": "google.worker@test.com",
                "first_name": "Google",
                "last_name": "Worker",
                "full_name": "Google Worker",
                "picture": "",
            }
            response = self.client.get("/auth/google/callback/", {"code": "tok", "state": "abc123"})
        self.assertRedirects(response, "/worker-dashboard/")
        user = User.objects.get(email="google.worker@test.com")
        self.assertEqual(user.role, "worker")
        self.assertTrue(WorkerProfile.objects.filter(user=user).exists())
        self.assertFalse(user.has_usable_password())
