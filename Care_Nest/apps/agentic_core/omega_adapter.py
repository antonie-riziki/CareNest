"""
Omega / ASI Alliance inference adapter.

What is real here
-----------------
* The hackathon provides access to the ASI Alliance inference layer used by the
  Omega agent framework (``singnet/Omega`` -> provider ``ASICloud``, env var
  ``ASI_API_KEY``). It is an OpenAI-compatible chat-completions endpoint.
* ``ASIInferenceBackend`` talks to that endpoint with the official ``openai``
  client, exactly as Omega's ASICloud provider does.

What is deliberately NOT done here
----------------------------------
* The full Omega runtime (MeTTa / Hyperon / PeTTa + SWI-Prolog) is not present in
  this environment, so we do not pretend to run it. Instead the CareNest agent
  follows Omega's neural-symbolic split: the LLM only *parses and explains*;
  every decision that matters is taken by the deterministic policy layer in
  ``policies.py`` and recorded in ``AgentDecision``.
* Nothing in the rest of CareNest imports a vendor SDK. Swap the backend by
  changing ``get_backend()``.

Rate limits for the hackathon key are low (2 req/s, 27/min), so the adapter is
conservative: one call per parse/explain, short prompts, and a deterministic
fallback when the network is unavailable or the quota is exhausted.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from django.conf import settings

from .schemas import JOB_TYPES, SKILL_HINTS, ParsedRequirement

logger = logging.getLogger("carenest.agent.omega")


class InferenceError(Exception):
    pass


@dataclass
class InferenceResult:
    text: str
    model: str
    backend: str
    latency_ms: int
    used_llm: bool


class InferenceBackend(Protocol):
    name: str

    def complete(self, system: str, user: str, *, json_mode: bool = False, max_tokens: int = 400) -> InferenceResult: ...

    def available(self) -> bool: ...


# ---------------------------------------------------------------------------
# ASI Alliance / Omega inference (OpenAI-compatible)
# ---------------------------------------------------------------------------


class ASIInferenceBackend:
    """OpenAI-compatible client against the ASI Alliance / SingularityNET endpoint."""

    name = "omega-asi"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("ASI_API_KEY", "")
        self.base_url = base_url or getattr(settings, "OMEGA_LLM_BASE_URL", "https://llm.c.singularitynet.io/v1")
        self.model = model or getattr(settings, "OMEGA_LLM_MODEL", "asi1-mini")
        self.timeout = getattr(settings, "OMEGA_LLM_TIMEOUT", 40)
        self._client = None
        self._cooldown_until = 0.0

    def available(self) -> bool:
        return bool(self.api_key) and getattr(settings, "OMEGA_LLM_ENABLED", True) and time.time() >= self._cooldown_until

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - dependency missing
                raise InferenceError("openai package not installed") from exc
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout, max_retries=0)
        return self._client

    def complete(self, system: str, user: str, *, json_mode: bool = False, max_tokens: int = 400) -> InferenceResult:
        if not self.available():
            raise InferenceError("ASI inference backend unavailable (no key, disabled or cooling down)")
        client = self._get_client()
        started = time.monotonic()
        try:
            kwargs: dict[str, Any] = dict(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0,
                max_tokens=max_tokens,
            )
            resp = client.chat.completions.create(**kwargs)
        except Exception as exc:  # network / quota / auth
            message = str(exc)
            if "429" in message or "rate limit" in message.lower():
                # Back off for a minute; the policy layer keeps working deterministically.
                self._cooldown_until = time.time() + 60
            logger.warning("ASI inference failed: %s", message[:200])
            raise InferenceError(message) from exc
        latency = int((time.monotonic() - started) * 1000)
        text = (resp.choices[0].message.content or "").strip()
        return InferenceResult(text=text, model=self.model, backend=self.name, latency_ms=latency, used_llm=True)


# ---------------------------------------------------------------------------
# Deterministic fallback (always available)
# ---------------------------------------------------------------------------


class DeterministicBackend:
    """Rule-based parser/explainer used when no LLM is reachable. Never invents facts."""

    name = "deterministic"

    def available(self) -> bool:
        return True

    def complete(self, system: str, user: str, *, json_mode: bool = False, max_tokens: int = 400) -> InferenceResult:
        # The deterministic backend does not generate free text; callers use
        # ``parse_requirement_rules`` / templated explanations instead.
        return InferenceResult(text="", model="rules", backend=self.name, latency_ms=0, used_llm=False)


_backend: InferenceBackend | None = None


def get_backend() -> InferenceBackend:
    global _backend
    if _backend is None:
        asi = ASIInferenceBackend()
        _backend = asi if asi.api_key else DeterministicBackend()
    return _backend


def set_backend(backend: InferenceBackend | None) -> None:
    """Test hook."""
    global _backend
    _backend = backend


# ---------------------------------------------------------------------------
# Requirement parsing
# ---------------------------------------------------------------------------

_PARSE_SYSTEM = (
    "You are the parsing module of CareNest WorkOS, a domestic-work platform in Kenya. "
    "Extract structured fields from the employer's request. Respond with ONLY a JSON object "
    "with keys: job_type (one of %s), schedule (full-time|part-time|live-in|one-off), "
    "location (string), pay_amount (number or null), pay_currency (ISO code, default KES), "
    "pay_period (month|week|day|one-off), start_date (string or empty), duration (string or empty), "
    "required_skills (array of short strings). Do not add commentary." % ", ".join(JOB_TYPES)
)

_AMOUNT_RE = re.compile(
    r"(?P<cur>KES|KSH|KSHS|USD|\$|SH)?\s*(?P<amt>\d{1,3}(?:[,\s]\d{3})+|\d+(?:\.\d+)?)\s*(?P<k>k)?\s*(?P<cur2>KES|KSH|USD)?",
    re.IGNORECASE,
)
_PERIOD_RE = re.compile(r"(?:per|/|a|each)\s*(month|mo|week|wk|day|hour|hr)", re.IGNORECASE)
_LOCATION_RE = re.compile(r"\b(?:in|at|around|near)\s+([A-Z][\w'\-]+(?:\s+[A-Z][\w'\-]+){0,2})")


def parse_requirement_rules(text: str) -> ParsedRequirement:
    """Deterministic parser — good enough for the demo sentence and most simple requests."""
    lowered = text.lower()
    parsed = ParsedRequirement(raw_text=text, parser="deterministic")

    for jt in JOB_TYPES:
        if jt in lowered or (jt == "housekeeper" and "house help" in lowered) or (jt == "caregiver" and "care giver" in lowered):
            parsed.job_type = jt
            break
    if "live-in" in lowered or "live in" in lowered:
        parsed.schedule = "live-in"
    elif "part-time" in lowered or "part time" in lowered:
        parsed.schedule = "part-time"
    elif "one-off" in lowered or "once" in lowered or "one time" in lowered:
        parsed.schedule = "one-off"
    else:
        parsed.schedule = "full-time"

    loc = _LOCATION_RE.search(text)
    if loc:
        parsed.location = loc.group(1).strip().rstrip(",.")

    # money: prefer a match that carries a currency token
    best = None
    for m in _AMOUNT_RE.finditer(text):
        if m.group("cur") or m.group("cur2") or m.group("k"):
            best = m
            break
    if best is None:
        candidates = [m for m in _AMOUNT_RE.finditer(text) if len(m.group("amt").replace(",", "").replace(" ", "")) >= 4]
        best = candidates[0] if candidates else None
    if best:
        raw = best.group("amt").replace(",", "").replace(" ", "")
        try:
            amt = Decimal(raw)
            if best.group("k"):
                amt *= 1000
            parsed.pay_amount = amt
        except InvalidOperation:
            pass
        cur = (best.group("cur") or best.group("cur2") or "KES").upper()
        parsed.pay_currency = "USD" if cur in {"$", "USD"} else "KES"
    per = _PERIOD_RE.search(text)
    if per:
        p = per.group(1).lower()
        parsed.pay_period = {"mo": "month", "wk": "week", "hr": "hour"}.get(p, p)
    elif parsed.schedule == "one-off":
        parsed.pay_period = "one-off"

    parsed.required_skills = list(SKILL_HINTS.get(parsed.job_type, []))[:3]
    parsed.confidence = 0.6 if parsed.pay_amount else 0.4
    return parsed


def parse_requirement(text: str) -> ParsedRequirement:
    """Parse with the Omega/ASI backend when available, validate, and fall back to rules."""
    baseline = parse_requirement_rules(text)
    backend = get_backend()
    if not backend.available() or isinstance(backend, DeterministicBackend):
        return baseline
    try:
        result = backend.complete(_PARSE_SYSTEM, text, json_mode=True, max_tokens=300)
        payload = _extract_json(result.text)
    except (InferenceError, ValueError) as exc:
        logger.info("Falling back to deterministic parser: %s", exc)
        return baseline

    merged = ParsedRequirement(raw_text=text, parser=f"omega:{result.model}")
    jt = str(payload.get("job_type", "")).lower().strip()
    merged.job_type = jt if jt in JOB_TYPES else baseline.job_type
    sched = str(payload.get("schedule", "")).lower().strip()
    merged.schedule = sched if sched in {"full-time", "part-time", "live-in", "one-off"} else baseline.schedule
    merged.location = str(payload.get("location") or baseline.location).strip()[:120]
    amt = payload.get("pay_amount")
    try:
        merged.pay_amount = Decimal(str(amt)) if amt not in (None, "", "null") else baseline.pay_amount
    except InvalidOperation:
        merged.pay_amount = baseline.pay_amount
    merged.pay_currency = str(payload.get("pay_currency") or baseline.pay_currency).upper()[:3]
    period = str(payload.get("pay_period", "")).lower().strip()
    merged.pay_period = period if period in {"month", "week", "day", "hour", "one-off"} else baseline.pay_period
    merged.start_date = str(payload.get("start_date") or "")[:60]
    merged.duration = str(payload.get("duration") or "")[:60]
    skills = payload.get("required_skills") or []
    merged.required_skills = [str(s).strip().lower()[:40] for s in skills if str(s).strip()][:6] or baseline.required_skills
    merged.confidence = 0.9 if merged.pay_amount else 0.7
    return merged


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).rstrip("`").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in response")
    return json.loads(text[start : end + 1])


# ---------------------------------------------------------------------------
# Plain-language explanations
# ---------------------------------------------------------------------------

_EXPLAIN_SYSTEM = (
    "You are the explanation module of CareNest WorkOS. You will receive FACTS about a domestic-work "
    "engagement gathered from the CareNest database and the Stellar blockchain. Write 2-4 short, warm, "
    "plain-language sentences for the user describing what is happening and the next step. "
    "Only use the facts provided. Never invent transactions, amounts or states. Never mention private data. "
    "Do not use crypto jargon beyond the words 'escrow', 'smart contract' and 'transaction'."
)


def explain_with_facts(facts: dict[str, Any], fallback_text: str) -> InferenceResult:
    """Ask the LLM to phrase known facts; fall back to a templated answer."""
    backend = get_backend()
    if not backend.available() or isinstance(backend, DeterministicBackend):
        return InferenceResult(text=fallback_text, model="rules", backend="deterministic", latency_ms=0, used_llm=False)
    try:
        result = backend.complete(_EXPLAIN_SYSTEM, "FACTS:\n" + json.dumps(facts, indent=2, default=str), max_tokens=220)
        if result.text:
            return result
    except InferenceError:
        pass
    return InferenceResult(text=fallback_text, model="rules", backend="deterministic", latency_ms=0, used_llm=False)
