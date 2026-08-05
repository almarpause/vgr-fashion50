#!/usr/bin/env python3
"""Render a self-contained HTML dashboard from the workbook.

Reads ``Fashion50_Index.xlsx`` and writes ``Fashion50_Dashboard.html`` — the
index line chart, KPI tiles, segment mix, a full constituents table (latest
price, YTD %, MoM %, full market cap, float %, index weight) and an interactive
per-company evolution selector (each stock rebased to 1000 at the same base as
the index).  Everything inline (no CDN, no external assets).

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
from engine.config import DEFAULT_SETTINGS, load_constituents  # noqa: E402

DASHBOARD_PATH = os.path.join(config.PROJECT_DIR, "Fashion50_Dashboard.html")
DATA_JSON_PATH = os.path.join(config.PROJECT_DIR, "fashion50_data.json")


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

    # Prices sheet -> {ticker: [(date_iso, price), ...]}
    prices = {}
    if "Prices" in wb.sheetnames:
        ws = wb["Prices"]
        hdr = [c.value for c in ws[1]]
        tickers = hdr[1:]
        for t in tickers:
            prices[t] = []
        for r in ws.iter_rows(min_row=2, values_only=True):
            d = r[0]
            if d is None:
                continue
            for j, t in enumerate(tickers, start=1):
                v = r[j] if j < len(r) else None
                if v is not None:
                    prices[t].append((str(d), float(v)))
    return (rows("Weekly"), rows("Audit"), rows("Constituents"),
            rows("Unscheduled"), prices)


def _fmt(x, nd=2):
    try:
        return f"{float(x):,.{nd}f}"
    except (TypeError, ValueError):
        return "—"


def _pct(x, nd=1):
    if x is None:
        return "—"
    return f"{x:+.{nd}f}%"


def _sign(v):
    if v is None:
        return "flat"
    return "up" if v >= 0 else "down"


def _series_stats(series):
    """(latest, ytd_%, mom_%) from a list of (date_iso, price)."""
    s = [(d, p) for d, p in series if p is not None]
    if not s:
        return None, None, None
    latest = s[-1][1]
    y2026 = [p for d, p in s if d >= "2026-01-01"]
    ytd = (latest / y2026[0] - 1) * 100 if y2026 and y2026[0] else None
    mom = (latest / s[-5][1] - 1) * 100 if len(s) >= 5 and s[-5][1] else None
    return latest, ytd, mom


def _line_chart(series, width=1040, height=320, pad=48):
    if len(series) < 2:
        return "<p>Not enough data to chart.</p>"
    levels = [lv for _, lv in series]
    lo, hi = min(levels), max(levels)
    span = (hi - lo) or 1.0
    pw, ph = width - 2 * pad, height - 2 * pad
    n = len(series)

    def x(i): return pad + pw * i / (n - 1)
    def y(v): return pad + ph * (1 - (v - lo) / span)

    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, (_, v) in enumerate(series))
    area = f"{pad:.1f},{pad+ph:.1f} " + pts + f" {pad+pw:.1f},{pad+ph:.1f}"
    base_line = ""
    if lo <= 1000 <= hi:
        yb = y(1000)
        base_line = (f'<line x1="{pad}" y1="{yb:.1f}" x2="{pad+pw}" y2="{yb:.1f}" '
                     f'class="baseline"/><text x="{pad+4}" y="{yb-5:.1f}" '
                     f'class="axis">1000 (base)</text>')
    grid = []
    for f in (0, 0.25, 0.5, 0.75, 1.0):
        val = lo + span * f
        yy = y(val)
        grid.append(f'<line x1="{pad}" y1="{yy:.1f}" x2="{pad+pw}" y2="{yy:.1f}" '
                    f'class="grid"/><text x="{pad-8}" y="{yy+4:.1f}" '
                    f'text-anchor="end" class="axis">{val:,.0f}</text>')
    xlab = []
    for f in (0, 0.25, 0.5, 0.75, 1.0):
        i = int(round((n - 1) * f))
        xlab.append(f'<text x="{x(i):.1f}" y="{pad+ph+22:.1f}" '
                    f'text-anchor="middle" class="axis">{series[i][0]}</text>')
    lx, ly = x(n - 1), y(series[-1][1])
    return f"""<svg viewBox="0 0 {width} {height}" class="chart">
  <defs><linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="var(--accent)" stop-opacity="0.28"/>
    <stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/>
  </linearGradient></defs>
  {''.join(grid)}{base_line}
  <polygon points="{area}" fill="url(#fill)"/>
  <polyline points="{pts}" fill="none" stroke="var(--accent)" stroke-width="2"/>
  <circle cx="{lx:.1f}" cy="{ly:.1f}" r="4" fill="var(--accent)"/>
  {''.join(xlab)}
</svg>"""


def _bars(items, unit="%"):
    if not items:
        return "<p>—</p>"
    mx = max(v for _, v in items) or 1.0
    out = []
    for label, v in items:
        w = max(2.0, 100.0 * v / mx)
        out.append(
            f'<div class="bar-row"><span class="bar-label">{html.escape(str(label))}</span>'
            f'<span class="bar-track"><span class="bar-fill" style="width:{w:.1f}%"></span></span>'
            f'<span class="bar-val">{v:.2f}{unit}</span></div>')
    return "".join(out)


def _constituents_table(recs, cap_pct):
    """recs: list of dicts sorted by capped weight desc."""
    mx = max((r["wt"] for r in recs), default=1.0) or 1.0
    body = []
    for r in recs:
        bw = max(2.0, 100.0 * r["wt"] / mx)
        capped = abs(r["wt"] - cap_pct) < 1e-3
        raw_sub = (f"<div class='sub'>raw {r['raw']:.1f}%</div>" if capped else "")
        tag = " <span class='tag'>cap</span>" if capped else ""
        nm = html.escape(str(r["name"]))
        if r.get("ir"):
            nm = (f'<a href="{html.escape(r["ir"])}" target="_blank" '
                  f'rel="noopener noreferrer">{nm}</a>')
        body.append(
            f"<tr><td class='r'>{r['rank']}</td>"
            f"<td>{nm}{tag}"
            f"<div class='sub'>{html.escape(str(r['ticker']))}</div></td>"
            f"<td class='num'>{_fmt(r['price'])}<div class='sub'>{r['ccy']}</div></td>"
            f"<td class='num {_sign(r['ytd'])}'>{_pct(r['ytd'])}</td>"
            f"<td class='num {_sign(r['mom'])}'>{_pct(r['mom'])}</td>"
            f"<td class='num'>{_fmt(r['fullcap'], 1)}</td>"
            f"<td class='num'>{r['float']:.0f}%</td>"
            f"<td class='barcell'><span class='tbar'><span class='tfill' "
            f"style='width:{bw:.1f}%'></span></span></td>"
            f"<td class='num'>{r['wt']:.2f}%{raw_sub}</td></tr>")
    return (
        "<table class='wt'><thead><tr>"
        "<th class='r'>#</th><th>Brand</th><th class='num'>Price</th>"
        "<th class='num'>YTD</th><th class='num'>MoM</th>"
        "<th class='num'>Mkt cap $bn</th><th class='num'>Float</th>"
        "<th>Weight</th><th class='num'>Index wt</th>"
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table>")


def build_html() -> str:
    weekly, audit, consts, unsched, prices = _read_workbook()
    settings = DEFAULT_SETTINGS
    cap_on = settings.effective_cap is not None
    cap_pct = settings.effective_cap * 100 if cap_on else 999
    weights_label = (f"post-{int(settings.effective_cap*100)}%-cap"
                     if cap_on else "float-adjusted, uncapped")

    series = [(str(r["run_date"]), float(r["index_level"]))
              for r in weekly if r.get("index_level") is not None]
    latest = series[-1][1] if series else 0.0
    since = (latest / 1000.0 - 1.0) * 100.0 if series else 0.0
    ret_1y = None
    if len(series) > 52:
        past = series[-53][1]
        ret_1y = (latest / past - 1.0) * 100.0 if past else None
    last_row = weekly[-1] if weekly else {}
    wow = last_row.get("weekly_return_%")
    n_ok = last_row.get("n_ok") or 0
    n_missing = last_row.get("n_missing") or 0

    seg_of = {c["yahoo_ticker"]: c.get("segment", "?") for c in consts}
    name_of = {c["yahoo_ticker"]: c.get("name") for c in consts}
    ir_of = {c.yahoo_ticker: c.ir_url for c in load_constituents()}
    caps = {r["ticker"]: float(r["cap_usd"]) for r in audit
            if r.get("cap_usd") and r.get("status") == "OK"}
    total_cap = sum(caps.values()) or 1.0
    weights = indexmath.compute_weights(caps, cap=settings.effective_cap)
    ff_of = {r["ticker"]: (r.get("float_factor") or 1.0) for r in audit}
    ccy_of = {r["ticker"]: r.get("currency") for r in audit}

    ranked = sorted(weights, key=weights.get, reverse=True)
    recs = []
    for i, t in enumerate(ranked, 1):
        latest_p, ytd, mom = _series_stats(prices.get(t, []))
        ff = ff_of.get(t, 1.0) or 1.0
        recs.append({
            "rank": i, "ticker": t, "name": name_of.get(t, t),
            "ir": ir_of.get(t, ""),
            "price": latest_p, "ccy": ccy_of.get(t, ""),
            "ytd": ytd, "mom": mom,
            "fullcap": caps[t] / ff / 1e9,        # full mkt cap $bn (no float)
            "float": ff * 100,
            "raw": caps[t] / total_cap * 100,     # uncapped weight
            "wt": weights[t] * 100,               # capped index weight
        })

    top10 = [(name_of.get(t, t), weights[t] * 100) for t in ranked[:10]]
    seg_w = {}
    for t, w in weights.items():
        seg_w[seg_of.get(t, "?")] = seg_w.get(seg_of.get(t, "?"), 0.0) + w * 100
    sectors = sorted(seg_w.items(), key=lambda kv: -kv[1])

    # Selector data: rebase each stock to 1000 at its first available point.
    sel_prices = {t: [[d, p] for d, p in prices.get(t, [])] for t in ranked
                  if prices.get(t)}
    sel_names = {t: name_of.get(t, t) for t in sel_prices}
    idx_series = [[d, lv] for d, lv in series]
    # Selector list sorted alphabetically by company name; default the drawn
    # company to the largest constituent.
    sel_order = sorted((t for t in ranked if t in sel_prices),
                       key=lambda t: (name_of.get(t, t) or "").lower())
    default_t = next((t for t in ranked if t in sel_prices), None)
    options = "".join(
        f'<option value="{t}"{" selected" if t == default_t else ""}>'
        f'{html.escape(str(name_of.get(t, t)))}</option>'
        for t in sel_order)

    kpi = f"""
    <div class="kpis">
      <div class="kpi"><div class="k-label">Latest level</div>
        <div class="k-val">{_fmt(latest)}</div>
        <div class="k-sub {_sign(wow)}">{('WoW '+_fmt(wow)+'%') if wow is not None else 'base date'}</div></div>
      <div class="kpi"><div class="k-label">Since inception</div>
        <div class="k-val {_sign(since)}">{since:+.1f}%</div>
        <div class="k-sub">base {series[0][0] if series else '—'} = 1000</div></div>
      <div class="kpi"><div class="k-label">1-year return</div>
        <div class="k-val {_sign(ret_1y)}">{(f'{ret_1y:+.1f}%') if ret_1y is not None else '—'}</div>
        <div class="k-sub">52 weeks</div></div>
      <div class="kpi"><div class="k-label">Constituents</div>
        <div class="k-val">{n_ok}</div>
        <div class="k-sub {'down' if n_missing else ''}">{n_missing} excluded/missing</div></div>
      <div class="kpi"><div class="k-label">History</div>
        <div class="k-val">{len(series)}</div>
        <div class="k-sub">weekly points</div></div>
    </div>"""

    head = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VGR Fashion 50 — Dashboard</title>
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
.kpis {{ display:grid; grid-template-columns:repeat(5,1fr); gap:12px; margin:22px 0; }}
.kpi {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }}
.k-label {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
.k-val {{ font-size:26px; font-weight:650; margin-top:4px; letter-spacing:-.02em; }}
.k-sub {{ font-size:12px; color:var(--muted); margin-top:2px; }}
.up {{ color:var(--up); }} .down {{ color:var(--down); }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:18px 20px; margin:16px 0; }}
.card h2 {{ margin:0 0 12px; font-size:15px; font-weight:600; }}
.chart {{ width:100%; height:auto; }}
.grid {{ stroke:var(--line); stroke-width:1; }}
.baseline {{ stroke:var(--muted); stroke-width:1; stroke-dasharray:4 4; opacity:.7; }}
.axis {{ fill:var(--muted); font-size:11px; }}
.cols {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
.bar-row {{ display:grid; grid-template-columns:150px 1fr 62px; align-items:center; gap:10px; margin:7px 0; }}
.bar-label {{ font-size:13px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.bar-track {{ background:var(--line); border-radius:6px; height:12px; overflow:hidden; }}
.bar-fill {{ display:block; height:100%; background:var(--accent); border-radius:6px; }}
.bar-val {{ font-size:12px; color:var(--muted); text-align:right; font-variant-numeric:tabular-nums; }}
ul.adds {{ margin:4px 0 0; padding-left:18px; color:var(--muted); font-size:13px; }}
table.wt {{ width:100%; border-collapse:collapse; font-size:13px; }}
table.wt th {{ text-align:left; color:var(--muted); font-weight:600; font-size:11px;
  text-transform:uppercase; letter-spacing:.03em; padding:6px 8px; border-bottom:1px solid var(--line); }}
table.wt td {{ padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
table.wt td.r {{ color:var(--muted); width:30px; text-align:right; font-variant-numeric:tabular-nums; }}
table.wt th.num, table.wt td.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
.sub {{ color:var(--muted); font-size:11px; }}
td.barcell {{ width:16%; }}
.tbar {{ display:block; background:var(--line); border-radius:5px; height:9px; overflow:hidden; }}
.tfill {{ display:block; height:100%; background:var(--accent); border-radius:5px; }}
.tag {{ font-size:10px; background:var(--line); color:var(--muted); border-radius:4px; padding:1px 5px; }}
table.wt a {{ color:var(--accent); text-decoration:none; }}
table.wt a:hover {{ text-decoration:underline; }}
.legend {{ color:var(--muted); font-size:12px; margin-top:10px; }}
select {{ background:var(--card); color:var(--ink); border:1px solid var(--line);
  border-radius:8px; padding:7px 10px; font-size:14px; max-width:320px; }}
.cocap {{ color:var(--muted); font-size:13px; margin-top:6px; }}
footer {{ color:var(--muted); font-size:12px; margin-top:22px; }}
@media (max-width:820px) {{ .kpis {{ grid-template-columns:repeat(2,1fr); }}
  .cols {{ grid-template-columns:1fr; }} td.barcell {{ display:none; }} }}
</style></head>
<body><div class="wrap">
<header>
  <h1>VGR Fashion 50</h1>
  <div class="sub">Market-cap weighted · divisor-based · float-adjusted · USD — {series[0][0] if series else ''} → {series[-1][0] if series else ''}</div>
</header>
{kpi}
<div class="card"><h2>Index level (base 1000)</h2>{_line_chart(series)}</div>
<div class="cols">
  <div class="card"><h2>Top 10 weights ({weights_label})</h2>{_bars(top10)}</div>
  <div class="card"><h2>Segment mix</h2>{_bars(sectors)}</div>
</div>
<div class="card"><h2>All {len(recs)} constituents</h2>
{_constituents_table(recs, cap_pct)}
<div class="legend"><b>Index wt</b> is float-adjusted (S&P-style){', then capped at ' + str(int(cap_pct)) + '% (cap names show raw weight beneath)' if cap_on else ' — no cap applied'}.
<b>Mkt cap</b> is full size (shares × price), <b>Float</b> the tradable share — e.g. Zara's larger
cap but low float is why Uniqlo's index weight is higher. Price/YTD/MoM are the local share price.</div>
</div>
<div class="card"><h2>Company evolution — rebased to 1000 at the base date</h2>
  <select id="co">{options}</select>
  <div id="cochart"></div>
  <div class="cocap" id="cocap"></div>
</div>
<footer>Generated from Fashion50_Index.xlsx · shares/float held at current snapshot (documented approximation) · not investment advice.</footer>
</div>"""

    script = """
<script>
const IDX = __IDX__;
const PRICES = __PRICES__;
const NAMES = __NAMES__;
function draw(t){
  const raw = (PRICES[t]||[]).filter(r=>r[1]!=null);
  if(!raw.length){return;}
  const base = raw[0][1];
  const comp = raw.map(r=>[r[0], r[1]/base*1000]);
  const W=1040,H=320,pad=48;
  const vals = comp.map(r=>r[1]).concat(IDX.map(r=>r[1]));
  let lo=Math.min.apply(null,vals), hi=Math.max.apply(null,vals);
  const span=(hi-lo)||1;
  const Y=v=>pad+(H-2*pad)*(1-(v-lo)/span);
  const poly=a=>a.map((r,i)=>((pad+(W-2*pad)*i/(a.length-1)).toFixed(1))+","+Y(r[1]).toFixed(1)).join(" ");
  let grid="";
  for(const f of [0,.25,.5,.75,1]){const val=lo+span*f,yy=Y(val);
    grid+='<line x1="'+pad+'" y1="'+yy.toFixed(1)+'" x2="'+(W-pad)+'" y2="'+yy.toFixed(1)+'" class="grid"/>'+
    '<text x="'+(pad-8)+'" y="'+(yy+4).toFixed(1)+'" text-anchor="end" class="axis">'+val.toFixed(0)+'</text>';}
  let xlab="";
  for(const f of [0,.25,.5,.75,1]){const i=Math.round((IDX.length-1)*f);
    const x=pad+(W-2*pad)*i/(IDX.length-1);
    xlab+='<text x="'+x.toFixed(1)+'" y="'+(H-pad+22)+'" text-anchor="middle" class="axis">'+IDX[i][0]+'</text>';}
  let base1000="";
  if(lo<=1000&&1000<=hi){const yb=Y(1000);
    base1000='<line x1="'+pad+'" y1="'+yb.toFixed(1)+'" x2="'+(W-pad)+'" y2="'+yb.toFixed(1)+'" class="baseline"/>';}
  const svg='<svg viewBox="0 0 '+W+' '+H+'" class="chart">'+grid+base1000+
    '<polyline points="'+poly(IDX)+'" fill="none" stroke="var(--muted)" stroke-width="1.6" stroke-dasharray="5 4"/>'+
    '<polyline points="'+poly(comp)+'" fill="none" stroke="var(--accent)" stroke-width="2.3"/>'+
    xlab+'</svg>';
  document.getElementById("cochart").innerHTML=svg;
  const last=comp[comp.length-1][1], pct=(last/1000-1)*100;
  document.getElementById("cocap").innerHTML=
    "<b style='color:var(--accent)'>"+NAMES[t]+"</b>: rebased 1000 → "+last.toFixed(1)+
    " ("+(pct>=0?"+":"")+pct.toFixed(1)+"% since base). Dashed grey = the index.";
}
const sel=document.getElementById("co");
sel.addEventListener("change",e=>draw(e.target.value));
draw(sel.value);
</script>
</body></html>"""
    script = (script
              .replace("__IDX__", json.dumps(idx_series))
              .replace("__PRICES__", json.dumps(sel_prices))
              .replace("__NAMES__", json.dumps(sel_names)))
    return head + script


def build_data() -> dict:
    """Machine-readable feed for the website (VGR Intelligence / any front-end).

    Everything the page needs to render natively: index history, the 50
    constituents (price, YTD, MoM, full cap, float, weight, IR link), each
    company's weekly series for the selector, segment mix, and the watchlist.
    """
    weekly, audit, consts, unsched, prices = _read_workbook()
    settings = DEFAULT_SETTINGS
    cap_on = settings.effective_cap is not None

    series = [(str(r["run_date"]), round(float(r["index_level"]), 4))
              for r in weekly if r.get("index_level") is not None]
    latest = series[-1][1] if series else 0.0
    since = (latest / 1000.0 - 1.0) * 100.0 if series else 0.0

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

    constituents = []
    for i, t in enumerate(ranked, 1):
        p, ytd, mom = _series_stats(prices.get(t, []))
        ff = ff_of.get(t, 1.0) or 1.0
        constituents.append({
            "rank": i, "ticker": t, "name": name_of.get(t, t),
            "ir_url": ir_of.get(t, ""),
            "price": round(p, 4) if p is not None else None,
            "currency": ccy_of.get(t, ""),
            "ytd_pct": round(ytd, 2) if ytd is not None else None,
            "mom_pct": round(mom, 2) if mom is not None else None,
            "full_cap_usd_bn": round(caps[t] / ff / 1e9, 3),
            "float_pct": round(ff * 100, 1),
            "raw_weight_pct": round(caps[t] / total_cap * 100, 4),
            "weight_pct": round(weights[t] * 100, 4),
        })

    seg_w: dict[str, float] = {}
    for t, w in weights.items():
        seg_w[seg_of.get(t, "?")] = seg_w.get(seg_of.get(t, "?"), 0.0) + w * 100

    # Watchlist (early-warning) — read the sheet if present.
    watch = []
    try:
        from openpyxl import load_workbook
        wb = load_workbook(config.WORKBOOK_PATH, data_only=True)
        if "Watchlist" in wb.sheetnames:
            ws = wb["Watchlist"]
            hdr = [c.value for c in ws[1]]
            for r in ws.iter_rows(min_row=2, values_only=True):
                if not r or r[0] is None:
                    continue
                row = {hdr[j]: r[j] for j in range(min(len(hdr), len(r)))}
                if str(row.get("severity")) in ("HIGH", "MEDIUM"):
                    watch.append({k: (v if v is not None else None)
                                  for k, v in row.items()})
    except Exception:
        pass

    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds"),
        "base_date": series[0][0] if series else None,
        "base_level": 1000.0,
        "latest_date": series[-1][0] if series else None,
        "latest_level": latest,
        "since_inception_pct": round(since, 2),
        "weight_cap_enabled": cap_on,
        "weight_cap_pct": settings.effective_cap * 100 if cap_on else None,
        "n_constituents": len(constituents),
        "index": [[d, lv] for d, lv in series],
        "constituents": constituents,
        "prices": {t: [[d, round(p, 4)] for d, p in prices.get(t, [])]
                   for t in ranked if prices.get(t)},
        "segments": sorted(([s, round(w, 3)] for s, w in seg_w.items()),
                           key=lambda kv: -kv[1]),
        "watchlist": watch,
    }


def main(argv: list[str]) -> int:
    if not os.path.exists(config.WORKBOOK_PATH):
        print("No workbook found — run backfill.py or run_weekly.py first.")
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
