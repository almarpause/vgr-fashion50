#!/usr/bin/env python3
"""Backfill a real multi-year weekly index time series into the workbook.

Fetches ~5y of weekly closes + weekly FX (free sources), reconstructs the index
(base = 1000 at the start), adds names on their first traded week via the divisor
rule, and writes the full Weekly series + Summary chart + Divisor_Log / Unscheduled
add-events + Methodology.  Rebuilds ``state.json`` so live weekly runs append
onto this history.

Usage:
    python backfill.py [--years 5] [--base YYYY-MM-DD] [--fresh]

Note: historical share counts are not available for free, so the *current*
shares/float are held constant across history — the series is price-and-FX
driven.  This is a documented approximation (see the Methodology sheet).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import config, indexmath  # noqa: E402
from engine.config import load_constituents  # noqa: E402
from engine.datafetch import DataProvider  # noqa: E402
from engine.history import (  # noqa: E402
    HistoryPanel, build_index_series, fetch_weekly_history,
)
from engine.state import EngineState, save_state  # noqa: E402
from engine.workbook import WorkbookManager  # noqa: E402
from engine.workflows import sync_constituents_sheet  # noqa: E402


class _Memo(DataProvider):
    def __init__(self):
        self._c = {}

    def get_quote(self, ticker, google_ticker=""):
        if ticker not in self._c:
            self._c[ticker] = super().get_quote(ticker, google_ticker)
        return self._c[ticker]


def _trim(panel: HistoryPanel, base: date) -> HistoryPanel:
    weeks = [w for w in panel.weeks if w >= base]
    price = {t: {w: v for w, v in s.items() if w >= base}
             for t, s in panel.price.items()}
    fx = {c: {w: v for w, v in s.items() if w >= base}
          for c, s in panel.fx.items()}
    return HistoryPanel(weeks=weeks, price=price, fx=fx,
                        currency=panel.currency, shares=panel.shares,
                        float_factor=panel.float_factor, name=panel.name,
                        segment=panel.segment,
                        missing_snapshot=panel.missing_snapshot)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--base", type=str, default=None,
                    help="base date YYYY-MM-DD (overrides implied start)")
    ap.add_argument("--fresh", action="store_true")
    args = ap.parse_args(argv[1:])

    if args.fresh:
        for p in (config.WORKBOOK_PATH, config.STATE_PATH):
            if os.path.exists(p):
                os.remove(p)

    settings = config.DEFAULT_SETTINGS
    constituents = load_constituents()
    period = f"{max(args.years, 1)}y"

    print(f"Fetching {period} of weekly history for {len(constituents)} names "
          f"(bulk prices + FX) ...")
    panel = fetch_weekly_history(constituents, period=period,
                                 data_provider=_Memo())
    if args.base:
        panel = _trim(panel, date.fromisoformat(args.base))
    if not panel.weeks:
        print("No history returned — aborting.")
        return 1
    if panel.missing_snapshot:
        print(f"  {len(panel.missing_snapshot)} names lacked a shares/currency "
              f"snapshot and are excluded: {panel.missing_snapshot}")

    print("Building index series ...")
    res = build_index_series(panel, settings)
    print(f"  {len(res.rows)} weekly points, base {res.base_date} = 1000.00, "
          f"latest {res.final_week} = {res.final_level:.2f}, "
          f"{len(res.events)} index-add events")

    wbm = WorkbookManager()
    # Weekly time series
    wbm.replace_sheet_rows("Weekly", res.rows)

    # Divisor changes + Unscheduled add-events
    wbm.replace_sheet_rows("Divisor_Log", [{
        "timestamp": e.effective_date.isoformat(),
        "reason": "backfill:index-add", "old_divisor": e.old_divisor,
        "new_divisor": e.new_divisor, "sum_before_usd": round(e.sum_before_usd, 2),
        "sum_after_usd": round(e.sum_after_usd, 2),
        "index_level_before": round(e.sum_before_usd / e.old_divisor, 6),
        "index_level_after": round(e.sum_after_usd / e.new_divisor, 6),
        "source_sheet": "Backfill",
    } for e in res.events])
    wbm.replace_sheet_rows("Unscheduled", [{
        "effective_date": e.effective_date.isoformat(), "company": e.company,
        "event_type": "index-add", "detected_by": "backfill",
        "sum_before_usd": round(e.sum_before_usd, 2),
        "sum_after_usd": round(e.sum_after_usd, 2),
        "old_divisor": e.old_divisor, "new_divisor": e.new_divisor,
        "status": "APPLIED",
    } for e in res.events])

    # FX (final week snapshot) + Constituents + Audit (final week)
    wbm.replace_sheet_rows("FX", [{
        "run_date": res.final_week.isoformat(), "currency": ccy,
        "rate_to_usd": rate, "source": "backfill", "status": "OK",
    } for ccy, rate in sorted(res.final_fx.items())])
    sync_constituents_sheet(wbm, constituents, settings)
    if panel.missing_snapshot:
        from datetime import datetime, timezone
        wbm.append_row("Alerts", {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "severity": "WARN",
            "subject": f"{len(panel.missing_snapshot)} name(s) excluded from "
                       f"backfill (insufficient history)",
            "detail": "MISSING (not invented): " + ", ".join(panel.missing_snapshot),
        })

    last_w = res.final_week
    audit_rows = []
    for t in res.active:
        # Actual price used = most recent available on/before the final week
        # (carried forward when this week's bar hasn't printed yet).
        series = panel.price.get(t, {})
        avail = [d for d in series if d <= last_w]
        asof = max(avail) if avail else None
        price = series.get(asof) if asof else None
        stale = asof is not None and asof != last_w
        src = "yfinance-history" + (f" (as-of {asof.isoformat()})" if stale else "")
        audit_rows.append({
            "run_date": last_w.isoformat(), "ticker": t,
            "name": panel.name.get(t, t), "price_major": price,
            "currency": panel.currency.get(t),
            "shares_outstanding": panel.shares.get(t),
            "float_shares": None,
            "float_factor": round(panel.float_factor.get(t, 1.0), 6),
            "fx_rate_to_usd": res.final_fx.get(panel.currency.get(t, "USD")),
            "cap_usd": round(res.final_caps_usd.get(t, 0.0), 2),
            "price_source": src, "fx_source": "frankfurter",
            "status": "OK",
        })
    wbm.replace_sheet_rows("Audit", audit_rows)

    # Per-company weekly price panel (drives the dashboard selector + YTD/MoM).
    series_weeks = [date.fromisoformat(r["run_date"]) for r in res.rows]
    wbm.write_prices(series_weeks, res.active, panel.price)

    # Summary KPIs + weights + sectors + chart
    caps = res.final_caps_usd
    weights = indexmath.compute_weights(caps, cap=settings.effective_cap)
    ranked = sorted(weights, key=weights.get, reverse=True)
    top10 = [(t, panel.name.get(t, t), weights[t]) for t in ranked[:10]]
    seg_w: dict[str, float] = {}
    for t, w in weights.items():
        seg = panel.segment.get(t, "?")
        seg_w[seg] = seg_w.get(seg, 0.0) + w
    sectors = sorted(seg_w.items(), key=lambda kv: -kv[1])

    def _ret(weeks_back: int) -> float | None:
        if len(res.rows) <= weeks_back:
            return None
        past = res.rows[-1 - weeks_back]["index_level"]
        return (res.final_level / past - 1.0) * 100.0 if past else None

    since = (res.final_level / 1000.0 - 1.0) * 100.0
    kpis = [
        ("Base date", res.base_date.isoformat()),
        ("Base level", 1000.0),
        ("Latest date", res.final_week.isoformat()),
        ("Latest level", round(res.final_level, 2)),
        ("Since-inception return %", round(since, 2)),
        ("1-year return % (52w)", round(_ret(52), 2) if _ret(52) is not None else "n/a"),
        ("Weeks of history", len(res.rows)),
        ("Constituents in index", len(res.active)),
        ("Names excluded (no snapshot)", len(panel.missing_snapshot)),
        ("Index-add events", len(res.events)),
        ("Current divisor", round(res.final_divisor, 4)),
        ("Weight cap", f"{int(settings.effective_cap*100)}%"
         if settings.effective_cap else "off"),
    ]
    wbm.write_summary(kpis, top10, sectors)
    wbm.add_index_chart()

    wbm.write_methodology([
        ("Construction", "Market-cap weighted, divisor-based, float-adjusted "
         "(S&P-500 style). All calculations in USD."),
        ("Base", f"Index = 1000.00 on {res.base_date.isoformat()}; "
         f"Divisor_base = Sum(cap_usd at base) / 1000."),
        ("Divisor rule", "On any add/drop/share change: Divisor_new = "
         "Divisor_old * (Sum caps AFTER / Sum caps BEFORE) so the level is "
         "continuous."),
        ("Prices", "yfinance weekly Close (split-adjusted), normalised out of "
         "minor units (GBp/ZAc -> major /100) before FX."),
        ("FX", "frankfurter.app (ECB) weekly USD-per-unit; yfinance FX pairs "
         "fallback. Stored on the FX sheet."),
        ("Shares / float — APPROXIMATION", "Historical share counts are not "
         "available for free, so the CURRENT sharesOutstanding and floatShares "
         "are held constant across the backfill. The reconstruction is "
         "therefore price-and-FX driven; historical share/float changes are not "
         "modelled. Live Quarterly refreshes update the basis going forward."),
        ("Mid-series adds", "Names that IPO'd after the base date are added on "
         "their first traded week via the divisor rule (see Divisor_Log / "
         "Unscheduled), keeping the level continuous."),
        ("Weight cap", f"Single-name cap {int(settings.effective_cap*100)}% "
         "with iterative redistribution (reporting weights; preserves total)."
         if settings.effective_cap else "Weight cap disabled."),
    ])

    # Rebuild state so live weekly runs continue the series.
    st = EngineState()
    st.base_date = res.base_date.isoformat()
    st.base_divisor = res.base_divisor
    st.current_divisor = res.final_divisor
    st.base_index_level = 1000.0
    st.base_total_cap_usd = res.base_total_cap_usd
    st.constituents = res.active
    st.last_caps_usd = res.final_caps_usd
    st.last_shares = {t: panel.shares[t] for t in res.active if t in panel.shares}
    st.last_float_factor = {t: panel.float_factor[t] for t in res.active
                            if t in panel.float_factor}
    st.last_fx = res.final_fx
    st.last_index_level = res.final_level
    st.last_run_date = res.final_week.isoformat()

    wbm.save()
    save_state(st)

    print(f"\nWrote {config.WORKBOOK_PATH}")
    print(f"  Summary: base {res.base_date} = 1000  ->  "
          f"{res.final_week} = {res.final_level:.2f}  "
          f"({since:+.1f}% since inception)")
    print("  Run  python dashboard.py  to generate the HTML dashboard.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
