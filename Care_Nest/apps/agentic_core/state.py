"""
Off-chain mirror of the Soroban WorkContract state machine.

Stellar is the source of truth for on-chain state. This module lets the Django
layer validate transitions *before* asking a human to sign a transaction and
lets the agent explain state in plain language. It must stay in lock-step with
``apps/contracts/src/lib.rs``.
"""

from __future__ import annotations

from dataclasses import dataclass


class EngagementStatus:
    """String constants shared by models, policies, templates and the indexer."""

    DRAFT = "DRAFT"  # off-chain only: agent prepared terms, nothing on-chain yet
    CREATED = "CREATED"
    FUNDED = "FUNDED"
    WORKING = "WORKING"
    SUBMITTED = "SUBMITTED"
    APPROVAL_PENDING = "APPROVAL_PENDING"
    DISPUTED = "DISPUTED"
    RELEASED = "RELEASED"
    CANCELLED = "CANCELLED"

    ON_CHAIN = [
        CREATED,
        FUNDED,
        WORKING,
        SUBMITTED,
        APPROVAL_PENDING,
        DISPUTED,
        RELEASED,
        CANCELLED,
    ]
    ALL = [DRAFT] + ON_CHAIN
    CHOICES = [(s, s.replace("_", " ").title()) for s in ALL]
    TERMINAL = {RELEASED, CANCELLED}
    FUNDS_IN_ESCROW = {FUNDED, WORKING, SUBMITTED, APPROVAL_PENDING, DISPUTED}

    # Numeric encoding used by the Rust ``Status`` enum.
    FROM_CHAIN_INDEX = {
        0: CREATED,
        1: FUNDED,
        2: WORKING,
        3: SUBMITTED,
        4: APPROVAL_PENDING,
        5: DISPUTED,
        6: RELEASED,
        7: CANCELLED,
    }


# Contract function -> (allowed source states, resulting state)
TRANSITIONS: dict[str, tuple[frozenset[str], str]] = {
    "create_agreement": (frozenset({EngagementStatus.DRAFT}), EngagementStatus.CREATED),
    "fund": (frozenset({EngagementStatus.CREATED}), EngagementStatus.FUNDED),
    "start_work": (frozenset({EngagementStatus.FUNDED}), EngagementStatus.WORKING),
    "submit_work": (
        frozenset({EngagementStatus.FUNDED, EngagementStatus.WORKING}),
        EngagementStatus.SUBMITTED,
    ),
    "request_approval": (
        frozenset({EngagementStatus.SUBMITTED}),
        EngagementStatus.APPROVAL_PENDING,
    ),
    "approve_and_release": (
        frozenset({EngagementStatus.SUBMITTED, EngagementStatus.APPROVAL_PENDING}),
        EngagementStatus.RELEASED,
    ),
    "dispute": (
        frozenset(
            {
                EngagementStatus.FUNDED,
                EngagementStatus.WORKING,
                EngagementStatus.SUBMITTED,
                EngagementStatus.APPROVAL_PENDING,
            }
        ),
        EngagementStatus.DISPUTED,
    ),
    "resolve_dispute": (frozenset({EngagementStatus.DISPUTED}), EngagementStatus.RELEASED),
    "cancel": (
        frozenset({EngagementStatus.CREATED, EngagementStatus.FUNDED}),
        EngagementStatus.CANCELLED,
    ),
    "issue_credential": (frozenset({EngagementStatus.RELEASED}), EngagementStatus.RELEASED),
}

# Event topic emitted by the contract -> resulting state.
EVENT_TO_STATUS: dict[str, str | None] = {
    "agreement_created": EngagementStatus.CREATED,
    "agreement_funded": EngagementStatus.FUNDED,
    "work_started": EngagementStatus.WORKING,
    "work_submitted": EngagementStatus.SUBMITTED,
    "approval_requested": EngagementStatus.APPROVAL_PENDING,
    "payment_released": EngagementStatus.RELEASED,
    "agreement_disputed": EngagementStatus.DISPUTED,
    "agreement_cancelled": EngagementStatus.CANCELLED,
    "dispute_resolved": None,  # followed by payment_released / agreement_cancelled
    "credential_issued": None,  # does not change lifecycle status
}

# Which contract functions move money and therefore ALWAYS need a human signature.
FINANCIAL_FUNCTIONS = frozenset(
    {"fund", "approve_and_release", "resolve_dispute", "cancel", "create_agreement"}
)


class InvalidTransition(Exception):
    pass


def can_transition(current: str, function: str) -> bool:
    allowed, _ = TRANSITIONS[function]
    return current in allowed


def next_status(current: str, function: str) -> str:
    if function not in TRANSITIONS:
        raise InvalidTransition(f"Unknown contract function '{function}'")
    allowed, target = TRANSITIONS[function]
    if current not in allowed:
        raise InvalidTransition(
            f"Cannot call {function} while engagement is {current}; "
            f"allowed from {sorted(allowed)}"
        )
    return target


@dataclass(frozen=True)
class StatusExplanation:
    headline: str
    detail: str
    next_step_employer: str
    next_step_worker: str
    tone: str  # neutral | attention | success | danger


PLAIN_LANGUAGE: dict[str, StatusExplanation] = {
    EngagementStatus.DRAFT: StatusExplanation(
        "Terms prepared",
        "The agent has drafted the engagement terms. Nothing is on the blockchain yet.",
        "Review the terms and approve contract creation.",
        "Wait for the employer to confirm the engagement.",
        "neutral",
    ),
    EngagementStatus.CREATED: StatusExplanation(
        "Agreement recorded on Stellar",
        "The agreement exists on-chain but the salary has not been placed in escrow yet.",
        "Fund the escrow so the worker can start with confidence.",
        "Wait for the employer to fund escrow before starting.",
        "attention",
    ),
    EngagementStatus.FUNDED: StatusExplanation(
        "Escrow funded",
        "The full payment is locked in the smart contract. Neither party can touch it "
        "until the work is approved or a dispute is resolved.",
        "Nothing to do — the worker can begin.",
        "You can start work. Your pay is secured.",
        "success",
    ),
    EngagementStatus.WORKING: StatusExplanation(
        "Work in progress",
        "The worker has acknowledged the engagement. Funds remain locked in escrow.",
        "Wait for the worker to submit completion.",
        "Submit completion when the agreed work is done.",
        "success",
    ),
    EngagementStatus.SUBMITTED: StatusExplanation(
        "Completion submitted",
        "The worker reports the work is complete. The employer must review it.",
        "Review the work and approve payment, or open a dispute.",
        "Waiting for the employer to review your submission.",
        "attention",
    ),
    EngagementStatus.APPROVAL_PENDING: StatusExplanation(
        "Awaiting employer approval",
        "The agent has asked the employer to approve payment. Funds move only after an "
        "explicit human approval and wallet signature.",
        "Approve payment to release escrow to the worker.",
        "Your payment request has been sent to the employer.",
        "attention",
    ),
    EngagementStatus.DISPUTED: StatusExplanation(
        "Dispute open",
        "A party has raised a dispute. Funds stay locked until a CareNest arbiter resolves it.",
        "Provide evidence to CareNest support.",
        "Provide evidence to CareNest support.",
        "danger",
    ),
    EngagementStatus.RELEASED: StatusExplanation(
        "Payment released",
        "Escrow was paid to the worker exactly once. This engagement is complete.",
        "Optionally confirm the Work Credential on-chain.",
        "Your Work Passport now shows this verified engagement.",
        "success",
    ),
    EngagementStatus.CANCELLED: StatusExplanation(
        "Cancelled",
        "The agreement was cancelled and any escrowed funds were returned to the employer.",
        "No further action.",
        "No further action.",
        "neutral",
    ),
}


def explain(status: str) -> StatusExplanation:
    return PLAIN_LANGUAGE.get(status, PLAIN_LANGUAGE[EngagementStatus.DRAFT])
