from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.accounts.models import User
from apps.contracts.models import Contract


class Wallet(models.Model):
    PROVIDER_CHOICES = [
        ("", "Not connected"),
        ("freighter", "Freighter"),
        ("xbull", "xBull"),
        ("albedo", "Albedo"),
        ("lobstr", "LOBSTR"),
        ("hana", "Hana"),
        ("metamask", "MetaMask"),
        ("walletconnect", "WalletConnect"),
        ("injected_evm", "Injected EVM"),
        ("demo", "Demo signer (server-held testnet key)"),
        ("manual", "Manual public key"),
    ]

    class Chain(models.TextChoices):
        NONE = "", "Not set"
        STELLAR = "stellar", "Stellar"
        EVM = "evm", "EVM"

    class ConnectionStatus(models.TextChoices):
        DISCONNECTED = "disconnected", "Disconnected"
        CONNECTED = "connected", "Connected"
        WRONG_NETWORK = "wrong_network", "Wrong network"
        UNSUPPORTED = "unsupported", "Unsupported wallet"
        PENDING = "pending", "Pending"
        FAILED = "failed", "Transaction failed"
        CONFIRMED = "confirmed", "Transaction confirmed"

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    onchain_balance = models.DecimalField(max_digits=28, decimal_places=8, default=0)
    onchain_symbol = models.CharField(max_length=12, blank=True, default="")
    onchain_synced_at = models.DateTimeField(null=True, blank=True)
    # Stellar public key (G...). Only the public address is ever stored.
    stellar_address = models.CharField(max_length=56, blank=True, default="", db_index=True)
    evm_address = models.CharField(max_length=42, blank=True, default="", db_index=True)
    address = models.CharField(max_length=64, blank=True, default="")
    wallet_provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, blank=True, default="")
    chain = models.CharField(max_length=16, choices=Chain.choices, blank=True, default="")
    network = models.CharField(max_length=32, blank=True, default="")
    connection_status = models.CharField(
        max_length=24, choices=ConnectionStatus.choices, default=ConnectionStatus.DISCONNECTED
    )
    connected_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        label = self.address or self.stellar_address or self.evm_address
        return f"Wallet({self.user_id}, {label[:6]}…)" if label else f"Wallet({self.user_id})"

    @property
    def is_connected(self) -> bool:
        return bool(self.stellar_address or self.evm_address)

    @property
    def short_address(self) -> str:
        value = self.stellar_address or self.evm_address or self.address
        if not value:
            return ""
        return f"{value[:6]}…{value[-6:]}"

    @property
    def stellar_connected(self) -> bool:
        return bool(self.stellar_address)

    @property
    def evm_connected(self) -> bool:
        return bool(self.evm_address)


class Settlement(models.Model):
    """Authoritative accounting record for one employer-funded engagement."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        FUNDED = "FUNDED", "Funded"
        SETTLED = "SETTLED", "Settled"
        FEE_CAPTURED = "FEE_CAPTURED", "Fee captured"
        FAILED = "FAILED", "Failed"
        REFUNDED = "REFUNDED", "Refunded"
        DISPUTED = "DISPUTED", "Disputed"

    engagement = models.OneToOneField(Contract, on_delete=models.CASCADE, related_name="settlement")
    employer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="employer_settlements")
    worker = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="worker_settlements")
    gross_amount = models.DecimalField(max_digits=14, decimal_places=2)
    platform_fee_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=5)
    platform_fee_amount = models.DecimalField(max_digits=14, decimal_places=2)
    worker_net_amount = models.DecimalField(max_digits=14, decimal_places=2)
    upskilling_recovery_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    worker_payout_amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=8, default="KES")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    payout_method = models.CharField(max_length=16, blank=True, default="")
    settlement_reference = models.CharField(max_length=80, blank=True, default="")
    breakdown = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "created_at"])]


class Transaction(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PROCESSING = "PROCESSING", "Processing"
        SETTLED = "SETTLED", "Settled"
        CONFIRMED = "CONFIRMED", "Confirmed"
        FAILED = "FAILED", "Failed"
        REFUNDED = "REFUNDED", "Refunded"

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    type = models.CharField(max_length=50)  # credit/debit
    status = models.CharField(max_length=50, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    tx_hash = models.CharField(max_length=64, blank=True, default="")
    memo = models.CharField(max_length=140, blank=True, default="")
    settlement = models.ForeignKey(Settlement, null=True, blank=True, on_delete=models.SET_NULL, related_name="ledger")
    gross_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    platform_fee_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    worker_net_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    currency = models.CharField(max_length=8, default="KES")


class PayoutMethod(models.Model):
    class Type(models.TextChoices):
        STELLAR = "stellar", "Blockchain wallet"
        MPESA = "mpesa", "M-Pesa"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payout_methods")
    method_type = models.CharField(max_length=16, choices=Type.choices)
    provider = models.CharField(max_length=24, blank=True, default="")
    masked_identifier = models.CharField(max_length=64, blank=True, default="")
    identifier_hash = models.CharField(max_length=64, blank=True, default="")
    verified = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    is_default = models.BooleanField(default=False)
    last_reference = models.CharField(max_length=80, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "method_type")


class Escrow(models.Model):
    contract = models.OneToOneField(Contract, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    released = models.BooleanField(default=False)
