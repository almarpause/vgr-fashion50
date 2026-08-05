#!/usr/bin/env python3
"""Weekly value job (fully automatic; never changes membership or divisor).

Usage:
    python run_weekly.py [YYYY-MM-DD] [--force]

Idempotent: re-running the same ISO week does not add a duplicate row.
Use --force to refresh (overwrite) the current week's row intra-week.
Run backfill.py first to establish the multi-year base + history.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.workflows import run_weekly  # noqa: E402


def main(argv: list[str]) -> int:
    force = "--force" in argv
    pos = [a for a in argv[1:] if not a.startswith("--")]
    run_date = pos[0] if pos else None

    out = run_weekly(run_date=run_date, force=force)
    if out.skipped_duplicate:
        print(f"[weekly] {out.iso_week}: already recorded — skipped (idempotent). "
              f"Use --force to overwrite.")
        return 0
    tag = "BASE=1000.00" if out.bootstrapped else (
        f"WoW={out.weekly_return_pct:+.2f}%"
        if out.weekly_return_pct is not None else "WoW=n/a")
    print(f"[weekly] {out.iso_week} {out.run_date}: level={out.index_level:.4f} "
          f"{tag} | n_ok={out.n_ok} n_missing={out.n_missing} "
          f"divisor={out.divisor_used:.4f}")
    if out.movers:
        movers = "  ".join(f"{t} {p:+.1f}%" for t, p in out.movers)
        print(f"[weekly] top movers: {movers}")
    if out.anomaly_flag:
        print(f"[weekly] {len(out.anomalies)} anomaly(ies) routed to "
              f"Unscheduled + Alerts:")
        for a in out.anomalies:
            print(f"   - [{a.kind}] {a.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
