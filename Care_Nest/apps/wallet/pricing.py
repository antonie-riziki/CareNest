"""
Backend price/conversion abstraction.

The rest of CareNest never talks to a specific market-data vendor. Prices are
cached briefly, labelled with source + last-updated, and never presented as
live when they are stale.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("carenest.pricing")

TWOPLACES = Decimal("0.01")
SUPPORTED = ("XLM", "USD", "KES")


@dataclass
class PriceQuote:
    base: str
    quote: str
    rate: Decimal
    source: str
    last_updated: float  # unix seconds
    stale: bool
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        iso = ""
        if self.last_updated:
            iso = timezone.datetime.fromtimestamp(self.last_updated, tz=timezone.get_current_timezone()).isoformat()
        return {
            "base": self.base,
            "quote": self.quote,
            "rate": str(self.rate) if self.rate is not None else None,
            "source": self.source,
            "last_updated": iso,
            "stale": self.stale,
            "realtime": bool(self.rate) and not self.stale and not self.error,
            "error": self.error,
            "disclaimer": "Market conversion estimate — not the settlement amount.",
        }


class PriceProvider:
    name = "base"

    def fetch_xlm_quotes(self) -> dict[str, Any]:
        raise NotImplementedError


class CoinGeckoProvider(PriceProvider):
    name = "coingecko"

    def fetch_xlm_quotes(self) -> dict[str, Any]:
        import httpx

        url = getattr(settings, "PRICE_FEED_URL", "https://api.coingecko.com/api/v3/simple/price")
        response = httpx.get(
            url,
            params={
                "ids": "stellar",
                "vs_currencies": "usd,kes",
                "include_last_updated_at": "true",
            },
            timeout=8.0,
            headers={"Accept": "application/json", "User-Agent": "CareNest/price-service"},
        )
        response.raise_for_status()
        payload = response.json().get("stellar") or {}
        usd = payload.get("usd")
        kes = payload.get("kes")
        updated = payload.get("last_updated_at") or time.time()
        if usd is None:
            raise ValueError("coingecko response missing usd")
        if kes is None:
            kes = self._usd_to_kes(Decimal(str(usd)))
        return {
            "XLM_USD": Decimal(str(usd)),
            "XLM_KES": Decimal(str(kes)),
            "USD_KES": Decimal(str(kes)) / Decimal(str(usd)),
            "last_updated": float(updated),
            "source": self.name,
        }

    def _usd_to_kes(self, usd_per_xlm: Decimal) -> Decimal:
        import httpx

        url = getattr(settings, "FX_FALLBACK_URL", "https://api.frankfurter.app/latest")
        response = httpx.get(url, params={"from": "USD", "to": "KES"}, timeout=8.0)
        response.raise_for_status()
        kes = (response.json().get("rates") or {}).get("KES")
        if kes is None:
            raise ValueError("fx fallback missing KES")
        return usd_per_xlm * Decimal(str(kes))


class PriceService:
    def __init__(self, provider: PriceProvider | None = None):
        self.provider = provider or CoinGeckoProvider()
        self._cache: dict[str, Any] | None = None
        self._cache_at = 0.0

    @property
    def cache_ttl(self) -> int:
        return int(getattr(settings, "PRICE_CACHE_SECONDS", 60))

    @property
    def stale_after(self) -> int:
        return int(getattr(settings, "PRICE_STALE_SECONDS", 300))

    def get_supported_assets(self) -> list[str]:
        return list(SUPPORTED)

    def get_last_updated(self) -> float | None:
        snap = self._snapshot(allow_stale=True)
        return None if not snap else snap.get("last_updated")

    def get_token_price(self, symbol: str, quote: str = "USD") -> PriceQuote:
        symbol = (symbol or "").upper()
        quote = (quote or "USD").upper()
        if symbol not in SUPPORTED or quote not in SUPPORTED:
            return PriceQuote(symbol, quote, Decimal("0"), self.provider.name, 0, True, error="unsupported token")
        if symbol == quote:
            return PriceQuote(symbol, quote, Decimal("1"), "identity", time.time(), False)
        snap = self._snapshot()
        if not snap:
            return PriceQuote(symbol, quote, Decimal("0"), self.provider.name, 0, True, error="price feed unavailable")
        rate = self._rate_from_snapshot(snap, symbol, quote)
        if rate is None:
            return PriceQuote(symbol, quote, Decimal("0"), snap.get("source", self.provider.name), snap.get("last_updated", 0), True, error="unsupported pair")
        age = time.time() - float(snap.get("last_updated") or 0)
        stale = age > self.stale_after or bool(snap.get("stale"))
        return PriceQuote(symbol, quote, rate, snap.get("source", self.provider.name), float(snap.get("last_updated") or 0), stale, error=snap.get("error", ""))

    def convert(self, amount, source: str, target: str) -> dict[str, Any]:
        quote = self.get_token_price(source, target)
        value = None
        if quote.rate and not quote.error:
            value = (Decimal(str(amount or 0)) * quote.rate).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
        payload = quote.to_dict()
        payload.update(
            {
                "amount": str(Decimal(str(amount or 0))),
                "converted": str(value) if value is not None else None,
                "disclaimer": "Market conversion estimate — not the settlement amount.",
            }
        )
        return payload

    def dashboard(self) -> dict[str, Any]:
        xlm_usd = self.get_token_price("XLM", "USD")
        xlm_kes = self.get_token_price("XLM", "KES")
        usd_kes = self.get_token_price("USD", "KES")
        return {
            "supported": self.get_supported_assets(),
            "quotes": [xlm_usd.to_dict(), xlm_kes.to_dict(), usd_kes.to_dict()],
            "last_updated": xlm_usd.to_dict().get("last_updated"),
            "source": xlm_usd.source,
            "stale": xlm_usd.stale or xlm_kes.stale,
            "unavailable": bool(xlm_usd.error and xlm_kes.error),
            "disclaimer": "Market conversion estimate — not the settlement amount.",
        }

    def _snapshot(self, allow_stale: bool = False) -> dict[str, Any] | None:
        now = time.time()
        if self._cache and now - self._cache_at < self.cache_ttl:
            return self._cache
        try:
            data = self.provider.fetch_xlm_quotes()
            data["stale"] = False
            data["error"] = ""
            self._cache = data
            self._cache_at = now
            return data
        except Exception as exc:  # noqa: BLE001
            logger.warning("price provider failed: %s", type(exc).__name__)
            if allow_stale and self._cache:
                stale = dict(self._cache)
                stale["stale"] = True
                stale["error"] = "provider failure; cached quote is stale"
                return stale
            if self._cache:
                stale = dict(self._cache)
                stale["stale"] = True
                stale["error"] = "provider failure; cached quote is stale"
                return stale
            return None

    def _rate_from_snapshot(self, snap: dict[str, Any], source: str, target: str) -> Decimal | None:
        key = f"{source}_{target}"
        if key in snap:
            return Decimal(str(snap[key])).quantize(Decimal("0.000001"))
        inverse = f"{target}_{source}"
        if inverse in snap and snap[inverse]:
            return (Decimal("1") / Decimal(str(snap[inverse]))).quantize(Decimal("0.000001"))
        if source == "USD" and target == "KES":
            return Decimal(str(snap.get("USD_KES") or 0)).quantize(Decimal("0.000001")) or None
        if source == "KES" and target == "USD" and snap.get("USD_KES"):
            return (Decimal("1") / Decimal(str(snap["USD_KES"]))).quantize(Decimal("0.000001"))
        if source == "KES" and target == "XLM" and snap.get("XLM_KES"):
            return (Decimal("1") / Decimal(str(snap["XLM_KES"]))).quantize(Decimal("0.000001"))
        if source == "USD" and target == "XLM" and snap.get("XLM_USD"):
            return (Decimal("1") / Decimal(str(snap["XLM_USD"]))).quantize(Decimal("0.000001"))
        return None


_SERVICE: PriceService | None = None


def get_price_service() -> PriceService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = PriceService()
    return _SERVICE


def reset_price_service(service: PriceService | None = None) -> None:
    global _SERVICE
    _SERVICE = service
