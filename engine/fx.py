"""FX to USD with free-source fallbacks and full auditability.

Priority:  frankfurter.app (ECB)  ->  exchangerate.host  ->  yfinance FX pair.
Every resolved rate carries the ``source`` string so the run is reproducible.

A rate is expressed as USD per 1 unit of the local currency, e.g. EUR->1.14.
USD itself is always exactly 1.0 with source ``identity``.
"""
from __future__ import annotations

from dataclasses import dataclass

import requests

HTTP_TIMEOUT = 20


@dataclass
class FxRate:
    currency: str          # major ISO code, e.g. "EUR"
    rate_to_usd: float     # USD per 1 unit of currency
    source: str            # provider that supplied it
    status: str = "OK"     # OK | MISSING


def _frankfurter(currency: str) -> float | None:
    try:
        r = requests.get(
            "https://api.frankfurter.app/latest",
            params={"from": currency, "to": "USD"},
            timeout=HTTP_TIMEOUT,
        )
        if r.status_code != 200:
            return None
        rate = r.json().get("rates", {}).get("USD")
        return float(rate) if rate else None
    except Exception:
        return None


def _exchangerate_host(currency: str) -> float | None:
    try:
        r = requests.get(
            "https://api.exchangerate.host/convert",
            params={"from": currency, "to": "USD"},
            timeout=HTTP_TIMEOUT,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        rate = data.get("result") or data.get("info", {}).get("rate")
        return float(rate) if rate else None
    except Exception:
        return None


def _yfinance_pair(currency: str) -> float | None:
    try:
        import yfinance as yf
        t = yf.Ticker(f"{currency}USD=X")
        px = t.fast_info.last_price
        return float(px) if px else None
    except Exception:
        return None


class FxProvider:
    """Resolves FX rates to USD, caching within a single run."""

    def __init__(self) -> None:
        self._cache: dict[str, FxRate] = {}

    def get(self, currency: str) -> FxRate:
        currency = currency.upper()
        if currency in self._cache:
            return self._cache[currency]

        if currency == "USD":
            fr = FxRate("USD", 1.0, "identity", "OK")
            self._cache[currency] = fr
            return fr

        for name, fn in (
            ("frankfurter", _frankfurter),
            ("exchangerate.host", _exchangerate_host),
            ("yfinance", _yfinance_pair),
        ):
            rate = fn(currency)
            if rate and rate > 0:
                fr = FxRate(currency, rate, name, "OK")
                self._cache[currency] = fr
                return fr

        fr = FxRate(currency, float("nan"), "none", "MISSING")
        self._cache[currency] = fr
        return fr

    def get_many(self, currencies) -> dict[str, FxRate]:
        return {c.upper(): self.get(c) for c in {c.upper() for c in currencies}}


class StaticFxProvider(FxProvider):
    """Deterministic provider for tests: fixed rate table, no network."""

    def __init__(self, table: dict[str, float]) -> None:
        super().__init__()
        self._table = {k.upper(): v for k, v in table.items()}
        self._table.setdefault("USD", 1.0)

    def get(self, currency: str) -> FxRate:
        currency = currency.upper()
        if currency in self._cache:
            return self._cache[currency]
        if currency == "USD":
            fr = FxRate("USD", 1.0, "identity", "OK")
            self._cache[currency] = fr
            return fr
        if currency in self._table:
            fr = FxRate(currency, float(self._table[currency]), "static", "OK")
        else:
            fr = FxRate(currency, float("nan"), "static", "MISSING")
        self._cache[currency] = fr
        return fr
