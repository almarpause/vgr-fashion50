#!/usr/bin/env python3
"""Backfill a daily index time series (and per-segment sub-indices) into the workbook.

Fetches daily closes + FX from ``--history-start`` (default 2020-01-01) to today,
reconstructs the index with the divisor rule (IPOs added mid-series), then rescales
the whole series so it equals 1000 on the anchor/base date (``--base``, default
2025-01-01).  Also builds a sub-index per segment (Luxury / Fashion / Sportswear /
Apparel / Footwear), each rebased to 1000 on the same anchor.

Usage:
    python backfill.py [--history-start 2020-01-01] [--base 2025-01-01] [--fresh]

Note: historical share counts are not free, so the *current* shares/float are held
constant across history — the series is price-and-FX driven (see Methodology).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import config, indexmath  # noqa: E402
from engine.config import load_constituents, primary_segment  # noqa: E402
from engine.datafetch import DataProvider  # noqa: E402
from engine.history import (  # noqa: E402
    build_index_series, fetch_weekly_history, filter_panel,
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


def _anchor_level(series, base_iso):
    """Level on the first series date >= base_iso (the =1000 anchor point)."""
    for d, lv in series:
        if d >= base_iso and lv:
            return lv
    return series[-1][1] if series else None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--history-start", type=str, default="2020-01-01",
                    help="fetch daily data from this date (or 'whenever available')")
    ap.add_argument("--base", type=str, default="2025-01-01",
                    help="anchor date where the index == 1000")
    ap.add_argument("--fresh", action="store_true")
    args = ap.parse_args(argv[1:])
    base_iso = args.base

    if args.fresh:
        for p in (config.WORKBOOK_PATH, config.STATE_PATH):
            if os.path.exists(p):
                os.remove(p)

    settings = config.DEFAULT_SETTINGS
    constituents = load_constituents()

    print(f"Fetching daily history from {args.history_start} for "
          f"{len(constituents)} names (bulk prices + FX) ...")
    panel = fetch_weekly_history(constituents, data_provider=_Memo(),
                                 interval="1d", start=args.history_start)
    if not panel.weeks:
        print("No history returned — aborting.")
        return 1
    if panel.missing_snapshot:
        print(f"  {len(panel.missing_snapshot)} names excluded (no snapshot): "
              f"{panel.missing_snapshot}")

    print("Building index series ...")
    res = build_index_series(panel, settings)

    # ---- Rescale the whole series so it equals 1000 on the anchor date. ----- #
    main_series = [(r["run_date"], r["index_level"]) for r in res.rows]
    anchor_raw = _anchor_level(main_series, base_iso)
    if not anchor_raw:
        print(f"No data at/after anchor {base_iso} — aborting.")
        return 1
    k = 1000.0 / anchor_raw
    for r in res.rows:
        r["index_level"] = round(r["index_level"] * k, 6)
        if r.get("divisor_used"):
            r["divisor_used"] = r["divisor_used"] / k
    final_level = res.rows[-1]["index_level"]
    since = (final_level / 1000.0 - 1.0) * 100.0
    print(f"  {len(res.rows)} daily points {res.base_date}..{res.final_week}; "
          f"anchored 1000 on {base_iso}; latest = {final_level:.2f} "
          f"({since:+.1f}% since {base_iso}); {len(res.events)} IPO adds")

    wbm = WorkbookManager()
    wbm.replace_sheet_rows("Weekly", res.rows)

    # ---- Segment sub-indices (each rebased to 1000 on the anchor). ---------- #
    seg_groups: dict[str, list[str]] = {}
    for t in res.active:
        seg = primary_segment(panel.segment.get(t, "?"))
        seg_groups.setdefault(seg, []).append(t)
    weights_now = indexmath.compute_weights(res.final_caps_usd,
                                            cap=settings.effective_cap)
    seg_weight = {s: sum(weights_now.get(t, 0) for t in tks)
                  for s, tks in seg_groups.items()}
    segments_sorted = sorted(seg_groups, key=lambda s: -seg_weight.get(s, 0))
    sub_dates = [r["run_date"] for r in res.rows if r["run_date"] >= base_iso]
    sub_levels: dict[str, dict[str, float]] = {}
    for seg in segments_sorted:
        sp = filter_panel(panel, seg_groups[seg])
        try:
            sres = build_index_series(sp, settings)
        except Exception:
            continue
        ss = [(r["run_date"], r["index_level"]) for r in sres.rows]
        a = _anchor_level(ss, base_iso)
        if not a:
            continue
        ks = 1000.0 / a
        sub_levels[seg] = {d: round(lv * ks, 4) for d, lv in ss if d >= base_iso}
    wbm.write_subindices(sub_dates, segments_sorted, sub_levels)

    # ---- Divisor log / Unscheduled add-events ------------------------------ #
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

    # ---- Audit (latest) ---------------------------------------------------- #
    last_w = res.final_week
    audit_rows = []
    for t in res.active:
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

    # ---- Per-company price panel (selector) — sliced to the anchor onward. -- #
    base_d = date.fromisoformat(base_iso)
    price_slice = {t: {d: v for d, v in panel.price[t].items() if d >= base_d}
                   for t in res.active if t in panel.price}
    sel_dates = [date.fromisoformat(r["run_date"]) for r in res.rows
                 if r["run_date"] >= base_iso]
    wbm.write_prices(sel_dates, res.active, price_slice)

    # ---- Summary + Methodology --------------------------------------------- #
    caps = res.final_caps_usd
    ranked = sorted(weights_now, key=weights_now.get, reverse=True)
    top10 = [(t, panel.name.get(t, t), weights_now[t]) for t in ranked[:10]]
    sectors = sorted(seg_weight.items(), key=lambda kv: -kv[1])
    kpis = [
        ("Anchor (base) date", base_iso),
        ("Base level", 1000.0),
        ("History start", res.base_date.isoformat()),
        ("Latest date", res.final_week.isoformat()),
        ("Latest level", round(final_level, 2)),
        (f"Since {base_iso} %", round(since, 2)),
        ("Daily points", len(res.rows)),
        ("Constituents in index", len(res.active)),
        ("Segments", len(sub_levels)),
        ("Names excluded (no snapshot)", len(panel.missing_snapshot)),
        ("IPO add events", len(res.events)),
        ("Weight cap", "off" if not settings.effective_cap
         else f"{int(settings.effective_cap*100)}%"),
    ]
    wbm.write_summary(kpis, top10, sectors)
    wbm.add_index_chart()
    wbm.write_methodology([
        ("Construction", "Market-cap weighted, divisor-based, float-adjusted "
         "(S&P-500 style). All calculations in USD."),
        ("Base / anchor", f"The whole series is rescaled so the index == 1000.00 "
         f"on {base_iso}. History runs from {res.base_date.isoformat()}."),
        ("Sub-indices", "Per-segment (Luxury / Fashion / Sportswear / Apparel / "
         "Footwear) mini-indices, each rebased to 1000 on the same anchor date."),
        ("Divisor rule", "On any add/drop/share change: Divisor_new = "
         "Divisor_old * (Sum caps AFTER / Sum caps BEFORE), so the level is "
         "continuous. Rescaling by a constant preserves that continuity."),
        ("Prices", "yfinance daily Close (split-adjusted), normalised out of "
         "minor units (GBp/ZAc -> major /100) before FX."),
        ("FX", "frankfurter.app (ECB) daily USD-per-unit; yfinance FX pairs "
         "fallback. Stored on the FX sheet."),
        ("Shares / float — APPROXIMATION", "Historical share counts are not "
         "available for free, so CURRENT sharesOutstanding and floatShares are "
         "held constant across history; the series is price-and-FX driven."),
        ("Mid-series adds", "Names that IPO'd after the history start are added "
         "on their first traded day via the divisor rule (see Divisor_Log)."),
        ("Weight cap", "Disabled — weights are pure float-adjusted market-cap "
         "shares." if not settings.effective_cap else
         f"Single-name cap {int(settings.effective_cap*100)}%."),
    ])

    # ---- State (rescaled basis so live weekly runs continue the series). ---- #
    st = EngineState()
    st.base_date = base_iso
    st.base_divisor = res.base_divisor / k
    st.current_divisor = res.final_divisor / k
    st.base_index_level = 1000.0
    st.base_total_cap_usd = res.base_total_cap_usd
    st.constituents = res.active
    st.last_caps_usd = res.final_caps_usd
    st.last_shares = {t: panel.shares[t] for t in res.active if t in panel.shares}
    st.last_float_factor = {t: panel.float_factor[t] for t in res.active
                            if t in panel.float_factor}
    st.last_fx = res.final_fx
    st.last_index_level = final_level
    st.last_run_date = res.final_week.isoformat()

    wbm.save()
    save_state(st)

    print(f"\nWrote {config.WORKBOOK_PATH}")
    print(f"  Segments: {', '.join(f'{s} ({int(seg_weight[s]*100)}%)' for s in segments_sorted)}")
    print("  Run  python dashboard.py  to generate the HTML dashboard.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
