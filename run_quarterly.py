#!/usr/bin/env python3
"""Quarterly rebalance PROPOSAL (3rd Friday of Mar/Jun/Sep/Dec).

Refreshes shares/float, re-applies the weight cap, and solves for the proposed
new divisor.  Writes a before/after diff with status=PROPOSED.  It never
self-approves — a human must set status=APPROVED and run approve_quarterly.py.

Usage:
    python run_quarterly.py [YYYY-MM-DD]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.workflows import run_quarterly  # noqa: E402


def main(argv: list[str]) -> int:
    run_date = argv[1] if len(argv) > 1 else None
    p = run_quarterly(run_date=run_date)
    print(f"[quarterly] {p.run_date}: PROPOSED")
    print(f"   old_divisor   = {p.old_divisor:.6f}")
    print(f"   proposed_div  = {p.proposed_divisor:.6f}")
    print(f"   level_before  = {p.level_before:.6f}")
    print(f"   level_after   = {p.level_after:.6f}  (continuous)")
    movers = [r for r in p.rows if r.get("biggest_mover") == "YES"]
    if movers:
        print("   biggest movers:")
        for r in movers:
            print(f"     {r['ticker']:<12} {r['old_weight_%']:.3f}% -> "
                  f"{r['new_weight_%']:.3f}%")
    print("   -> Review the Quarterly sheet, set status=APPROVED, then run "
          "approve_quarterly.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
