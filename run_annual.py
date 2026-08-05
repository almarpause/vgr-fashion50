#!/usr/bin/env python3
"""Annual reconstitution PROPOSAL (change which 50).

Screens the constituent universe (plus optional candidates.csv) by
float-adjusted USD cap, applies the 40/60 buffer rule to minimise churn, and
proposes ADD/DROP/HOLD rows.  Requires human scope_ok=YES + status=APPROVED
before approve_annual.py will apply anything.

Usage:
    python run_annual.py [YYYY-MM-DD]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.config import load_constituents  # noqa: E402
from engine.workflows import run_annual  # noqa: E402

CANDIDATES_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "candidates.csv")


def main(argv: list[str]) -> int:
    run_date = argv[1] if len(argv) > 1 else None
    candidates = []
    if os.path.exists(CANDIDATES_CSV):
        candidates = load_constituents(CANDIDATES_CSV)
    p = run_annual(run_date=run_date, candidates=candidates)
    print(f"[annual] {p.run_date}: reconstitution PROPOSED")
    print(f"   ADD  candidates: {p.adds or 'none'}")
    print(f"   DROP candidates: {p.drops or 'none'}")
    print(f"   {len(p.rows)} rows written to Annual sheet.")
    print("   -> Confirm scope_ok=YES and status=APPROVED, then run "
          "approve_annual.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
