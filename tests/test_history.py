"""Unit tests for the historical backfill index construction (offline)."""
from datetime import date

import pytest

from engine.history import HistoryPanel, build_index_series


def _panel(price, currency, shares, weeks, fx=None):
    return HistoryPanel(
        weeks=weeks, price=price, fx=fx or {}, currency=currency,
        shares=shares, float_factor={t: 1.0 for t in shares},
        name={t: t for t in shares}, segment={t: "Fashion" for t in shares})


WEEKS = [date(2021, 1, 1), date(2021, 1, 8), date(2021, 1, 15), date(2021, 1, 22)]


def test_base_is_1000():
    panel = _panel(
        price={"A": {w: 100.0 for w in WEEKS},
               "B": {w: 50.0 for w in WEEKS}},
        currency={"A": "USD", "B": "USD"},
        shares={"A": 1_000_000, "B": 2_000_000}, weeks=WEEKS)
    res = build_index_series(panel)
    assert res.rows[0]["index_level"] == pytest.approx(1000.0, abs=1e-6)
    assert res.rows[0]["anomaly_flag"] == "BASE"


def test_mid_series_add_is_continuous():
    """A name appearing mid-series is added via the divisor rule; the level at
    the add instant must not jump."""
    price = {
        "A": {WEEKS[0]: 100, WEEKS[1]: 110, WEEKS[2]: 110, WEEKS[3]: 120},
        "B": {WEEKS[0]: 50, WEEKS[1]: 50, WEEKS[2]: 55, WEEKS[3]: 55},
        "C": {WEEKS[2]: 20, WEEKS[3]: 22},   # IPO at week index 2
    }
    panel = _panel(price, {"A": "USD", "B": "USD", "C": "USD"},
                   {"A": 1_000_000, "B": 2_000_000, "C": 5_000_000}, WEEKS)
    res = build_index_series(panel)
    # exactly one add event, at week 2
    assert len(res.events) == 1
    ev = res.events[0]
    assert ev.ticker == "C" and ev.effective_date == WEEKS[2]
    # continuity: level implied before == after the add
    assert (ev.sum_before_usd / ev.old_divisor) == pytest.approx(
        ev.sum_after_usd / ev.new_divisor, abs=1e-6)
    # the reported week-2 level equals the pre-add level (no jump from the add)
    lvl_w2 = res.rows[2]["index_level"]
    assert lvl_w2 == pytest.approx(ev.sum_before_usd / ev.old_divisor, abs=1e-4)


def test_carry_forward_single_week_gap():
    """A one-week NaN for an active name is carried forward, not dropped (which
    would spuriously move the level)."""
    price = {
        "A": {WEEKS[0]: 100, WEEKS[1]: 100, WEEKS[3]: 100},   # missing week 2
        "B": {w: 50 for w in WEEKS},
    }
    panel = _panel(price, {"A": "USD", "B": "USD"},
                   {"A": 1_000_000, "B": 1_000_000}, WEEKS)
    res = build_index_series(panel)
    # week 2 level should equal week 1 level (nothing actually changed)
    assert res.rows[2]["index_level"] == pytest.approx(
        res.rows[1]["index_level"], abs=1e-6)
    assert res.rows[2]["n_ok"] == 2   # A still counted (carried forward)


def test_fx_applied_in_history():
    price = {"A": {w: 100.0 for w in WEEKS}}
    fx = {"EUR": {w: 1.2 for w in WEEKS}}
    panel = _panel(price, {"A": "EUR"}, {"A": 1_000_000}, WEEKS, fx=fx)
    res = build_index_series(panel)
    # base still 1000 regardless of FX (divisor absorbs the level)
    assert res.rows[0]["index_level"] == pytest.approx(1000.0, abs=1e-6)
    # cap in USD reflects the FX rate
    assert res.final_caps_usd["A"] == pytest.approx(100.0 * 1_000_000 * 1.2)


def test_weekly_return_computed():
    price = {"A": {WEEKS[0]: 100, WEEKS[1]: 110, WEEKS[2]: 110, WEEKS[3]: 110}}
    panel = _panel(price, {"A": "USD"}, {"A": 1_000_000}, WEEKS)
    res = build_index_series(panel)
    assert res.rows[1]["weekly_return_%"] == pytest.approx(10.0, abs=1e-6)
