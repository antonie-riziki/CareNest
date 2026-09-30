from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from apps.profiles.models import EmployerProfile, WorkerProfile
from apps.wallet.models import Wallet

User = get_user_model()

DEMO_PASSWORD = "CareNestDemo1!"

WORKERS = [
    {
        "email": "mary@carenest.demo",
        "first": "Mary",
        "last": "Wanjiku",
        "skills": "nanny, childcare, first aid, meal preparation, early learning",
        "rating": 4.9,
    },
    {
        "email": "jane@carenest.demo",
        "first": "Jane",
        "last": "Achieng",
        "skills": "housekeeper, cleaning, laundry, ironing, home organisation",
        "rating": 4.6,
    },
    {
        "email": "john@carenest.demo",
        "first": "John",
        "last": "Mutua",
        "skills": "gardener, landscaping, lawn care, pruning, irrigation",
        "rating": 4.4,
    },
]


class Command(BaseCommand):
    help = "Seed demo employer/worker accounts so the WorkOS flow is trackable from real Django rows."

    def handle(self, *args, **options):
        employer, created = User.objects.get_or_create(
            username="sarah@carenest.demo",
            defaults={
                "email": "sarah@carenest.demo",
                "first_name": "Sarah",
                "last_name": "Otieno",
                "role": "employer",
            },
        )
        if created:
            employer.set_password(DEMO_PASSWORD)
            employer.save()
        EmployerProfile.objects.get_or_create(user=employer, defaults={"rating": 4.8})
        Wallet.objects.get_or_create(user=employer, defaults={"balance": 0})
        self.stdout.write(self.style.SUCCESS(f"Employer {employer.email} / {DEMO_PASSWORD}"))

        for spec in WORKERS:
            user, created = User.objects.get_or_create(
                username=spec["email"],
                defaults={
                    "email": spec["email"],
                    "first_name": spec["first"],
                    "last_name": spec["last"],
                    "role": "worker",
                },
            )
            if created:
                user.set_password(DEMO_PASSWORD)
                user.save()
            profile, _ = WorkerProfile.objects.get_or_create(user=user, defaults={"skills": spec["skills"], "rating": spec["rating"], "verified": True})
            if not profile.skills:
                profile.skills = spec["skills"]
                profile.rating = spec["rating"]
                profile.verified = True
                profile.save()
            Wallet.objects.get_or_create(user=user, defaults={"balance": 0})
            self.stdout.write(self.style.SUCCESS(f"Worker {user.email} / {DEMO_PASSWORD}"))
