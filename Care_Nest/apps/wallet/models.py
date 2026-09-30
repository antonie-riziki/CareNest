from django.db import models
from apps.accounts.models import User
from apps.contracts.models import Contract


# Create your models here.
class Wallet(models.Model):
    PROVIDER_CHOICES = [
        ("", "Not connected"),
        ("freighter", "Freighter"),
        ("xbull", "xBull"),
        ("albedo", "Albedo"),
        ("lobstr", "LOBSTR"),
        ("hana", "Hana"),
        ("walletconnect", "WalletConnect"),
        ("demo", "Demo signer (server-held testnet key)"),
        ("manual", "Manual public key"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # Stellar public key (G...). Only the public address is ever stored.
    stellar_address = models.CharField(max_length=56, blank=True, default="", db_index=True)
    wallet_provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, blank=True, default="")
    connected_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Wallet({self.user_id}, {self.stellar_address[:6]}…)" if self.stellar_address else f"Wallet({self.user_id})"

    @property
    def is_connected(self) -> bool:
        return bool(self.stellar_address)

    @property
    def short_address(self) -> str:
        if not self.stellar_address:
            return ""
        return f"{self.stellar_address[:6]}…{self.stellar_address[-6:]}"


class Transaction(models.Model):
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    type = models.CharField(max_length=50)  # credit/debit
    status = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)
    tx_hash = models.CharField(max_length=64, blank=True, default="")
    memo = models.CharField(max_length=140, blank=True, default="")


class Escrow(models.Model):
    contract = models.OneToOneField(Contract, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    released = models.BooleanField(default=False)
