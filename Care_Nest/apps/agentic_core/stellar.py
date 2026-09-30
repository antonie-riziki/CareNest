"""
Stellar / Soroban integration.

Responsibilities
----------------
* Build (and simulate/prepare) invocations of the CareNest WorkContract.
* Hand unsigned XDR to a browser wallet (Freighter / Stellar Wallets Kit) or,
  for the guided demo only, sign with a server-held *testnet* key.
* Submit signed XDR through Stellar RPC and poll for the result.
* Pull contract events from Stellar RPC and persist them as ``StellarEvent``.
* Read live agreement state (``get_agreement``) via simulation so the Django
  cache can be reconciled against the chain.

Every public function is explicit about whether it is LIVE (touches the
network) or DEMO (recorded fixtures) so the UI never mislabels data.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone

from .models import DataSource, StellarEvent
from .state import EVENT_TO_STATUS, EngagementStatus

logger = logging.getLogger("carenest.stellar")

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class StellarUnavailable(Exception):
    """Raised when the RPC endpoint / contract is not reachable or configured."""


class InvalidStellarIdentifier(ValueError):
    pass


# ---------------------------------------------------------------------------
# Validation helpers (all user-controlled identifiers pass through here)
# ---------------------------------------------------------------------------


def validate_public_key(value: str) -> str:
    from stellar_sdk import StrKey

    value = (value or "").strip()
    if not value or not StrKey.is_valid_ed25519_public_key(value):
        raise InvalidStellarIdentifier("Invalid Stellar public key (expected G... address)")
    return value


def validate_contract_id(value: str) -> str:
    from stellar_sdk import StrKey

    value = (value or "").strip()
    if not value or not StrKey.is_valid_contract(value):
        raise InvalidStellarIdentifier("Invalid Soroban contract id (expected C... address)")
    return value


def validate_tx_hash(value: str) -> str:
    value = (value or "").strip().lower()
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise InvalidStellarIdentifier("Invalid transaction hash")
    return value


def sha256_hex(payload: Any) -> str:
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def to_base_units(amount: Decimal | str | float, decimals: int | None = None) -> int:
    decimals = settings.CARENEST_TOKEN_DECIMALS if decimals is None else decimals
    return int((Decimal(str(amount)) * (Decimal(10) ** decimals)).to_integral_value())


def explorer_tx_url(tx_hash: str) -> str:
    return f"{settings.STELLAR_EXPLORER_BASE}/tx/{tx_hash}" if tx_hash else ""


def explorer_contract_url(contract_id: str) -> str:
    return f"{settings.STELLAR_EXPLORER_BASE}/contract/{contract_id}" if contract_id else ""


# ---------------------------------------------------------------------------
# Configuration / mode
# ---------------------------------------------------------------------------


@dataclass
class ChainConfig:
    network: str
    rpc_url: str
    passphrase: str
    contract_id: str
    token_contract_id: str
    token_symbol: str
    token_decimals: int
    force_demo: bool

    @property
    def configured(self) -> bool:
        return bool(self.contract_id) and not self.force_demo

    @property
    def mode_label(self) -> str:
        return "LIVE TESTNET" if self.configured else "DEMO DATA"

    @property
    def data_source(self) -> str:
        return DataSource.LIVE if self.configured else DataSource.DEMO


def get_config() -> ChainConfig:
    return ChainConfig(
        network=settings.STELLAR_NETWORK,
        rpc_url=settings.STELLAR_RPC_URL,
        passphrase=settings.STELLAR_NETWORK_PASSPHRASE,
        contract_id=settings.CARENEST_CONTRACT_ID,
        token_contract_id=settings.CARENEST_TOKEN_CONTRACT_ID,
        token_symbol=settings.CARENEST_TOKEN_SYMBOL,
        token_decimals=settings.CARENEST_TOKEN_DECIMALS,
        force_demo=settings.CARENEST_FORCE_DEMO_MODE,
    )


def rpc_health() -> dict[str, Any]:
    """LIVE: ping the RPC endpoint. Returns a dict the UI can render."""
    cfg = get_config()
    if cfg.force_demo:
        return {"ok": False, "mode": "DEMO DATA", "reason": "CARENEST_FORCE_DEMO_MODE is enabled"}
    try:
        from stellar_sdk import SorobanServer

        server = SorobanServer(cfg.rpc_url)
        health = server.get_health()
        latest = server.get_latest_ledger()
        return {
            "ok": health.status == "healthy",
            "mode": cfg.mode_label,
            "status": health.status,
            "latest_ledger": latest.sequence,
            "protocol_version": latest.protocol_version,
            "rpc_url": cfg.rpc_url,
            "contract_id": cfg.contract_id,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "mode": cfg.mode_label, "reason": str(exc)[:200], "rpc_url": cfg.rpc_url}


# ---------------------------------------------------------------------------
# Argument encoding for the WorkContract
# ---------------------------------------------------------------------------


def _hash_to_bytes32(hex_hash: str) -> bytes:
    raw = bytes.fromhex(hex_hash)
    if len(raw) != 32:
        raise InvalidStellarIdentifier("hash must be 32 bytes")
    return raw


def encode_args(function: str, args: dict[str, Any]) -> list:
    """Translate a prepared action's JSON args into SCVals for the contract."""
    from stellar_sdk import scval

    if function == "initialize":
        return [scval.to_address(validate_public_key(args["arbiter"]))]
    if function == "create_agreement":
        return [
            scval.to_address(validate_public_key(args["client"])),
            scval.to_address(validate_public_key(args["worker"])),
            scval.to_address(validate_contract_id(args["token"])),
            scval.to_int128(int(args["amount"])),
            scval.to_bytes(_hash_to_bytes32(args["terms_hash"])),
        ]
    if function in {"fund", "start_work", "approve_and_release", "cancel", "get_agreement", "get_status"}:
        return [scval.to_uint64(int(args["id"]))]
    if function == "submit_work":
        return [scval.to_uint64(int(args["id"])), scval.to_bytes(_hash_to_bytes32(args["work_hash"]))]
    if function == "request_approval":
        return [scval.to_uint64(int(args["id"])), scval.to_address(validate_public_key(args["requester"]))]
    if function == "dispute":
        return [
            scval.to_uint64(int(args["id"])),
            scval.to_address(validate_public_key(args["opener"])),
            scval.to_bytes(_hash_to_bytes32(args["reason_hash"])),
        ]
    if function == "resolve_dispute":
        return [scval.to_uint64(int(args["id"])), scval.to_bool(bool(args["pay_worker"]))]
    if function == "issue_credential":
        return [scval.to_uint64(int(args["id"])), scval.to_bytes(_hash_to_bytes32(args["credential_hash"]))]
    if function in {"get_count", "get_arbiter"}:
        return []
    raise ValueError(f"Unknown contract function {function}")


# ---------------------------------------------------------------------------
# Transaction lifecycle (LIVE)
# ---------------------------------------------------------------------------


@dataclass
class PreparedTransaction:
    function: str
    args: dict[str, Any]
    source_account: str
    unsigned_xdr: str
    network_passphrase: str
    simulated_result: Any = None
    min_resource_fee: int | None = None


@dataclass
class SubmittedTransaction:
    tx_hash: str
    status: str  # PENDING | SUCCESS | FAILED | NOT_FOUND | ERROR
    ledger: int | None = None
    return_value: Any = None
    error: str = ""
    data_source: str = DataSource.LIVE
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def explorer_url(self) -> str:
        return explorer_tx_url(self.tx_hash)


def _server():
    from stellar_sdk import SorobanServer

    return SorobanServer(get_config().rpc_url)


def build_invocation(function: str, args: dict[str, Any], source_public_key: str, *, contract_id: str | None = None) -> PreparedTransaction:
    """LIVE: build + simulate + prepare an unsigned transaction for a wallet to sign."""
    from stellar_sdk import TransactionBuilder
    from stellar_sdk.exceptions import PrepareTransactionException

    cfg = get_config()
    contract_id = validate_contract_id(contract_id or cfg.contract_id)
    source_public_key = validate_public_key(source_public_key)
    if cfg.force_demo:
        raise StellarUnavailable("Demo mode forced; live transactions disabled")
    try:
        server = _server()
        source = server.load_account(source_public_key)
        tx = (
            TransactionBuilder(source, cfg.passphrase, base_fee=100)
            .set_timeout(300)
            .append_invoke_contract_function_op(
                contract_id=contract_id,
                function_name=function,
                parameters=encode_args(function, args),
            )
            .build()
        )
        sim = server.simulate_transaction(tx)
        if sim.error:
            raise StellarUnavailable(f"Simulation failed: {_humanize_sim_error(sim.error)}")
        prepared = server.prepare_transaction(tx, sim)
    except PrepareTransactionException as exc:
        raise StellarUnavailable(f"Could not prepare transaction: {_humanize_sim_error(str(exc))}") from exc
    except StellarUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise StellarUnavailable(f"Stellar RPC error: {str(exc)[:200]}") from exc

    result_val = None
    try:
        from stellar_sdk import scval

        if sim.results:
            result_val = scval.to_native(sim.results[0].xdr)
    except Exception:  # noqa: BLE001
        result_val = None

    return PreparedTransaction(
        function=function,
        args=args,
        source_account=source_public_key,
        unsigned_xdr=prepared.to_xdr(),
        network_passphrase=cfg.passphrase,
        simulated_result=result_val,
        min_resource_fee=int(sim.min_resource_fee or 0),
    )


def _humanize_sim_error(err: str) -> str:
    # Contract errors surface as "Error(Contract, #4)"; map to our enum for readability.
    mapping = {
        "#1": "AlreadyInitialized",
        "#2": "NotInitialized",
        "#3": "AgreementNotFound",
        "#4": "InvalidTransition (state machine rejected this call)",
        "#5": "Unauthorized",
        "#6": "InvalidAmount",
        "#7": "SameParty",
        "#8": "CredentialAlreadyIssued",
    }
    for code, label in mapping.items():
        if f"Error(Contract, {code})" in err:
            return f"contract error {label}"
    return err[:300]


def sign_with_secret(unsigned_xdr: str, secret: str) -> str:
    """Sign XDR with a server-held key. Demo signer ONLY; the secret is never logged."""
    from stellar_sdk import Keypair, TransactionEnvelope

    cfg = get_config()
    env = TransactionEnvelope.from_xdr(unsigned_xdr, cfg.passphrase)
    env.sign(Keypair.from_secret(secret))
    return env.to_xdr()


def submit_signed(signed_xdr: str, *, wait: bool = True, timeout_s: int = 45) -> SubmittedTransaction:
    """LIVE: submit a signed envelope through RPC and (optionally) wait for the result."""
    from stellar_sdk import TransactionEnvelope, scval
    from stellar_sdk.soroban_rpc import GetTransactionStatus, SendTransactionStatus

    cfg = get_config()
    try:
        env = TransactionEnvelope.from_xdr(signed_xdr, cfg.passphrase)
        server = _server()
        sent = server.send_transaction(env)
    except Exception as exc:  # noqa: BLE001
        raise StellarUnavailable(f"Failed to submit transaction: {str(exc)[:200]}") from exc

    tx_hash = sent.hash
    if sent.status == SendTransactionStatus.ERROR:
        return SubmittedTransaction(tx_hash=tx_hash, status="ERROR", error=str(sent.error_result_xdr or "")[:200])
    if not wait:
        return SubmittedTransaction(tx_hash=tx_hash, status="PENDING")

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(1.5)
        try:
            resp = server.get_transaction(tx_hash)
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_transaction failed: %s", exc)
            continue
        if resp.status == GetTransactionStatus.NOT_FOUND:
            continue
        if resp.status == GetTransactionStatus.SUCCESS:
            ret = None
            try:
                if resp.result_meta_xdr:
                    from stellar_sdk import xdr as stellar_xdr

                    meta = stellar_xdr.TransactionMeta.from_xdr(resp.result_meta_xdr)
                    v = getattr(meta, "v3", None) or getattr(meta, "v4", None)
                    if v is not None and v.soroban_meta and v.soroban_meta.return_value:
                        ret = scval.to_native(v.soroban_meta.return_value)
            except Exception:  # noqa: BLE001
                ret = None
            return SubmittedTransaction(tx_hash=tx_hash, status="SUCCESS", ledger=resp.ledger, return_value=ret)
        return SubmittedTransaction(tx_hash=tx_hash, status="FAILED", ledger=resp.ledger, error="transaction failed on-chain")
    return SubmittedTransaction(tx_hash=tx_hash, status="PENDING")


def get_transaction_status(tx_hash: str) -> SubmittedTransaction:
    from stellar_sdk.soroban_rpc import GetTransactionStatus

    tx_hash = validate_tx_hash(tx_hash)
    try:
        resp = _server().get_transaction(tx_hash)
    except Exception as exc:  # noqa: BLE001
        raise StellarUnavailable(str(exc)[:200]) from exc
    status = {
        GetTransactionStatus.SUCCESS: "SUCCESS",
        GetTransactionStatus.FAILED: "FAILED",
        GetTransactionStatus.NOT_FOUND: "NOT_FOUND",
    }.get(resp.status, str(resp.status))
    return SubmittedTransaction(tx_hash=tx_hash, status=status, ledger=resp.ledger)


def read_agreement(engagement_id: int) -> dict[str, Any] | None:
    """LIVE: read the on-chain Agreement struct via simulation (no signature needed)."""
    from stellar_sdk import Keypair, TransactionBuilder, scval
    from stellar_sdk.account import Account

    cfg = get_config()
    if not cfg.configured:
        return None
    try:
        server = _server()
        # Simulation does not need a real funded account.
        dummy = Account(Keypair.random().public_key, 0)
        tx = (
            TransactionBuilder(dummy, cfg.passphrase, base_fee=100)
            .set_timeout(60)
            .append_invoke_contract_function_op(
                contract_id=cfg.contract_id,
                function_name="get_agreement",
                parameters=encode_args("get_agreement", {"id": engagement_id}),
            )
            .build()
        )
        sim = server.simulate_transaction(tx)
        if sim.error or not sim.results:
            return None
        native = scval.to_native(sim.results[0].xdr)
    except Exception as exc:  # noqa: BLE001
        logger.warning("read_agreement failed: %s", exc)
        return None
    return _normalise_agreement(native)


def _normalise_agreement(native: Any) -> dict[str, Any]:
    if not isinstance(native, dict):
        return {"raw": str(native)}
    out: dict[str, Any] = {}
    for k, v in native.items():
        key = k if isinstance(k, str) else str(k)
        if isinstance(v, bytes):
            v = v.hex()
        elif hasattr(v, "address"):
            v = v.address
        out[key] = v
    status = out.get("status")
    if isinstance(status, list) and status:
        status = status[0]
    if isinstance(status, str):
        out["status"] = status.upper().replace("APPROVALPENDING", "APPROVAL_PENDING")
    elif isinstance(status, int):
        out["status"] = EngagementStatus.FROM_CHAIN_INDEX.get(status, str(status))
    return out


# ---------------------------------------------------------------------------
# Event ingestion (LIVE)
# ---------------------------------------------------------------------------


def _decode_topic_and_data(topics_xdr: list[str], value_xdr: str) -> tuple[str, int | None, list, Any]:
    from stellar_sdk import scval
    from stellar_sdk import xdr as stellar_xdr

    decoded_topics = []
    for t in topics_xdr:
        try:
            decoded_topics.append(_jsonable(scval.to_native(stellar_xdr.SCVal.from_xdr(t))))
        except Exception:  # noqa: BLE001
            decoded_topics.append(t)
    try:
        data = _jsonable(scval.to_native(stellar_xdr.SCVal.from_xdr(value_xdr)))
    except Exception:  # noqa: BLE001
        data = value_xdr
    event_type = str(decoded_topics[0]) if decoded_topics else "unknown"
    engagement_id = None
    if len(decoded_topics) > 1 and isinstance(decoded_topics[1], int):
        engagement_id = decoded_topics[1]
    return event_type, engagement_id, decoded_topics, data


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.hex()
    if hasattr(value, "address"):
        return value.address
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, int) and abs(value) > 2**53:
        return str(value)
    return value


def fetch_events(start_ledger: int | None = None, cursor: str | None = None, limit: int = 200) -> tuple[list[dict[str, Any]], str | None, int]:
    """LIVE: pull raw contract events from RPC. Returns (events, next_cursor, latest_ledger)."""
    from stellar_sdk.soroban_rpc import EventFilter, EventFilterType

    cfg = get_config()
    if not cfg.configured:
        raise StellarUnavailable("Contract id not configured")
    server = _server()
    if start_ledger is None and cursor is None:
        latest = server.get_latest_ledger().sequence
        start_ledger = max(latest - 17_000, 1)  # ~24h window on testnet
    try:
        resp = server.get_events(
            start_ledger=None if cursor else start_ledger,
            filters=[EventFilter(event_type=EventFilterType.CONTRACT, contract_ids=[cfg.contract_id])],
            cursor=cursor,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        raise StellarUnavailable(f"getEvents failed: {str(exc)[:200]}") from exc

    events = []
    for e in resp.events:
        event_type, engagement_id, topics, data = _decode_topic_and_data(e.topic, e.value)
        closed_at = None
        if e.ledger_close_at:
            try:
                closed_at = datetime.fromisoformat(str(e.ledger_close_at).replace("Z", "+00:00"))
            except ValueError:
                closed_at = None
        events.append(
            {
                "event_id": e.id,
                "contract_id": e.contract_id,
                "ledger": e.ledger,
                "ledger_closed_at": closed_at,
                "tx_hash": e.transaction_hash,
                "event_type": event_type,
                "engagement_id": engagement_id,
                "topics": topics,
                "data": data,
                "raw": {"topic": list(e.topic), "value": e.value, "in_successful_contract_call": e.in_successful_contract_call},
            }
        )
    return events, resp.cursor, resp.latest_ledger


def persist_events(raw_events: list[dict[str, Any]], data_source: str = DataSource.LIVE) -> list[StellarEvent]:
    """Idempotently store events; returns the newly created rows in ledger order."""
    from apps.contracts.models import Contract

    created: list[StellarEvent] = []
    for ev in raw_events:
        if StellarEvent.objects.filter(event_id=ev["event_id"]).exists():
            continue
        engagement = None
        if ev.get("engagement_id") is not None:
            engagement = Contract.objects.filter(
                engagement_id=ev["engagement_id"], contract_address=ev["contract_id"]
            ).first() or Contract.objects.filter(engagement_id=ev["engagement_id"]).first()
        row = StellarEvent.objects.create(
            event_id=ev["event_id"],
            contract_id=ev["contract_id"],
            ledger=ev["ledger"],
            ledger_closed_at=ev.get("ledger_closed_at"),
            tx_hash=ev["tx_hash"],
            event_type=ev["event_type"],
            chain_engagement_id=ev.get("engagement_id"),
            engagement=engagement,
            topics=ev.get("topics", []),
            data=ev.get("data", {}),
            raw=ev.get("raw", {}),
            data_source=data_source,
        )
        created.append(row)
    created.sort(key=lambda r: (r.ledger, r.id))
    return created


def status_from_event(event_type: str) -> str | None:
    return EVENT_TO_STATUS.get(event_type)


# ---------------------------------------------------------------------------
# Deterministic DEMO mode (recorded data, clearly labelled)
# ---------------------------------------------------------------------------


def load_demo_fixture(name: str = "demo_lifecycle.json") -> dict[str, Any]:
    path = FIXTURES_DIR / name
    with path.open() as fh:
        return json.load(fh)


def demo_tx_hash(seed: str) -> str:
    """Deterministic pseudo-hash so demo data is stable across runs. Clearly not live."""
    return sha256_hex(f"carenest-demo:{seed}")


def demo_submit(function: str, args: dict[str, Any], engagement_pk: int) -> SubmittedTransaction:
    """DEMO: emulate a successful submission without touching the network."""
    tx_hash = demo_tx_hash(f"{engagement_pk}:{function}:{json.dumps(args, sort_keys=True, default=str)}")
    ret = None
    if function == "create_agreement":
        # Demo engagement ids are derived from the Django pk so they stay unique.
        ret = 1000 + engagement_pk
    return SubmittedTransaction(tx_hash=tx_hash, status="SUCCESS", ledger=0, return_value=ret, data_source=DataSource.DEMO)


def demo_event(function: str, engagement_id: int, tx_hash: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """DEMO: build the event the contract would have emitted for `function`."""
    mapping = {
        "create_agreement": "agreement_created",
        "fund": "agreement_funded",
        "start_work": "work_started",
        "submit_work": "work_submitted",
        "request_approval": "approval_requested",
        "approve_and_release": "payment_released",
        "dispute": "agreement_disputed",
        "cancel": "agreement_cancelled",
        "issue_credential": "credential_issued",
    }
    event_type = mapping[function]
    now = timezone.now()
    return {
        "event_id": f"demo-{tx_hash[:16]}-{event_type}",
        "contract_id": "DEMO",
        "ledger": int(now.timestamp()) % 10_000_000,
        "ledger_closed_at": now,
        "tx_hash": tx_hash,
        "event_type": event_type,
        "engagement_id": engagement_id,
        "topics": [event_type, engagement_id],
        "data": extra or {},
        "raw": {"demo": True},
    }


__all__ = [
    "ChainConfig",
    "InvalidStellarIdentifier",
    "PreparedTransaction",
    "StellarUnavailable",
    "SubmittedTransaction",
    "build_invocation",
    "demo_event",
    "demo_submit",
    "demo_tx_hash",
    "encode_args",
    "explorer_contract_url",
    "explorer_tx_url",
    "fetch_events",
    "get_config",
    "get_transaction_status",
    "load_demo_fixture",
    "persist_events",
    "read_agreement",
    "rpc_health",
    "sha256_hex",
    "sign_with_secret",
    "status_from_event",
    "submit_signed",
    "to_base_units",
    "validate_contract_id",
    "validate_public_key",
    "validate_tx_hash",
]
