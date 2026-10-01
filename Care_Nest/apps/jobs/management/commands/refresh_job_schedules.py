from django.core.management.base import BaseCommand

from apps.jobs.models import Job
from apps.jobs.scheduling import refresh_job_status


class Command(BaseCommand):
    help = "Activate scheduled jobs and expire ended ones."

    def handle(self, *args, **options):
        count = 0
        for job in Job.objects.exclude(status__in=[Job.Status.CANCELLED, Job.Status.COMPLETED, Job.Status.DRAFT]):
            before = job.status
            refresh_job_status(job)
            if job.status != before:
                count += 1
        self.stdout.write(self.style.SUCCESS(f"Updated {count} job(s)"))
