from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.contracts.models import Contract
from apps.courses.models import Course, Enrollment
from apps.jobs.models import Application, Job
from apps.profiles.models import EmployerProfile, WorkerProfile
from apps.wallet.models import Wallet
from apps.wallet.settlement import compute_waterfall, remaining_training_for, apply_breakdown_to_contract

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

COURSES = [
    {"title": "Childcare Fundamentals", "description": "Daily childcare, nutrition, and safety.", "fee": Decimal("8000"), "category": "nanny", "slug": "childcare-fundamentals"},
    {"title": "Elder Care", "description": "Personal care and wellbeing support for older adults.", "fee": Decimal("6500"), "category": "caregiver", "slug": "elder-care"},
    {"title": "Housekeeping Professional", "description": "Residential cleaning and home organisation.", "fee": Decimal("5000"), "category": "housekeeper", "slug": "housekeeping-professional"},
]


class Command(BaseCommand):
    help = "Seed demo employer/worker accounts, jobs, courses, and a pending engagement."

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

        workers = []
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
            workers.append(user)
            self.stdout.write(self.style.SUCCESS(f"Worker {user.email} / {DEMO_PASSWORD}"))

        mary, jane, john = workers
        now = timezone.now()
        jobs_spec = [
            {
                "title": "Full-time nanny in Westlands",
                "job_type": "nanny",
                "pay": Decimal("45000"),
                "location": "Westlands",
                "lat": -1.2634,
                "lon": 36.8036,
                "status": Job.Status.ACTIVE,
                "description": "Weekday childcare for two children, school run, and meal prep.",
                "worker": mary,
            },
            {
                "title": "Housekeeping in Kilimani",
                "job_type": "housekeeper",
                "pay": Decimal("35000"),
                "location": "Kilimani",
                "lat": -1.2921,
                "lon": 36.7870,
                "status": Job.Status.ACTIVE,
                "description": "Daily cleaning, laundry, and kitchen hygiene for a family home.",
                "worker": jane,
            },
            {
                "title": "Garden maintenance in Lavington",
                "job_type": "gardener",
                "pay": Decimal("18000"),
                "location": "Lavington",
                "lat": -1.2770,
                "lon": 36.7660,
                "status": Job.Status.SCHEDULED,
                "description": "Twice-weekly lawn care starting next week.",
                "worker": john,
            },
        ]
        for spec in jobs_spec:
            job, _ = Job.objects.get_or_create(
                employer=employer,
                title=spec["title"],
                defaults={
                    "description": spec["description"],
                    "location": spec["location"],
                    "latitude": spec["lat"],
                    "longitude": spec["lon"],
                    "pay": spec["pay"],
                    "job_type": spec["job_type"],
                    "status": spec["status"],
                    "start_date": (now + timedelta(days=1)).date(),
                    "start_time": datetime.strptime("08:00", "%H:%M").time(),
                    "is_recurring": spec["job_type"] != "gardener",
                    "recurrence_frequency": "weekly" if spec["job_type"] != "gardener" else "weekly",
                    "recurrence_days": ["mon", "wed", "fri"],
                    "timezone": "Africa/Nairobi",
                    "published_at": now if spec["status"] == Job.Status.ACTIVE else None,
                    "scheduled_publish_at": now + timedelta(days=3) if spec["status"] == Job.Status.SCHEDULED else now,
                    "is_verified": True,
                },
            )
            Application.objects.get_or_create(worker=spec["worker"], job=job, defaults={"status": Application.Status.SUBMITTED})

        for spec in COURSES:
            Course.objects.get_or_create(slug=spec["slug"], defaults=spec)
        childcare = Course.objects.get(slug="childcare-fundamentals")
        Enrollment.objects.get_or_create(
            course=childcare,
            worker=mary,
            defaults={
                "total_fee": childcare.fee,
                "amount_remaining": childcare.fee,
                "commission_percentage": childcare.recovery_percentage,
                "status": Enrollment.Status.ACTIVE,
            },
        )

        nanny_job = Job.objects.filter(title__icontains="nanny", employer=employer).first()
        if nanny_job and not Contract.objects.filter(job=nanny_job, worker=mary).exists():
            remaining = remaining_training_for(mary)
            breakdown = compute_waterfall(nanny_job.pay, remaining_training_balance=remaining, currency="KES")
            contract = Contract.objects.create(
                job=nanny_job,
                worker=mary,
                employer=employer,
                scope="Daily childcare and supervision\nMeal preparation for children",
                status="draft",
                chain_status="DRAFT",
                approval_status=Contract.ApprovalStatus.PENDING_REVIEW,
                duration_text="1 month",
                amount=nanny_job.pay,
                currency="KES",
            )
            apply_breakdown_to_contract(contract, breakdown)
            contract.save()
            self.stdout.write(self.style.SUCCESS(f"Pending engagement #{contract.pk} ready for employer approval"))

        try:
            from apps.core.supabase_client import sync_account

            sync_account(employer)
            for worker in workers:
                sync_account(worker)
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(self.style.WARNING(f"Supabase sync skipped ({type(exc).__name__})"))
