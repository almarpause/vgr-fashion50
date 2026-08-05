"""Shared fetch->compute pipeline used by every workflow.

Given the constituent universe and (injectable) data / FX providers, produce
per-name USD caps plus a full audit trail.  Pure orchestration on top of the
data, fx and indexmath modules — no workbook or state side effects here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import indexmath
from .config import Constituent, Settings, DEFAULT_SETTINGS
from .datafetch import DataProvider, Quote
from .fx import FxProvider, FxRate


@dataclass
class NameResult:
    ticker: str
    name: str
    segment: str = ""
    price: float | None = None
    currency: str | None = None
    shares_outstanding: float | None = None
    float_shares: float | None = None
    float_factor: float = 1.0
    fx_rate_to_usd: float | None = None
    fx_source: str = "none"
    price_source: str = "none"
    cap_local: float | None = None
    cap_usd: float | None = None
    status: str = "OK"          # OK | MISSING
    manual_float_review: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "OK" and self.cap_usd is not None and self.cap_usd > 0


@dataclass
class RunResult:
    run_date: str
    names: list[NameResult]
    fx: dict[str, FxRate]

    @property
    def ok_names(self) -> list[NameResult]:
        return [n for n in self.names if n.ok]

    @property
    def caps_usd(self) -> dict[str, float]:
        return {n.ticker: n.cap_usd for n in self.ok_names}

    @property
    def total_cap_usd(self) -> float:
        return sum(n.cap_usd for n in self.ok_names)

    @property
    def n_ok(self) -> int:
        return len(self.ok_names)

    @property
    def n_missing(self) -> int:
        return sum(1 for n in self.names if not n.ok)


def fetch_all(constituents: list[Constituent], run_date: str,
              data_provider: DataProvider, fx_provider: FxProvider,
              settings: Settings = DEFAULT_SETTINGS) -> RunResult:
    """Fetch quotes + FX for every constituent and compute USD caps."""
    names: list[NameResult] = []
    quotes: dict[str, Quote] = {}

    for c in constituents:
        q = data_provider.get_quote(c.yahoo_ticker, c.google_ticker)
        quotes[c.yahoo_ticker] = q

    # Resolve FX only for currencies we actually saw.
    currencies = {q.currency for q in quotes.values() if q.currency}
    fx_table = fx_provider.get_many(currencies) if currencies else {}

    for c in constituents:
        q = quotes[c.yahoo_ticker]
        nr = NameResult(
            ticker=c.yahoo_ticker,
            name=c.name,
            segment=c.segment,
            price=q.price,
            currency=q.currency,
            shares_outstanding=q.shares_outstanding,
            float_shares=q.float_shares,
            price_source=q.source,
            manual_float_review=c.yahoo_ticker in settings.family_controlled,
            notes=list(q.notes),
        )

        if not q.ok:
            nr.status = "MISSING"
            names.append(nr)
            continue

        fx = fx_table.get(q.currency.upper()) if q.currency else None
        if fx is None or fx.status != "OK":
            nr.status = "MISSING"
            nr.notes.append(f"MISSING: fx for {q.currency}")
            names.append(nr)
            continue

        nr.float_factor = indexmath.float_factor_from(
            q.float_shares, q.shares_outstanding)
        nr.cap_local = indexmath.float_adjusted_cap_local(
            q.price, q.shares_outstanding, nr.float_factor)
        nr.fx_rate_to_usd = fx.rate_to_usd
        nr.fx_source = fx.source
        nr.cap_usd = indexmath.to_usd(nr.cap_local, fx.rate_to_usd)
        if nr.manual_float_review and nr.float_factor == 1.0:
            nr.notes.append("family-controlled: manual float review recommended")
        names.append(nr)

    return RunResult(run_date=run_date, names=names, fx=fx_table)
