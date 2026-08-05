#!/usr/bin/env python3
"""Early-warning pre-flight scan (run a few days BEFORE the weekly job).

Looks ahead for anything that might change the index calculation — share-count
drift (issuance/buyback), upcoming splits / earnings / ex-dividends, at-risk
data (a ticker going stale/404), membership drift, and the fixed index-review
dates — and writes them to the ``Watchlist`` sheet + emails a digest.

It NEVER changes the index; it only warns so a human can pre-decide the divisor
treatment instead of reacting after a jump.

Usage:
    python preflight.py [--horizon 21]
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import config, indexmath  # noqa: E402
from engine.alerts import send_alert  # noqa: E402
from engine.config import load_constituents  # noqa: E402
from engine.preflight import PreflightSignals, scan  # noqa: E402
from engine.state import load_state  # noqa: E402
from engine.workbook import WorkbookManager  # noqa: E402


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=None)
    args = ap.parse_args(argv[1:])

    settings = config.DEFAULT_SETTINGS
    if args.horizon:
        settings = config.Settings(watch_horizon_days=args.horizon)

    constituents = load_constituents()
    state = load_state()
    today = date.today()

    caps_ranks = None
    if state.last_caps_usd:
        caps_ranks = {t: i + 1 for i, t in enumerate(
            sorted(state.last_caps_usd, key=state.last_caps_usd.get,
                   reverse=True))}

    print(f"Pre-flight scan {today.isoformat()} "
          f"(horizon {settings.watch_horizon_days}d) — {len(constituents)} names ...")
    items = scan(constituents, state, PreflightSignals(), today, settings,
                 caps_ranks=caps_ranks)

    wbm = WorkbookManager()
    wbm.replace_sheet_rows("Watchlist", [{
        "scan_date": today.isoformat(), "ticker": it.ticker,
        "company": it.company, "category": it.category, "signal": it.signal,
        "severity": it.severity,
        "event_date": it.event_date.isoformat() if it.event_date else None,
        "days_out": it.days_out, "suggested_action": it.suggested_action,
        "source": it.source,
    } for it in items])

    highs = [it for it in items if it.severity in ("HIGH", "MEDIUM")]
    if highs:
        body = "\n".join(
            f"- [{it.severity}/{it.category}] {it.company} ({it.ticker}): "
            f"{it.signal} -> {it.suggested_action}" for it in highs)
        wbm.append_row("Alerts", {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "severity": "WATCH",
            "subject": f"Pre-flight: {len(highs)} item(s) need attention",
            "detail": body,
        })
        send_alert(f"Pre-flight watch: {len(highs)} item(s)", body)
    wbm.save()

    # Console digest
    by_cat: dict[str, int] = {}
    for it in items:
        by_cat[it.category] = by_cat.get(it.category, 0) + 1
    print(f"  {len(items)} watch item(s): "
          + ", ".join(f"{k}={v}" for k, v in sorted(by_cat.items())))
    for it in items:
        if it.severity in ("HIGH", "MEDIUM"):
            when = f" in {it.days_out}d" if it.days_out is not None else ""
            print(f"   [{it.severity}] {it.company} ({it.ticker}) — "
                  f"{it.signal}{when}")
    print(f"Wrote Watchlist to {config.WORKBOOK_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
