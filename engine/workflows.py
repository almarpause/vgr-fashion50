"""Workflow logic: Weekly, Quarterly, Annual, Unscheduled.

These functions contain the actual business logic and are called by the thin
``run_*.py`` entry-point scripts.  They are written to be testable: providers,
settings, workbook and state objects are all injectable.

Guardrail: the autonomous jobs calculate and *propose* freely, but NEVER
auto-commit a membership or divisor change.  Committing a divisor change is only
done by the explicit ``approve_*`` / ``commit_*`` functions when a human has set
status = APPROVED.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from . import indexmath
from .alerts import send_alert
from .config import Constituent, Settings, DEFAULT_SETTINGS, load_constituents
from .datafetch import DataProvider
from .fx import FxProvider
from .pipeline import RunResult, NameResult, fetch_all
from .state import EngineState, load_state, save_state
from .workbook import WorkbookManager


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso_week_of(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _write_fx(wbm: WorkbookManager, run: RunResult) -> None:
    for cur, fx in sorted(run.fx.items()):
        wbm.append_row("FX", {
            "run_date": run.run_date,
            "currency": cur,
            "rate_to_usd": fx.rate_to_usd,
            "source": fx.source,
            "status": fx.status,
        })


def _write_audit(wbm: WorkbookManager, run: RunResult) -> None:
    for n in run.names:
        wbm.append_row("Audit", {
            "run_date": run.run_date,
            "ticker": n.ticker,
            "name": n.name,
            "price_major": n.price,
            "currency": n.currency,
            "shares_outstanding": n.shares_outstanding,
            "float_shares": n.float_shares,
            "float_factor": round(n.float_factor, 6) if n.float_factor else None,
            "fx_rate_to_usd": n.fx_rate_to_usd,
            "cap_usd": n.cap_usd,
            "price_source": n.price_source,
            "fx_source": n.fx_source,
            "status": n.status,
        })


def _record_alert(wbm: WorkbookManager, severity: str, subject: str,
                  detail: str, email: bool = True) -> None:
    wbm.append_row("Alerts", {
        "timestamp": _now_iso(),
        "severity": severity,
        "subject": subject,
        "detail": detail,
    })
    if email:
        send_alert(subject, detail)


def _log_divisor(wbm: WorkbookManager, reason: str, old_div: float,
                 new_div: float, sum_before: float, sum_after: float,
                 level_before: float, level_after: float,
                 source_sheet: str) -> None:
    wbm.append_row("Divisor_Log", {
        "timestamp": _now_iso(),
        "reason": reason,
        "old_divisor": old_div,
        "new_divisor": new_div,
        "sum_before_usd": sum_before,
        "sum_after_usd": sum_after,
        "index_level_before": level_before,
        "index_level_after": level_after,
        "source_sheet": source_sheet,
    })


def sync_constituents_sheet(wbm: WorkbookManager,
                            constituents: list[Constituent],
                            settings: Settings) -> None:
    rows = [{
        "name": c.name,
        "yahoo_ticker": c.yahoo_ticker,
        "google_ticker": c.google_ticker,
        "trading_currency": c.trading_currency,
        "segment": c.segment,
        "country_hq": c.country_hq,
        "float_manual_review": "YES" if c.yahoo_ticker in
                               settings.family_controlled else "",
    } for c in constituents]
    wbm.replace_sheet_rows("Constituents", rows)


# --------------------------------------------------------------------------- #
# Anomaly guard
# --------------------------------------------------------------------------- #
@dataclass
class Anomaly:
    ticker: str
    kind: str          # MISSING | SHARES_CHANGE | CAP_MOVE | FX_OUTLIER | UNRESOLVED
    detail: str


def detect_anomalies(run: RunResult, state: EngineState,
                     settings: Settings) -> list[Anomaly]:
    """Flag anything that must not be silently absorbed into the index."""
    out: list[Anomaly] = []
    for n in run.names:
        if not n.ok:
            out.append(Anomaly(n.ticker, "MISSING",
                               f"{n.name}: {'; '.join(n.notes) or 'unresolved'}"))
            continue
        prior_sh = state.last_shares.get(n.ticker)
        if prior_sh and prior_sh > 0 and n.shares_outstanding:
            chg = abs(n.shares_outstanding - prior_sh) / prior_sh
            if chg > settings.shares_change_threshold:
                out.append(Anomaly(
                    n.ticker, "SHARES_CHANGE",
                    f"{n.name}: shares {prior_sh:.0f}->{n.shares_outstanding:.0f} "
                    f"({chg*100:.1f}%)"))
        prior_cap = state.last_caps_usd.get(n.ticker)
        if prior_cap and prior_cap > 0 and n.cap_usd:
            chg = abs(n.cap_usd - prior_cap) / prior_cap
            if chg > settings.cap_move_threshold:
                out.append(Anomaly(
                    n.ticker, "CAP_MOVE",
                    f"{n.name}: cap_usd moved {chg*100:.1f}% overnight"))
    # FX outliers
    for cur, fx in run.fx.items():
        prior = state.last_fx.get(cur)
        if fx.status == "OK" and prior and prior > 0:
            chg = abs(fx.rate_to_usd - prior) / prior
            if chg > settings.fx_outlier_threshold:
                out.append(Anomaly(
                    f"FX:{cur}", "FX_OUTLIER",
                    f"{cur} fx {prior:.4f}->{fx.rate_to_usd:.4f} ({chg*100:.1f}%)"))
    return out


# --------------------------------------------------------------------------- #
# WEEKLY
# --------------------------------------------------------------------------- #
@dataclass
class WeeklyOutcome:
    run_date: str
    iso_week: str
    index_level: float
    weekly_return_pct: float | None
    total_mcap_usd: float
    divisor_used: float
    n_ok: int
    n_missing: int
    anomaly_flag: bool
    anomalies: list[Anomaly] = field(default_factory=list)
    skipped_duplicate: bool = False
    bootstrapped: bool = False
    movers: list[tuple[str, float]] = field(default_factory=list)


def run_weekly(run_date: str | None = None,
               data_provider: DataProvider | None = None,
               fx_provider: FxProvider | None = None,
               settings: Settings = DEFAULT_SETTINGS,
               wbm: WorkbookManager | None = None,
               state: EngineState | None = None,
               constituents: list[Constituent] | None = None,
               persist: bool = True, force: bool = False) -> WeeklyOutcome:
    """Fully automatic value calculation.  Never changes membership/divisor.

    Idempotent per ISO week (``force=True`` overwrites the current week's row).
    Freezes anomalous names to their prior good cap and routes them to
    Unscheduled + Alerts rather than absorbing the move.
    """
    data_provider = data_provider or DataProvider()
    fx_provider = fx_provider or FxProvider()
    constituents = constituents or load_constituents()
    wbm = wbm or WorkbookManager()
    state = state if state is not None else load_state()

    d = date.fromisoformat(run_date) if run_date else date.today()
    run_date = d.isoformat()
    iso_week = iso_week_of(d)

    if wbm.has_week(iso_week):
        if not force:
            return WeeklyOutcome(
                run_date=run_date, iso_week=iso_week,
                index_level=state.last_index_level or 0.0,
                weekly_return_pct=None,
                total_mcap_usd=state.base_total_cap_usd or 0.0,
                divisor_used=state.current_divisor or 0.0,
                n_ok=0, n_missing=0, anomaly_flag=False, skipped_duplicate=True)
        wbm.delete_week(iso_week)   # --force: overwrite this week's row

    run = fetch_all(constituents, run_date, data_provider, fx_provider, settings)
    sync_constituents_sheet(wbm, constituents, settings)
    _write_fx(wbm, run)
    _write_audit(wbm, run)

    anomalies = detect_anomalies(run, state, settings)
    frozen = {a.ticker for a in anomalies
              if a.kind in ("SHARES_CHANGE", "CAP_MOVE", "MISSING")}

    bootstrapped = False
    if not state.initialized:
        # First run ever -> establish base.  No freezing (no history).
        caps = run.caps_usd
        if not caps:
            raise RuntimeError("cannot bootstrap base: no constituents resolved")
        base_div = indexmath.base_divisor(caps, settings.base_index_level)
        state.base_date = run_date
        state.base_divisor = base_div
        state.current_divisor = base_div
        state.base_index_level = settings.base_index_level
        state.base_total_cap_usd = indexmath.total_cap(caps)
        state.constituents = [c.yahoo_ticker for c in constituents]
        bootstrapped = True
        effective_caps = caps
    else:
        # Freeze anomalous names to last good cap; drop truly-missing-no-history.
        effective_caps = {}
        for n in run.names:
            prior = state.last_caps_usd.get(n.ticker)
            if n.ok and n.ticker not in frozen:
                effective_caps[n.ticker] = n.cap_usd
            elif prior is not None:
                effective_caps[n.ticker] = prior

    divisor = state.current_divisor
    level = indexmath.index_level(effective_caps, divisor)
    total_mcap = indexmath.total_cap(effective_caps)
    prev_level = state.last_index_level
    weekly_return = ((level / prev_level - 1.0) * 100.0
                     if prev_level and prev_level > 0 and not bootstrapped
                     else None)
    if bootstrapped:
        level = settings.base_index_level  # exactly 1000.00 on base date

    anomaly_flag = bool(anomalies)

    wbm.append_row("Weekly", {
        "run_date": run_date,
        "iso_week": iso_week,
        "index_level": round(level, 6),
        "weekly_return_%": round(weekly_return, 4)
        if weekly_return is not None else None,
        "total_mcap_usd": round(total_mcap, 2),
        "divisor_used": divisor,
        "n_ok": run.n_ok,
        "n_missing": run.n_missing,
        "anomaly_flag": "YES" if anomaly_flag else "",
    })

    # Route anomalies to Unscheduled (pending human review) + Alerts.
    for a in anomalies:
        company = next((n.name for n in run.names if n.ticker == a.ticker),
                       a.ticker)
        wbm.append_row("Unscheduled", {
            "effective_date": run_date,
            "company": company,
            "event_type": "data-error" if a.kind == "MISSING" else "corporate-action?",
            "detected_by": f"weekly-anomaly:{a.kind}",
            "sum_before_usd": None,
            "sum_after_usd": None,
            "old_divisor": divisor,
            "new_divisor": None,     # unchanged until human confirms
            "status": "PENDING_REVIEW",
        })
    if anomalies:
        detail = "\n".join(f"- [{a.kind}] {a.detail}" for a in anomalies)
        _record_alert(wbm, "WARN",
                      f"Weekly {iso_week}: {len(anomalies)} anomaly(ies)",
                      f"Frozen names kept at prior cap; divisor unchanged "
                      f"({divisor}).\n{detail}")

    # Top movers (week-over-week cap %), computed before state is overwritten.
    movers: list[tuple[str, float]] = []
    for n in run.names:
        prior = state.last_caps_usd.get(n.ticker)
        if n.ok and prior and prior > 0:
            movers.append((n.ticker, (n.cap_usd / prior - 1.0) * 100.0))
    movers.sort(key=lambda kv: abs(kv[1]), reverse=True)
    movers = movers[:3]

    # Update state (last-good values only for non-frozen ok names).
    for n in run.names:
        if n.ok and n.ticker not in frozen:
            state.last_shares[n.ticker] = n.shares_outstanding
            state.last_float_factor[n.ticker] = n.float_factor
            state.last_caps_usd[n.ticker] = n.cap_usd
    for cur, fx in run.fx.items():
        if fx.status == "OK":
            state.last_fx[cur] = fx.rate_to_usd
    state.last_index_level = level
    state.last_run_date = run_date

    if persist:
        wbm.save()
        save_state(state)

    return WeeklyOutcome(
        run_date=run_date, iso_week=iso_week, index_level=level,
        weekly_return_pct=weekly_return, total_mcap_usd=total_mcap,
        divisor_used=divisor, n_ok=run.n_ok, n_missing=run.n_missing,
        anomaly_flag=anomaly_flag, anomalies=anomalies,
        bootstrapped=bootstrapped, movers=movers)


# --------------------------------------------------------------------------- #
# QUARTERLY  (rebalance proposal — human-approved)
# --------------------------------------------------------------------------- #
@dataclass
class QuarterlyProposal:
    run_date: str
    old_divisor: float
    proposed_divisor: float
    sum_before_usd: float
    sum_after_usd: float
    level_before: float
    level_after: float
    rows: list[dict] = field(default_factory=list)


def run_quarterly(run_date: str | None = None,
                  data_provider: DataProvider | None = None,
                  fx_provider: FxProvider | None = None,
                  settings: Settings = DEFAULT_SETTINGS,
                  wbm: WorkbookManager | None = None,
                  state: EngineState | None = None,
                  constituents: list[Constituent] | None = None,
                  persist: bool = True) -> QuarterlyProposal:
    """Refresh shares/float, re-apply cap, solve the proposed new divisor.

    Writes a before/after diff with status PROPOSED.  Does NOT commit — a human
    must set status = APPROVED and run ``commit_quarterly``.
    """
    data_provider = data_provider or DataProvider()
    fx_provider = fx_provider or FxProvider()
    constituents = constituents or load_constituents()
    wbm = wbm or WorkbookManager()
    state = state if state is not None else load_state()
    if not state.initialized:
        raise RuntimeError("run a weekly job first to establish the base")

    d = date.fromisoformat(run_date) if run_date else date.today()
    run_date = d.isoformat()

    run = fetch_all(constituents, run_date, data_provider, fx_provider, settings)

    # Build before/after caps at CURRENT prices, isolating share/float change.
    caps_after: dict[str, float] = {}
    caps_before: dict[str, float] = {}
    for n in run.names:
        if not n.ok:
            # Cannot refresh -> hold at prior; contributes nothing to divisor move
            prior = state.last_caps_usd.get(n.ticker)
            if prior is not None:
                caps_after[n.ticker] = prior
                caps_before[n.ticker] = prior
            continue
        caps_after[n.ticker] = n.cap_usd
        old_sh = state.last_shares.get(n.ticker, n.shares_outstanding)
        old_ff = state.last_float_factor.get(n.ticker, n.float_factor)
        price_local = n.price
        cap_before_local = indexmath.float_adjusted_cap_local(
            price_local, old_sh, old_ff)
        caps_before[n.ticker] = indexmath.to_usd(cap_before_local,
                                                 n.fx_rate_to_usd)

    sum_before = indexmath.total_cap(caps_before)
    sum_after = indexmath.total_cap(caps_after)
    old_div = state.current_divisor
    new_div = indexmath.adjust_divisor(old_div, sum_before, sum_after)
    level_before = sum_before / old_div
    level_after = sum_after / new_div

    w_old = indexmath.compute_weights(caps_before, cap=settings.effective_cap)
    w_new = indexmath.compute_weights(caps_after, cap=settings.effective_cap)

    # Biggest movers by absolute weight change.
    deltas = {t: abs(w_new.get(t, 0) - w_old.get(t, 0)) for t in caps_after}
    top = sorted(deltas, key=deltas.get, reverse=True)[:5]

    name_of = {n.ticker: n.name for n in run.names}
    rows = []
    for n in run.names:
        t = n.ticker
        rows.append({
            "run_date": run_date,
            "ticker": t,
            "name": name_of.get(t, t),
            "old_weight_%": round(w_old.get(t, 0) * 100, 4),
            "new_weight_%": round(w_new.get(t, 0) * 100, 4),
            "weight_change_%": round((w_new.get(t, 0) - w_old.get(t, 0)) * 100, 4),
            "old_shares": state.last_shares.get(t),
            "new_shares": n.shares_outstanding,
            "old_divisor": old_div,
            "new_divisor": round(new_div, 6),
            "biggest_mover": "YES" if t in top else "",
            "status": "PROPOSED",
        })

    if persist:
        wbm.replace_sheet_rows("Quarterly", rows)
        _write_fx(wbm, run)
        _write_audit(wbm, run)
        _record_alert(wbm, "INFO",
                      f"Quarterly rebalance PROPOSED {run_date}",
                      f"old_divisor={old_div} -> proposed_divisor={new_div:.6f}\n"
                      f"level_before={level_before:.6f} level_after={level_after:.6f}\n"
                      f"Set status=APPROVED and run commit_quarterly to apply.")
        wbm.save()

    return QuarterlyProposal(
        run_date=run_date, old_divisor=old_div, proposed_divisor=new_div,
        sum_before_usd=sum_before, sum_after_usd=sum_after,
        level_before=level_before, level_after=level_after, rows=rows)


def commit_quarterly(settings: Settings = DEFAULT_SETTINGS,
                     data_provider: DataProvider | None = None,
                     fx_provider: FxProvider | None = None,
                     wbm: WorkbookManager | None = None,
                     state: EngineState | None = None,
                     constituents: list[Constituent] | None = None,
                     persist: bool = True) -> bool:
    """Commit a quarterly rebalance ONLY if a human marked status=APPROVED.

    Applies the proposed divisor and refreshes the share/float basis so the
    index level is continuous across the change.  Returns True if committed.
    """
    wbm = wbm or WorkbookManager()
    state = state if state is not None else load_state()
    rows = wbm.read_rows("Quarterly")
    if not rows:
        return False
    statuses = {str(r.get("status", "")).upper() for r in rows}
    if "APPROVED" not in statuses:
        return False  # guardrail: never self-approve

    new_div = None
    old_div = state.current_divisor
    for r in rows:
        if r.get("new_divisor"):
            new_div = float(r["new_divisor"])
            break
    if new_div is None:
        return False

    # Refresh the share/float basis to the approved snapshot.
    data_provider = data_provider or DataProvider()
    fx_provider = fx_provider or FxProvider()
    constituents = constituents or load_constituents()
    run = fetch_all(constituents, date.today().isoformat(),
                    data_provider, fx_provider, settings)

    sum_before = indexmath.total_cap({
        t: c for t, c in state.last_caps_usd.items()})
    for n in run.names:
        if n.ok:
            state.last_shares[n.ticker] = n.shares_outstanding
            state.last_float_factor[n.ticker] = n.float_factor
            state.last_caps_usd[n.ticker] = n.cap_usd
    sum_after = indexmath.total_cap(state.last_caps_usd)

    level_before = (sum_before / old_div) if old_div else None
    state.current_divisor = new_div
    level_after = sum_after / new_div

    for r in rows:
        r["status"] = "APPLIED"
    wbm.replace_sheet_rows("Quarterly", rows)

    _log_divisor(wbm, "quarterly-rebalance", old_div, new_div,
                 sum_before, sum_after,
                 level_before if level_before is not None else 0.0,
                 level_after, "Quarterly")
    wbm.append_row("Unscheduled", {
        "effective_date": date.today().isoformat(),
        "company": "ALL (quarterly)",
        "event_type": "rebalance",
        "detected_by": "quarterly-approved",
        "sum_before_usd": round(sum_before, 2),
        "sum_after_usd": round(sum_after, 2),
        "old_divisor": old_div,
        "new_divisor": new_div,
        "status": "APPLIED",
    })
    state.last_index_level = level_after
    if persist:
        wbm.save()
        save_state(state)
    return True


# --------------------------------------------------------------------------- #
# ANNUAL  (reconstitution — human-approved)
# --------------------------------------------------------------------------- #
OUT_OF_SCOPE_HINTS = ("off-price", "discount", "uniform", "workwear",
                      "rental", "beauty", "cosmetic")


@dataclass
class AnnualProposal:
    run_date: str
    rows: list[dict] = field(default_factory=list)
    adds: list[str] = field(default_factory=list)
    drops: list[str] = field(default_factory=list)


def rank_universe(caps_usd: dict[str, float]) -> dict[str, int]:
    """Rank 1 = largest float-adjusted USD cap."""
    ordered = sorted(caps_usd, key=caps_usd.get, reverse=True)
    return {t: i + 1 for i, t in enumerate(ordered)}


def apply_buffer_rule(current: set[str], ranks: dict[str, int],
                      add_rank: int, drop_rank: int) -> tuple[list[str], list[str]]:
    """40/60 buffer: newcomers added only above ``add_rank``; members dropped
    only below ``drop_rank``.  Returns (adds, drops)."""
    adds = [t for t, r in ranks.items()
            if t not in current and r <= add_rank]
    drops = [t for t in current
             if ranks.get(t, 10 ** 9) > drop_rank]
    return adds, drops


def run_annual(run_date: str | None = None,
               data_provider: DataProvider | None = None,
               fx_provider: FxProvider | None = None,
               settings: Settings = DEFAULT_SETTINGS,
               wbm: WorkbookManager | None = None,
               state: EngineState | None = None,
               constituents: list[Constituent] | None = None,
               candidates: list[Constituent] | None = None,
               persist: bool = True) -> AnnualProposal:
    """Reconstitution proposal.  Screens the universe by float-adjusted USD cap,
    applies the 40/60 buffer, and proposes ADD/DROP/HOLD rows requiring a human
    ``scope_ok`` confirmation and approval before anything is applied.
    """
    data_provider = data_provider or DataProvider()
    fx_provider = fx_provider or FxProvider()
    constituents = constituents or load_constituents()
    candidates = candidates or []
    wbm = wbm or WorkbookManager()
    state = state if state is not None else load_state()

    d = date.fromisoformat(run_date) if run_date else date.today()
    run_date = d.isoformat()

    universe = list(constituents) + list(candidates)
    run = fetch_all(universe, run_date, data_provider, fx_provider, settings)
    caps = run.caps_usd
    ranks = rank_universe(caps)

    current = {c.yahoo_ticker for c in constituents}
    adds, drops = apply_buffer_rule(current, ranks, settings.add_rank,
                                    settings.drop_rank)

    name_of = {c.yahoo_ticker: c.name for c in universe}
    seg_of = {c.yahoo_ticker: c.segment for c in universe}
    rows = []
    for n in run.names:
        t = n.ticker
        if t in adds:
            action = "ADD"
        elif t in drops:
            action = "DROP"
        elif t in current:
            action = "HOLD"
        else:
            action = "CANDIDATE"
        seg = (seg_of.get(t) or "").lower()
        scope_flag = "" if any(h in seg for h in OUT_OF_SCOPE_HINTS) else "REVIEW"
        rows.append({
            "run_date": run_date,
            "ticker": t,
            "name": name_of.get(t, t),
            "action": action,
            "rank": ranks.get(t),
            "float_adj_cap_usd": round(caps[t], 2) if t in caps else None,
            "segment": seg_of.get(t, ""),
            "scope_ok": scope_flag,     # human sets YES/NO
            "status": "PROPOSED",
        })
    rows.sort(key=lambda r: (r["rank"] is None, r["rank"] or 10 ** 9))

    if persist:
        wbm.replace_sheet_rows("Annual", rows)
        _write_fx(wbm, run)
        _record_alert(wbm, "INFO",
                      f"Annual reconstitution PROPOSED {run_date}",
                      f"ADD={adds or 'none'}  DROP={drops or 'none'}\n"
                      f"Confirm scope_ok=YES and status=APPROVED, then "
                      f"run commit_annual.")
        wbm.save()

    return AnnualProposal(run_date=run_date, rows=rows, adds=adds, drops=drops)


def commit_annual(settings: Settings = DEFAULT_SETTINGS,
                  wbm: WorkbookManager | None = None,
                  state: EngineState | None = None,
                  persist: bool = True) -> bool:
    """Apply approved ADD/DROP rows via the divisor rule (level continuous).

    Requires each acted-on row to have scope_ok=YES and status=APPROVED.
    """
    wbm = wbm or WorkbookManager()
    state = state if state is not None else load_state()
    rows = wbm.read_rows("Annual")
    approved = [r for r in rows
                if str(r.get("status", "")).upper() == "APPROVED"
                and str(r.get("scope_ok", "")).upper() == "YES"
                and str(r.get("action", "")).upper() in ("ADD", "DROP")]
    if not approved:
        return False

    current = set(state.constituents)
    caps = dict(state.last_caps_usd)
    old_div = state.current_divisor
    sum_before = indexmath.total_cap({t: caps[t] for t in current if t in caps})

    for r in approved:
        t = r["ticker"]
        if r["action"].upper() == "ADD":
            current.add(t)
            if r.get("float_adj_cap_usd"):
                caps[t] = float(r["float_adj_cap_usd"])
        elif r["action"].upper() == "DROP":
            current.discard(t)

    sum_after = indexmath.total_cap({t: caps[t] for t in current if t in caps})
    if sum_before <= 0:
        return False
    new_div = indexmath.adjust_divisor(old_div, sum_before, sum_after)
    level_before = sum_before / old_div
    level_after = sum_after / new_div

    state.constituents = sorted(current)
    state.current_divisor = new_div
    state.last_caps_usd = {t: caps[t] for t in current if t in caps}
    state.last_index_level = level_after

    for r in rows:
        if r in approved:
            r["status"] = "APPLIED"
    wbm.replace_sheet_rows("Annual", rows)
    _log_divisor(wbm, "annual-reconstitution", old_div, new_div,
                 sum_before, sum_after, level_before, level_after, "Annual")
    wbm.append_row("Unscheduled", {
        "effective_date": date.today().isoformat(),
        "company": "ALL (annual)",
        "event_type": "reconstitution",
        "detected_by": "annual-approved",
        "sum_before_usd": round(sum_before, 2),
        "sum_after_usd": round(sum_after, 2),
        "old_divisor": old_div,
        "new_divisor": new_div,
        "status": "APPLIED",
    })
    if persist:
        wbm.save()
        save_state(state)
    return True


# --------------------------------------------------------------------------- #
# UNSCHEDULED  (mid-quarter corporate action -> divisor change, human-confirmed)
# --------------------------------------------------------------------------- #
def apply_corporate_action(company: str, event_type: str,
                           sum_before_usd: float, sum_after_usd: float,
                           wbm: WorkbookManager | None = None,
                           state: EngineState | None = None,
                           detected_by: str = "manual",
                           persist: bool = True) -> float:
    """Apply a confirmed mid-quarter corporate action via the divisor rule.

    Logs to Unscheduled + Divisor_Log and returns the new divisor.  Caller is
    responsible for having obtained human confirmation (this is the commit).
    """
    wbm = wbm or WorkbookManager()
    state = state if state is not None else load_state()
    old_div = state.current_divisor
    new_div = indexmath.adjust_divisor(old_div, sum_before_usd, sum_after_usd)
    level_before = sum_before_usd / old_div
    level_after = sum_after_usd / new_div
    state.current_divisor = new_div

    wbm.append_row("Unscheduled", {
        "effective_date": date.today().isoformat(),
        "company": company,
        "event_type": event_type,
        "detected_by": detected_by,
        "sum_before_usd": round(sum_before_usd, 2),
        "sum_after_usd": round(sum_after_usd, 2),
        "old_divisor": old_div,
        "new_divisor": new_div,
        "status": "APPLIED",
    })
    _log_divisor(wbm, f"corporate-action:{event_type}", old_div, new_div,
                 sum_before_usd, sum_after_usd, level_before, level_after,
                 "Unscheduled")
    if persist:
        wbm.save()
        save_state(state)
    return new_div
