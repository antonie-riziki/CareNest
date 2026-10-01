"""
Domain services that glue the agent, the Django index and the Stellar layer.

* ``issue_work_credential`` – builds the verifiable Work Credential.
* ``apply_chain_event``     – updates the Django cache from an ingested event and
                              wakes the agent.
* ``ingest_events``         – pulls events from Stellar RPC (LIVE) and applies them.
* ``execute_contract_call`` – the ONLY code path that submits a transaction.
                              Financial functions refuse to run without an
                              approved ``AgentApproval`` (human gate).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.contracts.models import Contract
from apps.wallet.models import Wallet

from . import audit, memory as memory_mod, stellar
from .models import AgentApproval, AgentDecision, DataSource, StellarEvent, WorkCredential
from .policies import PolicyViolation, assert_human_gate, validate_prepared_call
from .state import FINANCIAL_FUNCTIONS, EngagementStatus, next_status

logger = logging.getLogger("carenest.agent.services")


# ---------------------------------------------------------------------------
# Work Credential
# ---------------------------------------------------------------------------


def build_credential_payload(contract: Contract) -> dict[str, Any]:
    """Canonical, privacy-preserving payload. No names/phones/IDs — only public refs."""
    started = contract.start_date.date() if contract.start_date else None
    completed = timezone.now().date()
    duration_days = (completed - started).days if started else 0
    skills = [s.strip() for s in (contract.job.job_type, *contract.scope.split("\n")) if s.strip()][:6]
    return {
        "schema": "carenest.work-credential.v1",
        "engagement_pk": contract.pk,
        "engagement_id": contract.engagement_id,
        "contract_address": contract.contract_address or settings.CARENEST_CONTRACT_ID,
        "worker_wallet": contract.worker_wallet,
        "employer_wallet": contract.employer_wallet,
        "job_type": contract.job.job_type,
        "skills": skills,
        "duration_days": max(duration_days, 0),
        "started_on": started.isoformat() if started else None,
        "completed_on": completed.isoformat(),
        "completion_status": WorkCredential.CompletionStatus.COMPLETED,
        "employer_confirmation": True,
        "payment_status": WorkCredential.PaymentStatus.RELEASED,
        "amount": str(contract.amount),
        "currency": contract.currency,
        "terms_hash": contract.terms_hash,
        "work_hash": contract.work_hash,
        "release_tx_hash": contract.release_tx_hash,
        "data_source": contract.data_source,
    }


@transaction.atomic
def issue_work_credential(contract: Contract) -> tuple[WorkCredential, bool]:
    existing = WorkCredential.objects.filter(engagement=contract).first()
    if existing:
        return existing, False
    if contract.chain_status != EngagementStatus.RELEASED:
        raise PolicyViolation("Work Credential requires RELEASED engagement")
    payload = build_credential_payload(contract)
    credential_hash = stellar.sha256_hex(payload)
    credential = WorkCredential.objects.create(
        worker=contract.worker,
        employer=contract.employer,
        engagement=contract,
        job_type=payload["job_type"],
        skills=payload["skills"],
        duration_days=payload["duration_days"],
        started_on=contract.start_date.date() if contract.start_date else None,
        completed_on=timezone.now().date(),
        completion_status=payload["completion_status"],
        employer_confirmation=True,
        payment_status=payload["payment_status"],
        amount=contract.amount,
        currency=contract.currency,
        credential_hash=credential_hash,
        on_chain_reference=contract.on_chain_reference,
        release_tx_hash=contract.release_tx_hash,
        payload=payload,
        data_source=DataSource.DEMO if contract.is_demo else DataSource.LIVE,
    )
    return credential, True


# ---------------------------------------------------------------------------
# Applying on-chain events to the Django index
# ---------------------------------------------------------------------------

_TX_FIELD_FOR_EVENT = {
    "agreement_created": "create_tx_hash",
    "agreement_funded": "fund_tx_hash",
    "work_submitted": "submit_tx_hash",
    "payment_released": "release_tx_hash",
    "credential_issued": "credential_tx_hash",
}


@transaction.atomic
def apply_chain_event(event: StellarEvent, *, wake_agent: bool = True) -> Contract | None:
    """Update the cached engagement from a persisted event, remember it, wake the agent."""
    from .agent import CareNestAgent

    contract = event.engagement
    if contract is None and event.chain_engagement_id is not None:
        contract = Contract.objects.filter(engagement_id=event.chain_engagement_id).first()
        if contract:
            event.engagement = contract
    if contract is None:
        event.processed = True
        event.save(update_fields=["processed", "engagement"])
        return None

    new_status = stellar.status_from_event(event.event_type)
    changed_fields = ["updated_at"]
    if new_status and new_status != contract.chain_status:
        contract.chain_status = new_status
        contract.status = new_status.lower()
        changed_fields += ["chain_status", "status"]
    tx_field = _TX_FIELD_FOR_EVENT.get(event.event_type)
    if tx_field and not getattr(contract, tx_field):
        setattr(contract, tx_field, event.tx_hash)
        changed_fields.append(tx_field)
    if event.event_type == "payment_released" and not contract.end_date:
        contract.end_date = timezone.now()
        changed_fields.append("end_date")
    contract.save(update_fields=list(dict.fromkeys(changed_fields)))

    mem = memory_mod.load_for_engagement(contract)
    memory_mod.remember(
        mem,
        action=f"ingested on-chain event {event.event_type} (tx {event.tx_hash[:8]}…)",
        contract_state=new_status or None,
        context_updates={"last_event": event.event_type, "last_tx_hash": event.tx_hash, "engagement_id": contract.engagement_id},
        last_event_ledger=event.ledger,
    )
    audit.record_decision(
        decision_type=AgentDecision.Type.RECORD_CHAIN_EVENT,
        decision=f"Recorded {event.event_type} from {'demo fixture' if event.data_source == DataSource.DEMO else 'Stellar RPC'}",
        engagement=contract,
        user=None,
        context={"event_id": event.event_id, "ledger": event.ledger, "topics": event.topics},
        evidence=[f"event {event.event_type} for engagement #{event.chain_engagement_id}", f"tx {event.tx_hash}"],
        action="update_engagement_index",
        tx_hash=event.tx_hash,
        data_source=event.data_source,
    )
    event.processed = True
    event.save(update_fields=["processed", "engagement"])

    if wake_agent:
        CareNestAgent.system().tick(contract)
    return contract


def ingest_events(*, start_ledger: int | None = None, limit: int = 200) -> dict[str, Any]:
    """LIVE: fetch from RPC, persist, apply. Safe to call repeatedly (idempotent)."""
    cfg = stellar.get_config()
    if not cfg.configured:
        return {"ok": False, "mode": cfg.mode_label, "reason": "contract not configured or demo mode forced", "ingested": 0}
    last = StellarEvent.objects.filter(data_source=DataSource.LIVE, contract_id=cfg.contract_id).order_by("-ledger").first()
    if start_ledger is None and last:
        start_ledger = max(last.ledger - 1, 1)
    try:
        raw, cursor, latest = stellar.fetch_events(start_ledger=start_ledger, limit=limit)
    except stellar.StellarUnavailable as exc:
        return {"ok": False, "mode": cfg.mode_label, "reason": str(exc), "ingested": 0}
    created = stellar.persist_events(raw, data_source=DataSource.LIVE)
    for ev in created:
        apply_chain_event(ev)
    return {"ok": True, "mode": cfg.mode_label, "fetched": len(raw), "ingested": len(created), "latest_ledger": latest, "cursor": cursor}


# ---------------------------------------------------------------------------
# Gated execution
# ---------------------------------------------------------------------------


class NeedsWalletSignature(Exception):
    """Raised when the caller must sign the prepared XDR in their browser wallet."""

    def __init__(self, prepared: stellar.PreparedTransaction):
        super().__init__("wallet signature required")
        self.prepared = prepared


@dataclass
class ExecutionResult:
    tx: stellar.SubmittedTransaction
    contract: Contract
    signed_by: str  # wallet | demo-signer | demo-mode


def _signer_wallet(contract: Contract, signer_role: str) -> str:
    if signer_role == "employer":
        return contract.employer_wallet or _wallet_of(contract.employer)
    if signer_role == "worker":
        return contract.worker_wallet or _wallet_of(contract.worker)
    if signer_role == "arbiter":
        secret = settings.STELLAR_ARBITER_SECRET
        if secret:
            from stellar_sdk import Keypair

            return Keypair.from_secret(secret).public_key
    return ""


def _wallet_of(user) -> str:
    w = Wallet.objects.filter(user=user).first()
    return w.stellar_address if w else ""


def _demo_secret_for(signer_role: str, expected_public: str) -> str:
    """Return a server-held testnet secret ONLY if it matches the connected wallet."""
    from stellar_sdk import Keypair

    secret = {
        "employer": settings.STELLAR_DEMO_EMPLOYER_SECRET,
        "worker": settings.STELLAR_DEMO_WORKER_SECRET,
        "arbiter": settings.STELLAR_ARBITER_SECRET,
    }.get(signer_role, "")
    if not secret:
        return ""
    try:
        if Keypair.from_secret(secret).public_key == expected_public:
            return secret
    except Exception:  # noqa: BLE001
        return ""
    return ""


@transaction.atomic
def execute_contract_call(
    contract: Contract,
    function: str,
    args: dict[str, Any],
    *,
    signer_role: str,
    actor,
    approval: AgentApproval | None = None,
    signed_xdr: str | None = None,
) -> ExecutionResult:
    """
    Submit a WorkContract call.

    Financial functions require ``approval`` to be APPROVED by a human. Even
    then the transaction still needs a signature from the signer's wallet (or,
    for the guided demo, a server-held testnet key bound to that wallet).
    """
    approved = approval is not None and approval.status in (AgentApproval.Status.APPROVED, AgentApproval.Status.EXECUTED)
    assert_human_gate(function, approved_by_human=approved)
    validate_prepared_call(contract.chain_status, function)

    if function in FINANCIAL_FUNCTIONS and approval and approval.requested_from_id != getattr(actor, "pk", None):
        raise PolicyViolation("Only the user the approval was requested from may execute it")
    if function in {"create_agreement", "fund", "approve_and_release"} and contract.approval_status != contract.ApprovalStatus.APPROVED:
        raise PolicyViolation("Employer engagement approval is required before any on-chain financial commitment")

    cfg = stellar.get_config()

    # ---- DEMO DATA path -------------------------------------------------
    if contract.is_demo or not cfg.configured:
        contract.data_source = Contract.DATA_SOURCE_DEMO
        tx = stellar.demo_submit(function, args, contract.pk)
        _record_success(contract, function, tx, approval=approval, actor=actor, signed_by="demo-mode")
        return ExecutionResult(tx=tx, contract=contract, signed_by="demo-mode")

    # ---- LIVE TESTNET path ---------------------------------------------
    signer_public = _signer_wallet(contract, signer_role)
    if not signer_public:
        raise stellar.StellarUnavailable(f"{signer_role} has no connected Stellar wallet")

    if signed_xdr:
        tx = stellar.submit_signed(signed_xdr)
        signed_by = "wallet"
    else:
        prepared = stellar.build_invocation(function, args, signer_public)
        secret = _demo_secret_for(signer_role, signer_public)
        if not secret:
            raise NeedsWalletSignature(prepared)
        tx = stellar.submit_signed(stellar.sign_with_secret(prepared.unsigned_xdr, secret))
        signed_by = "demo-signer"

    if tx.status != "SUCCESS":
        if approval:
            audit.mark_approval(approval, status=AgentApproval.Status.FAILED, decided_by=actor, note=tx.error or tx.status, tx_hash=tx.tx_hash)
        audit.record_decision(
            decision_type=AgentDecision.Type.RECORD_RESULT,
            decision=f"{function} did not succeed on-chain ({tx.status})",
            engagement=contract,
            user=actor,
            evidence=[f"tx {tx.tx_hash}", tx.error or tx.status],
            action=function,
            tx_hash=tx.tx_hash,
            data_source=DataSource.LIVE,
        )
        return ExecutionResult(tx=tx, contract=contract, signed_by=signed_by)

    _record_success(contract, function, tx, approval=approval, actor=actor, signed_by=signed_by)
    return ExecutionResult(tx=tx, contract=contract, signed_by=signed_by)


def _record_success(contract: Contract, function: str, tx: stellar.SubmittedTransaction, *, approval, actor, signed_by: str) -> None:
    from .agent import CareNestAgent

    is_demo = tx.data_source == DataSource.DEMO
    if function == "create_agreement":
        eng_id = tx.return_value
        if isinstance(eng_id, (int, str)) and str(eng_id).isdigit():
            contract.engagement_id = int(eng_id)
        elif contract.engagement_id is None:
            # Return value unavailable: fall back to reading the live event later.
            contract.engagement_id = None
        contract.contract_address = "DEMO" if is_demo else stellar.get_config().contract_id
    new_status = next_status(contract.chain_status, function)
    contract.chain_status = new_status
    contract.status = new_status.lower()
    tx_field = {
        "create_agreement": "create_tx_hash",
        "fund": "fund_tx_hash",
        "submit_work": "submit_tx_hash",
        "approve_and_release": "release_tx_hash",
        "issue_credential": "credential_tx_hash",
    }.get(function)
    if tx_field:
        setattr(contract, tx_field, tx.tx_hash)
    if function == "approve_and_release":
        contract.end_date = timezone.now()
    contract.save()
    try:
        from apps.wallet import settlement as settlement_mod

        if function == "fund":
            settlement_mod.mark_settlement_funded(contract, reference=tx.tx_hash)
        elif function == "approve_and_release":
            settlement_mod.capture_settled_commission(contract, reference=tx.tx_hash)
        elif function in {"cancel", "cancel_agreement"}:
            settlement_mod.refund_settlement(contract, reason="agreement cancelled")
    except Exception:  # noqa: BLE001
        logger.exception("settlement accounting failed after %s", function)

    if approval:
        audit.mark_approval(approval, status=AgentApproval.Status.EXECUTED, decided_by=actor, tx_hash=tx.tx_hash, note=f"signed by {signed_by}")

    mem = memory_mod.load_for_engagement(contract)
    memory_mod.remember(
        mem,
        action=f"{function} executed by human ({signed_by}); tx {tx.tx_hash[:8]}…",
        contract_state=new_status,
        decision={"type": "HUMAN_EXECUTED", "function": function, "tx_hash": tx.tx_hash, "signed_by": signed_by},
        context_updates={"engagement_id": contract.engagement_id, "last_tx_hash": tx.tx_hash},
    )
    audit.record_decision(
        decision_type=AgentDecision.Type.RECORD_RESULT,
        decision=f"Human executed {function}; engagement now {new_status}",
        engagement=contract,
        user=actor,
        evidence=[f"tx {tx.tx_hash} {tx.status}", f"signed by {signed_by}", f"approval #{approval.pk}" if approval else "non-financial call"],
        action=function,
        financial=function in FINANCIAL_FUNCTIONS,
        result={"status": tx.status, "ledger": tx.ledger, "return_value": stellar._jsonable(tx.return_value)},
        tx_hash=tx.tx_hash,
        data_source=tx.data_source,
    )

    if is_demo:
        # Record the event the contract would have emitted so the timeline is complete.
        ev = stellar.demo_event(function, contract.engagement_id or 0, tx.tx_hash, {"function": function})
        rows = stellar.persist_events([ev], data_source=DataSource.DEMO)
        for row in rows:
            row.engagement = contract
            row.save(update_fields=["engagement"])
            apply_chain_event(row, wake_agent=False)
    else:
        # Try to pull the real event immediately; ingestion is idempotent.
        try:
            ingest_events()
        except Exception:  # noqa: BLE001
            logger.exception("post-tx ingestion failed")

    if new_status == EngagementStatus.RELEASED:
        try:
            issue_work_credential(contract)
        except PolicyViolation:
            pass

    CareNestAgent.system().tick(contract)


# ---------------------------------------------------------------------------
# Wallet connection
# ---------------------------------------------------------------------------


def connect_wallet(user, public_key: str, provider: str, *, chain: str | None = None, network: str = "") -> Wallet:
    from apps.wallet.providers import persist_connection

    provider = provider if provider in dict(Wallet.PROVIDER_CHOICES) else "manual"
    if (chain or "stellar") == "stellar" or provider in {"freighter", "xbull", "albedo", "lobstr", "hana", "demo", "manual"}:
        if (public_key or "").startswith("G"):
            public_key = stellar.validate_public_key(public_key)
            chain = "stellar"
    return persist_connection(user, address=public_key, provider_name=provider, chain=chain, network=network)


def demo_signer_public_keys() -> dict[str, str]:
    """Public keys of configured demo signers (never the secrets)."""
    from stellar_sdk import Keypair

    out = {}
    for role, secret in (
        ("employer", settings.STELLAR_DEMO_EMPLOYER_SECRET),
        ("worker", settings.STELLAR_DEMO_WORKER_SECRET),
        ("arbiter", settings.STELLAR_ARBITER_SECRET),
    ):
        if secret:
            try:
                out[role] = Keypair.from_secret(secret).public_key
            except Exception:  # noqa: BLE001
                continue
    return out
