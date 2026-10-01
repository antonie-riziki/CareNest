from django.core.management.base import BaseCommand

from django.contrib.auth import get_user_model

from apps.core.supabase_client import SupabaseUnavailable, configured, upsert
from apps.jobs.models import Job


class Command(BaseCommand):
    help = "Mirror Django worker/employer/job rows into Supabase tables (secret key stays server-side)."

    def handle(self, *args, **options):
        if not configured():
            self.stdout.write(self.style.WARNING("Supabase env vars are not set; skipping."))
            return
        User = get_user_model()
        employers = [
            {"user_id": u.pk, "email": u.email or u.username, "full_name": u.get_full_name() or u.username, "role": "employer", "is_active": u.is_active}
            for u in User.objects.filter(role="employer")
        ]
        workers = [
            {"user_id": u.pk, "email": u.email or u.username, "full_name": u.get_full_name() or u.username, "role": "worker", "is_active": u.is_active}
            for u in User.objects.filter(role="worker")
        ]
        jobs = [
            {
                "id": j.pk,
                "employer_user_id": j.employer_id,
                "title": j.title,
                "job_type": j.job_type,
                "location": j.location,
                "pay": str(j.pay),
                "currency": j.currency,
                "status": j.status,
                "image_url": j.image_url,
                "start_date": j.start_date.isoformat() if j.start_date else None,
            }
            for j in Job.objects.all()
        ]
        try:
            if employers:
                upsert("carenest_employers", employers, on_conflict="user_id")
            if workers:
                upsert("carenest_workers", workers, on_conflict="user_id")
            if jobs:
                upsert("carenest_jobs", jobs, on_conflict="id")
            self.stdout.write(self.style.SUCCESS(f"Synced {len(employers)} employers, {len(workers)} workers, {len(jobs)} jobs"))
        except SupabaseUnavailable as exc:
            self.stdout.write(self.style.WARNING(f"Supabase unavailable: {exc}"))
