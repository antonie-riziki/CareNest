from django.contrib import admin

from .models import AgentAction, AgentApproval, AgentDecision, AgentMemory, AgentSession, StellarEvent, WorkCredential


@admin.register(AgentSession)
class AgentSessionAdmin(admin.ModelAdmin):
    list_display = ("session_key", "user", "role", "channel", "engagement", "started_at", "ended_at")
    list_filter = ("channel", "role")
    search_fields = ("session_key", "user__username")


@admin.register(AgentMemory)
class AgentMemoryAdmin(admin.ModelAdmin):
    list_display = ("scope_key", "user", "workflow_stage", "contract_state", "version", "updated_at")
    list_filter = ("workflow_stage", "contract_state")
    search_fields = ("scope_key",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(AgentDecision)
class AgentDecisionAdmin(admin.ModelAdmin):
    list_display = ("short_id", "timestamp", "decision_type", "engagement", "approval_required", "approval_status", "financial", "financial_action_executed", "tx_hash", "data_source")
    list_filter = ("decision_type", "approval_status", "financial", "data_source", "llm_used")
    search_fields = ("decision_id", "decision", "tx_hash")
    readonly_fields = ("decision_id", "timestamp")


@admin.register(AgentAction)
class AgentActionAdmin(admin.ModelAdmin):
    list_display = ("tool_name", "created_at", "success", "duration_ms", "engagement", "user")
    list_filter = ("tool_name", "success")


@admin.register(AgentApproval)
class AgentApprovalAdmin(admin.ModelAdmin):
    list_display = ("approval_type", "status", "engagement", "requested_from", "decided_by", "decided_at", "tx_hash", "data_source")
    list_filter = ("approval_type", "status", "data_source")


@admin.register(StellarEvent)
class StellarEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "chain_engagement_id", "ledger", "tx_hash", "data_source", "processed", "ingested_at")
    list_filter = ("event_type", "data_source", "processed")
    search_fields = ("tx_hash", "event_id")


@admin.register(WorkCredential)
class WorkCredentialAdmin(admin.ModelAdmin):
    list_display = ("short_id", "worker", "job_type", "payment_status", "employer_confirmation", "transaction_hash", "data_source", "issued_at")
    list_filter = ("payment_status", "completion_status", "data_source")
    search_fields = ("credential_id", "credential_hash", "transaction_hash")
