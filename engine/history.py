"""Historical backfill: reconstruct a real weekly index time series.

Two parts:
  * ``fetch_weekly_history`` — pulls ~5y of weekly closes (one bulk yfinance
    call) + weekly FX (frankfurter range endpoint), normalises minor units, and
    snapshots current shares/float (held constant — a documented free-data
    approximation).  Returns a pure ``HistoryPanel``.
  * ``build_index_series`` — pure, testable: seeds the index to 1000 at the base
    week and walks forward week-by-week.  Names that only start trading later
    (IPOs) are added on their first traded week via the divisor rule so the
    level stays continuous; each add is logged as a real divisor event.

The share/float basis is the *current* snapshot held constant, so the series is
price-and-FX driven.  This is stated in the README and the workbook Methodology.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date, datetime

import requests

from . import indexmath
from .config import Constituent, Settings, DEFAULT_SETTINGS
from .datafetch import MINOR_UNITS, DataProvider

HTTP_TIMEOUT = 30

# A constituent needs at least this many weekly points to be eligible for the
# backfill; below it we cannot source a real series, so the name is excluded and
# flagged (never invented / never added as a spurious final-week "IPO").
MIN_HISTORY_WEEKS = 8


def _minor_unit_factor(raw_ccy: str | None) -> tuple[str | None, float]:
    """(major ISO currency, divisor) for a raw quote currency code."""
    if raw_ccy is None:
        return None, 1.0
    key = raw_ccy.strip()
    if key in MINOR_UNITS:
        major, divisor = MINOR_UNITS[key]
        return major, divisor
    return key.upper(), 1.0


@dataclass
class HistoryPanel:
    weeks: list[date]                              # sorted week end-dates
    price: dict[str, dict[date, float]]            # ticker -> {week: price_major}
    fx: dict[str, dict[date, float]]               # ccy -> {week: usd_per_unit}
    currency: dict[str, str]                       # ticker -> major ISO ccy
    shares: dict[str, float]                       # ticker -> current shares
    float_factor: dict[str, float]                 # ticker -> current float factor
    name: dict[str, str] = field(default_factory=dict)
    segment: dict[str, str] = field(default_factory=dict)
    missing_snapshot: list[str] = field(default_factory=list)

    def cap_usd(self, ticker: str, week: date,
                price_override: float | None = None) -> float | None:
        price = price_override
        if price is None:
            price = self.price.get(ticker, {}).get(week)
        if price is None:
            return None
        sh = self.shares.get(ticker)
        if not sh or sh <= 0:
            return None
        ccy = self.currency.get(ticker, "USD")
        rate = 1.0 if ccy == "USD" else self.fx.get(ccy, {}).get(week)
        if rate is None or rate <= 0:
            return None
        ff = self.float_factor.get(ticker, 1.0)
        return float(price) * float(sh) * float(ff) * float(rate)


# --------------------------------------------------------------------------- #
# Network fetch
# --------------------------------------------------------------------------- #
def _download_prices(tickers: list[str], period: str):
    import warnings
    warnings.filterwarnings("ignore")
    import pandas as pd
    import yfinance as yf
    df = yf.download(tickers, period=period, interval="1wk",
                     auto_adjust=False, progress=False, threads=True)
    # Multi-ticker -> columns are a MultiIndex (field, ticker); single -> flat.
    if isinstance(df.columns, pd.MultiIndex):
        close = df["Close"]
    else:
        close = df[["Close"]].rename(columns={"Close": tickers[0]})
    return close


def _fx_series_frankfurter(currency: str, start: str, end: str
                           ) -> dict[date, float]:
    """USD-per-unit daily series for ``currency`` over [start, end]."""
    url = f"https://api.frankfurter.app/{start}..{end}?from={currency}&to=USD"
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            return {}
        rates = r.json().get("rates", {})
        out: dict[date, float] = {}
        for day, obj in rates.items():
            usd = obj.get("USD")
            if usd:
                out[date.fromisoformat(day)] = float(usd)
        return out
    except Exception:
        return {}


def _repair_series(ticker: str, period: str, divisor: float
                   ) -> dict[date, float]:
    """Per-ticker fallback when the bulk download returned a sparse column
    (rate-limited or partial).  Uses Ticker.history; empty on failure."""
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
        h = yf.Ticker(ticker).history(period=period, interval="1wk")
        out: dict[date, float] = {}
        for ts, val in h["Close"].items():
            if val is None or val != val:
                continue
            out[ts.date() if hasattr(ts, "date") else ts] = float(val) / divisor
        return out
    except Exception:
        return {}


def _fx_series_yf(currency: str, period: str) -> dict[date, float]:
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
        h = yf.Ticker(f"{currency}USD=X").history(period=period, interval="1wk")
        return {ts.date(): float(v) for ts, v in h["Close"].items()
                if v == v and v > 0}
    except Exception:
        return {}


def _sample_on_weeks(daily: dict[date, float], weeks: list[date]
                     ) -> dict[date, float]:
    """For each week end-date, take that day's rate or the most recent prior."""
    if not daily:
        return {}
    days = sorted(daily)
    out: dict[date, float] = {}
    j = 0
    last = None
    for w in weeks:
        while j < len(days) and days[j] <= w:
            last = daily[days[j]]
            j += 1
        if last is not None:
            out[w] = last
    return out


def fetch_weekly_history(constituents: list[Constituent], period: str = "5y",
                         data_provider: DataProvider | None = None
                         ) -> HistoryPanel:
    """Build a HistoryPanel from live sources (bulk prices + FX + snapshot)."""
    data_provider = data_provider or DataProvider()
    tickers = [c.yahoo_ticker for c in constituents]

    close = _download_prices(tickers, period)
    weeks = [ts.date() if hasattr(ts, "date") else ts for ts in close.index]

    # Current snapshot: shares, float, raw currency (held constant across history)
    price: dict[str, dict[date, float]] = {}
    currency: dict[str, str] = {}
    shares: dict[str, float] = {}
    float_factor: dict[str, float] = {}
    name = {c.yahoo_ticker: c.name for c in constituents}
    segment = {c.yahoo_ticker: c.segment for c in constituents}
    missing_snapshot: list[str] = []

    for c in constituents:
        t = c.yahoo_ticker
        q = data_provider.get_quote(t, c.google_ticker)
        # raw currency for minor-unit factor (fast_info gives e.g. 'GBp')
        raw_ccy = None
        try:
            import yfinance as yf
            raw_ccy = yf.Ticker(t).fast_info.currency
        except Exception:
            raw_ccy = q.currency
        major, divisor = _minor_unit_factor(raw_ccy)
        if not q.shares_outstanding or not major:
            missing_snapshot.append(t)
            continue
        currency[t] = major
        shares[t] = q.shares_outstanding
        float_factor[t] = indexmath.float_factor_from(
            q.float_shares, q.shares_outstanding)
        # normalise the whole price series into the major currency
        series = {}
        if t not in close.columns:
            missing_snapshot.append(t)
            continue
        col = close[t]
        for ts, val in col.items():
            if val is None or val != val:      # NaN
                continue
            series[ts.date() if hasattr(ts, "date") else ts] = float(val) / divisor

        # Repair sparse columns (rate-limited/partial bulk download).
        if len(series) < MIN_HISTORY_WEEKS:
            repaired = _repair_series(t, period, divisor)
            if len(repaired) > len(series):
                series = repaired

        # Still insufficient history -> cannot source a real series; exclude+flag.
        if len(series) < MIN_HISTORY_WEEKS:
            currency.pop(t, None)
            shares.pop(t, None)
            float_factor.pop(t, None)
            missing_snapshot.append(t)
            continue
        price[t] = series

    # FX: one range call per non-USD currency, sampled to week end-dates.
    start = weeks[0].isoformat()
    end = weeks[-1].isoformat()
    fx: dict[str, dict[date, float]] = {}
    for ccy in {v for v in currency.values() if v != "USD"}:
        daily = _fx_series_frankfurter(ccy, start, end)
        if not daily:
            daily = _fx_series_yf(ccy, period)
        fx[ccy] = _sample_on_weeks(daily, weeks)

    return HistoryPanel(weeks=weeks, price=price, fx=fx, currency=currency,
                        shares=shares, float_factor=float_factor, name=name,
                        segment=segment, missing_snapshot=missing_snapshot)


# --------------------------------------------------------------------------- #
# Pure index construction
# --------------------------------------------------------------------------- #
@dataclass
class DivisorEvent:
    effective_date: date
    company: str
    ticker: str
    event_type: str
    sum_before_usd: float
    sum_after_usd: float
    old_divisor: float
    new_divisor: float


@dataclass
class SeriesResult:
    rows: list[dict]                    # Weekly-schema rows
    events: list[DivisorEvent]
    base_date: date
    base_divisor: float
    base_total_cap_usd: float
    final_divisor: float
    final_caps_usd: dict[str, float]
    final_fx: dict[str, float]
    final_level: float
    final_week: date
    active: list[str]


def build_index_series(panel: HistoryPanel,
                       settings: Settings = DEFAULT_SETTINGS,
                       base_level: float = 1000.0) -> SeriesResult:
    """Walk the panel forward; seed base=1000; add IPO names via divisor rule."""
    weeks = list(panel.weeks)
    all_tickers = [t for t in panel.shares]      # tickers with a valid snapshot
    n_all = len(all_tickers)

    def iso_week(d: date) -> str:
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"

    active: set[str] = set()
    last_price: dict[str, float] = {}
    rows: list[dict] = []
    events: list[DivisorEvent] = []

    # ---- base week -------------------------------------------------------- #
    w0 = weeks[0]
    base_caps: dict[str, float] = {}
    for t in all_tickers:
        cap = panel.cap_usd(t, w0)
        if cap is not None and cap > 0:
            base_caps[t] = cap
            active.add(t)
            last_price[t] = panel.price[t][w0]
    if not base_caps:
        raise RuntimeError("no constituents have data at the base week")
    divisor = indexmath.base_divisor(base_caps, base_level)
    base_divisor = divisor
    base_total = indexmath.total_cap(base_caps)
    prev_level = base_level
    rows.append({
        "run_date": w0.isoformat(), "iso_week": iso_week(w0),
        "index_level": round(base_level, 6), "weekly_return_%": None,
        "total_mcap_usd": round(base_total, 2), "divisor_used": divisor,
        "n_ok": len(active), "n_missing": n_all - len(active),
        "anomaly_flag": "BASE",
    })

    # ---- subsequent weeks ------------------------------------------------- #
    for w in weeks[1:]:
        # price for each active name (carry forward single-week gaps)
        cur_price: dict[str, float] = {}
        for t in active:
            p = panel.price.get(t, {}).get(w)
            if p is None:
                p = last_price.get(t)
            if p is not None:
                cur_price[t] = p
                last_price[t] = p

        # additions: names with a first valid price this week
        new_names = []
        for t in all_tickers:
            if t in active:
                continue
            p = panel.price.get(t, {}).get(w)
            if p is None:
                continue
            cap = panel.cap_usd(t, w)
            if cap is not None and cap > 0:
                new_names.append((t, p, cap))

        for t, p, cap in new_names:
            sum_before = sum(panel.cap_usd(x, w, cur_price.get(x)) or 0.0
                             for x in active)
            if sum_before <= 0:
                # nothing active yet (shouldn't happen post-base) -> just seed
                active.add(t); cur_price[t] = p; last_price[t] = p
                continue
            sum_after = sum_before + cap
            old_div = divisor
            divisor = indexmath.adjust_divisor(divisor, sum_before, sum_after)
            events.append(DivisorEvent(
                effective_date=w, company=panel.name.get(t, t), ticker=t,
                event_type="index-add", sum_before_usd=sum_before,
                sum_after_usd=sum_after, old_divisor=old_div,
                new_divisor=divisor))
            active.add(t)
            cur_price[t] = p
            last_price[t] = p

        caps_now = {t: (panel.cap_usd(t, w, cur_price.get(t)) or 0.0)
                    for t in active}
        total = indexmath.total_cap(caps_now)
        level = total / divisor
        ret = ((level / prev_level - 1.0) * 100.0) if prev_level else None
        rows.append({
            "run_date": w.isoformat(), "iso_week": iso_week(w),
            "index_level": round(level, 6),
            "weekly_return_%": round(ret, 4) if ret is not None else None,
            "total_mcap_usd": round(total, 2), "divisor_used": divisor,
            "n_ok": len(active), "n_missing": n_all - len(active),
            "anomaly_flag": f"ADD({len(new_names)})" if new_names else "",
        })
        prev_level = level

    last_w = weeks[-1]
    final_caps = {t: (panel.cap_usd(t, last_w, last_price.get(t)) or 0.0)
                  for t in active}
    final_fx = {ccy: series.get(last_w) for ccy, series in panel.fx.items()
                if series.get(last_w)}
    final_fx["USD"] = 1.0
    return SeriesResult(
        rows=rows, events=events, base_date=w0, base_divisor=base_divisor,
        base_total_cap_usd=base_total, final_divisor=divisor,
        final_caps_usd=final_caps, final_fx=final_fx, final_level=prev_level,
        final_week=last_w, active=sorted(active))
