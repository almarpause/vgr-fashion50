#!/usr/bin/env python3
"""Render a self-contained HTML dashboard + JSON feed — VGR "figure" style.

Economist chart grammar as VGR publishes it: warm off-white page, white cards,
Georgia serif headlines and big values, Arial labels, a red "VERY GOOD RETAIL ·
FIGURE" kicker box + VGR wordmark, Econ Red accent (the 2 Jan 2025 anchor line,
negative values), Navy data on a warm-grey panel with white gridlines, and a
source line at the foot. ``fashion50_data.json`` carries the same data.

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
BASE_LABEL = "2 Jan 2025"


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


def _kpi(label, value, sub, cls=""):
    return (f'<div class="kpi"><div class="kl">{html.escape(label)}</div>'
            f'<div class="kv {cls}">{value}</div>'
            f'<div class="ks">{html.escape(sub)}</div></div>')


def _metric_kpi(label, pct):
    return _kpi(label, _pct(pct), "vs prior close" if label == "Yesterday"
               else "", _sign(pct))


def _cardhead(headline, dek):
    return (f'<div class="chead">{html.escape(headline)}</div>'
            f'<div class="cdek">{html.escape(dek)}</div>')


def _segment_table(seg_order, subidx):
    body = []
    for seg in seg_order:
        m = _changes(subidx.get(seg, []))
        body.append(
            f"<tr><td><span class='dot s-{html.escape(str(seg).lower())}'></span>"
            f"<b>{html.escape(str(seg))}</b></td>"
            f"<td class='num'>{_fmt(m['latest'], 1)}</td>"
            f"<td class='num {_sign(m['yesterday'])}'>{_pct(m['yesterday'])}</td>"
            f"<td class='num {_sign(m['l7d'])}'>{_pct(m['l7d'])}</td>"
            f"<td class='num {_sign(m['l30d'])}'>{_pct(m['l30d'])}</td>"
            f"<td class='num {_sign(m['mtd'])}'>{_pct(m['mtd'])}</td>"
            f"<td class='num {_sign(m['ytd'])}'>{_pct(m['ytd'])}</td></tr>")
    return (
        "<table class='wt'><thead><tr><th>Segment</th>"
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
            f"<td class='num'>{_fmt(r['price'])}<div class='sub'>{r['ccy']}</div></td>"
            f"<td class='num {_sign(r['yday'])}'>{_pct(r['yday'], 2)}</td>"
            f"<td class='num {_sign(r['l7d'])}'>{_pct(r['l7d'], 2)}</td>"
            f"<td class='num {_sign(r['ytd'])}'>{_pct(r['ytd'], 1)}</td>"
            f"<td class='num'>{_fmt(r['fullcap'], 1)}</td>"
            f"<td class='num'>{r['float']:.0f}%</td>"
            f"<td class='num'>{r['wt']:.2f}%</td></tr>")
    return (
        "<table class='wt'><thead><tr>"
        "<th class='r'>#</th><th>Company</th>"
        "<th class='num'>Price</th><th class='num'>Yday</th><th class='num'>L7D</th>"
        "<th class='num'>YTD</th><th class='num'>Cap $bn</th>"
        "<th class='num'>Float</th><th class='num'>Weight</th>"
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
    ytd = m["ytd"]
    as_of = series[-1][0] if series else "—"
    gen = datetime.datetime.now(datetime.timezone.utc).date().isoformat()

    def finding(v):
        if v is None:
            return "flat year to date"
        return (f"up {v:.1f}% year to date" if v >= 0
                else f"down {abs(v):.1f}% year to date")
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
        f'<button class="pill{" on" if i == 0 else ""}" '
        f'data-start="{s}">{lab}</button>'
        for i, (lab, s) in enumerate(horizons))

    kpis = (
        _kpi("Index level", _fmt(latest, 1), "1,000 = " + BASE_LABEL)
        + _metric_kpi("Yesterday", m["yesterday"])
        + _metric_kpi("L7D", m["l7d"])
        + _metric_kpi("L30D", m["l30d"])
        + _metric_kpi("MTD", m["mtd"])
        + _metric_kpi("YTD", m["ytd"]))

    meta = (f"{len(recs)} constituents · float-adjusted market cap · USD · "
            f"daily · 1,000 = {BASE_LABEL} · as of {as_of}")
    src = ("Sources: Yahoo Finance (daily split-adjusted close); FX from ECB via "
           "Frankfurter, yfinance fallback. Shares and float held at the current "
           f"snapshot; pre-{BASE_DATE[:4]} history is a reconstruction. Not investment advice.")

    head = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VGR 50 — Largest Lifestyle Companies</title>
<style>
:root {{ --paper:#F1ECE3; --card:#fff; --panel:#E8E3D9; --ink:#141414;
  --muted:#8a8272; --sub:#6f6a5f; --line:#E0DACE; --rule:#1a1a1a;
  --red:#C0110A; --navy:#10294B; --up:#1E7A46; --down:#C0110A;
  --serif:Georgia,"Times New Roman",serif; --sans:Arial,Helvetica,sans-serif; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--paper); color:var(--ink); font-family:var(--sans);
  font-size:14px; line-height:1.5; -webkit-font-smoothing:antialiased; }}
.topbar {{ height:3px; background:var(--red); }}
.wrap {{ max-width:1080px; margin:0 auto; padding:22px 22px 60px; }}
.hd {{ display:flex; justify-content:space-between; align-items:flex-start; }}
.figbox {{ background:var(--red); color:#fff; font-family:var(--sans); font-weight:700;
  font-size:11px; letter-spacing:.09em; text-transform:uppercase; padding:4px 9px; }}
.vgr {{ font-family:var(--sans); font-weight:800; font-size:30px; letter-spacing:-.02em; color:var(--ink); }}
h1 {{ font-family:var(--serif); font-weight:700; font-size:38px; line-height:1.05;
  margin:14px 0 8px; letter-spacing:-.01em; }}
.lede {{ font-family:var(--serif); font-size:17px; color:var(--sub); max-width:74ch; }}
.meta {{ font-family:var(--sans); font-size:11.5px; color:var(--muted); margin-top:10px;
  border-top:1px solid var(--line); padding-top:10px; }}
.kpis {{ display:grid; grid-template-columns:repeat(6,1fr); gap:12px; margin:16px 0 4px; }}
.kpi {{ background:var(--card); border:1px solid var(--line); padding:13px 15px; }}
.kl {{ font-family:var(--sans); font-size:10px; text-transform:uppercase; letter-spacing:.09em; color:var(--muted); }}
.kv {{ font-family:var(--serif); font-weight:700; font-size:25px; margin-top:5px; font-variant-numeric:tabular-nums; }}
.ks {{ font-family:var(--sans); font-size:11px; color:var(--muted); margin-top:2px; }}
.up {{ color:var(--up); }} .down {{ color:var(--down); }}
.card {{ background:var(--card); border:1px solid var(--line); padding:20px 22px; margin:16px 0; }}
.chead {{ font-family:var(--serif); font-weight:700; font-size:20px; letter-spacing:-.01em; }}
.cdek {{ font-family:var(--sans); font-size:12.5px; color:var(--sub); margin-top:2px; }}
.chart {{ width:100%; height:auto; margin-top:12px; }}
.pillrow {{ display:flex; gap:7px; margin:12px 0 2px; flex-wrap:wrap; }}
.pill {{ background:var(--card); color:var(--ink); border:1px solid var(--rule);
  font-family:var(--sans); font-size:11.5px; font-weight:600; padding:5px 13px; cursor:pointer; border-radius:2px; }}
.pill.on {{ background:var(--rule); color:#fff; }}
table.wt {{ width:100%; border-collapse:collapse; font-size:13px; margin-top:10px; }}
table.wt th {{ text-align:left; font-family:var(--sans); font-size:10px; font-weight:700;
  text-transform:uppercase; letter-spacing:.06em; color:var(--muted);
  padding:6px 8px; border-bottom:1.5px solid var(--rule); }}
table.wt td {{ padding:7px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
table.wt td.r {{ color:var(--muted); width:26px; text-align:right; font-variant-numeric:tabular-nums; }}
table.wt th.num, table.wt td.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
table.wt a {{ color:var(--navy); text-decoration:none; }}
table.wt a:hover {{ text-decoration:underline; }}
.sub {{ color:var(--muted); font-size:11px; }}
.dot {{ display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:7px; vertical-align:middle; }}
.s-luxury {{ background:var(--navy); }} .s-fashion {{ background:#A8382B; }}
.s-sportswear {{ background:#C8891E; }} .s-apparel {{ background:#4E7A57; }}
.s-footwear {{ background:#79A9C1; }}
select {{ background:var(--card); color:var(--ink); border:1px solid var(--rule); border-radius:2px;
  padding:7px 11px; font-family:var(--sans); font-size:13px; max-width:340px; margin-top:12px; }}
.cocap {{ color:var(--sub); font-size:12.5px; margin-top:8px; }}
.src {{ font-family:var(--sans); font-size:11px; color:var(--muted); margin-top:16px;
  border-top:1.5px solid var(--rule); padding-top:10px; }}
.foot {{ display:flex; justify-content:space-between; align-items:flex-end; margin-top:6px; }}
.foot .fnote {{ font-size:11px; color:var(--muted); }}
@media (max-width:860px) {{ .kpis {{ grid-template-columns:repeat(3,1fr); }} h1 {{ font-size:30px; }} }}
@media (max-width:520px) {{ .kpis {{ grid-template-columns:repeat(2,1fr); }} }}
</style></head>
<body>
<div class="topbar"></div>
<div class="wrap">
<header>
  <div class="hd"><span class="figbox">Very Good Retail · Figure</span><span class="vgr">VGR</span></div>
  <h1>VGR 50 — Largest Lifestyle Companies</h1>
  <div class="lede">A market-cap-weighted, float-adjusted index of the 50 largest listed fashion, luxury and sportswear companies, priced daily in US dollars and anchored to 1,000 on {BASE_LABEL}.</div>
  <div class="meta">{html.escape(meta)}</div>
</header>
<div class="kpis">{kpis}</div>
<div class="card">
  {_cardhead("VGR 50 is " + finding(ytd), "Daily index level · USD · red line marks the " + BASE_LABEL + " base of 1,000")}
  <div class="pillrow">{hbtns}</div>
  <div id="idxchart"></div>
</div>
<div class="card">
  {_cardhead("The 50 largest listed lifestyle companies", "Ranked by float-adjusted market cap · local share price · negatives in red · names link to investor relations")}
{_constituents_table(recs)}
</div>
<div class="card">
  {_cardhead("Single-company evolution", "Any constituent, rebased to 1,000 at " + BASE_LABEL + ", against the index (grey)")}
  <select id="co">{options}</select>
  <div id="cochart"></div>
  <div class="cocap" id="cocap"></div>
</div>
<div class="src">{html.escape(src)}</div>
<div class="foot"><span class="vgr" style="font-size:24px">VGR</span>
  <span class="fnote">VGR 50 — Largest Lifestyle Companies · generated {gen}</span></div>
</div>"""

    script = """
<script>
const IDXFULL = __IDXFULL__;
const BASE = __BASE__;
const PRICES = __PRICES__;
const NAMES = __NAMES__;
const W=1000,H=300,L=52,R=16,T=16,B=30;
const NAVY="#10294B",RED="#C0110A",PANEL="#E8E3D9",GRIDW="#ffffff",
      AXIS="#8a8272",STEEL="#8E9BAA",DARK="#3D4A5A";
const PW=W-L-R, PH=H-T-B;

function frame(lo,span){
  let s='<rect x="'+L+'" y="'+T+'" width="'+PW+'" height="'+PH+'" fill="'+PANEL+'"/>';
  for(const f of [0,.25,.5,.75,1]){const val=lo+span*f,yy=T+PH*(1-f);
    s+='<line x1="'+L+'" y1="'+yy.toFixed(1)+'" x2="'+(W-R)+'" y2="'+yy.toFixed(1)+'" stroke="'+GRIDW+'" stroke-width="1.1"/>'+
    '<text x="'+(L-7)+'" y="'+(yy+3.5).toFixed(1)+'" text-anchor="end" fill="'+AXIS+'" font-size="10.5" font-family="Arial">'+val.toFixed(0)+'</text>';}
  s+='<line x1="'+L+'" y1="'+(T+PH)+'" x2="'+(W-R)+'" y2="'+(T+PH)+'" stroke="'+DARK+'" stroke-width="1"/>';
  return s;
}
function xlabels(data){
  let s='';
  for(const f of [0,.25,.5,.75,1]){const i=Math.round((data.length-1)*f);
    const x=L+PW*i/(data.length-1);
    s+='<text x="'+x.toFixed(1)+'" y="'+(H-9)+'" text-anchor="middle" fill="'+AXIS+'" font-size="10.5" font-family="Arial">'+data[i][0]+'</text>';}
  return s;
}
function drawIndex(startISO){
  const data=IDXFULL.filter(r=>r[0]>=startISO);
  if(data.length<2) return;
  const vals=data.map(r=>r[1]);
  let lo=Math.min.apply(null,vals),hi=Math.max.apply(null,vals);
  lo=Math.floor(lo/50)*50; hi=Math.ceil(hi/50)*50; const span=(hi-lo)||1;
  const X=i=>L+PW*i/(data.length-1), Y=v=>T+PH*(1-(v-lo)/span);
  const pts=data.map((r,i)=>X(i).toFixed(1)+','+Y(r[1]).toFixed(1)).join(' ');
  let s=frame(lo,span);
  if(lo<=1000&&hi>=1000){const yb=Y(1000);
    s+='<line x1="'+L+'" y1="'+yb.toFixed(1)+'" x2="'+(W-R)+'" y2="'+yb.toFixed(1)+'" stroke="'+STEEL+'" stroke-dasharray="4 3"/>';}
  let bi=data.findIndex(r=>r[0]>=BASE);
  if(bi>0){const xb=X(bi);
    s+='<line x1="'+xb.toFixed(1)+'" y1="'+T+'" x2="'+xb.toFixed(1)+'" y2="'+(T+PH)+'" stroke="'+RED+'" stroke-dasharray="3 3"/>'+
    '<text x="'+(xb+4).toFixed(1)+'" y="'+(T+12)+'" fill="'+RED+'" font-size="10.5" font-family="Arial">'+BASE+' = 1000</text>';}
  s+='<polyline points="'+pts+'" fill="none" stroke="'+NAVY+'" stroke-width="1.9"/>';
  s+='<circle cx="'+X(data.length-1).toFixed(1)+'" cy="'+Y(data[data.length-1][1]).toFixed(1)+'" r="3.2" fill="'+NAVY+'"/>';
  s+=xlabels(data);
  document.getElementById("idxchart").innerHTML='<svg viewBox="0 0 '+W+' '+H+'" class="chart">'+s+'</svg>';
}
document.querySelectorAll(".pill").forEach(b=>b.addEventListener("click",e=>{
  document.querySelectorAll(".pill").forEach(x=>x.classList.remove("on"));
  e.target.classList.add("on"); drawIndex(e.target.dataset.start);
}));
drawIndex("__BASE__");

function drawCo(t){
  const raw=(PRICES[t]||[]).filter(r=>r[1]!=null);
  if(!raw.length) return;
  const base=raw[0][1];
  const comp=raw.map(r=>[r[0], r[1]/base*1000]);
  const idx=IDXFULL.filter(r=>r[0]>=BASE);
  const vals=comp.map(r=>r[1]).concat(idx.map(r=>r[1]));
  let lo=Math.min.apply(null,vals),hi=Math.max.apply(null,vals);
  lo=Math.floor(lo/50)*50; hi=Math.ceil(hi/50)*50; const span=(hi-lo)||1;
  const X=(i,n)=>L+PW*i/(n-1), Y=v=>T+PH*(1-(v-lo)/span);
  const poly=a=>a.map((r,i)=>X(i,a.length).toFixed(1)+','+Y(r[1]).toFixed(1)).join(' ');
  let s=frame(lo,span);
  if(lo<=1000&&hi>=1000){const yb=Y(1000);
    s+='<line x1="'+L+'" y1="'+yb.toFixed(1)+'" x2="'+(W-R)+'" y2="'+yb.toFixed(1)+'" stroke="'+RED+'" stroke-dasharray="3 3"/>';}
  s+='<polyline points="'+poly(idx)+'" fill="none" stroke="'+STEEL+'" stroke-width="1.5" stroke-dasharray="5 4"/>';
  s+='<polyline points="'+poly(comp)+'" fill="none" stroke="'+NAVY+'" stroke-width="2.1"/>';
  s+=xlabels(idx);
  document.getElementById("cochart").innerHTML='<svg viewBox="0 0 '+W+' '+H+'" class="chart">'+s+'</svg>';
  const last=comp[comp.length-1][1], pct=(last/1000-1)*100;
  document.getElementById("cocap").innerHTML=
    "<b>"+NAMES[t]+"</b> rebased 1,000 &rarr; "+last.toFixed(1)+
    " ("+(pct>=0?"+":"")+pct.toFixed(1)+"% since base). Grey dashed = the VGR 50 index; red line = 1,000.";
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
