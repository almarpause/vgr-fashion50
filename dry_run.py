#!/usr/bin/env python3
"""Full dry-run of all four workflows against LIVE-fetched data.

Runs, in order, against Yahoo/Stooq/Google + free FX:
  1. Weekly value job          (establishes the base, index == 1000.00)
  2. Quarterly rebalance        (PROPOSED)
  3. (simulated human approval) -> commit_quarterly  -> divisor change logged
  4. A simulated mid-quarter corporate action via the Unscheduled path
  5. Annual reconstitution      (PROPOSED)

Produces / updates ``Fashion50_Index.xlsx`` with the four worksheets populated.
A shared memoizing data provider fetches each ticker once to be gentle on the
free endpoints.  ``--fresh`` starts from a clean base.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import config, indexmath  # noqa: E402
from engine.datafetch import DataProvider  # noqa: E402
from engine.fx import FxProvider  # noqa: E402
from engine.state import load_state  # noqa: E402
from engine.workbook import WorkbookManager  # noqa: E402
from engine.workflows import (  # noqa: E402
    apply_corporate_action, commit_quarterly, run_annual, run_quarterly,
    run_weekly,
)

CANDIDATES_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "candidates.csv")


class MemoizingDataProvider(DataProvider):
    """Fetch each ticker at most once per dry-run."""

    def __init__(self) -> None:
        self._cache = {}

    def get_quote(self, ticker: str, google_ticker: str = ""):
        if ticker not in self._cache:
            self._cache[ticker] = super().get_quote(ticker, google_ticker)
        return self._cache[ticker]


def _fresh() -> None:
    for p in (config.WORKBOOK_PATH, config.STATE_PATH):
        if os.path.exists(p):
            os.remove(p)


def main(argv: list[str]) -> int:
    if "--fresh" in argv:
        _fresh()

    data = MemoizingDataProvider()
    fx = FxProvider()

    print("=" * 72)
    print("DRY RUN — VGR Fashion 50 (live data)")
    print("=" * 72)

    # 1) WEEKLY -------------------------------------------------------------
    print("\n[1/5] Weekly value job ...")
    w = run_weekly(data_provider=data, fx_provider=fx)
    if w.skipped_duplicate:
        print(f"   weekly already recorded for {w.iso_week} (idempotent).")
    else:
        print(f"   index_level={w.index_level:.4f}  n_ok={w.n_ok}  "
              f"n_missing={w.n_missing}  anomalies={len(w.anomalies)}")
        if w.bootstrapped:
            print("   -> base established at 1000.00")

    # 2) QUARTERLY (proposal) ----------------------------------------------
    print("\n[2/5] Quarterly rebalance (PROPOSED) ...")
    q = run_quarterly(data_provider=data, fx_provider=fx)
    print(f"   old_divisor={q.old_divisor:.4f} -> proposed={q.proposed_divisor:.4f}"
          f"  level_before={q.level_before:.4f} level_after={q.level_after:.4f}")

    # 3) SIMULATED APPROVAL -> commit --------------------------------------
    print("\n[3/5] Simulating human approval of the quarterly proposal ...")
    wbm = WorkbookManager()
    rows = wbm.read_rows("Quarterly")
    for r in rows:
        r["status"] = "APPROVED"
    wbm.replace_sheet_rows("Quarterly", rows)
    wbm.save()
    committed = commit_quarterly(data_provider=data, fx_provider=fx)
    print(f"   commit_quarterly -> {'APPLIED' if committed else 'refused'}")

    # 4) UNSCHEDULED corporate action --------------------------------------
    print("\n[4/5] Simulated mid-quarter corporate action (buyback) ...")
    st = load_state()
    sum_before = indexmath.total_cap(st.last_caps_usd)
    # Simulate a 2% share buyback on the largest name (cap shrinks 2%).
    largest = max(st.last_caps_usd, key=st.last_caps_usd.get)
    sum_after = sum_before - st.last_caps_usd[largest] * 0.02
    new_div = apply_corporate_action(
        f"{largest} (buyback)", "buyback", sum_before, sum_after)
    print(f"   divisor -> {new_div:.4f} (level continuous, logged to "
          f"Unscheduled + Divisor_Log)")

    # 5) ANNUAL (proposal) --------------------------------------------------
    print("\n[5/5] Annual reconstitution (PROPOSED) ...")
    candidates = []
    if os.path.exists(CANDIDATES_CSV):
        candidates = config.load_constituents(CANDIDATES_CSV)
    a = run_annual(data_provider=data, fx_provider=fx, candidates=candidates)
    print(f"   {len(a.rows)} ranked rows.  ADD={a.adds or 'none'}  "
          f"DROP={a.drops or 'none'}")

    # Summary ---------------------------------------------------------------
    wbm = WorkbookManager()
    print("\n" + "-" * 72)
    print("Workbook sheet row counts:")
    for sheet in ("Weekly", "Quarterly", "Annual", "Unscheduled",
                  "Constituents", "FX", "Alerts", "Divisor_Log", "Audit"):
        print(f"   {sheet:<14} {len(wbm.read_rows(sheet)):>4} rows")
    print(f"\nArtifact: {config.WORKBOOK_PATH}")
    print("DRY RUN complete — no unhandled exceptions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
