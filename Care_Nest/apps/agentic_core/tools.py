"""
Controlled tool layer.

Every tool is a plain function registered in ``TOOLS``. ``ToolRunner.run``
executes it, times it and writes an ``AgentAction`` row — no tool can be
invoked by the agent without leaving a trace.

Tools only *read* CareNest state or *prepare* actions. The only tools that
write are non-financial (job drafts, approval requests, credentials, audit
records). Nothing here signs or submits a blockchain transaction.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Any, Callable

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from apps.contracts.models import Contract
from apps.jobs.models import Job
from apps.profiles.models import EmployerProfile, WorkerProfile
from apps.wallet.models import Wallet

from . import audit, stellar
from .models import AgentApproval, AgentDecision, AgentSession, DataSource, StellarEvent, WorkCredential
from .schemas import SKILL_HINTS, ContractTerms, ParsedRequirement, PreparedAction, ToolResult, WorkerMatch
from .state import EngagementStatus

logger = logging.getLogger("carenest.agent.tools")
User = get_user_model()

ToolFn = Callable[..., ToolResult]
TOOLS: dict[str, ToolFn] = {}


def tool(name: str):
    def deco(fn: ToolFn) -> ToolFn:
        TOOLS[name] = fn
        fn.tool_name = name  # type: ignore[attr-defined]
        return fn

    return deco


class ToolRunner:
    """Executes tools with mandatory logging."""

    def __init__(self, *, session: AgentSession | None = None, user=None, engagement: Contract | None = None, decision: AgentDecision | None = None):
        self.session = session
        self.user = user
        self.engagement = engagement
        self.decision = decision

    def run(self, name: str, **kwargs: Any) -> ToolResult:
        if name not in TOOLS:
            result = ToolResult(ok=False, error=f"unknown tool {name}")
            audit.record_action(tool_name=name, arguments=kwargs, result=result.to_dict(), success=False, error=result.error, session=self.session, decision=self.decision, user=self.user, engagement=self.engagement)
            return result
        started = time.monotonic()
        try:
            result = TOOLS[name](**kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.exception("tool %s failed", name)
            result = ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        duration = int((time.monotonic() - started) * 1000)
        audit.record_action(
            tool_name=name,
            arguments=kwargs,
            result=result.to_dict(),
            success=result.ok,
            error=result.error,
            duration_ms=duration,
            session=self.session,
            decision=self.decision,
            user=self.user,
            engagement=self.engagement or _engagement_from_kwargs(kwargs),
        )
        return result


def _engagement_from_kwargs(kwargs: dict[str, Any]) -> Contract | None:
    pk = kwargs.get("engagement_pk")
    if pk:
        return Contract.objects.filter(pk=pk).first()
    return None


def _display(user) -> str:
    return user.get_full_name() or user.username


def _wallet_address(user) -> str:
    w = Wallet.objects.filter(user=user).first()
    return w.stellar_address if w else ""


# ---------------------------------------------------------------------------
# Context tools
# ---------------------------------------------------------------------------


@tool("get_user_context")
def get_user_context(*, user_id: int) -> ToolResult:
    user = User.objects.filter(pk=user_id).first()
    if not user:
        return ToolResult(ok=False, error="user not found")
    role = getattr(user, "role", "")
    engagements = Contract.objects.filter(Q(employer=user) | Q(worker=user)).select_related("job")
    return ToolResult(
        ok=True,
        data={
            "user_id": user.pk,
            "display_name": _display(user),
            "role": role,
            "wallet_address": _wallet_address(user),
            "engagements": [
                {
                    "pk": c.pk,
                    "engagement_id": c.engagement_id,
                    "title": c.job.title,
                    "status": c.chain_status,
                    "counterparty": _display(c.worker if c.employer_id == user.pk else c.employer),
                    "data_source": c.data_source,
                }
                for c in engagements[:20]
            ],
        },
    )


@tool("get_worker_profile")
def get_worker_profile(*, user_id: int) -> ToolResult:
    profile = WorkerProfile.objects.filter(user_id=user_id).select_related("user").first()
    if not profile:
        return ToolResult(ok=False, error="worker profile not found")
    credentials = WorkCredential.objects.filter(worker_id=user_id).count()
    return ToolResult(
        ok=True,
        data={
            "user_id": user_id,
            "display_name": _display(profile.user),
            "skills": _skills_list(profile.skills),
            "rating": profile.rating,
            "verified": profile.verified,
            "verified_engagements": credentials,
            "wallet_address": _wallet_address(profile.user),
        },
    )


@tool("get_employer_profile")
def get_employer_profile(*, user_id: int) -> ToolResult:
    profile = EmployerProfile.objects.filter(user_id=user_id).select_related("user").first()
    if not profile:
        return ToolResult(ok=False, error="employer profile not found")
    return ToolResult(
        ok=True,
        data={
            "user_id": user_id,
            "display_name": _display(profile.user),
            "rating": profile.rating,
            "wallet_address": _wallet_address(profile.user),
            "active_engagements": Contract.objects.filter(employer_id=user_id, chain_status__in=list(EngagementStatus.FUNDS_IN_ESCROW)).count(),
        },
    )


def _skills_list(raw: str | list) -> list[str]:
    if isinstance(raw, list):
        return [str(s).strip().lower() for s in raw if str(s).strip()]
    return [s.strip().lower() for s in str(raw or "").replace(";", ",").split(",") if s.strip()]


# ---------------------------------------------------------------------------
# Marketplace tools
# ---------------------------------------------------------------------------


@tool("search_jobs")
def search_jobs(*, query: str = "", location: str = "", limit: int = 10) -> ToolResult:
    qs = Job.objects.all()
    if query:
        qs = qs.filter(Q(title__icontains=query) | Q(job_type__icontains=query) | Q(description__icontains=query))
    if location:
        qs = qs.filter(location__icontains=location)
    jobs = [
        {"id": j.pk, "title": j.title, "job_type": j.job_type, "location": j.location, "pay": str(j.pay), "employer_id": j.employer_id}
        for j in qs.order_by("-created_at")[: max(1, min(limit, 50))]
    ]
    return ToolResult(ok=True, data={"count": len(jobs), "jobs": jobs})


@tool("search_workers")
def search_workers(*, job_type: str, required_skills: list[str] | None = None, location: str = "", limit: int = 5) -> ToolResult:
    """Rank worker profiles for a parsed requirement. Deterministic scoring so it is explainable."""
    required = {s.lower() for s in (required_skills or [])} | set(SKILL_HINTS.get(job_type, [])[:2]) | {job_type.lower()}
    matches: list[WorkerMatch] = []
    for profile in WorkerProfile.objects.select_related("user").all():
        if profile.user.role != "worker":
            continue
        skills = set(_skills_list(profile.skills))
        overlap = required & skills
        type_match = job_type.lower() in skills or any(job_type.lower() in s for s in skills)
        score = 0.0
        reasons: list[str] = []
        if type_match:
            score += 40
            reasons.append(f"lists {job_type} experience")
        if overlap:
            score += 10 * len(overlap)
            reasons.append("skills: " + ", ".join(sorted(overlap)))
        score += min(profile.rating, 5) * 6
        if profile.rating:
            reasons.append(f"rating {profile.rating:.1f}")
        if profile.verified:
            score += 10
            reasons.append("identity verified")
        creds = WorkCredential.objects.filter(worker=profile.user).count()
        if creds:
            score += min(creds, 5) * 4
            reasons.append(f"{creds} verified engagement(s) in Work Passport")
        wallet = bool(_wallet_address(profile.user))
        if wallet:
            score += 5
            reasons.append("wallet connected")
        if score <= 0:
            continue
        matches.append(
            WorkerMatch(
                user_id=profile.user_id,
                display_name=_display(profile.user),
                skills=sorted(skills),
                rating=profile.rating,
                verified=profile.verified,
                wallet_connected=wallet,
                score=round(score, 1),
                reasons=reasons,
            )
        )
    matches.sort(key=lambda m: m.score, reverse=True)
    return ToolResult(ok=True, data={"count": len(matches), "matches": [m.to_dict() for m in matches[: max(1, min(limit, 20))]]})


@tool("create_job_draft")
def create_job_draft(*, employer_id: int, requirement: dict[str, Any]) -> ToolResult:
    employer = User.objects.filter(pk=employer_id, role="employer").first()
    if not employer:
        return ToolResult(ok=False, error="employer not found")
    parsed = ParsedRequirement(**{k: v for k, v in requirement.items() if k in ParsedRequirement.__dataclass_fields__})
    if isinstance(parsed.pay_amount, str):
        parsed.pay_amount = Decimal(parsed.pay_amount) if parsed.pay_amount not in ("", "None") else None
    job = Job.objects.create(
        employer=employer,
        title=parsed.title,
        description=parsed.raw_text or parsed.title,
        location=parsed.location or "Nairobi",
        latitude=-1.2634,  # Westlands default; refined later in the UI
        longitude=36.8036,
        pay=parsed.pay_amount or Decimal("0"),
        job_type=parsed.job_type,
    )
    return ToolResult(ok=True, data={"job_id": job.pk, "title": job.title, "job_type": job.job_type, "location": job.location, "pay": str(job.pay)})


@tool("prepare_contract_terms")
def prepare_contract_terms(*, employer_id: int, worker_id: int, job_id: int, requirement: dict[str, Any]) -> ToolResult:
    """Create a DRAFT engagement with hashed off-chain terms. Nothing on-chain."""
    employer = User.objects.filter(pk=employer_id).first()
    worker = User.objects.filter(pk=worker_id).first()
    job = Job.objects.filter(pk=job_id).first()
    if not (employer and worker and job):
        return ToolResult(ok=False, error="employer, worker or job missing")
    if employer.pk == worker.pk:
        return ToolResult(ok=False, error="employer and worker must differ")

    cfg = stellar.get_config()
    pay_amount = Decimal(str(requirement.get("pay_amount") or job.pay or 0))
    # Demo conversion: 1 XLM-equivalent escrow unit per KES 1,000 keeps testnet amounts tiny.
    escrow_units = _escrow_units_for(pay_amount, requirement.get("pay_currency", "KES"))
    scope = _scope_for(requirement.get("job_type", job.job_type))
    terms = ContractTerms(
        job_type=requirement.get("job_type", job.job_type),
        schedule=requirement.get("schedule", "full-time"),
        location=job.location,
        pay_amount=str(pay_amount),
        pay_currency=requirement.get("pay_currency", "KES"),
        pay_period=requirement.get("pay_period", "month"),
        scope=scope,
        start_date=requirement.get("start_date", ""),
        duration=requirement.get("duration", "1 month"),
        employer_user_id=employer.pk,
        worker_user_id=worker.pk,
        escrow_token=cfg.token_contract_id,
        escrow_amount_base_units=escrow_units,
    )
    terms_hash = stellar.sha256_hex(terms.to_dict())
    contract = Contract.objects.create(
        job=job,
        worker=worker,
        employer=employer,
        scope="\n".join(scope),
        status="draft",
        chain_status=EngagementStatus.DRAFT,
        amount=pay_amount,
        currency=terms.pay_currency,
        token_amount=escrow_units,
        token_symbol=cfg.token_symbol,
        employer_wallet=_wallet_address(employer),
        worker_wallet=_wallet_address(worker),
        terms_hash=terms_hash,
        contract_address=cfg.contract_id,
        data_source=cfg.data_source,
        start_date=timezone.now(),
    )
    return ToolResult(
        ok=True,
        data={
            "engagement_pk": contract.pk,
            "terms": terms.to_dict(),
            "terms_hash": terms_hash,
            "escrow_amount_base_units": escrow_units,
            "token_symbol": cfg.token_symbol,
            "data_source": contract.data_source,
        },
    )


def _escrow_units_for(pay_amount: Decimal, currency: str) -> int:
    """Testnet demo sizing. Real deployments would use a KES stablecoin SAC 1:1."""
    if currency.upper() == "KES":
        xlm = (pay_amount / Decimal(1000)).quantize(Decimal("0.0000001"))
    else:
        xlm = (pay_amount / Decimal(10)).quantize(Decimal("0.0000001"))
    xlm = max(xlm, Decimal("1"))
    return stellar.to_base_units(xlm)


def _scope_for(job_type: str) -> list[str]:
    base = {
        "nanny": ["Daily childcare and supervision", "Meal preparation for children", "School run support", "Weekly progress note to employer"],
        "housekeeper": ["Daily cleaning and floor care", "Laundry and ironing weekly", "Kitchen hygiene", "Grocery list management"],
        "caregiver": ["Daily personal care support", "Medication reminders", "Mobility assistance", "Daily wellbeing note"],
        "chef": ["Meal planning weekly", "Daily meal preparation", "Kitchen hygiene and inventory"],
        "gardener": ["Lawn care and pruning", "Irrigation checks", "Waste management"],
    }
    return base.get(job_type, ["Agreed household duties", "Weekly summary to employer"])


# ---------------------------------------------------------------------------
# Chain / wallet read tools
# ---------------------------------------------------------------------------


@tool("get_contract_state")
def get_contract_state(*, engagement_pk: int, reconcile: bool = False) -> ToolResult:
    c = Contract.objects.filter(pk=engagement_pk).select_related("job").first()
    if not c:
        return ToolResult(ok=False, error="engagement not found")
    on_chain = None
    if reconcile and c.engagement_id is not None and not c.is_demo:
        on_chain = stellar.read_agreement(c.engagement_id)
        if on_chain and on_chain.get("status") in EngagementStatus.ON_CHAIN and on_chain["status"] != c.chain_status:
            c.chain_status = on_chain["status"]
            c.save(update_fields=["chain_status", "updated_at"])
    events = list(StellarEvent.objects.filter(engagement=c).order_by("ledger", "id").values("event_type", "tx_hash", "ledger", "data_source"))
    return ToolResult(
        ok=True,
        data={
            "engagement_pk": c.pk,
            "engagement_id": c.engagement_id,
            "status": c.chain_status,
            "amount": str(c.amount),
            "currency": c.currency,
            "escrow": c.token_amount_display,
            "employer_wallet": c.employer_wallet,
            "worker_wallet": c.worker_wallet,
            "terms_hash": c.terms_hash,
            "tx": {"create": c.create_tx_hash, "fund": c.fund_tx_hash, "submit": c.submit_tx_hash, "release": c.release_tx_hash, "credential": c.credential_tx_hash},
            "events": events,
            "on_chain": on_chain,
            "data_source": c.data_source,
        },
    )


@tool("get_wallet_state")
def get_wallet_state(*, user_id: int) -> ToolResult:
    w = Wallet.objects.filter(user_id=user_id).first()
    if not w:
        return ToolResult(ok=True, data={"connected": False})
    return ToolResult(ok=True, data={"connected": w.is_connected, "address": w.stellar_address, "provider": w.wallet_provider, "connected_at": w.connected_at.isoformat() if w.connected_at else None})


@tool("read_stellar_events")
def read_stellar_events(*, engagement_pk: int | None = None, limit: int = 50) -> ToolResult:
    qs = StellarEvent.objects.all()
    if engagement_pk:
        qs = qs.filter(engagement_id=engagement_pk)
    rows = [
        {
            "event_type": e.event_type,
            "engagement_id": e.chain_engagement_id,
            "tx_hash": e.tx_hash,
            "ledger": e.ledger,
            "closed_at": e.ledger_closed_at.isoformat() if e.ledger_closed_at else None,
            "data_source": e.data_source,
        }
        for e in qs.order_by("-ledger", "-id")[: max(1, min(limit, 200))]
    ]
    return ToolResult(ok=True, data={"count": len(rows), "events": rows})


# ---------------------------------------------------------------------------
# Action-preparing tools (never execute financial calls)
# ---------------------------------------------------------------------------


@tool("request_employer_approval")
def request_employer_approval(*, engagement_pk: int, approval_type: str, decision_id: str, summary: str, prepared_action: dict[str, Any]) -> ToolResult:
    c = Contract.objects.filter(pk=engagement_pk).first()
    decision = AgentDecision.objects.filter(decision_id=decision_id).first()
    if not (c and decision):
        return ToolResult(ok=False, error="engagement or decision missing")
    existing = AgentApproval.objects.filter(engagement=c, approval_type=approval_type, status=AgentApproval.Status.PENDING).first()
    if existing:
        return ToolResult(ok=True, data={"approval_id": existing.pk, "status": existing.status, "already_pending": True})
    approval = audit.open_approval(
        decision=decision,
        approval_type=approval_type,
        requested_from=c.employer,
        summary=summary,
        prepared_action=prepared_action,
        engagement=c,
        data_source=DataSource.DEMO if c.is_demo else DataSource.LIVE,
    )
    return ToolResult(ok=True, data={"approval_id": approval.pk, "status": approval.status, "already_pending": False})


@tool("submit_work_status")
def submit_work_status(*, engagement_pk: int, worker_id: int, report: str) -> ToolResult:
    """Prepare the worker's submit_work call (worker signs). Records the report hash off-chain."""
    c = Contract.objects.filter(pk=engagement_pk, worker_id=worker_id).first()
    if not c:
        return ToolResult(ok=False, error="engagement not found for worker")
    work_hash = stellar.sha256_hex({"engagement": c.pk, "report": report, "at": timezone.now().date().isoformat()})
    c.work_hash = work_hash
    c.save(update_fields=["work_hash", "updated_at"])
    action = PreparedAction(function="submit_work", args={"id": c.engagement_id, "work_hash": work_hash}, signer_role="worker", financial=False, description="Worker submits completion report hash", engagement_pk=c.pk, engagement_id=c.engagement_id)
    return ToolResult(ok=True, data={"prepared_action": action.to_dict(), "work_hash": work_hash})


@tool("open_dispute")
def open_dispute(*, engagement_pk: int, user_id: int, reason: str) -> ToolResult:
    c = Contract.objects.filter(pk=engagement_pk).filter(Q(worker_id=user_id) | Q(employer_id=user_id)).first()
    if not c:
        return ToolResult(ok=False, error="engagement not found for user")
    reason_hash = stellar.sha256_hex({"engagement": c.pk, "reason": reason})
    opener = c.worker_wallet if c.worker_id == user_id else c.employer_wallet
    action = PreparedAction(function="dispute", args={"id": c.engagement_id, "opener": opener, "reason_hash": reason_hash}, signer_role="worker" if c.worker_id == user_id else "employer", financial=False, description="Open a dispute (locks escrow until arbiter resolves)", engagement_pk=c.pk, engagement_id=c.engagement_id)
    return ToolResult(ok=True, data={"prepared_action": action.to_dict(), "reason_hash": reason_hash})


@tool("generate_work_credential")
def generate_work_credential(*, engagement_pk: int) -> ToolResult:
    from .services import issue_work_credential

    c = Contract.objects.filter(pk=engagement_pk).select_related("job", "worker", "employer").first()
    if not c:
        return ToolResult(ok=False, error="engagement not found")
    if c.chain_status != EngagementStatus.RELEASED:
        return ToolResult(ok=False, error=f"credential requires RELEASED status, got {c.chain_status}")
    credential, created = issue_work_credential(c)
    return ToolResult(ok=True, data={"credential_id": str(credential.credential_id), "credential_hash": credential.credential_hash, "created": created, "on_chain_reference": credential.on_chain_reference})


@tool("build_audit_record")
def build_audit_record(*, engagement_pk: int) -> ToolResult:
    c = Contract.objects.filter(pk=engagement_pk).first()
    if not c:
        return ToolResult(ok=False, error="engagement not found")
    decisions = AgentDecision.objects.filter(engagement=c).order_by("timestamp")
    approvals = AgentApproval.objects.filter(engagement=c).order_by("created_at")
    events = StellarEvent.objects.filter(engagement=c).order_by("ledger", "id")
    return ToolResult(
        ok=True,
        data={
            "engagement_pk": c.pk,
            "decisions": [{"id": str(d.decision_id), "type": d.decision_type, "at": d.timestamp.isoformat(), "approval_status": d.approval_status, "financial_executed": d.financial_action_executed, "tx_hash": d.tx_hash} for d in decisions],
            "approvals": [{"id": a.pk, "type": a.approval_type, "status": a.status, "tx_hash": a.tx_hash} for a in approvals],
            "events": [{"type": e.event_type, "tx_hash": e.tx_hash, "ledger": e.ledger, "source": e.data_source} for e in events],
        },
    )
