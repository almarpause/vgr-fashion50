"""Unit tests for the pure index mathematics."""
import math

import pytest

from engine import indexmath


TOL = 1e-6


def test_float_adjusted_cap_and_usd():
    cap_local = indexmath.float_adjusted_cap_local(50.0, 2_000_000, 0.8)
    assert cap_local == 50.0 * 2_000_000 * 0.8
    cap_usd = indexmath.to_usd(cap_local, 1.10)
    assert cap_usd == pytest.approx(cap_local * 1.10)


def test_float_factor_from():
    assert indexmath.float_factor_from(900_000, 1_000_000) == pytest.approx(0.9)
    assert indexmath.float_factor_from(None, 1_000_000) == 1.0
    assert indexmath.float_factor_from(0, 1_000_000) == 1.0
    # nonsense (>1) falls back to 1.0
    assert indexmath.float_factor_from(2_000_000, 1_000_000) == 1.0


def test_base_divisor_gives_1000():
    caps = {"A": 4_000_000.0, "B": 3_000_000.0, "C": 3_000_000.0}
    div = indexmath.base_divisor(caps, 1000.0)
    level = indexmath.index_level(caps, div)
    assert level == pytest.approx(1000.0, abs=TOL)


def test_divisor_continuity_on_add():
    """Adding a constituent must not jump the level (divisor rule)."""
    caps = {"A": 4_000_000.0, "B": 3_000_000.0, "C": 3_000_000.0}
    div = indexmath.base_divisor(caps, 1000.0)
    level_before = indexmath.index_level(caps, div)

    caps_after = dict(caps, D=2_500_000.0)
    sum_before = indexmath.total_cap(caps)
    sum_after = indexmath.total_cap(caps_after)
    new_div = indexmath.adjust_divisor(div, sum_before, sum_after)
    level_after = indexmath.index_level(caps_after, new_div)

    assert level_after == pytest.approx(level_before, abs=TOL)


def test_divisor_continuity_on_drop():
    caps = {"A": 4_000_000.0, "B": 3_000_000.0, "C": 3_000_000.0}
    div = indexmath.base_divisor(caps, 1000.0)
    level_before = indexmath.index_level(caps, div)
    caps_after = {"A": 4_000_000.0, "B": 3_000_000.0}
    new_div = indexmath.adjust_divisor(div, indexmath.total_cap(caps),
                                       indexmath.total_cap(caps_after))
    level_after = indexmath.index_level(caps_after, new_div)
    assert level_after == pytest.approx(level_before, abs=TOL)


def test_weight_cap_redistribution():
    # One dominant name at 60% must be capped to 10%.
    caps = {"BIG": 60.0, "A": 10.0, "B": 10.0, "C": 10.0, "D": 10.0,
            "E": 10.0, "F": 10.0, "G": 10.0, "H": 10.0, "I": 10.0, "J": 10.0}
    weights = indexmath.compute_weights(caps, cap=0.10)
    assert sum(weights.values()) == pytest.approx(1.0, abs=TOL)
    assert max(weights.values()) <= 0.10 + TOL
    assert weights["BIG"] == pytest.approx(0.10, abs=TOL)


def test_weight_cap_disabled():
    caps = {"BIG": 60.0, "A": 40.0}
    weights = indexmath.compute_weights(caps, cap=None)
    assert weights["BIG"] == pytest.approx(0.6, abs=TOL)


def test_weight_cap_infeasible_raises():
    # 5 names cannot all be <= 10% (sum would be <= 0.5 < 1).
    caps = {k: 1.0 for k in "ABCDE"}
    with pytest.raises(ValueError):
        indexmath.apply_weight_cap(caps, cap=0.10)


def test_weight_cap_preserves_total():
    caps = {"BIG": 500.0, "A": 100.0, "B": 100.0, "C": 100.0, "D": 100.0,
            "E": 100.0, "F": 100.0, "G": 100.0, "H": 100.0, "I": 100.0,
            "J": 100.0}
    capped = indexmath.apply_weight_cap(caps, cap=0.10)
    assert sum(capped.values()) == pytest.approx(sum(caps.values()), abs=1e-6)


def test_cascading_cap():
    """After capping the biggest, a second name may breach and must also cap."""
    caps = {"A": 55.0, "B": 30.0, "C": 3.0, "D": 3.0, "E": 3.0, "F": 3.0,
            "G": 3.0, "H": 3.0, "I": 3.0, "J": 3.0, "K": 3.0, "L": 3.0}
    weights = indexmath.compute_weights(caps, cap=0.10)
    assert max(weights.values()) <= 0.10 + TOL
    assert sum(weights.values()) == pytest.approx(1.0, abs=TOL)


def test_index_level_zero_divisor():
    with pytest.raises(ZeroDivisionError):
        indexmath.index_level({"A": 1.0}, 0)
