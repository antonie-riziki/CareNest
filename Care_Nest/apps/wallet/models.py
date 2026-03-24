from django.db import models
from apps.accounts.models import User
from apps.contracts.models import Contract


# Create your models here.
class Wallet(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    balance = models.DecimalField(max_digits=10, decimal_places=2)


class Transaction(models.Model):
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    type = models.CharField(max_length=50)  # credit/debit
    status = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)


class Escrow(models.Model):
    contract = models.OneToOneField(Contract, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    released = models.BooleanField(default=False)
