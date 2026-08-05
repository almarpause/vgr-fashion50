#!/usr/bin/env python3
"""Commit an APPROVED annual reconstitution.

Applies ADD/DROP rows that a human confirmed (scope_ok=YES, status=APPROVED)
via the divisor rule so the index level stays continuous.

Usage:
    python approve_annual.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.workflows import commit_annual  # noqa: E402


def main() -> int:
    committed = commit_annual()
    if committed:
        print("[approve_annual] APPROVED adds/drops applied via divisor rule; "
              "level continuous. Logged to Divisor_Log + Unscheduled.")
        return 0
    print("[approve_annual] No rows with scope_ok=YES and status=APPROVED — "
          "nothing applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
