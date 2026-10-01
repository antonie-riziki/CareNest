"""
Chain-aware wallet provider abstraction.

Stellar wallets sign Stellar transactions. EVM wallets sign EVM transactions.
They are never interchangeable. Private keys and seed phrases are never stored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from django.utils import timezone

from apps.wallet.models import Wallet

STELLAR_RE = re.compile(r"^G[A-Z2-7]{55}$")
EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

STELLAR_PROVIDERS = {"freighter", "xbull", "albedo", "lobstr", "hana", "demo", "manual"}
EVM_PROVIDERS = {"metamask", "walletconnect", "injected_evm"}


class WalletError(ValueError):
    pass


@dataclass
class WalletSession:
    provider_name: str
    chain: str
    address: str
    network: str
    status: str
    extra: dict[str, Any]


class WalletProvider(Protocol):
    provider_name: str
    chain: str

    def connect(self, address: str, *, network: str = "") -> WalletSession: ...
    def disconnect(self) -> None: ...
    def get_address(self) -> str: ...
    def get_balance(self) -> str: ...
    def sign_transaction(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def send_transaction(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class StellarWalletProvider:
    provider_name = "freighter"
    chain = "stellar"

    def __init__(self, provider_name: str = "freighter"):
        self.provider_name = provider_name

    def connect(self, address: str, *, network: str = "testnet") -> WalletSession:
        address = (address or "").strip()
        if not STELLAR_RE.match(address):
            raise WalletError("Stellar wallets require a G… public key")
        return WalletSession(self.provider_name, "stellar", address, network or "testnet", Wallet.ConnectionStatus.CONNECTED, {})

    def disconnect(self) -> None:
        return None

    def get_address(self) -> str:
        return ""

    def get_balance(self) -> str:
        return ""

    def sign_transaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("chain") not in (None, "stellar"):
            raise WalletError("Freighter cannot sign EVM transactions")
        return {"status": Wallet.ConnectionStatus.PENDING, "chain": "stellar", "needs_browser_signature": True}

    def send_transaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.sign_transaction(payload)


class EvmWalletProvider:
    provider_name = "metamask"
    chain = "evm"

    def __init__(self, provider_name: str = "metamask"):
        self.provider_name = provider_name

    def connect(self, address: str, *, network: str = "sepolia") -> WalletSession:
        address = (address or "").strip()
        if not EVM_RE.match(address):
            raise WalletError("EVM wallets require a 0x… address")
        return WalletSession(self.provider_name, "evm", address, network or "sepolia", Wallet.ConnectionStatus.CONNECTED, {})

    def disconnect(self) -> None:
        return None

    def get_address(self) -> str:
        return ""

    def get_balance(self) -> str:
        return ""

    def sign_transaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("chain") == "stellar" or payload.get("xdr"):
            raise WalletError("MetaMask cannot sign Stellar transactions. Connect Freighter for XLM settlement.")
        return {"status": Wallet.ConnectionStatus.PENDING, "chain": "evm", "needs_browser_signature": True}

    def send_transaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.sign_transaction(payload)


PROVIDERS: dict[str, type] = {
    "freighter": StellarWalletProvider,
    "xbull": StellarWalletProvider,
    "albedo": StellarWalletProvider,
    "lobstr": StellarWalletProvider,
    "hana": StellarWalletProvider,
    "demo": StellarWalletProvider,
    "manual": StellarWalletProvider,
    "metamask": EvmWalletProvider,
    "walletconnect": EvmWalletProvider,
    "injected_evm": EvmWalletProvider,
}


def get_provider(provider_name: str, chain: str | None = None) -> WalletProvider:
    name = (provider_name or "manual").lower()
    if name not in PROVIDERS:
        raise WalletError("Unsupported wallet")
    if chain == "stellar" and name in EVM_PROVIDERS:
        raise WalletError("EVM wallets cannot be used for Stellar operations")
    if chain == "evm" and name in STELLAR_PROVIDERS and name != "walletconnect":
        raise WalletError("Stellar wallets cannot be used for EVM operations")
    cls = PROVIDERS[name]
    return cls(name)  # type: ignore[call-arg]


def infer_chain(address: str, provider_name: str = "") -> str:
    if STELLAR_RE.match((address or "").strip()):
        return "stellar"
    if EVM_RE.match((address or "").strip()):
        return "evm"
    if provider_name in EVM_PROVIDERS:
        return "evm"
    if provider_name in STELLAR_PROVIDERS:
        return "stellar"
    raise WalletError("Unable to determine wallet chain")


def persist_connection(user, *, address: str, provider_name: str, chain: str | None = None, network: str = "") -> Wallet:
    chain = chain or infer_chain(address, provider_name)
    provider = get_provider(provider_name, chain=chain)
    session = provider.connect(address, network=network)
    wallet, _ = Wallet.objects.get_or_create(user=user, defaults={"balance": 0})
    wallet.wallet_provider = session.provider_name
    wallet.chain = session.chain
    wallet.network = session.network
    wallet.connection_status = session.status
    wallet.connected_at = timezone.now()
    if session.chain == "stellar":
        wallet.stellar_address = session.address
        wallet.address = session.address
        wallet.evm_address = wallet.evm_address or ""
    else:
        wallet.evm_address = session.address
        wallet.address = session.address
        # Keep any existing Stellar address; do not pretend this EVM wallet is Stellar.
    wallet.save()
    if session.chain == "stellar":
        from apps.contracts.models import Contract
        from apps.agentic_core.state import EngagementStatus

        Contract.objects.filter(employer=user, chain_status=EngagementStatus.DRAFT).update(employer_wallet=session.address)
        Contract.objects.filter(worker=user, chain_status=EngagementStatus.DRAFT).update(worker_wallet=session.address)
    return wallet


def disconnect_wallet(user, *, chain: str | None = None) -> Wallet:
    wallet, _ = Wallet.objects.get_or_create(user=user, defaults={"balance": 0})
    if chain in (None, "stellar"):
        wallet.stellar_address = ""
    if chain in (None, "evm"):
        wallet.evm_address = ""
    if not wallet.stellar_address and not wallet.evm_address:
        wallet.address = ""
        wallet.wallet_provider = ""
        wallet.chain = ""
        wallet.network = ""
        wallet.connection_status = Wallet.ConnectionStatus.DISCONNECTED
        wallet.connected_at = None
    else:
        wallet.chain = "stellar" if wallet.stellar_address else "evm"
        wallet.address = wallet.stellar_address or wallet.evm_address
        wallet.connection_status = Wallet.ConnectionStatus.CONNECTED
    wallet.save()
    return wallet


def require_stellar_for_settlement(wallet: Wallet | None) -> str:
    if not wallet or not wallet.stellar_address:
        raise WalletError("This is a Stellar transaction. Connect Freighter (or another Stellar wallet). MetaMask cannot sign it.")
    if wallet.chain == "evm" and not wallet.stellar_address:
        raise WalletError("Wrong network: an EVM wallet is connected, but settlement is on Stellar.")
    return wallet.stellar_address
