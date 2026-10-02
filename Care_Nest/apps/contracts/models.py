import uuid

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

    class ApprovalStatus(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PENDING_REVIEW = "PENDING_REVIEW", "Pending employer review"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        CHANGES_REQUESTED = "CHANGES_REQUESTED", "Changes requested"

    job = models.ForeignKey(Job, on_delete=models.CASCADE)
    worker = models.ForeignKey(User, on_delete=models.CASCADE, related_name="work_contracts")
    employer = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="employed_contracts"
    )
    scope = models.TextField()
    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)
    duration_text = models.CharField(max_length=80, blank=True, default="")
    status = models.CharField(max_length=50)

    approval_status = models.CharField(
        max_length=24, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT, db_index=True
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_engagements",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default="")
    change_request = models.TextField(blank=True, default="")
    employer_terms = models.TextField(blank=True, default="")
    worker_terms = models.TextField(blank=True, default="")
    worker_hours_note = models.TextField(blank=True, default="")
    worker_responded_at = models.DateTimeField(null=True, blank=True)
    bound_at = models.DateTimeField(null=True, blank=True)

    platform_fee_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=5)
    platform_fee_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    worker_net_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    upskilling_recovery_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    worker_payout_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)

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
        indexes = [
            models.Index(fields=["employer", "approval_status"]),
            models.Index(fields=["worker", "chain_status"]),
        ]

    def __str__(self):
        return f"Engagement #{self.pk} ({self.chain_status})"

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

    @property
    def needs_employer_approval(self) -> bool:
        return self.approval_status == self.ApprovalStatus.PENDING_REVIEW

    @property
    def needs_worker_terms(self) -> bool:
        return self.approval_status in (
            self.ApprovalStatus.DRAFT,
            self.ApprovalStatus.CHANGES_REQUESTED,
        )

    @property
    def is_bound(self) -> bool:
        return self.approval_status == self.ApprovalStatus.APPROVED

    @property
    def invoices_cleared(self) -> bool:
        return not self.service_invoices.exclude(
            status__in=[ServiceInvoice.Status.SETTLED, ServiceInvoice.Status.VOID]
        ).exists()

    @property
    def funds_cleared(self) -> bool:
        from apps.agentic_core.state import EngagementStatus
        from apps.wallet.models import Settlement

        if self.chain_status == EngagementStatus.RELEASED:
            return True
        try:
            settlement = self.settlement
        except Exception:
            return False
        return settlement.status in {Settlement.Status.SETTLED, Settlement.Status.FEE_CAPTURED}

    @property
    def is_complete(self) -> bool:
        return self.is_bound and self.invoices_cleared and self.funds_cleared

    @property
    def display_status(self) -> str:
        if self.is_complete:
            return "COMPLETED"
        if self.approval_status == self.ApprovalStatus.APPROVED:
            if self.chain_status in {"", "DRAFT"}:
                return "BOUND"
            return self.chain_status
        if self.approval_status == self.ApprovalStatus.PENDING_REVIEW:
            return "PENDING_REVIEW"
        return self.approval_status or self.chain_status or "DRAFT"

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


class ShiftAttendance(models.Model):
    engagement = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="shifts")
    worker = models.ForeignKey(User, on_delete=models.CASCADE, related_name="shift_logs")
    checked_in_at = models.DateTimeField(default=timezone.now)
    checked_out_at = models.DateTimeField(null=True, blank=True)
    check_in_note = models.CharField(max_length=255, blank=True, default="")
    check_out_note = models.CharField(max_length=255, blank=True, default="")
    work_summary = models.TextField(blank=True, default="")
    employer_verified = models.BooleanField(default=False)
    employer_verified_at = models.DateTimeField(null=True, blank=True)
    employer_verification_note = models.TextField(blank=True, default="")
    employer_rating = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-checked_in_at"]

    def __str__(self):
        return f"Shift #{self.pk} engagement {self.engagement_id}"

    @property
    def is_open(self) -> bool:
        return self.checked_out_at is None

    @property
    def duration_minutes(self) -> int | None:
        if not self.checked_out_at:
            return None
        return int((self.checked_out_at - self.checked_in_at).total_seconds() // 60)

    @property
    def needs_employer_review(self) -> bool:
        return bool(self.checked_out_at and self.work_summary and not self.employer_verified)


class Notification(models.Model):
    class Kind(models.TextChoices):
        CHECK_IN = "check_in", "Check in"
        CHECK_OUT = "check_out", "Check out"
        WORK_SUBMITTED = "work_submitted", "Work submitted"
        WORK_APPROVED = "work_approved", "Work approved"
        DISPUTE = "dispute", "Dispute"
        CONTRACT = "contract", "Contract"

    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    actor = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="sent_notifications"
    )
    engagement = models.ForeignKey(
        Contract, null=True, blank=True, on_delete=models.CASCADE, related_name="notifications"
    )
    kind = models.CharField(max_length=24, choices=Kind.choices, default=Kind.CONTRACT)
    title = models.CharField(max_length=160)
    body = models.TextField(blank=True, default="")
    url = models.CharField(max_length=255, blank=True, default="")
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_unread(self) -> bool:
        return self.read_at is None


class Dispute(models.Model):
    class Status(models.TextChoices):
        ONGOING = "ongoing", "Ongoing"
        UNRESOLVED = "unresolved", "Unresolved"
        SETTLED = "settled", "Settled"

    class Category(models.TextChoices):
        ATTENDANCE = "attendance", "Attendance"
        PAYMENT = "payment", "Payment"
        SCOPE = "scope", "Work scope"
        CONDUCT = "conduct", "Conduct"
        OTHER = "other", "Other"

    engagement = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="disputes")
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="disputes")
    reporter = models.ForeignKey(User, on_delete=models.CASCADE, related_name="reported_disputes")
    against = models.ForeignKey(User, on_delete=models.CASCADE, related_name="received_disputes")
    category = models.CharField(max_length=24, choices=Category.choices, default=Category.OTHER)
    title = models.CharField(max_length=160)
    description = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ONGOING, db_index=True)
    resolution = models.TextField(blank=True, default="")
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Dispute #{self.pk} ({self.status})"


class DisputeEvidence(models.Model):
    class Kind(models.TextChoices):
        IMAGE = "image", "Image"
        DOCUMENT = "document", "Document"
        VIDEO = "video", "Video"

    dispute = models.ForeignKey(Dispute, on_delete=models.CASCADE, related_name="evidence")
    uploaded_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="dispute_evidence")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    file_url = models.CharField(max_length=500)
    file_name = models.CharField(max_length=255, blank=True, default="")
    content_type = models.CharField(max_length=80, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return self.file_name or f"Evidence #{self.pk}"


class CompletionReport(models.Model):
    engagement = models.OneToOneField(Contract, on_delete=models.CASCADE, related_name="completion_report")
    share_token = models.UUIDField(unique=True, db_index=True, default=uuid.uuid4)
    snapshot = models.JSONField(default=dict, blank=True)
    generated_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-generated_at"]

    def __str__(self):
        return f"Report {self.share_token}"

    @property
    def share_path(self) -> str:
        return f"/reports/{self.share_token}/"


class ServiceInvoice(models.Model):
    """CareNest → worker training-recovery invoice. Never shown to employers."""

    class Status(models.TextChoices):
        ISSUED = "issued", "Issued"
        SETTLED = "settled", "Settled"
        VOID = "void", "Void"

    worker = models.ForeignKey(User, on_delete=models.CASCADE, related_name="service_invoices")
    engagement = models.ForeignKey(
        Contract, on_delete=models.CASCADE, related_name="service_invoices", null=True, blank=True
    )
    invoice_number = models.CharField(max_length=32, unique=True, blank=True, default="")
    description = models.CharField(max_length=255, default="CareNest upskilling recovery")
    eligible_earnings = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    recovery_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=20)
    recovery_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    remaining_training_after = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    currency = models.CharField(max_length=8, default="KES")
    line_items = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ISSUED)
    issued_at = models.DateTimeField(default=timezone.now)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-issued_at"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.invoice_number:
            self.invoice_number = f"CN-INV-{self.issued_at.year}-{self.pk:05d}"
            super().save(update_fields=["invoice_number"])

    def __str__(self):
        return self.invoice_number or f"Invoice {self.pk}"
