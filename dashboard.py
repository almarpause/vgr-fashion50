#!/usr/bin/env python3
"""Render a self-contained HTML dashboard + JSON feed from the workbook.

Layout: top index-growth metrics (Yesterday / L7D / L30D / MTD / YTD, colour-
coded); the index chart with a time-horizon selector (Since 2025 / 2023 / 2020)
and a vertical baseline line at 2025-01-01; a per-segment sub-index table; the
full constituents table (segment, price, yesterday & L7D change, YTD, cap, float,
weight, with IR links); and a per-company evolution selector. Everything inline
(no CDN); ``fashion50_data.json`` carries the same data for the website.

Usage:
    python dashboard.py [--open]
"""
from __future__ import annotations

import datetime
import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from openpyxl import load_workbook  # noqa: E402

from engine import config, indexmath  # noqa: E402
from engine.config import DEFAULT_SETTINGS, load_constituents, primary_segment  # noqa: E402

DASHBOARD_PATH = os.path.join(config.PROJECT_DIR, "Fashion50_Dashboard.html")
DATA_JSON_PATH = os.path.join(config.PROJECT_DIR, "fashion50_data.json")
BASE_DATE = "2025-01-01"


def _read_workbook():
    wb = load_workbook(config.WORKBOOK_PATH, data_only=True)

    def rows(sheet):
        ws = wb[sheet]
        headers = [c.value for c in ws[1]]
        out = []
        for r in ws.iter_rows(min_row=2, values_only=True):
            if r is None or all(v is None for v in r):
                continue
            out.append({h: r[i] if i < len(r) else None
                        for i, h in enumerate(headers)})
        return out

    def wide(sheet):
        """Wide sheet -> (col_order, {col: [(date, value)]})."""
        order, data = [], {}
        if sheet in wb.sheetnames:
            ws = wb[sheet]
            hdr = [c.value for c in ws[1]]
            order = hdr[1:]
            for c in order:
                data[c] = []
            for r in ws.iter_rows(min_row=2, values_only=True):
                d = r[0]
                if d is None:
                    continue
                for j, c in enumerate(order, start=1):
                    v = r[j] if j < len(r) else None
                    if v is not None:
                        data[c].append((str(d), float(v)))
        return order, data

    _, prices = wide("Prices")
    seg_order, subidx = wide("SubIndices")
    return (rows("Weekly"), rows("Audit"), rows("Constituents"),
            rows("Unscheduled"), prices, seg_order, subidx)


def _fmt(x, nd=2):
    try:
        return f"{float(x):,.{nd}f}"
    except (TypeError, ValueError):
        return "—"


def _pct(x, nd=2):
    if x is None:
        return "—"
    return f"{x:+.{nd}f}%"


def _sign(v):
    if v is None:
        return "flat"
    return "up" if v >= 0 else "down"


def _changes(series) -> dict:
    """latest + Yesterday / L7D / L30D / MTD / YTD from [(date_iso, value)]."""
    s = [(d, v) for d, v in series if v is not None]
    out = {"latest": None, "yesterday": None, "l7d": None, "l30d": None,
           "mtd": None, "ytd": None}
    if not s:
        return out
    ld, lv = s[-1]
    out["latest"] = lv

    def base_days(n):
        target = datetime.date.fromisoformat(ld) - datetime.timedelta(days=n)
        b = None
        for d, v in s:
            if datetime.date.fromisoformat(d) <= target:
                b = v
            else:
                break
        return b

    def base_before(since_iso):
        b = None
        for d, v in s:
            if d < since_iso:
                b = v
            else:
                break
        return b

    if len(s) >= 2 and s[-2][1]:
        out["yesterday"] = (lv / s[-2][1] - 1) * 100
    for key, n in (("l7d", 7), ("l30d", 30)):
        b = base_days(n)
        if b:
            out[key] = (lv / b - 1) * 100
    y, m = ld[:4], ld[5:7]
    bm = base_before(f"{y}-{m}-01")
    if bm:
        out["mtd"] = (lv / bm - 1) * 100
    by = base_before(f"{y}-01-01")
    if by:
        out["ytd"] = (lv / by - 1) * 100
    return out


def _metric_tile(label, pct):
    return (f'<div class="kpi"><div class="k-label">{label}</div>'
            f'<div class="k-val {_sign(pct)}">{_pct(pct)}</div></div>')


def _segment_table(seg_order, subidx):
    body = []
    for seg in seg_order:
        m = _changes(subidx.get(seg, []))
        body.append(
            f"<tr><td><b>{html.escape(str(seg))}</b></td>"
            f"<td class='num'>{_fmt(m['latest'])}</td>"
            f"<td class='num {_sign(m['yesterday'])}'>{_pct(m['yesterday'])}</td>"
            f"<td class='num {_sign(m['l7d'])}'>{_pct(m['l7d'])}</td>"
            f"<td class='num {_sign(m['l30d'])}'>{_pct(m['l30d'])}</td>"
            f"<td class='num {_sign(m['mtd'])}'>{_pct(m['mtd'])}</td>"
            f"<td class='num {_sign(m['ytd'])}'>{_pct(m['ytd'])}</td></tr>")
    return (
        "<table class='wt'><thead><tr><th>Segment sub-index</th>"
        "<th class='num'>Level</th><th class='num'>Yday</th><th class='num'>L7D</th>"
        "<th class='num'>L30D</th><th class='num'>MTD</th><th class='num'>YTD</th>"
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table>")


def _constituents_table(recs):
    body = []
    for r in recs:
        nm = html.escape(str(r["name"]))
        if r.get("ir"):
            nm = (f'<a href="{html.escape(r["ir"])}" target="_blank" '
                  f'rel="noopener noreferrer">{nm}</a>')
        body.append(
            f"<tr><td class='r'>{r['rank']}</td>"
            f"<td>{nm}<div class='sub'>{html.escape(str(r['ticker']))}</div></td>"
            f"<td>{html.escape(str(r['segment']))}</td>"
            f"<td class='num'>{_fmt(r['price'])}<div class='sub'>{r['ccy']}</div></td>"
            f"<td class='num {_sign(r['yday'])}'>{_pct(r['yday'], 2)}</td>"
            f"<td class='num {_sign(r['l7d'])}'>{_pct(r['l7d'], 2)}</td>"
            f"<td class='num {_sign(r['ytd'])}'>{_pct(r['ytd'], 1)}</td>"
            f"<td class='num'>{_fmt(r['fullcap'], 1)}</td>"
            f"<td class='num'>{r['float']:.0f}%</td>"
            f"<td class='num'>{r['wt']:.2f}%</td></tr>")
    return (
        "<table class='wt'><thead><tr>"
        "<th class='r'>#</th><th>Brand</th><th>Segment</th>"
        "<th class='num'>Price</th><th class='num'>Yday</th><th class='num'>L7D</th>"
        "<th class='num'>YTD</th><th class='num'>Mkt cap $bn</th>"
        "<th class='num'>Float</th><th class='num'>Index wt</th>"
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table>")


def _prepare(weekly, audit, consts, prices):
    settings = DEFAULT_SETTINGS
    series = [(str(r["run_date"]), round(float(r["index_level"]), 4))
              for r in weekly if r.get("index_level") is not None]
    seg_of = {c["yahoo_ticker"]: c.get("segment", "?") for c in consts}
    name_of = {c["yahoo_ticker"]: c.get("name") for c in consts}
    ir_of = {c.yahoo_ticker: c.ir_url for c in load_constituents()}
    ccy_of = {r["ticker"]: r.get("currency") for r in audit}
    ff_of = {r["ticker"]: (r.get("float_factor") or 1.0) for r in audit}
    caps = {r["ticker"]: float(r["cap_usd"]) for r in audit
            if r.get("cap_usd") and r.get("status") == "OK"}
    total_cap = sum(caps.values()) or 1.0
    weights = indexmath.compute_weights(caps, cap=settings.effective_cap)
    ranked = sorted(weights, key=weights.get, reverse=True)

    recs = []
    for i, t in enumerate(ranked, 1):
        ch = _changes(prices.get(t, []))
        ff = ff_of.get(t, 1.0) or 1.0
        recs.append({
            "rank": i, "ticker": t, "name": name_of.get(t, t),
            "ir": ir_of.get(t, ""), "segment": primary_segment(seg_of.get(t)),
            "price": ch["latest"], "ccy": ccy_of.get(t, ""),
            "yday": ch["yesterday"], "l7d": ch["l7d"], "ytd": ch["ytd"],
            "fullcap": caps[t] / ff / 1e9, "float": ff * 100,
            "raw": caps[t] / total_cap * 100, "wt": weights[t] * 100,
        })
    idx_metrics = _changes([(d, lv) for d, lv in series])
    return series, recs, idx_metrics, name_of, ranked


def build_html() -> str:
    weekly, audit, consts, unsched, prices, seg_order, subidx = _read_workbook()
    series, recs, m, name_of, ranked = _prepare(weekly, audit, consts, prices)
    latest = m["latest"] or 0.0

    sel_prices = {t: [[d, p] for d, p in prices.get(t, [])] for t in ranked
                  if prices.get(t)}
    sel_names = {t: name_of.get(t, t) for t in sel_prices}
    idx_series = [[d, lv] for d, lv in series]
    start_year = int(series[0][0][:4]) if series else 2020
    sel_order = sorted((t for t in ranked if t in sel_prices),
                       key=lambda t: (name_of.get(t, t) or "").lower())
    default_t = next((t for t in ranked if t in sel_prices), None)
    options = "".join(
        f'<option value="{t}"{" selected" if t == default_t else ""}>'
        f'{html.escape(str(name_of.get(t, t)))}</option>' for t in sel_order)

    horizons = [("Since 2025", "2025-01-01")]
    if start_year <= 2023:
        horizons.append(("Since 2023", "2023-01-01"))
    horizons.append((f"Since {start_year}", series[0][0] if series else "2020-01-01"))
    hbtns = "".join(
        f'<button class="hbtn{" active" if i == 0 else ""}" '
        f'data-start="{s}">{lab}</button>'
        for i, (lab, s) in enumerate(horizons))

    kpis = (
        f'<div class="kpi hero"><div class="k-label">Index level</div>'
        f'<div class="k-val">{_fmt(latest)}</div>'
        f'<div class="k-sub">1000 on {BASE_DATE} · as of {series[-1][0] if series else "—"}</div></div>'
        + _metric_tile("Yesterday", m["yesterday"])
        + _metric_tile("L7D", m["l7d"])
        + _metric_tile("L30D", m["l30d"])
        + _metric_tile("MTD", m["mtd"])
        + _metric_tile("YTD", m["ytd"]))

    head = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VGR 50 — Largest Lifestyle Companies</title>
<style>
:root {{ --bg:#f7f7f8; --card:#fff; --ink:#1a1a1f; --muted:#6b7280;
  --line:#e5e7eb; --accent:#4f46e5; --up:#0f9d58; --down:#d1453b; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0e0e12; --card:#17171d;
  --ink:#ececf1; --muted:#9aa0ab; --line:#26262e; --accent:#8b85ff;
  --up:#3ecf8e; --down:#ff6b5e; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
.wrap {{ max-width:1120px; margin:0 auto; padding:28px 20px 60px; }}
header h1 {{ margin:0 0 2px; font-size:24px; letter-spacing:-.02em; }}
header .sub {{ color:var(--muted); font-size:13px; }}
.kpis {{ display:grid; grid-template-columns:repeat(6,1fr); gap:12px; margin:22px 0; }}
.kpi {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }}
.k-label {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
.k-val {{ font-size:24px; font-weight:650; margin-top:4px; letter-spacing:-.02em; }}
.k-sub {{ font-size:11px; color:var(--muted); margin-top:2px; }}
.up {{ color:var(--up); }} .down {{ color:var(--down); }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:18px 20px; margin:16px 0; }}
.card h2 {{ margin:0 0 12px; font-size:15px; font-weight:600; }}
.chart {{ width:100%; height:auto; }}
.grid {{ stroke:var(--line); stroke-width:1; }}
.baseline {{ stroke:var(--muted); stroke-width:1; stroke-dasharray:4 4; opacity:.7; }}
.vline {{ stroke:var(--accent); stroke-width:1.2; stroke-dasharray:3 3; opacity:.8; }}
.axis {{ fill:var(--muted); font-size:11px; }}
.hrow {{ display:flex; gap:8px; margin-bottom:8px; flex-wrap:wrap; }}
.hbtn {{ background:var(--card); color:var(--muted); border:1px solid var(--line);
  border-radius:8px; padding:5px 12px; font-size:13px; cursor:pointer; }}
.hbtn.active {{ background:var(--accent); color:#fff; border-color:var(--accent); }}
table.wt {{ width:100%; border-collapse:collapse; font-size:13px; }}
table.wt th {{ text-align:left; color:var(--muted); font-weight:600; font-size:11px;
  text-transform:uppercase; letter-spacing:.03em; padding:6px 8px; border-bottom:1px solid var(--line); }}
table.wt td {{ padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
table.wt td.r {{ color:var(--muted); width:30px; text-align:right; font-variant-numeric:tabular-nums; }}
table.wt th.num, table.wt td.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
table.wt a {{ color:var(--accent); text-decoration:none; }}
table.wt a:hover {{ text-decoration:underline; }}
.sub {{ color:var(--muted); font-size:11px; }}
.legend {{ color:var(--muted); font-size:12px; margin-top:10px; }}
select {{ background:var(--card); color:var(--ink); border:1px solid var(--line);
  border-radius:8px; padding:7px 10px; font-size:14px; max-width:320px; }}
.cocap {{ color:var(--muted); font-size:13px; margin-top:6px; }}
footer {{ color:var(--muted); font-size:12px; margin-top:22px; }}
@media (max-width:860px) {{ .kpis {{ grid-template-columns:repeat(3,1fr); }} }}
@media (max-width:520px) {{ .kpis {{ grid-template-columns:repeat(2,1fr); }} }}
</style></head>
<body><div class="wrap">
<header>
  <h1>VGR 50 — Largest Lifestyle Companies</h1>
  <div class="sub">Market-cap weighted · float-adjusted · USD — daily, 1000 on {BASE_DATE}</div>
</header>
<div class="kpis">{kpis}</div>
<div class="card"><h2>Index level (1000 on {BASE_DATE})</h2>
  <div class="hrow">{hbtns}</div>
  <div id="idxchart"></div>
</div>
<div class="card"><h2>Segment sub-indices (1000 on {BASE_DATE})</h2>
{_segment_table(seg_order, subidx)}</div>
<div class="card"><h2>All {len(recs)} constituents</h2>
{_constituents_table(recs)}
<div class="legend"><b>Index wt</b> is the float-adjusted market-cap share.
<b>Mkt cap</b> is full size (shares × price); <b>Float</b> the tradable share.
Price · Yday · L7D · YTD are the local share price.</div>
</div>
<div class="card"><h2>Company evolution — rebased to 1000 at {BASE_DATE}</h2>
  <select id="co">{options}</select>
  <div id="cochart"></div>
  <div class="cocap" id="cocap"></div>
</div>
<footer>Generated from Fashion50_Index.xlsx · shares/float held at current snapshot (documented approximation) · not investment advice.</footer>
</div>"""

    script = """
<script>
const IDXFULL = __IDXFULL__;
const BASE = __BASE__;
const PRICES = __PRICES__;
const NAMES = __NAMES__;
const W=1040,H=320,pad=48;

function axisPoly(a, lo, span){
  return a.map((r,i)=>((pad+(W-2*pad)*i/(a.length-1)).toFixed(1))+","+
    (pad+(H-2*pad)*(1-(r[1]-lo)/span)).toFixed(1)).join(" ");
}
function drawIndex(startISO){
  const data = IDXFULL.filter(r=>r[0]>=startISO);
  if(data.length<2) return;
  const vals=data.map(r=>r[1]);
  let lo=Math.min.apply(null,vals), hi=Math.max.apply(null,vals);
  const span=(hi-lo)||1;
  const Y=v=>pad+(H-2*pad)*(1-(v-lo)/span);
  const pts=axisPoly(data,lo,span);
  const area=pad.toFixed(1)+","+(H-pad).toFixed(1)+" "+pts+" "+(W-pad).toFixed(1)+","+(H-pad).toFixed(1);
  let grid="";
  for(const f of [0,.25,.5,.75,1]){const val=lo+span*f,yy=Y(val);
    grid+='<line x1="'+pad+'" y1="'+yy.toFixed(1)+'" x2="'+(W-pad)+'" y2="'+yy.toFixed(1)+'" class="grid"/>'+
    '<text x="'+(pad-8)+'" y="'+(yy+4).toFixed(1)+'" text-anchor="end" class="axis">'+val.toFixed(0)+'</text>';}
  let base1000="";
  if(lo<=1000&&1000<=hi){const yb=Y(1000);
    base1000='<line x1="'+pad+'" y1="'+yb.toFixed(1)+'" x2="'+(W-pad)+'" y2="'+yb.toFixed(1)+'" class="baseline"/>'+
    '<text x="'+(pad+4)+'" y="'+(yb-5).toFixed(1)+'" class="axis">1000</text>';}
  // vertical baseline line at BASE date (if within view)
  let vline="";
  let bi=data.findIndex(r=>r[0]>=BASE);
  if(bi>0){const xb=pad+(W-2*pad)*bi/(data.length-1);
    vline='<line x1="'+xb.toFixed(1)+'" y1="'+pad+'" x2="'+xb.toFixed(1)+'" y2="'+(H-pad)+'" class="vline"/>'+
    '<text x="'+(xb+4).toFixed(1)+'" y="'+(pad+12)+'" class="axis" fill="var(--accent)">'+BASE+' = 1000</text>';}
  let xlab="";
  for(const f of [0,.25,.5,.75,1]){const i=Math.round((data.length-1)*f);
    const x=pad+(W-2*pad)*i/(data.length-1);
    xlab+='<text x="'+x.toFixed(1)+'" y="'+(H-pad+22)+'" text-anchor="middle" class="axis">'+data[i][0]+'</text>';}
  const lx=pad+(W-2*pad), ly=Y(data[data.length-1][1]);
  document.getElementById("idxchart").innerHTML=
    '<svg viewBox="0 0 '+W+' '+H+'" class="chart">'+grid+base1000+vline+
    '<polygon points="'+area+'" fill="var(--accent)" fill-opacity="0.12"/>'+
    '<polyline points="'+pts+'" fill="none" stroke="var(--accent)" stroke-width="2"/>'+
    '<circle cx="'+lx.toFixed(1)+'" cy="'+ly.toFixed(1)+'" r="4" fill="var(--accent)"/>'+
    xlab+'</svg>';
}
document.querySelectorAll(".hbtn").forEach(b=>b.addEventListener("click",e=>{
  document.querySelectorAll(".hbtn").forEach(x=>x.classList.remove("active"));
  e.target.classList.add("active");
  drawIndex(e.target.dataset.start);
}));
drawIndex("__BASE__");

function drawCo(t){
  const raw=(PRICES[t]||[]).filter(r=>r[1]!=null);
  if(!raw.length) return;
  const base=raw[0][1];
  const comp=raw.map(r=>[r[0], r[1]/base*1000]);
  const vals=comp.map(r=>r[1]).concat(IDXFULL.filter(r=>r[0]>=BASE).map(r=>r[1]));
  let lo=Math.min.apply(null,vals), hi=Math.max.apply(null,vals);
  const span=(hi-lo)||1;
  const Y=v=>pad+(H-2*pad)*(1-(v-lo)/span);
  const idx=IDXFULL.filter(r=>r[0]>=BASE);
  const poly=a=>a.map((r,i)=>((pad+(W-2*pad)*i/(a.length-1)).toFixed(1))+","+Y(r[1]).toFixed(1)).join(" ");
  let grid="";
  for(const f of [0,.25,.5,.75,1]){const val=lo+span*f,yy=Y(val);
    grid+='<line x1="'+pad+'" y1="'+yy.toFixed(1)+'" x2="'+(W-pad)+'" y2="'+yy.toFixed(1)+'" class="grid"/>'+
    '<text x="'+(pad-8)+'" y="'+(yy+4).toFixed(1)+'" text-anchor="end" class="axis">'+val.toFixed(0)+'</text>';}
  let b1="";
  if(lo<=1000&&1000<=hi){const yb=Y(1000);
    b1='<line x1="'+pad+'" y1="'+yb.toFixed(1)+'" x2="'+(W-pad)+'" y2="'+yb.toFixed(1)+'" class="baseline"/>';}
  let xlab="";
  for(const f of [0,.25,.5,.75,1]){const i=Math.round((idx.length-1)*f);
    const x=pad+(W-2*pad)*i/(idx.length-1);
    xlab+='<text x="'+x.toFixed(1)+'" y="'+(H-pad+22)+'" text-anchor="middle" class="axis">'+idx[i][0]+'</text>';}
  document.getElementById("cochart").innerHTML=
    '<svg viewBox="0 0 '+W+' '+H+'" class="chart">'+grid+b1+
    '<polyline points="'+poly(idx)+'" fill="none" stroke="var(--muted)" stroke-width="1.6" stroke-dasharray="5 4"/>'+
    '<polyline points="'+poly(comp)+'" fill="none" stroke="var(--accent)" stroke-width="2.3"/>'+xlab+'</svg>';
  const last=comp[comp.length-1][1], pct=(last/1000-1)*100;
  document.getElementById("cocap").innerHTML=
    "<b style='color:var(--accent)'>"+NAMES[t]+"</b>: rebased 1000 -> "+last.toFixed(1)+
    " ("+(pct>=0?"+":"")+pct.toFixed(1)+"% since base). Dashed grey = the index.";
}
const sel=document.getElementById("co");
sel.addEventListener("change",e=>drawCo(e.target.value));
drawCo(sel.value);
</script>
</body></html>"""
    script = (script
              .replace("__IDXFULL__", json.dumps(idx_series))
              .replace("__PRICES__", json.dumps(sel_prices))
              .replace("__NAMES__", json.dumps(sel_names))
              .replace("__BASE__", json.dumps(BASE_DATE)))
    return head + script


def _r(x, nd=2):
    return round(x, nd) if isinstance(x, (int, float)) else None


def build_data() -> dict:
    weekly, audit, consts, unsched, prices, seg_order, subidx = _read_workbook()
    series, recs, m, name_of, ranked = _prepare(weekly, audit, consts, prices)
    segments = []
    for seg in seg_order:
        sm = _changes(subidx.get(seg, []))
        segments.append({
            "segment": seg, "level": _r(sm["latest"]),
            "chg_yesterday_pct": _r(sm["yesterday"]), "chg_l7d_pct": _r(sm["l7d"]),
            "chg_l30d_pct": _r(sm["l30d"]), "mtd_pct": _r(sm["mtd"]),
            "ytd_pct": _r(sm["ytd"]),
        })
    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds"),
        "base_date": BASE_DATE, "base_level": 1000.0,
        "history_start": series[0][0] if series else None,
        "latest_date": series[-1][0] if series else None,
        "latest_level": m["latest"],
        "index_change": {"yesterday": _r(m["yesterday"]), "l7d": _r(m["l7d"]),
                         "l30d": _r(m["l30d"]), "mtd": _r(m["mtd"]),
                         "ytd": _r(m["ytd"])},
        "n_constituents": len(recs),
        "index": [[d, lv] for d, lv in series],
        "segments": segments,
        "subindices": {seg: [[d, round(v, 3)] for d, v in subidx.get(seg, [])]
                       for seg in seg_order},
        "constituents": [{
            "rank": r["rank"], "ticker": r["ticker"], "name": r["name"],
            "ir_url": r["ir"], "segment": r["segment"],
            "price": _r(r["price"], 4), "currency": r["ccy"],
            "chg_yesterday_pct": _r(r["yday"]), "chg_l7d_pct": _r(r["l7d"]),
            "ytd_pct": _r(r["ytd"], 1), "full_cap_usd_bn": _r(r["fullcap"], 3),
            "float_pct": _r(r["float"], 1), "weight_pct": _r(r["wt"], 4),
        } for r in recs],
        "prices": {t: [[d, round(p, 4)] for d, p in prices.get(t, [])]
                   for t in ranked if prices.get(t)},
    }


def main(argv: list[str]) -> int:
    if not os.path.exists(config.WORKBOOK_PATH):
        print("No workbook found — run backfill.py first.")
        return 1
    with open(DASHBOARD_PATH, "w", encoding="utf-8") as fh:
        fh.write(build_html())
    print(f"Wrote {DASHBOARD_PATH}")
    with open(DATA_JSON_PATH, "w", encoding="utf-8") as fh:
        json.dump(build_data(), fh, ensure_ascii=False)
    print(f"Wrote {DATA_JSON_PATH}")
    if "--open" in argv:
        try:
            import subprocess
            subprocess.Popen(["cmd", "/c", "start", "chrome", DASHBOARD_PATH])
        except Exception:
            print("(could not auto-open Chrome; open the file manually)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
