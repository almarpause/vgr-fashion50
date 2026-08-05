"""Pure index mathematics — no I/O, no network, fully unit-testable.

Methodology (implemented exactly as specified in the brief):

    float_adjusted_cap_local = price * shares_outstanding * float_factor
    cap_usd                  = float_adjusted_cap_local * fx_rate_to_usd
    Index level              = Sum(cap_usd) / Divisor

Base date:   Divisor_base = Sum(cap_usd at base) / base_level   (base_level=1000)
Divisor rule (composition / shares / float changes only, never Weekly):
    Divisor_new = Divisor_old * ( Sum caps_usd AFTER / Sum caps_usd BEFORE )

The single-name weight cap redistributes excess weight across the uncapped
names *while preserving the total sum of caps*.  Because the total is
preserved, the index level (Sum caps / divisor) is identical with or without
capping; the cap only reshapes the reported weights and enforces the ceiling.
"""
from __future__ import annotations

from typing import Mapping


def float_adjusted_cap_local(price: float, shares_outstanding: float,
                             float_factor: float) -> float:
    """Float-adjusted market cap in the security's local (major) currency."""
    return float(price) * float(shares_outstanding) * float(float_factor)


def to_usd(cap_local: float, fx_rate_to_usd: float) -> float:
    """Convert a local-currency cap to USD.

    ``fx_rate_to_usd`` is USD per 1 unit of local currency (USD itself == 1.0).
    """
    return float(cap_local) * float(fx_rate_to_usd)


def float_factor_from(float_shares: float | None,
                      shares_outstanding: float | None) -> float:
    """float_factor = floatShares / sharesOutstanding, else 1.0.

    Guards against nonsense (<=0, >1, missing) by falling back to 1.0.
    """
    if not float_shares or not shares_outstanding:
        return 1.0
    if shares_outstanding <= 0 or float_shares <= 0:
        return 1.0
    ff = float(float_shares) / float(shares_outstanding)
    if ff <= 0 or ff > 1.0:
        return 1.0
    return ff


def total_cap(caps: Mapping[str, float]) -> float:
    return float(sum(caps.values()))


def index_level(caps: Mapping[str, float], divisor: float) -> float:
    """Index level = Sum(cap_usd) / Divisor."""
    if divisor == 0:
        raise ZeroDivisionError("divisor is zero")
    return total_cap(caps) / float(divisor)


def base_divisor(caps: Mapping[str, float], base_level: float = 1000.0) -> float:
    """Divisor that makes the index equal ``base_level`` for these caps."""
    s = total_cap(caps)
    if s <= 0:
        raise ValueError("cannot derive base divisor from non-positive total cap")
    return s / float(base_level)


def adjust_divisor(old_divisor: float, sum_before: float,
                   sum_after: float) -> float:
    """Divisor_new = Divisor_old * (sum_after / sum_before).

    Applied at the effective instant so the index level is continuous:
    level_before == level_after.
    """
    if sum_before <= 0:
        raise ValueError("sum_before must be positive")
    return float(old_divisor) * (float(sum_after) / float(sum_before))


def apply_weight_cap(caps: Mapping[str, float], cap: float | None = 0.10,
                     max_iter: int = 1000, tol: float = 1e-12
                     ) -> dict[str, float]:
    """Return capped caps (total preserved) so that no weight exceeds ``cap``.

    Iterative redistribution: over-cap names are pinned to ``cap`` of the total
    and the freed weight is spread across the remaining names in proportion to
    their existing size; repeat until no name breaches the ceiling.  ``cap=None``
    (or <=0) disables capping and returns the caps unchanged.
    """
    vals = {k: float(v) for k, v in caps.items()}
    if cap is None or cap <= 0:
        return vals

    keys = list(vals)
    n = len(keys)
    if n == 0:
        return vals
    if cap * n < 1.0 - 1e-9:
        raise ValueError(
            f"weight cap {cap:.4f} infeasible for {n} names "
            f"(cap * n = {cap * n:.4f} < 1)"
        )

    total = sum(vals.values())
    if total <= 0:
        return vals

    for _ in range(max_iter):
        weights = {k: vals[k] / total for k in keys}
        over = [k for k in keys if weights[k] > cap + tol]
        if not over:
            break
        under = [k for k in keys if k not in over]
        target = cap * total                      # capped names pinned here
        freed = sum(vals[k] for k in over) - target * len(over)
        for k in over:
            vals[k] = target
        under_sum = sum(vals[k] for k in under)
        if under_sum <= 0:
            break
        for k in under:
            vals[k] += freed * (vals[k] / under_sum)
    return vals


def cap_is_feasible(n_names: int, cap: float | None) -> bool:
    """True if a per-name cap can be satisfied while weights sum to 1."""
    if cap is None or cap <= 0:
        return True          # "no cap" is always feasible
    return cap * n_names >= 1.0 - 1e-9


def compute_weights(caps: Mapping[str, float], cap: float | None = None,
                    max_iter: int = 1000, strict: bool = False
                    ) -> dict[str, float]:
    """Weights (summing to 1.0) after applying the optional weight cap.

    If the cap is infeasible for this universe size (cap * n < 1) and
    ``strict`` is False, capping is skipped rather than raising.
    """
    if not strict and not cap_is_feasible(len(caps), cap):
        cap = None
    capped = apply_weight_cap(caps, cap=cap, max_iter=max_iter)
    total = sum(capped.values())
    if total <= 0:
        return {k: 0.0 for k in capped}
    return {k: capped[k] / total for k in capped}
