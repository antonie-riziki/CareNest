"""
Persistent agent memory.

Memory is keyed by *scope*: one record per engagement ("engagement:<pk>") plus
one per user for pre-engagement context ("user:<pk>"). Loading a memory in a
new session gives the agent everything it needs to continue: workflow stage,
last known contract state, previous actions, unresolved tasks and the
important decisions it made earlier.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.contracts.models import Contract

from .models import AgentMemory
from .state import EngagementStatus

MAX_ACTIONS = 40
MAX_DECISIONS = 30


def engagement_scope(engagement: Contract | int) -> str:
    pk = engagement.pk if isinstance(engagement, Contract) else int(engagement)
    return f"engagement:{pk}"


def user_scope(user) -> str:
    return f"user:{user.pk}"


def load_for_engagement(engagement: Contract) -> AgentMemory:
    memory, created = AgentMemory.objects.get_or_create(
        scope_key=engagement_scope(engagement),
        defaults={
            "engagement": engagement,
            "user": engagement.employer,
            "user_role": "employer",
            "workflow_stage": _stage_for_status(engagement.chain_status),
            "contract_state": engagement.chain_status,
            "engagement_context": {
                "engagement_pk": engagement.pk,
                "engagement_id": engagement.engagement_id,
                "job_title": engagement.job.title,
                "job_type": engagement.job.job_type,
                "location": engagement.job.location,
                "amount": str(engagement.amount),
                "currency": engagement.currency,
                "worker_user_id": engagement.worker_id,
                "employer_user_id": engagement.employer_id,
                "worker_display": engagement.worker.get_full_name() or engagement.worker.username,
                "employer_display": engagement.employer.get_full_name() or engagement.employer.username,
                "data_source": engagement.data_source,
            },
        },
    )
    if created:
        memory.summary = f"Engagement created for {memory.engagement_context['job_title']}."
        memory.save(update_fields=["summary"])
    return memory


def load_for_user(user) -> AgentMemory:
    memory, _ = AgentMemory.objects.get_or_create(
        scope_key=user_scope(user),
        defaults={
            "user": user,
            "user_role": getattr(user, "role", ""),
            "workflow_stage": "intake",
            "contract_state": EngagementStatus.DRAFT,
            "engagement_context": {"user_id": user.pk},
        },
    )
    return memory


def _stage_for_status(status: str) -> str:
    return {
        EngagementStatus.DRAFT: "terms_prepared",
        EngagementStatus.CREATED: "awaiting_funding",
        EngagementStatus.FUNDED: "active",
        EngagementStatus.WORKING: "active",
        EngagementStatus.SUBMITTED: "awaiting_review",
        EngagementStatus.APPROVAL_PENDING: "awaiting_employer_approval",
        EngagementStatus.DISPUTED: "dispute",
        EngagementStatus.RELEASED: "completed",
        EngagementStatus.CANCELLED: "closed",
    }.get(status, "intake")


@transaction.atomic
def remember(
    memory: AgentMemory,
    *,
    action: str | None = None,
    decision: dict[str, Any] | None = None,
    contract_state: str | None = None,
    add_tasks: list[str] | None = None,
    resolve_tasks: list[str] | None = None,
    context_updates: dict[str, Any] | None = None,
    summary: str | None = None,
    last_event_ledger: int | None = None,
) -> AgentMemory:
    """Append to memory and bump the version. All fields optional."""
    memory = AgentMemory.objects.select_for_update().get(pk=memory.pk)
    now = timezone.now().isoformat(timespec="seconds")

    if action:
        actions = list(memory.previous_actions)
        actions.append({"at": now, "action": action})
        memory.previous_actions = actions[-MAX_ACTIONS:]

    if decision:
        decisions = list(memory.important_decisions)
        decisions.append({"at": now, **decision})
        memory.important_decisions = decisions[-MAX_DECISIONS:]

    if contract_state and contract_state != memory.contract_state:
        memory.contract_state = contract_state
        memory.workflow_stage = _stage_for_status(contract_state)

    tasks = list(memory.unresolved_tasks)
    for t in resolve_tasks or []:
        tasks = [x for x in tasks if x != t]
    for t in add_tasks or []:
        if t not in tasks:
            tasks.append(t)
    memory.unresolved_tasks = tasks

    if context_updates:
        ctx = dict(memory.engagement_context)
        ctx.update(context_updates)
        memory.engagement_context = ctx

    if summary is not None:
        memory.summary = summary

    if last_event_ledger is not None:
        memory.last_event_ledger = max(last_event_ledger, memory.last_event_ledger or 0)

    memory.version += 1
    memory.save()
    return memory


def snapshot(memory: AgentMemory) -> dict[str, Any]:
    """Compact, JSON-safe view used by the UI and by the explain prompt."""
    return {
        "scope": memory.scope_key,
        "version": memory.version,
        "role": memory.user_role,
        "workflow_stage": memory.workflow_stage,
        "contract_state": memory.contract_state,
        "context": memory.engagement_context,
        "unresolved_tasks": memory.unresolved_tasks,
        "recent_actions": memory.previous_actions[-6:],
        "important_decisions": memory.important_decisions[-5:],
        "summary": memory.summary,
        "updated_at": memory.updated_at.isoformat(timespec="seconds") if memory.updated_at else None,
    }
