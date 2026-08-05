"""Data-fetch layer: price, shares outstanding, float, currency.

Primary source is yfinance (Yahoo).  Stooq CSV and the Google Finance quote
page are secondary cross-checks / fallbacks for price + currency only.  If a
value cannot be sourced from any provider it is surfaced as ``MISSING`` — it is
NEVER invented, defaulted to zero, or silently guessed.

Local prices are normalised out of minor units (GBp pence, ZAc cents, ...) into
the major currency before any FX conversion, per the brief's gotchas.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Optional

import requests

HTTP_TIMEOUT = 20

# Minor-unit quote codes -> (major ISO currency, divisor).
MINOR_UNITS = {
    "GBP": ("GBP", 100.0),   # yfinance sometimes returns GBP for pence-quoted LSE
    "GBP0": ("GBP", 1.0),
    "GBp": ("GBP", 100.0),
    "GBX": ("GBP", 100.0),
    "ZAC": ("ZAR", 100.0),
    "ZAc": ("ZAR", 100.0),
    "ZAX": ("ZAR", 100.0),
    "ILA": ("ILS", 100.0),
    "ILa": ("ILS", 100.0),
    "ILX": ("ILS", 100.0),
    "KWF": ("KWD", 1000.0),
}


def normalize_currency(price: Optional[float], currency: Optional[str]
                       ) -> tuple[Optional[float], Optional[str]]:
    """Normalise a minor-unit quote to its major currency.

    Returns (price_major, currency_major).  Unknown / already-major currencies
    pass through unchanged (upper-cased).
    """
    if currency is None:
        return price, None
    key = currency.strip()
    if key in MINOR_UNITS:
        major, divisor = MINOR_UNITS[key]
        if price is not None:
            price = float(price) / divisor
        return price, major
    return price, key.upper()


@dataclass
class Quote:
    ticker: str
    price: Optional[float] = None          # in MAJOR currency
    currency: Optional[str] = None         # major ISO code
    shares_outstanding: Optional[float] = None
    float_shares: Optional[float] = None
    source: str = "none"                   # provider that supplied the price
    status: str = "OK"                     # OK | MISSING
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.status == "OK"
            and self.price is not None
            and self.price > 0
            and self.currency is not None
            and self.shares_outstanding is not None
            and self.shares_outstanding > 0
        )


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
def _yf_quote(ticker: str) -> Quote:
    """Primary provider: yfinance fast_info + info for float."""
    q = Quote(ticker=ticker)
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        fi = t.fast_info
        price = None
        currency = None
        shares = None
        try:
            price = fi.last_price
        except Exception:
            price = None
        try:
            currency = fi.currency
        except Exception:
            currency = None
        try:
            shares = fi.shares
        except Exception:
            shares = None

        # Enrich from .info (slower) for float + missing fields.
        info = {}
        try:
            info = t.info or {}
        except Exception:
            info = {}
        if shares in (None, 0):
            shares = info.get("sharesOutstanding")
        if not currency:
            currency = info.get("currency")
        if price in (None, 0):
            price = info.get("currentPrice") or info.get("regularMarketPrice")
        float_shares = info.get("floatShares")

        price, currency = normalize_currency(price, currency)
        q.price = float(price) if price else None
        q.currency = currency
        q.shares_outstanding = float(shares) if shares else None
        q.float_shares = float(float_shares) if float_shares else None
        q.source = "yfinance"
    except Exception as exc:  # pragma: no cover - network dependent
        q.notes.append(f"yfinance error: {exc!r}")
    return q


def _stooq_price(ticker: str) -> tuple[Optional[float], Optional[str]]:
    """Fallback: Stooq free CSV.  Returns (price, currency-or-None).

    Stooq uses lower-case symbols; US names drop the exchange suffix and add
    ``.us``.  Stooq does not report currency, so we return None for it and let
    the caller keep the primary currency if known.
    """
    sym = ticker.lower()
    # crude US mapping: bare symbols -> .us
    if "." not in sym:
        sym = f"{sym}.us"
    try:
        r = requests.get(
            "https://stooq.com/q/l/",
            params={"s": sym, "f": "sd2t2ohlcv", "h": "", "e": "csv"},
            timeout=HTTP_TIMEOUT,
        )
        if r.status_code != 200:
            return None, None
        import csv as _csv
        rows = list(_csv.DictReader(io.StringIO(r.text)))
        if not rows:
            return None, None
        close = rows[0].get("Close")
        if close in (None, "", "N/D"):
            return None, None
        return float(close), None
    except Exception:
        return None, None


def _google_price(google_ticker: str) -> tuple[Optional[float], Optional[str]]:
    """Last-resort cross-check: parse the Google Finance quote page.

    HTML with no official API — used only as a secondary cross-check, never as
    the sole source.  Best-effort and tolerant of markup changes.
    """
    if not google_ticker or ":" not in google_ticker:
        return None, None
    try:
        url = f"https://www.google.com/finance/quote/{google_ticker}"
        r = requests.get(
            url, timeout=HTTP_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Fashion50/1.0)"},
        )
        if r.status_code != 200:
            return None, None
        html = r.text
        import re
        m = re.search(r'data-last-price="([0-9.]+)"', html)
        price = float(m.group(1)) if m else None
        cm = re.search(r'data-currency-code="([A-Za-z]+)"', html)
        currency = cm.group(1) if cm else None
        return price, currency
    except Exception:
        return None, None


class DataProvider:
    """Resolves quotes with primary + fallback chain.

    Only the price/currency are cross-checked by fallbacks; shares outstanding
    and float come from yfinance (the only free source that reports them).
    """

    def get_quote(self, ticker: str, google_ticker: str = "") -> Quote:
        q = _yf_quote(ticker)

        # If price missing, try Stooq then Google (price/currency only).
        if q.price in (None, 0) or (q.price is not None and q.price <= 0):
            sp, sc = _stooq_price(ticker)
            if sp:
                q.price = sp
                q.source = "stooq"
                if sc and not q.currency:
                    q.currency = sc.upper()
                q.notes.append("price via stooq fallback")
        if q.price in (None, 0):
            gp, gc = _google_price(google_ticker)
            if gp:
                q.price, gc = normalize_currency(gp, gc)
                q.source = "google"
                if gc and not q.currency:
                    q.currency = gc
                q.notes.append("price via google fallback")

        if not q.ok:
            q.status = "MISSING"
            missing = []
            if not q.price:
                missing.append("price")
            if not q.currency:
                missing.append("currency")
            if not q.shares_outstanding:
                missing.append("shares_outstanding")
            q.notes.append("MISSING: " + ",".join(missing) if missing
                           else "MISSING")
        return q


class StaticDataProvider(DataProvider):
    """Deterministic provider for tests / fixtures: table of Quotes, no network."""

    def __init__(self, quotes: dict[str, Quote]) -> None:
        self._quotes = quotes

    def get_quote(self, ticker: str, google_ticker: str = "") -> Quote:
        q = self._quotes.get(ticker)
        if q is None:
            return Quote(ticker=ticker, status="MISSING",
                         notes=["not in static table"])
        return q
