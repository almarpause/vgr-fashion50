"""Unit tests for the early-warning pre-flight scan (offline)."""
from datetime import date

from engine.config import Constituent, Settings
from engine.preflight import (
    StaticPreflightSignals, TickerSignals, next_annual_review,
    next_quarterly_review, scan, third_friday,
)
from engine.state import EngineState

CONSTS = [
    Constituent("Alpha", "ALPHA", "A:X", "USD", "Fashion", "USA"),
    Constituent("Beta", "BETA", "B:X", "USD", "Luxury", "France"),
]


def _state():
    st = EngineState()
    st.last_shares = {"ALPHA": 1_000_000, "BETA": 2_000_000}
    st.last_caps_usd = {"ALPHA": 5e8, "BETA": 3e8}
    return st


def test_shares_drift_issuance_flagged():
    sig = StaticPreflightSignals({
        "ALPHA": TickerSignals(latest_shares=1_100_000),  # +10% issuance
        "BETA": TickerSignals(latest_shares=2_000_000),   # unchanged
    })
    items = scan(CONSTS, _state(), sig, date(2026, 8, 4))
    drift = [i for i in items if i.category == "SHARES_DRIFT"]
    assert len(drift) == 1
    assert drift[0].ticker == "ALPHA"
    assert "issuance" in drift[0].signal


def test_shares_buyback_direction():
    sig = StaticPreflightSignals({
        "ALPHA": TickerSignals(latest_shares=940_000),    # -6% buyback
    })
    items = scan(CONSTS, _state(), sig, date(2026, 8, 4))
    drift = [i for i in items if i.category == "SHARES_DRIFT"]
    assert drift and "buyback" in drift[0].signal


def test_small_drift_not_flagged():
    sig = StaticPreflightSignals({
        "ALPHA": TickerSignals(latest_shares=1_010_000),  # +1% < 3% early
    })
    items = scan(CONSTS, _state(), sig, date(2026, 8, 4))
    assert not [i for i in items if i.category == "SHARES_DRIFT"]


def test_earnings_within_horizon():
    sig = StaticPreflightSignals({
        "ALPHA": TickerSignals(next_earnings=date(2026, 8, 12)),  # 8 days
        "BETA": TickerSignals(next_earnings=date(2026, 12, 1)),   # far out
    })
    items = scan(CONSTS, _state(), sig, date(2026, 8, 4),
                 Settings(watch_horizon_days=21))
    earn = [i for i in items if i.category == "EARNINGS"]
    assert len(earn) == 1 and earn[0].ticker == "ALPHA"
    assert earn[0].days_out == 8


def test_data_risk_flagged_high():
    sig = StaticPreflightSignals({
        "ALPHA": TickerSignals(resolves=False, health_reason="404"),
    })
    items = scan(CONSTS, _state(), sig, date(2026, 8, 4))
    risk = [i for i in items if i.category == "DATA_RISK"]
    assert risk and risk[0].severity == "HIGH" and risk[0].ticker == "ALPHA"


def test_split_flagged():
    sig = StaticPreflightSignals({
        "ALPHA": TickerSignals(recent_split=date(2026, 8, 6)),
    })
    items = scan(CONSTS, _state(), sig, date(2026, 8, 4))
    assert [i for i in items if i.category == "CORP_ACTION"
            and "split" in i.signal]


def test_membership_drift():
    ranks = {"ALPHA": 3, "BETA": 48}   # BETA below drop-zone 45
    items = scan(CONSTS, _state(), StaticPreflightSignals({}),
                 date(2026, 8, 4), caps_ranks=ranks)
    mem = [i for i in items if i.category == "MEMBERSHIP"]
    assert mem and mem[0].ticker == "BETA"


def test_index_review_countdowns_present():
    items = scan(CONSTS, _state(), StaticPreflightSignals({}), date(2026, 8, 4))
    reviews = [i for i in items if i.category == "INDEX_REVIEW"]
    assert len(reviews) == 2   # quarterly + annual
    assert all(i.days_out is not None and i.days_out >= 0 for i in reviews)


def test_third_friday_and_review_dates():
    # 3rd Friday of Sep 2026 is 2026-09-18
    assert third_friday(2026, 9) == date(2026, 9, 18)
    q = next_quarterly_review(date(2026, 8, 4))
    assert q == date(2026, 9, 18)          # next quarter end after Aug
    a = next_annual_review(date(2026, 8, 4))
    assert a == date(2027, 6, 18)          # June already passed in 2026


def test_severity_ordering():
    sig = StaticPreflightSignals({
        "ALPHA": TickerSignals(resolves=False),                 # HIGH
        "BETA": TickerSignals(next_earnings=date(2026, 8, 10)),  # MEDIUM
    })
    items = scan(CONSTS, _state(), sig, date(2026, 8, 4))
    sevs = [i.severity for i in items]
    # HIGH items must come before INFO items
    assert sevs.index("HIGH") < sevs.index("INFO")
