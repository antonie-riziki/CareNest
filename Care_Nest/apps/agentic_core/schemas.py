"""
Typed payloads exchanged between the agent, its tools and the UI.

Plain dataclasses (no third-party validation library) keep the dependency
footprint small and make the payloads trivially JSON-serialisable for storage
in ``AgentMemory`` / ``AgentDecision``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any


JOB_TYPES = [
    "nanny",
    "housekeeper",
    "caregiver",
    "chef",
    "gardener",
    "driver",
    "cleaner",
    "security",
]

SKILL_HINTS: dict[str, list[str]] = {
    "nanny": ["childcare", "first aid", "meal preparation", "early learning"],
    "housekeeper": ["cleaning", "laundry", "ironing", "home organisation"],
    "caregiver": ["elder care", "medication reminders", "mobility support", "first aid"],
    "chef": ["cooking", "meal planning", "nutrition", "kitchen hygiene"],
    "gardener": ["landscaping", "lawn care", "pruning", "irrigation"],
    "driver": ["defensive driving", "vehicle maintenance", "route planning"],
    "cleaner": ["deep cleaning", "sanitisation", "laundry"],
    "security": ["gate management", "access control", "incident reporting"],
}


@dataclass
class ParsedRequirement:
    """Structured output of parsing an employer's natural-language requirement."""

    job_type: str = "housekeeper"
    schedule: str = "full-time"  # full-time | part-time | live-in | one-off
    location: str = ""
    pay_amount: Decimal | None = None
    pay_currency: str = "KES"
    pay_period: str = "month"  # month | week | day | one-off
    start_date: str = ""
    duration: str = ""
    required_skills: list[str] = field(default_factory=list)
    raw_text: str = ""
    confidence: float = 0.0
    parser: str = "deterministic"  # deterministic | omega:<model>
    notes: str = ""
    workers_needed: int = 1

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["pay_amount"] = str(self.pay_amount) if self.pay_amount is not None else None
        return d

    @property
    def title(self) -> str:
        sched = self.schedule.replace("-", " ").title()
        return f"{sched} {self.job_type.title()}" + (f" in {self.location}" if self.location else "")


@dataclass
class WorkerMatch:
    user_id: int
    display_name: str
    skills: list[str]
    rating: float
    verified: bool
    wallet_connected: bool
    score: float
    reasons: list[str] = field(default_factory=list)
    photo_url: str = ""
    bio: str = ""
    location_label: str = ""
    verified_engagements: int = 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("email", None)
        data.pop("phone", None)
        return data


@dataclass
class ContractTerms:
    """Off-chain terms. Only the hash of this payload goes on-chain."""

    job_type: str
    schedule: str
    location: str
    pay_amount: str
    pay_currency: str
    pay_period: str
    scope: list[str]
    start_date: str
    duration: str
    employer_user_id: int
    worker_user_id: int
    escrow_token: str
    escrow_amount_base_units: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PreparedAction:
    """A contract call the agent prepared but did NOT execute."""

    function: str
    args: dict[str, Any]
    signer_role: str  # employer | worker | arbiter
    financial: bool
    description: str
    engagement_pk: int | None = None
    engagement_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "data": self.data, "error": self.error}
