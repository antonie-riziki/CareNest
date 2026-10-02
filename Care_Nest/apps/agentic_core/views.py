"""
Views for the agent panel, command center, worker engagement, Work Passport and wallet.

Design rule: the agent recommends, the human approves, the blockchain executes.
The views make that split visible and never let a POST move funds without an
approved ``AgentApproval`` owned by the requesting user.
"""

from __future__ import annotations

import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.contracts.models import Contract
from apps.wallet.models import Wallet

from . import memory as memory_mod, services, stellar
from .agent import CareNestAgent, command_center_context
from .models import AgentApproval, AgentDecision, AgentSession, StellarEvent, WorkCredential
from .policies import PolicyViolation
from .schemas import PreparedAction
from .state import EngagementStatus, explain

logger = logging.getLogger("carenest.agent.views")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _is_employer(user) -> bool:
    return getattr(user, "role", "") == "employer" or user.is_staff


def _is_worker(user) -> bool:
    return getattr(user, "role", "") == "worker"


def _wallet(user) -> Wallet | None:
    return Wallet.objects.filter(user=user).first()


def _chain_banner() -> dict:
    cfg = stellar.get_config()
    return {
        "mode_label": cfg.mode_label,
        "is_live": cfg.configured,
        "network": cfg.network,
        "contract_id": cfg.contract_id,
        "contract_url": stellar.explorer_contract_url(cfg.contract_id) if cfg.contract_id else "",
        "token_symbol": cfg.token_symbol,
    }


def _employer_engagements(user):
    return Contract.objects.filter(employer=user).select_related("job", "worker").order_by("-updated_at")


def _enrich_candidates(candidates: list) -> list[dict]:
    from apps.profiles.avatars import avatar_url
    from apps.profiles.models import WorkerProfile

    rows = [dict(c) for c in candidates or [] if isinstance(c, dict)]
    ids = [c.get("user_id") for c in rows if c.get("user_id")]
    profiles = {
        p.user_id: p for p in WorkerProfile.objects.filter(user_id__in=ids).select_related("user")
    }
    enriched = []
    for row in rows:
        row.pop("email", None)
        row.pop("phone", None)
        profile = profiles.get(row.get("user_id"))
        if profile:
            row["photo_url"] = row.get("photo_url") or avatar_url(profile.user, profile)
            row["bio"] = row.get("bio") or (profile.bio or "")[:220]
            row["location_label"] = row.get("location_label") or profile.location_label or ""
            row["rating"] = row.get("rating") if row.get("rating") is not None else profile.rating
            row["verified"] = row.get("verified", profile.verified)
        enriched.append(row)
    return enriched


def _tx_payload(result: services.ExecutionResult) -> dict:
    return {
        "tx_hash": result.tx.tx_hash,
        "status": result.tx.status,
        "ledger": result.tx.ledger,
        "explorer_url": result.tx.explorer_url if result.tx.data_source != "DEMO_DATA" else "",
        "data_source": result.tx.data_source,
        "signed_by": result.signed_by,
        "engagement_status": result.contract.chain_status,
    }


# ---------------------------------------------------------------------------
# Employer: agent panel
# ---------------------------------------------------------------------------


@login_required(login_url="employer-signin")
def employer_agent_panel(request):
    if not _is_employer(request.user):
        return HttpResponseForbidden("Employer account required")
    user_mem = memory_mod.load_for_user(request.user)
    proposal = None
    answer = None

    if request.method == "POST" and request.POST.get("requirement"):
        agent = CareNestAgent.start(request.user, engagement=None, metadata={"view": "agent-panel"})
        try:
            proposal = agent.intake(request.POST["requirement"])
        finally:
            agent.end()
        job_id = (proposal.get("job") or {}).get("job_id")
        if job_id:
            from apps.jobs.images import ImageUploadError, attach_primary_image
            from apps.jobs.models import Job
            from apps.contracts.agreement import ensure_employer_terms

            job = Job.objects.filter(pk=job_id, employer=request.user).first()
            if job:
                terms = (request.POST.get("employer_terms") or "").strip()
                if terms:
                    job.employer_terms = terms
                    job.save(update_fields=["employer_terms", "updated_at"])
                else:
                    ensure_employer_terms(job)
                upload = request.FILES.get("image")
                if upload:
                    try:
                        attach_primary_image(job, upload)
                    except ImageUploadError as exc:
                        messages.error(request, str(exc))
                proposal["job"]["image_url"] = job.image_url
                proposal["job"]["cover_image"] = job.cover_image
                proposal["job"]["employer_terms"] = job.employer_terms
                try:
                    needed = max(1, min(int(request.POST.get("workers_needed") or 1), 20))
                except (TypeError, ValueError):
                    needed = 1
                if job.workers_needed != needed:
                    job.workers_needed = needed
                    job.save(update_fields=["workers_needed", "updated_at"])
                proposal["job"]["workers_needed"] = job.workers_needed
        user_mem.refresh_from_db()
    elif user_mem.engagement_context.get("pending_requirement") and user_mem.engagement_context.get("candidates") is not None and not user_mem.engagement_context.get("active_engagement_pk"):
        proposal = {
            "requirement": user_mem.engagement_context["pending_requirement"],
            "job": {"job_id": user_mem.engagement_context.get("pending_job_id")},
            "candidates": user_mem.engagement_context.get("candidates", []),
            "summary": user_mem.summary,
            "decisions": [],
        }

    pending_job = None
    pending_job_id = None
    if proposal and proposal.get("job"):
        pending_job_id = proposal["job"].get("job_id")
    elif user_mem.engagement_context.get("pending_job_id"):
        pending_job_id = user_mem.engagement_context.get("pending_job_id")
    if pending_job_id:
        from apps.jobs.models import Job

        pending_job = Job.objects.filter(pk=pending_job_id, employer=request.user).first()
        if pending_job and pending_job.status not in (Job.Status.DRAFT, Job.Status.SCHEDULED):
            memory_mod.clear_pending_intake(request.user)
            user_mem.refresh_from_db()
            pending_job = None
            proposal = None
        elif pending_job and proposal:
            proposal.setdefault("job", {})
            proposal["job"]["cover_image"] = pending_job.cover_image
            proposal["job"]["image_url"] = pending_job.image_url
            proposal["job"]["employer_terms"] = pending_job.employer_terms
            proposal["job"]["workers_needed"] = pending_job.workers_needed
    if proposal and proposal.get("candidates") is not None:
        proposal["candidates"] = _enrich_candidates(proposal.get("candidates") or [])

    engagements = _employer_engagements(request.user)
    pending = AgentApproval.objects.filter(requested_from=request.user, status=AgentApproval.Status.PENDING).select_related("engagement", "engagement__worker", "engagement__job")
    recent_decisions = AgentDecision.objects.filter(Q(user=request.user) | Q(engagement__employer=request.user)).select_related("engagement")[:8]
    context = {
        "chain": _chain_banner(),
        "wallet": _wallet(request.user),
        "proposal": proposal,
        "answer": answer,
        "engagements": engagements,
        "pending_approvals": pending,
        "recent_decisions": recent_decisions,
        "memory": memory_mod.snapshot(user_mem),
        "example_requirement": "I need a full-time nanny in Westlands, KES 45,000/month.",
        "pending_job": pending_job,
    }
    return render(request, "agent/employer_agent_panel.html", context)


@login_required
@require_POST
def ask_agent(request):
    question = request.POST.get("question", "")
    engagement_pk = request.POST.get("engagement_pk")
    engagement = None
    if engagement_pk:
        engagement = Contract.objects.filter(pk=engagement_pk).filter(Q(employer=request.user) | Q(worker=request.user)).first()
    agent = CareNestAgent.start(request.user, engagement=engagement, metadata={"view": "ask"})
    try:
        result = agent.answer(question)
    finally:
        agent.end()
    if request.headers.get("x-requested-with") == "fetch":
        return JsonResponse({"answer": result["answer"], "facts": result["facts"], "memory": result["memory"], "used_llm": result.get("used_llm"), "model": result.get("model"), "engagement_pk": result["engagement"].pk if result["engagement"] else None})
    request.session["agent_last_answer"] = {"question": question, "answer": result["answer"], "facts": result["facts"], "memory": result["memory"], "used_llm": result.get("used_llm"), "model": result.get("model")}
    if result["engagement"] is not None and _is_employer(request.user):
        return redirect("agent-command-center", pk=result["engagement"].pk)
    if _is_worker(request.user):
        return redirect("worker-engagements")
    return redirect("agent-panel")


@login_required(login_url="employer-signin")
@require_POST
def prepare_engagement(request):
    if not _is_employer(request.user):
        return HttpResponseForbidden("Employer account required")
    user_mem = memory_mod.load_for_user(request.user)
    requirement = user_mem.engagement_context.get("pending_requirement")
    job_id = request.POST.get("job_id") or user_mem.engagement_context.get("pending_job_id")
    worker_id = request.POST.get("worker_id")
    if not (requirement and job_id and worker_id):
        messages.error(request, "Describe your requirement and choose a worker first.")
        return redirect("agent-panel")
    agent = CareNestAgent.start(request.user, metadata={"view": "prepare"})
    try:
        contract = agent.prepare_engagement(worker_id=int(worker_id), job_id=int(job_id), requirement=requirement)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("agent-panel")
    finally:
        agent.end()
    messages.success(request, "Draft offer prepared. The worker still adds their terms before you can bind the contract.")
    return redirect("employer-engagement-review", pk=contract.pk)


@login_required(login_url="employer-signin")
@require_POST
def publish_job_offer(request):
    if not _is_employer(request.user):
        return HttpResponseForbidden("Employer account required")
    from django.utils import timezone

    from apps.contracts.agreement import ensure_employer_terms
    from apps.jobs.images import ImageUploadError, attach_primary_image
    from apps.jobs.models import Job

    job_id = request.POST.get("job_id")
    job = get_object_or_404(Job, pk=job_id, employer=request.user)
    if job.locked:
        messages.error(request, "This job is already locked.")
        return redirect("agent-panel")
    terms = (request.POST.get("employer_terms") or "").strip()
    if terms:
        job.employer_terms = terms
        job.save(update_fields=["employer_terms", "updated_at"])
    else:
        ensure_employer_terms(job)
    upload = request.FILES.get("image")
    if upload:
        try:
            attach_primary_image(job, upload)
        except ImageUploadError as exc:
            messages.error(request, str(exc))
            return redirect("agent-panel")
    job.status = Job.Status.ACTIVE
    job.published_at = job.published_at or timezone.now()
    job.save(update_fields=["status", "published_at", "updated_at"])
    memory_mod.clear_pending_intake(request.user)
    messages.success(request, "Job posted. Workers can review your terms, add theirs, and send them back for your approval.")
    return redirect(f"/employer-job-details/?id={job.pk}")


# ---------------------------------------------------------------------------
# Command center
# ---------------------------------------------------------------------------


@login_required(login_url="employer-signin")
def command_center(request, pk: int):
    contract = get_object_or_404(Contract.objects.select_related("job", "worker", "employer"), pk=pk)
    if not (request.user.is_staff or contract.employer_id == request.user.pk or contract.worker_id == request.user.pk):
        return HttpResponseForbidden("Not your engagement")
    ctx = command_center_context(contract)
    ctx.update(
        {
            "chain": _chain_banner(),
            "wallet": _wallet(request.user),
            "is_employer": contract.employer_id == request.user.pk or request.user.is_staff,
            "last_answer": request.session.pop("agent_last_answer", None),
            "explorer_tx": stellar.explorer_tx_url,
            "tx_links": {name: (h, stellar.explorer_tx_url(h) if not contract.is_demo else "") for name, h in (("Agreement created", contract.create_tx_hash), ("Escrow funded", contract.fund_tx_hash), ("Work submitted", contract.submit_tx_hash), ("Payment released", contract.release_tx_hash), ("Credential anchored", contract.credential_tx_hash)) if h},
            "demo_signers": services.demo_signer_public_keys(),
        }
    )
    return render(request, "agent/command_center.html", ctx)


@login_required(login_url="employer-signin")
@require_POST
def tick_engagement(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk)
    if not (request.user.is_staff or contract.employer_id == request.user.pk or contract.worker_id == request.user.pk):
        return HttpResponseForbidden("Not your engagement")
    agent = CareNestAgent.start(request.user, engagement=contract, metadata={"view": "tick"})
    try:
        agent.runner.run("get_contract_state", engagement_pk=contract.pk, reconcile=True)
        agent.tick(contract)
    finally:
        agent.end()
    messages.info(request, "Agent re-evaluated the engagement.")
    return redirect("agent-command-center", pk=pk)


# ---------------------------------------------------------------------------
# Human approval gates
# ---------------------------------------------------------------------------


def _execute_approval(request, approval: AgentApproval, signed_xdr: str | None):
    action = PreparedAction(**{k: v for k, v in approval.prepared_action.items() if k in PreparedAction.__dataclass_fields__})
    contract = approval.engagement
    try:
        result = services.execute_contract_call(contract, action.function, action.args, signer_role=action.signer_role, actor=request.user, approval=approval, signed_xdr=signed_xdr)
    except services.NeedsWalletSignature as exc:
        return JsonResponse(
            {
                "needs_signature": True,
                "approval_id": approval.pk,
                "xdr": exc.prepared.unsigned_xdr,
                "network_passphrase": exc.prepared.network_passphrase,
                "signer": exc.prepared.source_account,
                "function": action.function,
                "description": action.description,
                "submit_url": request.build_absolute_uri(f"/agent/approvals/{approval.pk}/submit-signed/"),
            }
        )
    except (PolicyViolation, stellar.StellarUnavailable, stellar.InvalidStellarIdentifier) as exc:
        approval.refresh_from_db()
        return JsonResponse({"ok": False, "error": str(exc), "approval_status": approval.status}, status=400)
    payload = _tx_payload(result)
    payload["ok"] = result.tx.status == "SUCCESS"
    payload["approval_status"] = approval.status
    return JsonResponse(payload, status=200 if payload["ok"] else 400)


@login_required(login_url="employer-signin")
@require_POST
def approve_action(request, pk: int):
    approval = get_object_or_404(AgentApproval.objects.select_related("engagement", "decision"), pk=pk)
    if approval.requested_from_id != request.user.pk:
        return HttpResponseForbidden("This approval was not requested from you")
    if approval.status != AgentApproval.Status.PENDING:
        return JsonResponse({"ok": False, "error": f"approval already {approval.status}"}, status=400)
    from .audit import mark_approval

    mark_approval(approval, status=AgentApproval.Status.APPROVED, decided_by=request.user, note=request.POST.get("note", "")[:500])
    return _execute_approval(request, approval, signed_xdr=None)


@login_required(login_url="employer-signin")
@require_POST
def submit_signed_approval(request, pk: int):
    approval = get_object_or_404(AgentApproval.objects.select_related("engagement"), pk=pk)
    if approval.requested_from_id != request.user.pk:
        return HttpResponseForbidden("This approval was not requested from you")
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "invalid JSON"}, status=400)
    signed_xdr = body.get("signed_xdr", "")
    if not signed_xdr or len(signed_xdr) > 20000:
        return JsonResponse({"ok": False, "error": "signed_xdr missing"}, status=400)
    if approval.status not in (AgentApproval.Status.APPROVED,):
        return JsonResponse({"ok": False, "error": f"approval is {approval.status}"}, status=400)
    return _execute_approval(request, approval, signed_xdr=signed_xdr)


@login_required(login_url="employer-signin")
@require_POST
def reject_action(request, pk: int):
    approval = get_object_or_404(AgentApproval, pk=pk)
    if approval.requested_from_id != request.user.pk:
        return HttpResponseForbidden("This approval was not requested from you")
    from .audit import mark_approval, record_decision

    mark_approval(approval, status=AgentApproval.Status.REJECTED, decided_by=request.user, note=request.POST.get("note", "")[:500])
    record_decision(decision_type=AgentDecision.Type.RECORD_RESULT, decision=f"Employer rejected {approval.approval_type}", user=request.user, engagement=approval.engagement, evidence=[f"approval #{approval.pk} rejected"], action="human_rejected")
    if approval.engagement:
        mem = memory_mod.load_for_engagement(approval.engagement)
        memory_mod.remember(mem, action=f"employer rejected {approval.approval_type}", add_tasks=["employer: clarify why the action was rejected"])
    messages.info(request, "Action rejected. Nothing was executed.")
    return redirect("agent-command-center", pk=approval.engagement_id)


@login_required(login_url="employer-signin")
@require_POST
def ingest_now(request):
    result = services.ingest_events()
    if request.headers.get("x-requested-with") == "fetch":
        return JsonResponse(result)
    if result.get("ok"):
        messages.success(request, f"Ingested {result['ingested']} new on-chain event(s) from Stellar RPC.")
    else:
        messages.warning(request, f"Event ingestion unavailable: {result.get('reason')}")
    return redirect(request.META.get("HTTP_REFERER") or "agent-panel")


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


@login_required(login_url="worker-signin")
def worker_engagements(request):
    engagements = Contract.objects.filter(worker=request.user).select_related("job", "employer").order_by("-updated_at")
    cards = []
    for c in engagements:
        mem = memory_mod.load_for_engagement(c)
        cards.append(
            {
                "engagement": c,
                "explanation": explain(c.chain_status),
                "memory": memory_mod.snapshot(mem),
                "events": StellarEvent.objects.filter(engagement=c).order_by("-ledger", "-id")[:6],
                "can_start": c.chain_status == EngagementStatus.FUNDED and c.engagement_id is not None,
                "can_submit": c.chain_status in (EngagementStatus.FUNDED, EngagementStatus.WORKING) and c.engagement_id is not None,
                "can_request_approval": c.chain_status == EngagementStatus.SUBMITTED and c.engagement_id is not None,
                "can_dispute": c.chain_status in EngagementStatus.FUNDS_IN_ESCROW - {EngagementStatus.DISPUTED} and c.engagement_id is not None,
                "credential": WorkCredential.objects.filter(engagement=c).first(),
                "can_check_in": c.is_bound and not c.shifts.filter(checked_out_at__isnull=True).exists(),
                "can_check_out": c.shifts.filter(checked_out_at__isnull=True).exists(),
                "open_shift": c.shifts.filter(checked_out_at__isnull=True).first(),
            }
        )
    return render(
        request,
        "agent/worker_engagements.html",
        {"cards": cards, "chain": _chain_banner(), "wallet": _wallet(request.user), "last_answer": request.session.pop("agent_last_answer", None)},
    )


@login_required(login_url="worker-signin")
@require_POST
def worker_action(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, worker=request.user)
    action_name = request.POST.get("action", "")
    report = request.POST.get("report", "")[:2000]
    agent = CareNestAgent.start(request.user, engagement=contract, metadata={"view": "worker-action", "action": action_name})
    try:
        if action_name == "submit_work":
            prep = agent.runner.run("submit_work_status", engagement_pk=contract.pk, worker_id=request.user.pk, report=report or "Work completed as agreed")
        elif action_name == "start_work":
            prep = type("R", (), {"ok": True, "data": {"prepared_action": PreparedAction(function="start_work", args={"id": contract.engagement_id}, signer_role="worker", financial=False, description="Acknowledge start of work", engagement_pk=contract.pk, engagement_id=contract.engagement_id).to_dict()}})()
        elif action_name == "request_approval":
            prep = type("R", (), {"ok": True, "data": {"prepared_action": PreparedAction(function="request_approval", args={"id": contract.engagement_id, "requester": contract.worker_wallet}, signer_role="worker", financial=False, description="Ask employer to review", engagement_pk=contract.pk, engagement_id=contract.engagement_id).to_dict()}})()
        elif action_name == "dispute":
            prep = agent.runner.run("open_dispute", engagement_pk=contract.pk, user_id=request.user.pk, reason=report or "Dispute raised by worker")
        else:
            return JsonResponse({"ok": False, "error": "unknown action"}, status=400)
        if not prep.ok:
            return JsonResponse({"ok": False, "error": prep.error}, status=400)
        action = PreparedAction(**prep.data["prepared_action"])
        try:
            result = services.execute_contract_call(contract, action.function, action.args, signer_role="worker", actor=request.user, approval=None, signed_xdr=None)
        except services.NeedsWalletSignature as exc:
            request.session[f"worker_prepared_{contract.pk}"] = {"function": action.function, "args": action.args}
            return JsonResponse({"needs_signature": True, "xdr": exc.prepared.unsigned_xdr, "network_passphrase": exc.prepared.network_passphrase, "signer": exc.prepared.source_account, "function": action.function, "description": action.description, "submit_url": request.build_absolute_uri(f"/agent/my-engagements/{contract.pk}/submit-signed/")})
        except (PolicyViolation, stellar.StellarUnavailable, stellar.InvalidStellarIdentifier) as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    finally:
        agent.end()
    payload = _tx_payload(result)
    payload["ok"] = result.tx.status == "SUCCESS"
    return JsonResponse(payload, status=200 if payload["ok"] else 400)


@login_required(login_url="worker-signin")
@require_POST
def worker_submit_signed(request, pk: int):
    contract = get_object_or_404(Contract, pk=pk, worker=request.user)
    prepared = request.session.get(f"worker_prepared_{contract.pk}")
    if not prepared:
        return JsonResponse({"ok": False, "error": "no prepared action in session"}, status=400)
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "invalid JSON"}, status=400)
    signed_xdr = body.get("signed_xdr", "")
    if not signed_xdr:
        return JsonResponse({"ok": False, "error": "signed_xdr missing"}, status=400)
    try:
        result = services.execute_contract_call(contract, prepared["function"], prepared["args"], signer_role="worker", actor=request.user, approval=None, signed_xdr=signed_xdr)
    except (PolicyViolation, stellar.StellarUnavailable, stellar.InvalidStellarIdentifier, services.NeedsWalletSignature) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    request.session.pop(f"worker_prepared_{contract.pk}", None)
    payload = _tx_payload(result)
    payload["ok"] = result.tx.status == "SUCCESS"
    return JsonResponse(payload, status=200 if payload["ok"] else 400)


# ---------------------------------------------------------------------------
# Work Passport
# ---------------------------------------------------------------------------


@login_required(login_url="worker-signin")
def work_passport(request):
    worker = request.user
    if _is_employer(worker) and request.GET.get("worker"):
        from django.contrib.auth import get_user_model

        worker = get_object_or_404(get_user_model(), pk=request.GET["worker"], role="worker")
    credentials = WorkCredential.objects.filter(worker=worker).select_related("engagement", "engagement__job", "employer")
    skills: dict[str, int] = {}
    for c in credentials:
        for s in c.skills:
            skills[s] = skills.get(s, 0) + 1
    active = Contract.objects.filter(worker=worker).exclude(chain_status__in=EngagementStatus.TERMINAL | {EngagementStatus.DRAFT}).select_related("job")
    from apps.profiles.models import WorkerProfile

    profile = WorkerProfile.objects.filter(user=worker).first()
    return render(
        request,
        "agent/work_passport.html",
        {
            "worker": worker,
            "profile": profile,
            "wallet": _wallet(worker),
            "credentials": credentials,
            "skills": sorted(skills.items(), key=lambda kv: -kv[1]),
            "active": active,
            "chain": _chain_banner(),
            "total_earned": sum((c.amount for c in credentials), 0),
            "explorer_tx": stellar.explorer_tx_url,
        },
    )


def credential_verify(request, credential_id):
    credential = get_object_or_404(WorkCredential.objects.select_related("engagement", "engagement__job"), credential_id=credential_id)
    recomputed = stellar.sha256_hex(credential.payload)
    return render(
        request,
        "agent/credential_verify.html",
        {
            "credential": credential,
            "hash_matches": recomputed == credential.credential_hash,
            "recomputed": recomputed,
            "chain": _chain_banner(),
            "tx_url": stellar.explorer_tx_url(credential.transaction_hash) if credential.transaction_hash and credential.data_source != "DEMO_DATA" else "",
            "release_url": stellar.explorer_tx_url(credential.release_tx_hash) if credential.release_tx_hash and credential.data_source != "DEMO_DATA" else "",
            "payload_json": json.dumps(credential.payload, indent=2, sort_keys=True),
        },
    )


# ---------------------------------------------------------------------------
# Wallet
# ---------------------------------------------------------------------------


@login_required
def wallet_connect(request):
    wallet = _wallet(request.user)
    if request.method == "POST":
        public_key = request.POST.get("public_key") or request.POST.get("address") or ""
        provider = request.POST.get("provider", "manual")
        chain = request.POST.get("chain") or ""
        network = request.POST.get("network") or ""
        try:
            from apps.wallet.providers import persist_connection

            wallet = persist_connection(request.user, address=public_key, provider_name=provider, chain=chain or None, network=network)
        except Exception as exc:  # noqa: BLE001
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Wallet connected: {wallet.short_address}")
            next_url = request.POST.get("next") or request.GET.get("next")
            if next_url and next_url.startswith("/"):
                return redirect(next_url)
    demo_keys = services.demo_signer_public_keys()
    role = getattr(request.user, "role", "")
    from apps.wallet.pricing import get_price_service
    from apps.wallet.payouts import preferred_payout_method
    from apps.wallet.models import PayoutMethod

    return render(
        request,
        "agent/wallet_connect.html",
        {
            "wallet": wallet,
            "chain": _chain_banner(),
            "demo_key": demo_keys.get(role, ""),
            "role": role,
            "next": request.GET.get("next", ""),
            "friendbot": stellar.get_config().network == "testnet",
            "prices": get_price_service().dashboard(),
            "payout_methods": PayoutMethod.objects.filter(user=request.user),
            "preferred_payout": preferred_payout_method(request.user),
        },
    )


@login_required
@require_POST
def wallet_disconnect(request):
    from apps.wallet.providers import disconnect_wallet

    disconnect_wallet(request.user, chain=request.POST.get("chain") or None)
    messages.info(request, "Wallet disconnected.")
    return redirect("wallet-connect")


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


@login_required
def tx_status(request, tx_hash: str):
    try:
        tx_hash = stellar.validate_tx_hash(tx_hash)
    except stellar.InvalidStellarIdentifier as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    demo = StellarEvent.objects.filter(tx_hash=tx_hash, data_source="DEMO_DATA").exists() or Contract.objects.filter(Q(create_tx_hash=tx_hash) | Q(fund_tx_hash=tx_hash) | Q(release_tx_hash=tx_hash) | Q(submit_tx_hash=tx_hash), data_source=Contract.DATA_SOURCE_DEMO).exists()
    if demo:
        return JsonResponse({"ok": True, "tx_hash": tx_hash, "status": "SUCCESS", "data_source": "DEMO_DATA"})
    try:
        status = stellar.get_transaction_status(tx_hash)
    except stellar.StellarUnavailable as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=503)
    return JsonResponse({"ok": True, "tx_hash": tx_hash, "status": status.status, "ledger": status.ledger, "explorer_url": status.explorer_url, "data_source": "LIVE_TESTNET"})


@login_required
def chain_health(request):
    return JsonResponse(stellar.rpc_health())
