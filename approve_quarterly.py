#!/usr/bin/env python3
"""Commit an APPROVED quarterly rebalance.

Reads the Quarterly sheet; commits the proposed divisor only if a human has set
status=APPROVED on the rows.  Otherwise it refuses (guardrail).

Usage:
    python approve_quarterly.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.workflows import commit_quarterly  # noqa: E402


def main() -> int:
    committed = commit_quarterly()
    if committed:
        print("[approve_quarterly] APPROVED rebalance committed; divisor "
              "updated and logged to Divisor_Log + Unscheduled.")
        return 0
    print("[approve_quarterly] No APPROVED rows found — nothing committed. "
          "Set status=APPROVED in the Quarterly sheet first.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
