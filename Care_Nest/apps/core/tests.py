from django.test import TestCase


class PwaTests(TestCase):
    def test_manifest_and_service_worker(self):
        manifest = self.client.get("/manifest.webmanifest")
        self.assertEqual(manifest.status_code, 200)
        self.assertIn("CareNest", manifest.content.decode())
        worker = self.client.get("/sw.js")
        self.assertEqual(worker.status_code, 200)
        self.assertEqual(worker["Service-Worker-Allowed"], "/")
        offline = self.client.get("/offline/")
        self.assertEqual(offline.status_code, 200)
        home = self.client.get("/")
        self.assertContains(home, "manifest.webmanifest")
        self.assertContains(home, "/static/pwa/pwa.js")
