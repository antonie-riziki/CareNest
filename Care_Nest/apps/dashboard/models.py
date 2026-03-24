from django.db import models
from apps.jobs.models import Job
from apps.contracts.models import Contract
from apps.wallet.models import Wallet
from rest_framework.response import Response


# Create your models here.
def worker_dashboard(request):
    user = request.user
    jobs = Job.objects.filter(application__worker=user)
    contracts = Contract.objects.filter(worker=user)
    wallet = Wallet.objects.get(user=user)

    return Response(
        {
            "jobs": jobs.count(),
            "contracts": contracts.count(),
            "balance": wallet.balance,
        }
    )
