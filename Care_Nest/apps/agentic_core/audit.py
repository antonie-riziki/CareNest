"""
Audit trail helpers.

Every meaningful agent decision -> ``AgentDecision``.
Every tool call -> ``AgentAction``.
Every financial gate -> ``AgentApproval``.

These records are a product feature (the Command Center renders them), so they
are written synchronously inside the request/transaction rather than fire-and-forget.
"""

from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

from .models import AgentAction, AgentApproval, AgentDecision, AgentSession, DataSource

logger = logging.getLogger("carenest.agent.audit")

# Keys that must never be persisted in audit payloads.
_SENSITIVE_KEYS = {"secret", "secret_key", "seed", "mnemonic", "private_key", "password", "api_key", "token_secret", "authorization"}


def sanitize(payload: Any) -> Any:
    """Recursively strip secrets from dict/list payloads before persisting."""
    if isinstance(payload, dict):
        return {
            k: ("[REDACTED]" if str(k).lower() in _SENSITIVE_KEYS or str(k).lower().endswith("_secret") else sanitize(v))
            for k, v in payload.items()
        }
    if isinstance(payload, (list, tuple)):
        return [sanitize(v) for v in payload]
    if isinstance(payload, str) and payload.startswith("S") and len(payload) == 56 and payload.isupper():
        # Looks like a Stellar secret seed.
        return "[REDACTED_SEED]"
    return payload


def record_decision(
    *,
    decision_type: str,
    decision: str,
    session: AgentSession | None = None,
    user=None,
    engagement=None,
    context: dict[str, Any] | None = None,
    evidence: list[str] | None = None,
    rationale: str = "",
    action: str = "",
    approval_required: bool = False,
    financial: bool = False,
    result: dict[str, Any] | None = None,
    tx_hash: str = "",
    data_source: str = DataSource.OFFCHAIN,
    model_used: str = "",
    llm_used: bool = False,
) -> AgentDecision:
    record = AgentDecision.objects.create(
        session=session,
        user=user,
        engagement=engagement,
        decision_type=decision_type,
        context=sanitize(context or {}),
        evidence=list(evidence or []),
        decision=decision,
        rationale=rationale,
        action=action,
        approval_required=approval_required,
        approval_status=(
            AgentDecision.ApprovalStatus.PENDING if approval_required else AgentDecision.ApprovalStatus.NOT_REQUIRED
        ),
        financial=financial,
        financial_action_executed=False,
        result=sanitize(result or {}),
        tx_hash=tx_hash,
        data_source=data_source,
        model_used=model_used,
        llm_used=llm_used,
    )
    logger.info(
        "decision=%s type=%s engagement=%s approval_required=%s financial=%s",
        record.short_id,
        decision_type,
        getattr(engagement, "pk", None),
        approval_required,
        financial,
    )
    return record


def record_action(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    result: dict[str, Any] | None,
    success: bool,
    error: str = "",
    duration_ms: int = 0,
    session: AgentSession | None = None,
    decision: AgentDecision | None = None,
    user=None,
    engagement=None,
) -> AgentAction:
    return AgentAction.objects.create(
        session=session,
        decision=decision,
        user=user,
        engagement=engagement,
        tool_name=tool_name,
        arguments=sanitize(arguments),
        result=sanitize(result or {}),
        success=success,
        error=error[:2000],
        duration_ms=duration_ms,
    )


def open_approval(
    *,
    decision: AgentDecision,
    approval_type: str,
    requested_from,
    summary: str,
    prepared_action: dict[str, Any],
    engagement=None,
    data_source: str = DataSource.LIVE,
) -> AgentApproval:
    approval = AgentApproval.objects.create(
        decision=decision,
        engagement=engagement,
        approval_type=approval_type,
        requested_from=requested_from,
        summary=summary,
        prepared_action=sanitize(prepared_action),
        data_source=data_source,
    )
    decision.approval_required = True
    decision.approval_status = AgentDecision.ApprovalStatus.PENDING
    decision.save(update_fields=["approval_required", "approval_status"])
    return approval


def mark_approval(approval: AgentApproval, *, status: str, decided_by=None, note: str = "", tx_hash: str = "") -> AgentApproval:
    approval.status = status
    approval.decided_by = decided_by or approval.decided_by
    approval.decided_at = approval.decided_at or timezone.now()
    if note:
        approval.note = note
    if tx_hash:
        approval.tx_hash = tx_hash
    if status == AgentApproval.Status.EXECUTED:
        approval.executed_at = timezone.now()
    approval.save()

    decision = approval.decision
    mapping = {
        AgentApproval.Status.APPROVED: AgentDecision.ApprovalStatus.APPROVED,
        AgentApproval.Status.REJECTED: AgentDecision.ApprovalStatus.REJECTED,
        AgentApproval.Status.EXECUTED: AgentDecision.ApprovalStatus.EXECUTED,
        AgentApproval.Status.FAILED: AgentDecision.ApprovalStatus.FAILED,
    }
    if status in mapping:
        decision.approval_status = mapping[status]
        if status == AgentApproval.Status.EXECUTED:
            decision.financial_action_executed = approval.is_financial
            decision.tx_hash = tx_hash or decision.tx_hash
        decision.save(update_fields=["approval_status", "financial_action_executed", "tx_hash"])
    return approval
