# VGR Fashion 50 — index engine

An autonomous, market-cap–weighted index engine tracking 50 listed fashion,
luxury and sportswear companies, built on the same construction principles as
the S&P 500 (market-cap weighted, **divisor-based**, **float-adjusted**). All
internal calculations are in **USD**. The engine maintains one Excel workbook
and runs on a schedule with **human-approval gates** for anything that changes
membership or the divisor.

```
float_adjusted_cap_local = price × shares_outstanding × float_factor
cap_usd                  = float_adjusted_cap_local × fx_rate_to_usd
Index level              = Σ(cap_usd) / Divisor
```

* **Base**: on the base date the index is set to `1000.00`, so
  `Divisor_base = Σ(cap_usd at base) / 1000`.
* **Divisor rule** (Quarterly / Annual / Unscheduled — *never* Weekly):
  `Divisor_new = Divisor_old × (Σ caps AFTER / Σ caps BEFORE)`, applied at the
  effective instant so the index level is continuous.
* `float_factor = floatShares / sharesOutstanding` when available, else `1.0`
  (family-controlled names — LVMH, Hermès, Inditex, EssilorLuxottica, … — are
  flagged for manual float review).
* Optional single-name **weight cap** with iterative redistribution of the
  excess across the uncapped names. **Currently OFF** (`WEIGHT_CAP_ENABLED =
  False` in `engine/config.py`) — weights are pure float-adjusted market-cap
  shares. Capping preserves the total cap, so toggling it never changes the
  index level; it only reshapes reported weights. Flip to `True` to reinstate
  the 10% ceiling.

---

## Project layout

```
vgr-index/
├── constituents.csv         # the 50-name universe (name, tickers, ccy, segment, hq)
├── candidates.csv           # OPTIONAL extra universe for Annual screening
├── engine/
│   ├── config.py            # paths, thresholds, constituent loader, Settings
│   ├── indexmath.py         # pure index math (cap, divisor, weight cap) — no I/O
│   ├── fx.py                # FX→USD: frankfurter → exchangerate.host → yfinance
│   ├── datafetch.py         # yfinance → stooq → google; minor-unit normalization
│   ├── history.py           # 5y bulk backfill + weekly index construction
│   ├── preflight.py         # early-warning scan (upcoming events / risks)
│   ├── pipeline.py          # fetch → compute USD caps + audit trail
│   ├── state.py             # persistent divisor / base / last-run values (JSON)
│   ├── workbook.py          # Excel I/O (openpyxl), schemas, chart, idempotency
│   ├── alerts.py            # SMTP email via env vars, log/sheet fallback
│   └── workflows.py         # Weekly / Quarterly / Annual / Unscheduled logic
├── backfill.py              # build the real multi-year weekly series (base=1000)
├── preflight.py             # early-warning scan -> Watchlist sheet + digest
├── dashboard.py             # render self-contained Fashion50_Dashboard.html
├── run_weekly.py            # entry point — automatic value calc
├── run_quarterly.py         # entry point — rebalance PROPOSAL
├── approve_quarterly.py     # commit an APPROVED rebalance
├── run_annual.py            # entry point — reconstitution PROPOSAL
├── approve_annual.py        # commit APPROVED adds/drops
├── dry_run.py               # runs all four workflows against live data
├── verify.py                # acceptance harness (synthetic + live-workbook checks)
├── tests/                   # pytest suite (offline, deterministic)
├── Fashion50_Index.xlsx     # THE ARTIFACT (created/updated by the jobs)
├── Fashion50_Dashboard.html # rendered visual dashboard
└── state.json               # machine source of truth for the divisor
```

The workbook `Fashion50_Index.xlsx` has the four workflow worksheets —
**Weekly, Quarterly, Annual, Unscheduled** — plus **Summary** (KPIs + index
chart), **Watchlist** (forward-looking early warning), **Methodology**, and
supporting sheets **Constituents, FX, Alerts, Divisor_Log, Audit, Prices**
(weekly per-company price panel) — a full per-run audit trail so any index level
can be reproduced.

---

## Setup

```bash
cd fashion50
python -m pip install -r requirements.txt
```

Requires Python 3.10+ (`pandas`, `openpyxl`, `yfinance`, `requests`, `pytest`).
All data sources are free (Yahoo/Stooq/Google Finance for quotes; frankfurter /
exchangerate.host for FX).

---

## Quick start — get real data you can see

```bash
python backfill.py --fresh --base 2025-01-01 --years 2   # weekly series, base = 1000
python dashboard.py --open             # open the visual dashboard in Chrome
python verify.py                       # 14 checks incl. the live workbook
```

Pick the start with `--base YYYY-MM-DD` (e.g. `--base 2025-01-01`) or a rolling
window with `--years N`. The dashboard shows the index chart, KPIs, segment mix,
a **full constituents table** (latest price, YTD %, MoM %, full market cap,
float %, index weight), and an **interactive per-company selector** that rebases
any stock to 1000 at the same base to compare its evolution against the index.
Weights are **float-adjusted** (S&P-style), so a founder-controlled name like
Inditex (36% float) can outweigh-by-size yet under-weight-by-float versus a
higher-float peer — the table's *Mkt cap* and *Float* columns make that explicit.

`backfill.py` reconstructs a **real ~5-year weekly index** from live prices +
FX (base = 1000 at the start), so `Fashion50_Index.xlsx` opens on a **Summary**
sheet with an index chart and top weights — not a single seed row. Names that
IPO'd after the base date (On, Birkenstock, Amer Sports) are added on their
first traded week via the divisor rule, logged to `Divisor_Log` / `Unscheduled`.
After that, `run_weekly.py` appends each new week onto the series.

> **Historical shares are an approximation.** Free sources don't provide
> point-in-time share counts, so the backfill holds the **current**
> `sharesOutstanding` / `floatShares` constant across history — the series is
> price-and-FX driven. This is stated on the workbook **Methodology** sheet.
> A name whose history can't be sourced at all (e.g. a broken Yahoo series) is
> **excluded and flagged** on the `Alerts` sheet — never invented.

---

## Running each job

| Job | Command | Effect |
|-----|---------|--------|
| **Backfill** | `python backfill.py [--years 5] [--base YYYY-MM-DD] [--fresh]` | Reconstructs the multi-year weekly series (base = 1000), Summary chart, Divisor_Log/Unscheduled adds, and rebuilds `state.json` so live runs continue it. |
| **Dashboard** | `python dashboard.py [--open]` | Renders self-contained `Fashion50_Dashboard.html` (index chart, KPI tiles, top-10 weights, segment mix). |
| **Pre-flight** | `python preflight.py [--horizon 21]` | **Early warning.** Runs a few days *before* the weekly job; writes the `Watchlist` sheet with upcoming events (share drift, splits, earnings, ex-div, at-risk data, membership drift, review countdowns) + emails a digest. Never changes the index. |
| **Weekly** | `python run_weekly.py [YYYY-MM-DD] [--force]` | Fully automatic. Fetches prices+FX, computes the level, appends one row. **Never** changes membership/divisor. Idempotent per ISO week (`--force` overwrites the current week). Prints top movers. |
| **Quarterly** | `python run_quarterly.py [YYYY-MM-DD]` | Refreshes shares/float, re-applies the cap, solves the proposed new divisor, writes a before/after diff with `status = PROPOSED`. Does **not** self-approve. |
| **Approve Q** | `python approve_quarterly.py` | Commits **only if** a human set `status = APPROVED` in the Quarterly sheet. Applies the divisor, logs to Divisor_Log + Unscheduled. |
| **Annual** | `python run_annual.py [YYYY-MM-DD]` | Screens the universe (+ `candidates.csv`) by float-adjusted USD cap, applies the 40/60 buffer, proposes `ADD/DROP/HOLD` rows with a `scope_ok` flag. |
| **Approve A** | `python approve_annual.py` | Applies rows a human confirmed (`scope_ok = YES`, `status = APPROVED`) via the divisor rule so the level stays continuous. |
| **Dry run** | `python dry_run.py [--fresh]` | Runs all four workflows end-to-end against live data (demonstrates the full approval + corporate-action paths). |

### The human-approval gate

The autonomous jobs **calculate and propose freely but never auto-commit** a
membership or divisor change. To apply a proposal:

1. Open `Fashion50_Index.xlsx`.
2. On the **Quarterly** sheet, review the before/after diff and set
   `status = APPROVED` (all rows). For **Annual**, set `scope_ok = YES` and
   `status = APPROVED` on the ADD/DROP rows you accept (scope = fashion / luxury
   / sportswear incl. jewellery, watches & eyewear; exclude off-price
   discounters, uniform/workwear rental, e-commerce-services and beauty).
3. Save and run `python approve_quarterly.py` / `python approve_annual.py`.

Mid-quarter corporate actions caught by the Weekly anomaly guard land on the
**Unscheduled** sheet as `PENDING_REVIEW`; the divisor is **not** changed until
a human confirms (the Weekly job keeps using the old divisor and keeps
flagging).

---

## Anomaly guard (Weekly)

Between rebalances, the Weekly job flags and routes to **Unscheduled + Alerts**
(and freezes the affected name at its prior good cap — never silently absorbing
the move) when any of these occur:

* a ticker fails to resolve, or any value is `MISSING`;
* `shares_outstanding` changed vs last run by more than **15%** (default);
* a name's `cap_usd` moved more than **25%** overnight (default);
* an FX rate is an outlier (>**15%** vs last run).

Thresholds live in `engine/config.py` (`Settings`).

---

## Early warning — pre-flight scan (proactive, before the calc)

The anomaly guard above is **reactive** (it fires after a big move). `preflight.py`
is **proactive** — run it a few days before the weekly job to see what's coming and
pre-decide the divisor treatment. It writes the **Watchlist** sheet and emails a
digest, using free forward signals:

| Signal | Source | Catches |
|--------|--------|---------|
| Share-count drift (issuance / buyback) | yfinance `get_shares_full()` time series vs the count baked into the divisor | Dilution/buybacks the day they post (flagged at 3%, well before the 15% reactive trip) |
| Splits / ex-dividend / earnings | `.splits`, `.calendar`, `get_earnings_dates()` | Upcoming corporate actions & expected cap moves in the next N days |
| At-risk data (the VSCO case) | current-quote health check | A ticker going stale/404 **before** it distorts a run |
| Membership drift | live cap ranks vs the drop-zone | Names drifting toward a reconstitution |
| M&A / delisting | `.news` keyword scan | Weak but early signal of a mid-quarter event |
| Index-review dates | deterministic (3rd Fri Mar/Jun/Sep/Dec; annual) | Countdown to the next scheduled review |

Each item carries a `severity`, `days_out`, and a `suggested_action`. It never
changes the index — HIGH/MEDIUM items become a heads-up so you can stage the
divisor review through the normal `Unscheduled` → approve path.

---

## Email alerts (configure via environment variables — never hardcode secrets)

| Variable | Meaning |
|----------|---------|
| `FASHION50_SMTP_HOST` | SMTP server, e.g. `smtp.gmail.com` |
| `FASHION50_SMTP_PORT` | Port (default `587`) |
| `FASHION50_SMTP_USER` | Login / from address |
| `FASHION50_SMTP_PASS` | App password / token |
| `FASHION50_ALERT_TO`  | Comma-separated recipient list |
| `FASHION50_SMTP_TLS`  | `1` (default) to STARTTLS, `0` to disable |

If SMTP is **not** configured, alerts are still written to the `Alerts` sheet
and appended to `logs/alerts.log` — they are never lost.

```bash
# bash
export FASHION50_SMTP_HOST=smtp.gmail.com
export FASHION50_SMTP_USER=you@example.com
export FASHION50_SMTP_PASS='app-password'
export FASHION50_ALERT_TO='desk@example.com,risk@example.com'
```

```powershell
# PowerShell
$env:FASHION50_SMTP_HOST = 'smtp.gmail.com'
$env:FASHION50_SMTP_USER = 'you@example.com'
$env:FASHION50_SMTP_PASS = 'app-password'
$env:FASHION50_ALERT_TO  = 'desk@example.com'
```

---

## Scheduling

### cron (Linux/macOS)

```cron
# Weekly — every Friday 22:00
0 22 * * 5  cd /path/to/fashion50 && /usr/bin/python3 run_weekly.py >> logs/weekly.log 2>&1

# Quarterly PROPOSAL — 3rd Friday of Mar/Jun/Sep/Dec, 22:30
30 22 15-21 3,6,9,12 5  cd /path/to/fashion50 && /usr/bin/python3 run_quarterly.py >> logs/quarterly.log 2>&1

# Annual PROPOSAL — 3rd Friday of June, 23:00
0 23 15-21 6 5  cd /path/to/fashion50 && /usr/bin/python3 run_annual.py >> logs/annual.log 2>&1
```

`15-21 <month> 5` fires on the day in that range that is a Friday — i.e. the
3rd Friday of the month. Approvals stay manual.

### Windows Task Scheduler

```powershell
# Pre-flight early warning — every weekday 08:00 (ALREADY INSTALLED as
# "Fashion50 Preflight"; uses run_preflight.bat which self-locates + logs)
schtasks /Create /TN "Fashion50 Preflight" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 08:00 `
  /TR "C:\Users\aresi\Claude\code\vgr-index\run_preflight.bat" /F
# manage it:  schtasks /Run /TN "Fashion50 Preflight"   |   /Query /TN ...   |   /Delete /TN ... /F

# Weekly — Fridays 22:00
schtasks /Create /TN "Fashion50 Weekly" /SC WEEKLY /D FRI /ST 22:00 `
  /TR "cmd /c cd /d C:\path\to\fashion50 && python run_weekly.py >> logs\weekly.log 2>&1"

# Quarterly PROPOSAL — run weekly, the script no-ops unless it's a rebalance date
schtasks /Create /TN "Fashion50 Quarterly" /SC MONTHLY /MO 3 /D FRI /ST 22:30 `
  /TR "cmd /c cd /d C:\path\to\fashion50 && python run_quarterly.py >> logs\quarterly.log 2>&1"

# Annual PROPOSAL
schtasks /Create /TN "Fashion50 Annual" /SC YEARLY /ST 23:00 `
  /TR "cmd /c cd /d C:\path\to\fashion50 && python run_annual.py >> logs\annual.log 2>&1"
```

Adjust the exact trigger dates to the 3rd-Friday convention in your operations
calendar; the engine itself is date-argument driven (`run_*.py YYYY-MM-DD`).

---

## Verification

```bash
python verify.py     # exits 0 only when every check is green
pytest -q            # unit + integration tests
```

`verify.py` runs **synthetic** checks — (1) base == 1000.00, (2) divisor
continuity for every logged change incl. a synthetic add-a-constituent proof,
(3) weights sum to 100% post-cap with no weight over the cap, (4) no silent
nulls (un-sourced → `MISSING` + anomaly), (5) Weekly idempotency, (6) the pytest
suite — **and live-workbook checks** against the real `Fashion50_Index.xlsx` when
present: ≥100 weekly rows, strictly-increasing weekly dates, every level > 0,
base ≈ 1000, every `Divisor_Log` change continuous, and current post-cap weights
summing to 100% ≤ cap. "Green" therefore certifies the **real** artifact.

---

## Data-source notes / gotchas handled

* **Minor units**: LSE quotes in pence (`GBp`/`GBX`) and JSE in cents (`ZAc`) are
  normalised to the major currency (÷100) before FX.
* **Suffixes**: HK (`.HK`), Tokyo (`.T`), India (`.NS`), etc. are verified on
  first fetch; anything that won't resolve is logged as `MISSING`, never guessed.
* **Stale shares**: Yahoo `sharesOutstanding` can be stale for non-US names —
  that is exactly what the Quarterly refresh + anomaly guard exist for.
* **Reproducibility**: every run persists the fetched price, shares, float, FX
  rate and the source used on the `Audit` and `FX` sheets.
