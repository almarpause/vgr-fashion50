"""Early-warning pre-flight scan — see what might change the calculation BEFORE
it happens, so the divisor treatment can be pre-decided rather than reacted to.

The reactive Weekly anomaly guard only fires *after* a big move (>15% shares,
>25% cap).  This layer looks *ahead*: it detects share-count drift the day it
posts, upcoming corporate actions (earnings / splits / ex-div), at-risk data
(a ticker going stale/404, the VSCO case), membership drift, and the fixed
index-review dates — and writes them to a ``Watchlist`` with a suggested action.

All network access goes through a ``PreflightSignals`` object so the scan logic
is pure and unit-testable with a static (offline) provider.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .config import Constituent, Settings, DEFAULT_SETTINGS, NEWS_KEYWORDS
from .state import EngineState


# --------------------------------------------------------------------------- #
# Signal provider abstraction (mockable)
# --------------------------------------------------------------------------- #
@dataclass
class TickerSignals:
    latest_shares: float | None = None
    next_earnings: date | None = None
    next_ex_div: date | None = None
    recent_split: date | None = None       # split effective date, if any recent
    resolves: bool = True                  # data-health: quote sourceable now
    health_reason: str = ""                # why not, if resolves is False
    news_hits: list[str] = field(default_factory=list)  # matched keywords


class PreflightSignals:
    """Live signals via yfinance.  Best-effort: anything unavailable is left
    None / empty and simply produces no watch item (never invented)."""

    def __init__(self, keywords=NEWS_KEYWORDS) -> None:
        self.keywords = tuple(k.lower() for k in keywords)

    def get(self, ticker: str, google_ticker: str = "") -> TickerSignals:
        sig = TickerSignals()
        try:
            import warnings
            warnings.filterwarnings("ignore")
            import yfinance as yf
            tk = yf.Ticker(ticker)

            # health: does a current quote resolve?
            try:
                fi = tk.fast_info
                px = fi.last_price
                sig.resolves = bool(px and px > 0)
                if not sig.resolves:
                    sig.health_reason = "no current price"
            except Exception as exc:
                sig.resolves = False
                sig.health_reason = f"unresolved: {exc!r}"[:80]

            # latest shares outstanding (issuance/buyback early signal)
            try:
                s = tk.get_shares_full(start=(date.today()
                                              - timedelta(days=400)).isoformat())
                if s is not None and len(s):
                    sig.latest_shares = float(s.iloc[-1])
            except Exception:
                pass

            # forward calendar: earnings + ex-dividend
            try:
                cal = tk.calendar or {}
                sig.next_earnings = _as_date(cal.get("Earnings Date"))
                sig.next_ex_div = _as_date(cal.get("Ex-Dividend Date"))
            except Exception:
                pass
            if sig.next_earnings is None:
                try:
                    ed = tk.get_earnings_dates(limit=12)
                    if ed is not None and len(ed):
                        fut = [d.date() for d in ed.index
                               if hasattr(d, "date") and d.date() >= date.today()]
                        sig.next_earnings = min(fut) if fut else None
                except Exception:
                    pass

            # recent split (share-count changing corporate action)
            try:
                sp = tk.splits
                if sp is not None and len(sp):
                    last = sp.index[-1]
                    d = last.date() if hasattr(last, "date") else None
                    if d and d >= date.today() - timedelta(days=30):
                        sig.recent_split = d
            except Exception:
                pass

            # news keyword scan (weak M&A / delisting signal)
            try:
                for item in (tk.news or [])[:10]:
                    title = (item.get("title")
                             or item.get("content", {}).get("title") or "")
                    tl = title.lower()
                    for kw in self.keywords:
                        if kw in tl and kw not in sig.news_hits:
                            sig.news_hits.append(kw)
            except Exception:
                pass
        except Exception:
            sig.resolves = False
            sig.health_reason = "signal fetch failed"
        return sig


class StaticPreflightSignals(PreflightSignals):
    """Deterministic provider for tests (no network)."""

    def __init__(self, table: dict[str, TickerSignals]) -> None:
        self._table = table

    def get(self, ticker: str, google_ticker: str = "") -> TickerSignals:
        return self._table.get(ticker, TickerSignals())


def _as_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    if isinstance(v, (list, tuple)) and v:
        return _as_date(v[0])
    try:
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Watch items + scan
# --------------------------------------------------------------------------- #
SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}


@dataclass
class WatchItem:
    ticker: str
    company: str
    category: str          # SHARES_DRIFT | CORP_ACTION | EARNINGS | DATA_RISK |
    #                        MEMBERSHIP | NEWS | INDEX_REVIEW
    signal: str
    severity: str          # HIGH | MEDIUM | LOW | INFO
    suggested_action: str
    event_date: date | None = None
    days_out: int | None = None
    source: str = ""


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    first_friday = d + timedelta(days=(4 - d.weekday()) % 7)
    return first_friday + timedelta(days=14)


def next_quarterly_review(today: date) -> date:
    for m in (3, 6, 9, 12):
        d = third_friday(today.year, m)
        if d >= today:
            return d
    return third_friday(today.year + 1, 3)


def next_annual_review(today: date) -> date:
    d = third_friday(today.year, 6)   # convention: 3rd Friday of June
    return d if d >= today else third_friday(today.year + 1, 6)


def scan(constituents: list[Constituent], state: EngineState,
         signals: PreflightSignals, today: date,
         settings: Settings = DEFAULT_SETTINGS,
         caps_ranks: dict[str, int] | None = None) -> list[WatchItem]:
    """Produce the forward-looking watch list.  Pure over ``signals``."""
    items: list[WatchItem] = []
    horizon = settings.watch_horizon_days

    for c in constituents:
        t = c.yahoo_ticker
        sig = signals.get(t, c.google_ticker)

        # 1) data-health (the VSCO case) — flag BEFORE it distorts a run
        if not sig.resolves:
            items.append(WatchItem(
                t, c.name, "DATA_RISK",
                f"ticker not resolving ({sig.health_reason or 'unknown'})",
                "HIGH", "Verify source; exclude+flag if it stays broken "
                "(do not zero/guess).", source="health"))

        # 2) share-count drift (issuance / buyback) — the divisor's whole reason
        prior = state.last_shares.get(t)
        if sig.latest_shares and prior and prior > 0:
            drift = (sig.latest_shares - prior) / prior
            if abs(drift) >= settings.shares_drift_early:
                direction = "issuance/dilution" if drift > 0 else "buyback"
                sev = "HIGH" if abs(drift) >= settings.shares_change_threshold \
                    else "MEDIUM"
                items.append(WatchItem(
                    t, c.name, "SHARES_DRIFT",
                    f"shares {prior:,.0f} -> {sig.latest_shares:,.0f} "
                    f"({drift*100:+.1f}%, {direction})", sev,
                    "Pre-stage a divisor review at the effective date.",
                    source="shares_full"))

        # 3) upcoming corporate actions
        if sig.recent_split:
            items.append(WatchItem(
                t, c.name, "CORP_ACTION",
                f"split effective {sig.recent_split.isoformat()}", "HIGH",
                "Shares change -> divisor adjustment on the effective date.",
                event_date=sig.recent_split,
                days_out=(sig.recent_split - today).days, source="splits"))
        for label, ev in (("earnings", sig.next_earnings),
                          ("ex-dividend", sig.next_ex_div)):
            if ev is not None and 0 <= (ev - today).days <= horizon:
                cat = "EARNINGS" if label == "earnings" else "CORP_ACTION"
                items.append(WatchItem(
                    t, c.name, cat, f"{label} on {ev.isoformat()}",
                    "MEDIUM" if label == "earnings" else "LOW",
                    "Expect a cap move; don't auto-freeze a legitimate gap."
                    if label == "earnings" else "Minor cap effect on ex-date.",
                    event_date=ev, days_out=(ev - today).days,
                    source="calendar"))

        # 4) news keyword hits (weak M&A / delisting signal)
        if sig.news_hits:
            items.append(WatchItem(
                t, c.name, "NEWS",
                "headlines mention: " + ", ".join(sig.news_hits), "MEDIUM",
                "Investigate; may lead to a mid-quarter divisor event.",
                source="news"))

        # 5) membership drift (near the drop zone within the 50)
        if caps_ranks and t in caps_ranks and caps_ranks[t] > settings.drop_zone_rank:
            items.append(WatchItem(
                t, c.name, "MEMBERSHIP",
                f"rank {caps_ranks[t]} (below drop-zone {settings.drop_zone_rank})",
                "LOW", "Reconstitution candidate at the next Annual review.",
                source="ranks"))

    # 6) fixed index-review countdowns (deterministic)
    qd = next_quarterly_review(today)
    items.append(WatchItem(
        "-", "Quarterly rebalance", "INDEX_REVIEW",
        f"next quarterly review {qd.isoformat()}", "INFO",
        "Scheduled: refresh shares/float, re-apply cap, propose divisor.",
        event_date=qd, days_out=(qd - today).days, source="schedule"))
    ad = next_annual_review(today)
    items.append(WatchItem(
        "-", "Annual reconstitution", "INDEX_REVIEW",
        f"next annual review {ad.isoformat()}", "INFO",
        "Scheduled: screen universe, apply 40/60 buffer, propose adds/drops.",
        event_date=ad, days_out=(ad - today).days, source="schedule"))

    items.sort(key=lambda it: (SEVERITY_ORDER.get(it.severity, 9),
                               it.days_out if it.days_out is not None else 9999))
    return items
