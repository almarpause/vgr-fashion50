"""Workflow-level tests: weekly, anomaly guard, continuity, approval gates."""
import pytest

from engine import indexmath
from engine.config import Constituent
from engine.datafetch import Quote, StaticDataProvider
from engine.fx import StaticFxProvider
from engine.pipeline import fetch_all
from engine.state import EngineState
from engine.workflows import (
    apply_buffer_rule, apply_corporate_action, commit_annual, commit_quarterly,
    detect_anomalies, rank_universe, run_annual, run_quarterly, run_weekly,
)
from tests.conftest import make_quote

TOL = 1e-6


# --------------------------------------------------------------------------- #
# Weekly: base == 1000, idempotency, missing accounting
# --------------------------------------------------------------------------- #
def test_weekly_bootstrap_is_1000(three_stock_universe, wbm, fresh_state,
                                  settings):
    consts, data, fx = three_stock_universe
    out = run_weekly(run_date="2026-01-05", data_provider=data, fx_provider=fx,
                     settings=settings, wbm=wbm, state=fresh_state,
                     constituents=consts, persist=False)
    assert out.bootstrapped
    assert out.index_level == pytest.approx(1000.0, abs=TOL)
    assert out.n_ok == 3
    assert out.n_missing == 0
    assert fresh_state.initialized


def test_weekly_idempotent(three_stock_universe, wbm, fresh_state, settings):
    consts, data, fx = three_stock_universe
    run_weekly(run_date="2026-01-05", data_provider=data, fx_provider=fx,
               settings=settings, wbm=wbm, state=fresh_state,
               constituents=consts, persist=False)
    out2 = run_weekly(run_date="2026-01-05", data_provider=data, fx_provider=fx,
                      settings=settings, wbm=wbm, state=fresh_state,
                      constituents=consts, persist=False)
    assert out2.skipped_duplicate
    weekly_rows = wbm.read_rows("Weekly")
    assert len(weekly_rows) == 1


def test_weekly_force_overwrites_row(three_stock_universe, wbm, fresh_state,
                                     settings):
    consts, data, fx = three_stock_universe
    run_weekly(run_date="2026-01-05", data_provider=data, fx_provider=fx,
               settings=settings, wbm=wbm, state=fresh_state,
               constituents=consts, persist=False)
    # force a re-run of the SAME week -> still exactly one row, not skipped
    out = run_weekly(run_date="2026-01-05", data_provider=data, fx_provider=fx,
                     settings=settings, wbm=wbm, state=fresh_state,
                     constituents=consts, persist=False, force=True)
    assert not out.skipped_duplicate
    assert len(wbm.read_rows("Weekly")) == 1


def test_weekly_missing_surfaces_not_zero(wbm, fresh_state, settings):
    consts = [
        Constituent("Alpha", "ALPHA", "A:X", "USD", "Fashion", "USA"),
        Constituent("Ghost", "GHOST", "G:X", "USD", "Fashion", "USA"),
    ]
    quotes = {"ALPHA": make_quote("ALPHA", 100.0, "USD", 1_000_000)}
    data = StaticDataProvider(quotes)
    fx = StaticFxProvider({"USD": 1.0})
    out = run_weekly(run_date="2026-01-05", data_provider=data, fx_provider=fx,
                     settings=settings, wbm=wbm, state=fresh_state,
                     constituents=consts, persist=False)
    assert out.n_missing == 1
    assert out.anomaly_flag
    assert any(a.kind == "MISSING" for a in out.anomalies)


# --------------------------------------------------------------------------- #
# Anomaly guard thresholds
# --------------------------------------------------------------------------- #
def test_shares_change_anomaly(three_stock_universe, settings):
    consts, data, fx = three_stock_universe
    run = fetch_all(consts, "2026-02-01", data, fx, settings)
    state = EngineState()
    # prior shares for ALPHA far from current (1,000,000) -> >15% change
    state.last_shares = {"ALPHA": 500_000}
    anomalies = detect_anomalies(run, state, settings)
    assert any(a.kind == "SHARES_CHANGE" and a.ticker == "ALPHA"
               for a in anomalies)


def test_cap_move_anomaly(three_stock_universe, settings):
    consts, data, fx = three_stock_universe
    run = fetch_all(consts, "2026-02-01", data, fx, settings)
    state = EngineState()
    caps = run.caps_usd
    # halve ALPHA's prior cap -> ~100% overnight move
    state.last_caps_usd = {"ALPHA": caps["ALPHA"] / 2.0}
    state.last_shares = {"ALPHA": 1_000_000}
    anomalies = detect_anomalies(run, state, settings)
    assert any(a.kind == "CAP_MOVE" and a.ticker == "ALPHA" for a in anomalies)


def test_fx_outlier_anomaly(three_stock_universe, settings):
    consts, data, fx = three_stock_universe
    run = fetch_all(consts, "2026-02-01", data, fx, settings)
    state = EngineState()
    state.last_fx = {"EUR": 0.80}  # current 1.10 -> ~37% move
    anomalies = detect_anomalies(run, state, settings)
    assert any(a.kind == "FX_OUTLIER" for a in anomalies)


def test_weekly_freezes_anomalous_name(three_stock_universe, wbm, settings):
    consts, data, fx = three_stock_universe
    state = EngineState()
    run_weekly(run_date="2026-01-05", data_provider=data, fx_provider=fx,
               settings=settings, wbm=wbm, state=state, constituents=consts,
               persist=False)
    prior_alpha_cap = state.last_caps_usd["ALPHA"]

    # Next week: ALPHA price doubles -> >25% cap move -> should freeze.
    quotes2 = {
        "ALPHA": make_quote("ALPHA", 200.0, "USD", 1_000_000, 900_000),
        "BETA.PA": make_quote("BETA.PA", 50.0, "EUR", 2_000_000, 2_000_000),
        "GAMMA.L": make_quote("GAMMA.L", 20.0, "GBP", 5_000_000, 4_000_000),
    }
    data2 = StaticDataProvider(quotes2)
    out = run_weekly(run_date="2026-01-12", data_provider=data2, fx_provider=fx,
                     settings=settings, wbm=wbm, state=state,
                     constituents=consts, persist=False)
    assert out.anomaly_flag
    assert any(a.kind == "CAP_MOVE" for a in out.anomalies)
    # Frozen: ALPHA's stored cap must NOT be updated to the anomalous value.
    assert state.last_caps_usd["ALPHA"] == pytest.approx(prior_alpha_cap)
    # Unscheduled got a pending review row.
    uns = wbm.read_rows("Unscheduled")
    assert any(r["status"] == "PENDING_REVIEW" for r in uns)


# --------------------------------------------------------------------------- #
# End-to-end continuity: add a constituent, level must not jump
# --------------------------------------------------------------------------- #
def test_continuity_add_constituent_end_to_end(three_stock_universe, wbm,
                                               settings):
    consts, data, fx = three_stock_universe
    state = EngineState()
    out = run_weekly(run_date="2026-01-05", data_provider=data, fx_provider=fx,
                     settings=settings, wbm=wbm, state=state,
                     constituents=consts, persist=False)
    level_before = out.index_level
    assert level_before == pytest.approx(1000.0, abs=TOL)

    # A new constituent joins mid-quarter (corporate action / index add).
    sum_before = indexmath.total_cap(state.last_caps_usd)
    new_cap = 3_000_000.0
    sum_after = sum_before + new_cap
    old_div = state.current_divisor
    apply_corporate_action("Delta", "index-add", sum_before, sum_after,
                           wbm=wbm, state=state, persist=False)
    new_div = state.current_divisor

    caps_after = dict(state.last_caps_usd)
    caps_after["DELTA"] = new_cap
    level_after = indexmath.index_level(caps_after, new_div)
    assert level_after == pytest.approx(level_before, abs=TOL)
    assert new_div != old_div


# --------------------------------------------------------------------------- #
# Quarterly proposal + approval gate
# --------------------------------------------------------------------------- #
def test_quarterly_proposes_continuous_and_needs_approval(three_stock_universe,
                                                          wbm, settings):
    consts, data, fx = three_stock_universe
    state = EngineState()
    run_weekly(run_date="2026-01-05", data_provider=data, fx_provider=fx,
               settings=settings, wbm=wbm, state=state, constituents=consts,
               persist=False)

    # Quarterly with a share change on BETA (float/shares refresh).
    quotes2 = {
        "ALPHA": make_quote("ALPHA", 100.0, "USD", 1_000_000, 900_000),
        "BETA.PA": make_quote("BETA.PA", 50.0, "EUR", 2_400_000, 2_400_000),
        "GAMMA.L": make_quote("GAMMA.L", 20.0, "GBP", 5_000_000, 4_000_000),
    }
    data2 = StaticDataProvider(quotes2)
    p = run_quarterly(run_date="2026-03-20", data_provider=data2,
                      fx_provider=fx, settings=settings, wbm=wbm, state=state,
                      constituents=consts, persist=False)
    # Proposed divisor keeps the level continuous.
    assert p.level_after == pytest.approx(p.level_before, abs=TOL)
    assert all(r["status"] == "PROPOSED" for r in p.rows)

    # commit refuses while nothing is APPROVED.
    wbm.replace_sheet_rows("Quarterly", p.rows)
    assert commit_quarterly(wbm=wbm, state=state, data_provider=data2,
                            fx_provider=fx, settings=settings,
                            constituents=consts, persist=False) is False


# --------------------------------------------------------------------------- #
# Annual buffer rule
# --------------------------------------------------------------------------- #
def test_buffer_rule():
    current = {"A", "B", "C"}
    ranks = {"A": 1, "B": 65, "C": 30, "NEW": 20, "FAR": 80}
    adds, drops = apply_buffer_rule(current, ranks, add_rank=40, drop_rank=60)
    assert "NEW" in adds        # rank 20 <= 40
    assert "FAR" not in adds     # rank 80 > 40
    assert "B" in drops          # rank 65 > 60
    assert "C" not in drops      # rank 30 <= 60


def test_rank_universe():
    caps = {"A": 100.0, "B": 300.0, "C": 200.0}
    ranks = rank_universe(caps)
    assert ranks["B"] == 1
    assert ranks["C"] == 2
    assert ranks["A"] == 3


def test_annual_no_churn_holds(three_stock_universe, wbm, settings):
    consts, data, fx = three_stock_universe
    state = EngineState()
    state.constituents = [c.yahoo_ticker for c in consts]
    p = run_annual(run_date="2026-06-01", data_provider=data, fx_provider=fx,
                   settings=settings, wbm=wbm, state=state,
                   constituents=consts, persist=False)
    # With only 3 names and no candidates, all HOLD, no adds/drops.
    assert p.adds == []
    assert p.drops == []
    assert all(r["action"] == "HOLD" for r in p.rows)


def test_commit_annual_needs_approval(three_stock_universe, wbm, settings):
    consts, data, fx = three_stock_universe
    state = EngineState()
    state.constituents = [c.yahoo_ticker for c in consts]
    state.last_caps_usd = {"ALPHA": 1e8, "BETA.PA": 1e8, "GAMMA.L": 1e8}
    state.current_divisor = 3e5
    # No approved rows -> refuse.
    wbm.replace_sheet_rows("Annual", [{
        "run_date": "2026-06-01", "ticker": "ALPHA", "name": "Alpha",
        "action": "DROP", "rank": 3, "float_adj_cap_usd": 1e8,
        "segment": "Fashion", "scope_ok": "REVIEW", "status": "PROPOSED",
    }])
    assert commit_annual(wbm=wbm, state=state, settings=settings,
                         persist=False) is False
