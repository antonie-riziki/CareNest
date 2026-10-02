from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.profiles.models import EmployerProfile, WorkerProfile

User = get_user_model()


class SettingsPageTests(TestCase):
    def test_employer_settings_saves_household(self):
        user = User.objects.create_user(username="set-e@test.com", password="x", role="employer", first_name="Sarah")
        EmployerProfile.objects.create(user=user)
        self.client.force_login(user)
        response = self.client.post(
            "/employer-settings/",
            {"first_name": "Sarah", "last_name": "Otieno", "organisation": "Otieno House", "location_label": "Westlands"},
        )
        self.assertEqual(response.status_code, 302)
        profile = EmployerProfile.objects.get(user=user)
        self.assertEqual(profile.organisation, "Otieno House")
        page = self.client.get("/employer-settings/")
        self.assertContains(page, "Household settings")
        self.assertContains(page, "Otieno House")

    def test_worker_settings_uses_real_profile(self):
        user = User.objects.create_user(username="set-w@test.com", password="x", role="worker", first_name="Mary")
        WorkerProfile.objects.create(user=user, phone="+254722000001", skills="nanny")
        self.client.force_login(user)
        page = self.client.get("/worker-settings/")
        self.assertContains(page, "Mary")
        self.assertContains(page, "+254722000001")
