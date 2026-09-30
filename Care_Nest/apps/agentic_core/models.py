"""
Persistent state for the CareNest WorkOS agent.

Everything the agent knows, decides, or does is written here so that it
survives process restarts and every decision can be audited later.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.contracts.models import Contract

from .state import EngagementStatus


class DataSource(models.TextChoices):
    LIVE = "LIVE_TESTNET", "Live testnet"
    DEMO = "DEMO_DATA", "Demo data"
    OFFCHAIN = "OFF_CHAIN", "Off-chain only"


class AgentSession(models.Model):
    """A bounded interaction window (web page load, USSD dial, background tick)."""

    class Channel(models.TextChoices):
        WEB = "web", "Web"
        USSD = "ussd", "USSD"
        SYSTEM = "system", "System / scheduler"

    session_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    role = models.CharField(max_length=20, blank=True, default="")
    engagement = models.ForeignKey(
        Contract, null=True, blank=True, on_delete=models.SET_NULL, related_name="agent_sessions"
    )
    channel = models.CharField(max_length=10, choices=Channel.choices, default=Channel.WEB)
    started_at = models.DateTimeField(auto_now_add=True)
    last_active_at = models.DateTimeField(auto_now=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def end(self):
        self.ended_at = timezone.now()
        self.save(update_fields=["ended_at"])

    def __str__(self):
        return f"Session {str(self.session_key)[:8]} ({self.channel})"


class AgentMemory(models.Model):
    """
    Long-lived memory scoped to an engagement (or a user when no engagement exists).

    The memory is *the* thing that makes the agent stateful across sessions: a
    brand-new session loads the memory for the engagement and continues from
    where the previous one left off.
    """

    scope_key = models.CharField(max_length=64, unique=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    engagement = models.OneToOneField(
        Contract, null=True, blank=True, on_delete=models.CASCADE, related_name="agent_memory"
    )
    user_role = models.CharField(max_length=20, blank=True, default="")
    workflow_stage = models.CharField(max_length=40, default="intake")
    contract_state = models.CharField(
        max_length=32, choices=EngagementStatus.CHOICES, default=EngagementStatus.DRAFT
    )
    engagement_context = models.JSONField(default=dict, blank=True)
    previous_actions = models.JSONField(default=list, blank=True)
    unresolved_tasks = models.JSONField(default=list, blank=True)
    important_decisions = models.JSONField(default=list, blank=True)
    summary = models.TextField(blank=True, default="")
    version = models.PositiveIntegerField(default=1)
    last_event_ledger = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "agent memories"

    def __str__(self):
        return f"Memory[{self.scope_key}] {self.workflow_stage}/{self.contract_state}"


class AgentDecision(models.Model):
    """Auditable record of one meaningful agent decision."""

    class Type(models.TextChoices):
        PARSE_REQUIREMENT = "PARSE_REQUIREMENT"
        RECOMMEND_WORKERS = "RECOMMEND_WORKERS"
        PREPARE_CONTRACT = "PREPARE_CONTRACT"
        REQUEST_CONTRACT_APPROVAL = "REQUEST_CONTRACT_APPROVAL"
        REQUEST_FUNDING_APPROVAL = "REQUEST_FUNDING_APPROVAL"
        REQUEST_EMPLOYER_APPROVAL = "REQUEST_EMPLOYER_APPROVAL"
        RECORD_CHAIN_EVENT = "RECORD_CHAIN_EVENT"
        RECORD_RESULT = "RECORD_RESULT"
        ISSUE_CREDENTIAL = "ISSUE_CREDENTIAL"
        FLAG_DISPUTE = "FLAG_DISPUTE"
        EXPLAIN_STATE = "EXPLAIN_STATE"
        NOTIFY = "NOTIFY"
        NO_ACTION = "NO_ACTION"

    class ApprovalStatus(models.TextChoices):
        NOT_REQUIRED = "NOT_REQUIRED"
        PENDING = "PENDING"
        APPROVED = "APPROVED"
        REJECTED = "REJECTED"
        EXECUTED = "EXECUTED"
        FAILED = "FAILED"

    decision_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    session = models.ForeignKey(
        AgentSession, null=True, blank=True, on_delete=models.SET_NULL, related_name="decisions"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    engagement = models.ForeignKey(
        Contract, null=True, blank=True, on_delete=models.SET_NULL, related_name="agent_decisions"
    )
    decision_type = models.CharField(max_length=40, choices=Type.choices)
    context = models.JSONField(default=dict, blank=True)
    evidence = models.JSONField(default=list, blank=True)
    decision = models.TextField()
    rationale = models.TextField(blank=True, default="")
    action = models.CharField(max_length=80, blank=True, default="")
    approval_required = models.BooleanField(default=False)
    approval_status = models.CharField(
        max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.NOT_REQUIRED
    )
    financial = models.BooleanField(
        default=False, help_text="True if the prepared action moves funds"
    )
    financial_action_executed = models.BooleanField(default=False)
    result = models.JSONField(default=dict, blank=True)
    tx_hash = models.CharField(max_length=64, blank=True, default="")
    data_source = models.CharField(
        max_length=16, choices=DataSource.choices, default=DataSource.OFFCHAIN
    )
    model_used = models.CharField(max_length=80, blank=True, default="")
    llm_used = models.BooleanField(default=False)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.decision_type} @ {self.timestamp:%Y-%m-%d %H:%M}"

    @property
    def short_id(self) -> str:
        return str(self.decision_id)[:8]


class AgentAction(models.Model):
    """Log of every tool invocation performed by the agent."""

    session = models.ForeignKey(
        AgentSession, null=True, blank=True, on_delete=models.SET_NULL, related_name="actions"
    )
    decision = models.ForeignKey(
        AgentDecision, null=True, blank=True, on_delete=models.SET_NULL, related_name="actions"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    engagement = models.ForeignKey(
        Contract, null=True, blank=True, on_delete=models.SET_NULL, related_name="agent_actions"
    )
    tool_name = models.CharField(max_length=80, db_index=True)
    arguments = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    success = models.BooleanField(default=True)
    error = models.TextField(blank=True, default="")
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.tool_name} ({'ok' if self.success else 'error'})"


class AgentApproval(models.Model):
    """
    A human approval gate. The agent prepares an action; a human must approve it
    (and sign the transaction in their wallet) before anything financial happens.
    """

    class Type(models.TextChoices):
        CREATE_AGREEMENT = "CREATE_AGREEMENT"
        FUND_ESCROW = "FUND_ESCROW"
        RELEASE_PAYMENT = "RELEASE_PAYMENT"
        RESOLVE_DISPUTE = "RESOLVE_DISPUTE"
        CANCEL_AGREEMENT = "CANCEL_AGREEMENT"
        ISSUE_CREDENTIAL = "ISSUE_CREDENTIAL"

    class Status(models.TextChoices):
        PENDING = "PENDING"
        APPROVED = "APPROVED"
        REJECTED = "REJECTED"
        EXECUTED = "EXECUTED"
        FAILED = "FAILED"

    decision = models.ForeignKey(AgentDecision, on_delete=models.CASCADE, related_name="approvals")
    engagement = models.ForeignKey(
        Contract, null=True, blank=True, on_delete=models.SET_NULL, related_name="approvals"
    )
    approval_type = models.CharField(max_length=32, choices=Type.choices)
    requested_from = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pending_agent_approvals",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    summary = models.TextField()
    prepared_action = models.JSONField(
        default=dict, blank=True, help_text="Contract function + arguments prepared by the agent"
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True, default="")
    tx_hash = models.CharField(max_length=64, blank=True, default="")
    executed_at = models.DateTimeField(null=True, blank=True)
    data_source = models.CharField(
        max_length=16, choices=DataSource.choices, default=DataSource.LIVE
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.approval_type} [{self.status}]"

    @property
    def is_financial(self) -> bool:
        return self.approval_type in {
            self.Type.FUND_ESCROW,
            self.Type.RELEASE_PAYMENT,
            self.Type.RESOLVE_DISPUTE,
            self.Type.CANCEL_AGREEMENT,
        }


class StellarEvent(models.Model):
    """A contract event ingested from Stellar RPC (or a recorded demo fixture)."""

    event_id = models.CharField(max_length=80, unique=True)
    contract_id = models.CharField(max_length=56, db_index=True)
    ledger = models.BigIntegerField(db_index=True)
    ledger_closed_at = models.DateTimeField(null=True, blank=True)
    tx_hash = models.CharField(max_length=64, db_index=True)
    event_type = models.CharField(max_length=40, db_index=True)
    # On-chain engagement id (u64). Named distinctly from the Django FK column
    # ``engagement_id`` created by the ForeignKey below.
    chain_engagement_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    engagement = models.ForeignKey(
        Contract, null=True, blank=True, on_delete=models.SET_NULL, related_name="stellar_events"
    )
    topics = models.JSONField(default=list, blank=True)
    data = models.JSONField(default=dict, blank=True)
    raw = models.JSONField(default=dict, blank=True)
    data_source = models.CharField(
        max_length=16, choices=DataSource.choices, default=DataSource.LIVE
    )
    processed = models.BooleanField(default=False)
    ingested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ledger", "id"]

    def __str__(self):
        return f"{self.event_type}#{self.chain_engagement_id} @ {self.ledger}"


class WorkCredential(models.Model):
    """
    Verifiable digital record of a completed engagement (the "Work Passport" entry).

    The credential is *proof of completed work*, not a token representing the
    worker. The canonical payload is hashed; the hash is anchored on-chain via
    ``issue_credential`` and the transaction hash is stored here.
    """

    class CompletionStatus(models.TextChoices):
        COMPLETED = "COMPLETED"
        DISPUTED_RESOLVED = "DISPUTED_RESOLVED"

    class PaymentStatus(models.TextChoices):
        RELEASED = "RELEASED"
        PARTIAL = "PARTIAL"
        UNPAID = "UNPAID"

    credential_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    worker = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="work_credentials"
    )
    employer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    engagement = models.OneToOneField(
        Contract, on_delete=models.CASCADE, related_name="work_credential"
    )
    job_type = models.CharField(max_length=100)
    skills = models.JSONField(default=list, blank=True)
    duration_days = models.PositiveIntegerField(default=0)
    started_on = models.DateField(null=True, blank=True)
    completed_on = models.DateField(null=True, blank=True)
    completion_status = models.CharField(
        max_length=24, choices=CompletionStatus.choices, default=CompletionStatus.COMPLETED
    )
    employer_confirmation = models.BooleanField(default=False)
    payment_status = models.CharField(
        max_length=12, choices=PaymentStatus.choices, default=PaymentStatus.RELEASED
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    currency = models.CharField(max_length=8, default="KES")
    credential_hash = models.CharField(max_length=64, db_index=True)
    on_chain_reference = models.CharField(max_length=80, blank=True, default="")
    transaction_hash = models.CharField(max_length=64, blank=True, default="")
    release_tx_hash = models.CharField(max_length=64, blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    data_source = models.CharField(
        max_length=16, choices=DataSource.choices, default=DataSource.LIVE
    )
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issued_at"]

    def __str__(self):
        return f"Credential {str(self.credential_id)[:8]} for {self.worker_id}"

    @property
    def short_id(self) -> str:
        return str(self.credential_id)[:8].upper()

    @property
    def anchored(self) -> bool:
        return bool(self.transaction_hash)
