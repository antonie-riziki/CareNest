"""
Policy layer — the deterministic ("symbolic") half of the agent.

Given the current engagement state and the evidence gathered by the tools, the
policy decides what the agent should do next. The LLM never gets a vote here.

Hard rules
----------
1. The agent NEVER executes a financial action. It may only *prepare* one and
   open an ``AgentApproval`` for a human.
2. Financial functions are those in ``state.FINANCIAL_FUNCTIONS``.
3. Invalid state transitions are rejected before any transaction is built.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import AgentApproval, AgentDecision
from .state import FINANCIAL_FUNCTIONS, EngagementStatus, can_transition


class PolicyViolation(Exception):
    """Raised when something tries to bypass a human gate."""


@dataclass
class Recommendation:
    decision_type: str
    decision: str
    action: str  # human-readable action label
    evidence: list[str] = field(default_factory=list)
    rationale: str = ""
    approval_type: str | None = None  # AgentApproval.Type when a gate is needed
    contract_function: str | None = None
    signer_role: str | None = None  # employer | worker | arbiter
    add_tasks: list[str] = field(default_factory=list)
    resolve_tasks: list[str] = field(default_factory=list)

    @property
    def approval_required(self) -> bool:
        return self.approval_type is not None

    @property
    def financial(self) -> bool:
        return self.contract_function in FINANCIAL_FUNCTIONS


def assert_human_gate(function: str, approved_by_human: bool) -> None:
    """Guard used by the executor: financial functions require an approved gate."""
    if function in FINANCIAL_FUNCTIONS and not approved_by_human:
        raise PolicyViolation(
            f"'{function}' moves funds and cannot run without explicit human approval"
        )


def recommend(
    *,
    status: str,
    has_pending_approval: bool,
    worker_wallet_connected: bool,
    employer_wallet_connected: bool,
    funds_confirmed_on_chain: bool,
    submission_confirmed_on_chain: bool,
    credential_exists: bool,
    engagement_approved: bool = True,
) -> Recommendation:
    """Map (state, evidence) -> next recommendation. Pure function; easy to test."""

    ev: list[str] = [f"engagement status is {status}"]

    if status == EngagementStatus.DRAFT and not engagement_approved:
        return Recommendation(
            decision_type=AgentDecision.Type.NOTIFY,
            decision="Engagement terms are ready. The employer must review and approve them before any on-chain contract or funds movement. The agent cannot approve this.",
            action="wait_for_engagement_approval",
            evidence=ev + ["employer engagement approval is still pending"],
            rationale="Human approval is required for financial commitments. The agent only recommends.",
            add_tasks=["employer: review and approve engagement"],
        )

    if status == EngagementStatus.DRAFT:
        ev.append("terms prepared off-chain, nothing on-chain yet")
        if not employer_wallet_connected:
            return Recommendation(
                decision_type=AgentDecision.Type.NOTIFY,
                decision="Ask employer to connect a Stellar wallet before creating the agreement",
                action="notify_employer_connect_wallet",
                evidence=ev + ["employer wallet not connected"],
                rationale="A wallet signature is required to create the on-chain agreement.",
                add_tasks=["employer: connect wallet"],
            )
        if not worker_wallet_connected:
            ev.append("worker wallet not connected")
        return Recommendation(
            decision_type=AgentDecision.Type.REQUEST_CONTRACT_APPROVAL,
            decision="Request employer approval to create the Soroban agreement",
            action="request_employer_approval(create_agreement)",
            evidence=ev,
            rationale="Creating the agreement anchors the terms hash on-chain; only the employer may authorise it.",
            approval_type=AgentApproval.Type.CREATE_AGREEMENT,
            contract_function="create_agreement",
            signer_role="employer",
            add_tasks=["employer: approve contract creation"],
        )

    if status == EngagementStatus.CREATED:
        ev.append("agreement exists on-chain")
        ev.append("escrow not funded")
        if has_pending_approval:
            return Recommendation(
                decision_type=AgentDecision.Type.NO_ACTION,
                decision="Wait — a funding approval is already pending with the employer",
                action="wait_for_human",
                evidence=ev + ["funding approval already pending"],
            )
        return Recommendation(
            decision_type=AgentDecision.Type.REQUEST_FUNDING_APPROVAL,
            decision="Request employer approval to fund escrow",
            action="request_employer_approval(fund)",
            evidence=ev,
            rationale="Funding moves money into escrow; the employer must sign it.",
            approval_type=AgentApproval.Type.FUND_ESCROW,
            contract_function="fund",
            signer_role="employer",
            add_tasks=["employer: fund escrow"],
            resolve_tasks=["employer: approve contract creation"],
        )

    if status in (EngagementStatus.FUNDED, EngagementStatus.WORKING):
        ev.append("escrow funded" + (" (confirmed by on-chain event)" if funds_confirmed_on_chain else ""))
        ev.append("no completion submitted yet")
        return Recommendation(
            decision_type=AgentDecision.Type.NOTIFY,
            decision="Notify worker that the engagement is active and monitor for completion",
            action="notify_worker_engagement_active",
            evidence=ev,
            rationale="Nothing financial to do; the agent watches for the work_submitted event.",
            add_tasks=["worker: submit completion when done"],
            resolve_tasks=["employer: fund escrow"],
        )

    if status == EngagementStatus.SUBMITTED:
        ev.append("worker submitted completion" + (" (confirmed by on-chain event)" if submission_confirmed_on_chain else ""))
        ev.append("agreement funded")
        ev.append("agreement not disputed")
        if has_pending_approval:
            return Recommendation(
                decision_type=AgentDecision.Type.NO_ACTION,
                decision="Wait — payment approval already requested from the employer",
                action="wait_for_human",
                evidence=ev + ["payment approval already pending"],
            )
        return Recommendation(
            decision_type=AgentDecision.Type.REQUEST_EMPLOYER_APPROVAL,
            decision="Request employer approval to release escrow to the worker",
            action="request_employer_approval(approve_and_release)",
            evidence=ev,
            rationale="All preconditions for payment are met, but releasing funds is irreversible and requires the employer's explicit approval and signature.",
            approval_type=AgentApproval.Type.RELEASE_PAYMENT,
            contract_function="approve_and_release",
            signer_role="employer",
            add_tasks=["employer: review and approve payment"],
            resolve_tasks=["worker: submit completion when done"],
        )

    if status == EngagementStatus.APPROVAL_PENDING:
        ev.append("approval requested on-chain")
        if has_pending_approval:
            return Recommendation(
                decision_type=AgentDecision.Type.NO_ACTION,
                decision="Waiting for employer to approve payment",
                action="wait_for_human",
                evidence=ev + ["payment approval pending"],
            )
        return Recommendation(
            decision_type=AgentDecision.Type.REQUEST_EMPLOYER_APPROVAL,
            decision="Re-open payment approval for the employer",
            action="request_employer_approval(approve_and_release)",
            evidence=ev,
            approval_type=AgentApproval.Type.RELEASE_PAYMENT,
            contract_function="approve_and_release",
            signer_role="employer",
            add_tasks=["employer: review and approve payment"],
        )

    if status == EngagementStatus.DISPUTED:
        ev.append("dispute open; funds locked")
        return Recommendation(
            decision_type=AgentDecision.Type.FLAG_DISPUTE,
            decision="Escalate to CareNest arbiter; collect evidence from both parties",
            action="notify_arbiter",
            evidence=ev,
            rationale="Only the arbiter can resolve a dispute and only with explicit approval.",
            add_tasks=["arbiter: review evidence and resolve"],
        )

    if status == EngagementStatus.RELEASED:
        ev.append("payment released on-chain exactly once")
        if credential_exists:
            return Recommendation(
                decision_type=AgentDecision.Type.NO_ACTION,
                decision="Engagement complete; Work Credential already issued",
                action="none",
                evidence=ev + ["credential exists"],
                resolve_tasks=["employer: review and approve payment", "issue work credential"],
            )
        return Recommendation(
            decision_type=AgentDecision.Type.ISSUE_CREDENTIAL,
            decision="Generate the Work Credential for the worker's Work Passport",
            action="generate_work_credential",
            evidence=ev + ["employer confirmed by releasing payment"],
            rationale="The credential is a non-financial record; the agent may generate it, and the employer may optionally anchor its hash on-chain.",
            add_tasks=["employer: (optional) anchor credential on-chain"],
            resolve_tasks=["employer: review and approve payment"],
        )

    if status == EngagementStatus.CANCELLED:
        return Recommendation(
            decision_type=AgentDecision.Type.NO_ACTION,
            decision="Engagement cancelled; nothing further",
            action="none",
            evidence=ev,
        )

    return Recommendation(
        decision_type=AgentDecision.Type.NO_ACTION,
        decision=f"No policy for status {status}",
        action="none",
        evidence=ev,
    )


def validate_prepared_call(current_status: str, function: str) -> None:
    if not can_transition(current_status, function):
        raise PolicyViolation(
            f"Refusing to prepare '{function}' from state {current_status}: invalid transition"
        )
