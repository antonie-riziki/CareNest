from django.core.management.base import BaseCommand

from apps.agentic_core.services import ingest_events


class Command(BaseCommand):
    help = "Pull CareNest WorkContract events from Stellar RPC and update the Django index."

    def add_arguments(self, parser):
        parser.add_argument("--start-ledger", type=int, default=None)
        parser.add_argument("--limit", type=int, default=200)

    def handle(self, *args, **options):
        result = ingest_events(start_ledger=options["start_ledger"], limit=options["limit"])
        if result.get("ok"):
            self.stdout.write(self.style.SUCCESS(f"Ingested {result['ingested']} event(s). latest_ledger={result.get('latest_ledger')}"))
        else:
            self.stdout.write(self.style.WARNING(f"Ingestion skipped: {result.get('reason')} (mode={result.get('mode')})"))
