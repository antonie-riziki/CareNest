from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.accounts.models import User
from apps.jobs.models import Job


class Contract(models.Model):
    """
    A domestic-work engagement.

    The original marketplace fields are preserved. The additional fields index
    the on-chain Soroban agreement so the Django database acts as a cache while
    Stellar remains the source of truth for lifecycle state and settlement.
    """

    DATA_SOURCE_LIVE = "LIVE_TESTNET"
    DATA_SOURCE_DEMO = "DEMO_DATA"
    DATA_SOURCE_CHOICES = [
        (DATA_SOURCE_LIVE, "Live testnet"),
        (DATA_SOURCE_DEMO, "Demo data"),
    ]

    job = models.ForeignKey(Job, on_delete=models.CASCADE)
    worker = models.ForeignKey(User, on_delete=models.CASCADE, related_name="work_contracts")
    employer = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="employed_contracts"
    )
    scope = models.TextField()
    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=50)

    # ---- on-chain index (public, non-sensitive) ---------------------------
    engagement_id = models.BigIntegerField(
        null=True, blank=True, db_index=True, help_text="Engagement id inside the Soroban contract"
    )
    chain_status = models.CharField(max_length=32, default="DRAFT", db_index=True)
    contract_address = models.CharField(max_length=56, blank=True, default="")
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    currency = models.CharField(max_length=8, default="KES")
    token_amount = models.BigIntegerField(
        null=True, blank=True, help_text="Escrow amount in token base units (stroops)"
    )
    token_symbol = models.CharField(max_length=12, default="XLM")
    employer_wallet = models.CharField(max_length=56, blank=True, default="")
    worker_wallet = models.CharField(max_length=56, blank=True, default="")
    terms_hash = models.CharField(max_length=64, blank=True, default="")
    work_hash = models.CharField(max_length=64, blank=True, default="")
    create_tx_hash = models.CharField(max_length=64, blank=True, default="")
    fund_tx_hash = models.CharField(max_length=64, blank=True, default="")
    submit_tx_hash = models.CharField(max_length=64, blank=True, default="")
    release_tx_hash = models.CharField(max_length=64, blank=True, default="")
    credential_tx_hash = models.CharField(max_length=64, blank=True, default="")
    data_source = models.CharField(
        max_length=16, choices=DATA_SOURCE_CHOICES, default=DATA_SOURCE_LIVE
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Engagement #{self.pk} ({self.chain_status})"

    # ---- helpers ------------------------------------------------------------
    @property
    def is_demo(self) -> bool:
        return self.data_source == self.DATA_SOURCE_DEMO

    @property
    def on_chain_reference(self) -> str:
        if self.engagement_id is None:
            return ""
        addr = self.contract_address or getattr(settings, "CARENEST_CONTRACT_ID", "")
        return f"{addr}#{self.engagement_id}"

    @property
    def token_amount_display(self) -> str:
        if self.token_amount is None:
            return ""
        decimals = getattr(settings, "CARENEST_TOKEN_DECIMALS", 7)
        return f"{self.token_amount / (10 ** decimals):,.2f} {self.token_symbol}"

    def latest_tx_hash(self) -> str:
        for field in (
            "credential_tx_hash",
            "release_tx_hash",
            "submit_tx_hash",
            "fund_tx_hash",
            "create_tx_hash",
        ):
            value = getattr(self, field)
            if value:
                return value
        return ""
