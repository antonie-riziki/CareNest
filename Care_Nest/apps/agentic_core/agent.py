"""
CareNest WorkOS agent.

The agent is not a chatbot. It is a persistent workflow manager:

* ``intake``             – parse an employer requirement, draft the job, rank workers.
* ``prepare_engagement`` – turn a chosen worker + requirement into hashed terms
                            (DRAFT engagement) and open the first approval gate.
* ``tick``               – observe the engagement (Django index + on-chain
                            events), decide the next step via the policy layer,
                            record the decision, perform only non-financial
                            actions and open approval gates for the rest.
* ``answer``             – answer "what is happening with Mary's contract?" from
                            persisted memory + chain events, never from imagination.

Every public method opens/uses an ``AgentSession`` and writes to
``AgentMemory`` so state survives restarts.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.db.models import Q

from apps.contracts.models import Contract

from . import audit, memory as memory_mod, omega_adapter, policies
from .models import AgentApproval, AgentDecision, AgentSession, DataSource, StellarEvent, WorkCredential
from .schemas import ParsedRequirement, PreparedAction
from .state import EngagementStatus, explain
from .tools import ToolRunner

logger = logging.getLogger("carenest.agent")
User = get_user_model()


class CareNestAgent:
    def __init__(self, session: AgentSession):
        self.session = session
        self.user = session.user
        self.runner = ToolRunner(session=session, user=session.user, engagement=session.engagement)

    # ------------------------------------------------------------------ sessions
    @classmethod
    def start(cls, user, *, channel: str = AgentSession.Channel.WEB, engagement: Contract | None = None, metadata: dict | None = None) -> "CareNestAgent":
        session = AgentSession.objects.create(
            user=user,
            role=getattr(user, "role", ""),
            engagement=engagement,
            channel=channel,
            metadata=metadata or {},
        )
        return cls(session)

    @classmethod
    def system(cls) -> "CareNestAgent":
        """Background/system session (event ingestion, schedulers)."""
        session = AgentSession.objects.create(channel=AgentSession.Channel.SYSTEM, role="system")
        return cls(session)

    def end(self) -> None:
        self.session.end()

    # ------------------------------------------------------------------ intake
    def intake(self, text: str) -> dict[str, Any]:
        """Employer describes a requirement -> parsed requirement, job draft, ranked workers."""
        text = (text or "").strip()[:1000]
        parsed = omega_adapter.parse_requirement(text)
        user_mem = memory_mod.load_for_user(self.user)

        parse_decision = audit.record_decision(
            session=self.session,
            user=self.user,
            decision_type=AgentDecision.Type.PARSE_REQUIREMENT,
            decision=f"Parsed requirement as {parsed.title} ({parsed.pay_currency} {parsed.pay_amount or '?'}/{parsed.pay_period})",
            context={"requirement": parsed.to_dict()},
            evidence=[f"employer text: {text[:140]}", f"parser: {parsed.parser}", f"confidence: {parsed.confidence}"],
            action="parse_requirement",
            model_used=parsed.parser,
            llm_used=parsed.parser.startswith("omega"),
        )
        self.runner.decision = parse_decision

        draft = self.runner.run("create_job_draft", employer_id=self.user.pk, requirement=parsed.to_dict())
        matches = self.runner.run("search_workers", job_type=parsed.job_type, required_skills=parsed.required_skills, location=parsed.location, limit=5)
        candidates = matches.data.get("matches", []) if matches.ok else []

        rec_decision = audit.record_decision(
            session=self.session,
            user=self.user,
            decision_type=AgentDecision.Type.RECOMMEND_WORKERS,
            decision=(f"Recommended {len(candidates)} worker(s); top match {candidates[0]['display_name']}" if candidates else "No matching workers found"),
            context={"job_id": draft.data.get("job_id"), "candidates": candidates},
            evidence=[f"job type {parsed.job_type}", f"required skills {', '.join(parsed.required_skills) or 'n/a'}"] + [f"{c['display_name']}: {'; '.join(c['reasons'])}" for c in candidates[:3]],
            action="search_workers",
            rationale="Deterministic scoring over CareNest worker profiles and verified Work Passport history.",
        )

        memory_mod.remember(
            user_mem,
            action=f"intake: parsed '{text[:60]}' -> {parsed.title}",
            decision={"type": "PARSE_REQUIREMENT", "decision_id": str(parse_decision.decision_id)},
            context_updates={"pending_requirement": parsed.to_dict(), "pending_job_id": draft.data.get("job_id"), "candidates": candidates},
            add_tasks=["employer: choose a worker or publish the job offer"],
            summary=f"Employer is hiring a {parsed.title}.",
        )
        return {
            "requirement": parsed.to_dict(),
            "job": draft.data,
            "candidates": candidates,
            "decisions": [parse_decision, rec_decision],
            "summary": self._intake_summary(parsed, candidates),
        }

    @staticmethod
    def _intake_summary(parsed: ParsedRequirement, candidates: list[dict]) -> str:
        pay = f"{parsed.pay_currency} {parsed.pay_amount:,.0f}/{parsed.pay_period}" if parsed.pay_amount else "pay to be confirmed"
        head = f"You need a {parsed.schedule} {parsed.job_type}" + (f" in {parsed.location}" if parsed.location else "") + f" at {pay}."
        if candidates:
            return head + f" I found {len(candidates)} suitable worker(s); {candidates[0]['display_name']} is the strongest match. Attach an image, add your terms, and publish the job — or invite a worker. They still add their own terms before you bind the contract."
        return head + " I could not find a matching worker yet; you can still prepare terms once a worker registers."

    # ------------------------------------------------------------------ engagement preparation
    def prepare_engagement(self, *, worker_id: int, job_id: int, requirement: dict[str, Any]) -> Contract:
        """Employer picked a worker -> DRAFT engagement + summary + first approval gate."""
        result = self.runner.run("prepare_contract_terms", employer_id=self.user.pk, worker_id=worker_id, job_id=job_id, requirement=requirement)
        if not result.ok:
            raise ValueError(result.error)
        contract = Contract.objects.select_related("job", "worker", "employer").get(pk=result.data["engagement_pk"])
        self.session.engagement = contract
        self.session.save(update_fields=["engagement"])
        self.runner.engagement = contract

        mem = memory_mod.load_for_engagement(contract)
        decision = audit.record_decision(
            session=self.session,
            user=self.user,
            engagement=contract,
            decision_type=AgentDecision.Type.PREPARE_CONTRACT,
            decision=f"Prepared engagement terms for {contract.worker.get_full_name() or contract.worker.username}: {contract.job.title}, {contract.currency} {contract.amount:,.0f}",
            context={"terms": result.data["terms"], "terms_hash": result.data["terms_hash"]},
            evidence=["employer selected worker", f"terms hash {result.data['terms_hash'][:16]}…", f"escrow {contract.token_amount_display}"],
            action="prepare_contract_terms",
            rationale="Terms are stored off-chain; only their hash will be anchored on Stellar.",
            data_source=DataSource.DEMO if contract.is_demo else DataSource.OFFCHAIN,
        )
        memory_mod.remember(
            mem,
            action="prepared engagement terms",
            decision={"type": "PREPARE_CONTRACT", "decision_id": str(decision.decision_id)},
            summary=f"Draft offer prepared for {contract.job.title}. Waiting for the worker to add their terms before you can bind the contract.",
        )
        user_mem = memory_mod.load_for_user(self.user)
        memory_mod.remember(user_mem, action=f"engagement #{contract.pk} prepared", resolve_tasks=["employer: choose a worker and approve contract preparation"], context_updates={"active_engagement_pk": contract.pk})
        self.tick(contract)
        return contract

    # ------------------------------------------------------------------ core loop
    def tick(self, contract: Contract) -> AgentDecision:
        """Observe -> decide -> act (non-financial) / request approval (financial)."""
        contract.refresh_from_db()
        self.runner.engagement = contract
        mem = memory_mod.load_for_engagement(contract)

        state = self.runner.run("get_contract_state", engagement_pk=contract.pk)
        pending = AgentApproval.objects.filter(engagement=contract, status=AgentApproval.Status.PENDING)
        events = {e["event_type"] for e in state.data.get("events", [])} if state.ok else set()
        credential_exists = WorkCredential.objects.filter(engagement=contract).exists()

        rec = policies.recommend(
            status=contract.chain_status,
            has_pending_approval=pending.exists(),
            worker_wallet_connected=bool(contract.worker_wallet),
            employer_wallet_connected=bool(contract.employer_wallet),
            funds_confirmed_on_chain="agreement_funded" in events,
            submission_confirmed_on_chain="work_submitted" in events,
            credential_exists=credential_exists,
            engagement_approved=contract.approval_status == contract.ApprovalStatus.APPROVED,
        )

        # Do not spam identical NO_ACTION decisions on every page load.
        last = AgentDecision.objects.filter(engagement=contract).order_by("-timestamp").first()
        if rec.decision_type == AgentDecision.Type.NO_ACTION and last and last.decision_type == AgentDecision.Type.NO_ACTION and last.decision == rec.decision:
            return last

        decision = audit.record_decision(
            session=self.session,
            user=self.user,
            engagement=contract,
            decision_type=rec.decision_type,
            decision=rec.decision,
            context={"status": contract.chain_status, "pending_approvals": pending.count(), "events_seen": sorted(events), "memory_version": mem.version},
            evidence=rec.evidence,
            rationale=rec.rationale,
            action=rec.action,
            approval_required=rec.approval_required,
            financial=rec.financial,
            data_source=DataSource.DEMO if contract.is_demo else (DataSource.LIVE if contract.engagement_id is not None else DataSource.OFFCHAIN),
        )
        self.runner.decision = decision

        if rec.approval_type:
            prepared = self._prepare_action(contract, rec.contract_function, rec.signer_role)
            summary = self._approval_summary(contract, rec.approval_type)
            self.runner.run(
                "request_employer_approval",
                engagement_pk=contract.pk,
                approval_type=rec.approval_type,
                decision_id=str(decision.decision_id),
                summary=summary,
                prepared_action=prepared.to_dict(),
            )
        elif rec.decision_type == AgentDecision.Type.ISSUE_CREDENTIAL:
            cred = self.runner.run("generate_work_credential", engagement_pk=contract.pk)
            decision.result = cred.data
            decision.save(update_fields=["result"])

        memory_mod.remember(
            mem,
            action=f"tick: {rec.action}",
            decision={"type": rec.decision_type, "decision_id": str(decision.decision_id), "approval_required": rec.approval_required},
            contract_state=contract.chain_status,
            add_tasks=rec.add_tasks,
            resolve_tasks=rec.resolve_tasks,
            summary=f"{explain(contract.chain_status).headline}. Next: {rec.decision}.",
        )
        return decision

    def _prepare_action(self, contract: Contract, function: str, signer_role: str) -> PreparedAction:
        policies.validate_prepared_call(contract.chain_status, function)
        if function == "create_agreement":
            from .stellar import get_config

            args = {
                "client": contract.employer_wallet,
                "worker": contract.worker_wallet,
                "token": get_config().token_contract_id,
                "amount": contract.token_amount,
                "terms_hash": contract.terms_hash,
            }
            desc = "Create the on-chain agreement (anchors terms hash; no funds move yet)"
        elif function == "fund":
            args = {"id": contract.engagement_id}
            desc = f"Move {contract.token_amount_display} into escrow"
        elif function == "approve_and_release":
            args = {"id": contract.engagement_id}
            desc = f"Release {contract.token_amount_display} from escrow to the worker (irreversible)"
        elif function == "issue_credential":
            cred = WorkCredential.objects.filter(engagement=contract).first()
            args = {"id": contract.engagement_id, "credential_hash": cred.credential_hash if cred else ""}
            desc = "Anchor the Work Credential hash on-chain"
        else:
            args = {"id": contract.engagement_id}
            desc = function
        return PreparedAction(function=function, args=args, signer_role=signer_role, financial=function in policies.FINANCIAL_FUNCTIONS, description=desc, engagement_pk=contract.pk, engagement_id=contract.engagement_id)

    @staticmethod
    def _approval_summary(contract: Contract, approval_type: str) -> str:
        worker = contract.worker.get_full_name() or contract.worker.username
        if approval_type == AgentApproval.Type.CREATE_AGREEMENT:
            return f"Create the Soroban agreement with {worker} for {contract.job.title} ({contract.currency} {contract.amount:,.0f}). This anchors the terms hash on Stellar. No funds move yet."
        if approval_type == AgentApproval.Type.FUND_ESCROW:
            return f"Fund escrow with {contract.token_amount_display} for {worker}. Funds stay locked in the smart contract until you approve completed work."
        if approval_type == AgentApproval.Type.RELEASE_PAYMENT:
            return f"{worker} reports the work is complete. Approve to release {contract.token_amount_display} from escrow. This can only happen once and cannot be reversed."
        return approval_type.replace("_", " ").title()

    # ------------------------------------------------------------------ Q&A over persisted state
    def answer(self, question: str) -> dict[str, Any]:
        """Session-persistence demo: resolve the engagement, load memory, answer with facts only."""
        question = (question or "").strip()[:500]
        contract = self._resolve_engagement(question)
        if contract is None:
            text = "I could not find an engagement matching that question in your CareNest account."
            decision = audit.record_decision(session=self.session, user=self.user, decision_type=AgentDecision.Type.EXPLAIN_STATE, decision=text, context={"question": question}, evidence=["no engagement matched"], action="explain_state")
            return {"answer": text, "engagement": None, "facts": {}, "memory": None, "decision": decision}

        self.runner.engagement = contract
        mem = memory_mod.load_for_engagement(contract)
        state = self.runner.run("get_contract_state", engagement_pk=contract.pk, reconcile=True)
        events = self.runner.run("read_stellar_events", engagement_pk=contract.pk, limit=10)
        pending = list(AgentApproval.objects.filter(engagement=contract, status=AgentApproval.Status.PENDING).values_list("approval_type", flat=True))
        exp = explain(contract.chain_status)
        worker_name = contract.worker.get_full_name() or contract.worker.username
        pricing = self.runner.run("explain_engagement_pricing", engagement_pk=contract.pk)

        facts = {
            "worker_first_name": worker_name.split(" ")[0],
            "job": contract.job.title,
            "status": contract.chain_status,
            "approval_status": contract.approval_status,
            "status_headline": exp.headline,
            "status_detail": exp.detail,
            "next_step_for_employer": exp.next_step_employer,
            "escrow": contract.token_amount_display,
            "pay": f"{contract.currency} {contract.amount:,.0f}",
            "pricing": pricing.data if pricing.ok else {},
            "on_chain_events": [f"{e['event_type']} (tx {e['tx_hash'][:8]}…)" for e in events.data.get("events", [])][::-1],
            "pending_human_approvals": pending,
            "memory_summary": mem.summary,
            "unresolved_tasks": mem.unresolved_tasks,
            "data_source": "DEMO DATA" if contract.is_demo else "LIVE TESTNET",
            "memory_version": mem.version,
        }
        fallback = self._templated_answer(facts)
        result = omega_adapter.explain_with_facts(facts, fallback)

        decision = audit.record_decision(
            session=self.session,
            user=self.user,
            engagement=contract,
            decision_type=AgentDecision.Type.EXPLAIN_STATE,
            decision=result.text[:500],
            context={"question": question, "facts": facts},
            evidence=[f"memory v{mem.version} loaded from scope {mem.scope_key}", f"status {contract.chain_status}", f"{len(facts['on_chain_events'])} on-chain events"],
            action="explain_state",
            model_used=result.model,
            llm_used=result.used_llm,
            data_source=DataSource.DEMO if contract.is_demo else DataSource.LIVE,
        )
        memory_mod.remember(mem, action=f"answered question: {question[:60]}")
        return {"answer": result.text, "engagement": contract, "facts": facts, "memory": memory_mod.snapshot(mem), "decision": decision, "used_llm": result.used_llm, "model": result.model}

    def _resolve_engagement(self, question: str) -> Contract | None:
        qs = Contract.objects.filter(Q(employer=self.user) | Q(worker=self.user)).select_related("job", "worker", "employer")
        if self.session.engagement_id:
            return qs.filter(pk=self.session.engagement_id).first() or self.session.engagement
        names = re.findall(r"\b([A-Z][a-z]{2,})\b", question)
        for name in names:
            hit = qs.filter(Q(worker__first_name__iexact=name) | Q(worker__last_name__iexact=name) | Q(employer__first_name__iexact=name)).order_by("-created_at").first()
            if hit:
                return hit
        m = re.search(r"#?(\d{1,6})", question)
        if m:
            hit = qs.filter(Q(pk=int(m.group(1))) | Q(engagement_id=int(m.group(1)))).first()
            if hit:
                return hit
        return qs.order_by("-updated_at").first()

    @staticmethod
    def _templated_answer(facts: dict[str, Any]) -> str:
        parts = [f"{facts['worker_first_name']}'s contract for {facts['job']} is currently: {facts['status_headline']}.", facts["status_detail"]]
        if facts.get("approval_status") and facts["approval_status"] != "APPROVED":
            parts.append(f"Employer engagement approval is {facts['approval_status']}. The agent cannot approve this.")
        pricing = facts.get("pricing") or {}
        breakdown = pricing.get("breakdown") or {}
        if breakdown:
            parts.append(
                f"Employer pays {breakdown.get('currency')} {breakdown.get('gross_amount')}; CareNest fee {breakdown.get('platform_fee_amount')}; worker net {breakdown.get('worker_net_amount')}; upskilling recovery {breakdown.get('upskilling_recovery_amount')}; final payout {breakdown.get('worker_payout_amount')}."
            )
        if facts["on_chain_events"]:
            parts.append("On-chain so far: " + ", ".join(facts["on_chain_events"][-3:]) + ".")
        if facts["pending_human_approvals"]:
            parts.append("Waiting on you: " + ", ".join(a.replace("_", " ").lower() for a in facts["pending_human_approvals"]) + ".")
        else:
            parts.append("Next step: " + facts["next_step_for_employer"])
        parts.append(f"(Source: {facts['data_source']}; memory v{facts['memory_version']}.)")
        return " ".join(parts)


# ---------------------------------------------------------------------------
# Convenience for views
# ---------------------------------------------------------------------------


def command_center_context(contract: Contract) -> dict[str, Any]:
    """Everything the Agent Command Center needs for one engagement."""
    mem = memory_mod.load_for_engagement(contract)
    decisions = AgentDecision.objects.filter(engagement=contract).order_by("-timestamp")
    approvals = AgentApproval.objects.filter(engagement=contract).order_by("-created_at")
    events = StellarEvent.objects.filter(engagement=contract).order_by("-ledger", "-id")
    actions = contract.agent_actions.order_by("-created_at")[:15]
    latest = decisions.first()
    credential = WorkCredential.objects.filter(engagement=contract).first()
    return {
        "engagement": contract,
        "explanation": explain(contract.chain_status),
        "memory": memory_mod.snapshot(mem),
        "latest_decision": latest,
        "decisions": decisions[:25],
        "pending_approvals": approvals.filter(status=AgentApproval.Status.PENDING),
        "approvals": approvals[:10],
        "events": events[:25],
        "actions": actions,
        "credential": credential,
        "timeline": _timeline(decisions, approvals, events),
    }


def _timeline(decisions, approvals, events) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for d in decisions[:40]:
        items.append({"at": d.timestamp, "kind": "AI recommendation" if d.approval_required or d.decision_type not in (AgentDecision.Type.RECORD_RESULT, AgentDecision.Type.RECORD_CHAIN_EVENT) else ("Blockchain execution" if d.tx_hash else "Agent record"), "title": d.get_decision_type_display(), "detail": d.decision, "tx_hash": d.tx_hash, "source": d.data_source, "approval_status": d.approval_status})
    for a in approvals[:40]:
        items.append({"at": a.decided_at or a.created_at, "kind": "Human approval", "title": f"{a.get_approval_type_display()} — {a.status}", "detail": a.summary, "tx_hash": a.tx_hash, "source": a.data_source, "approval_status": a.status})
    for e in events[:40]:
        items.append({"at": e.ledger_closed_at or e.ingested_at, "kind": "Blockchain execution", "title": e.event_type, "detail": f"ledger {e.ledger}", "tx_hash": e.tx_hash, "source": e.data_source, "approval_status": ""})
    items.sort(key=lambda i: i["at"], reverse=True)
    return items[:60]
