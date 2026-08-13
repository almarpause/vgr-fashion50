#!/usr/bin/env python3
"""Render a self-contained HTML dashboard + JSON feed from the workbook.

Top of the page: the index % growth over Yesterday / L7D / L30D / MTD / YTD
(colour-coded). Below: the index chart, the full constituents table (segment,
price, yesterday & L7D price change, YTD, full market cap, float, index weight),
and an interactive per-company evolution selector rebased to 1000. Everything is
inline (no CDN); ``fashion50_data.json`` carries the same data for the website.

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


def _pct(x, nd=2):
    if x is None:
        return "—"
    return f"{x:+.{nd}f}%"


def _sign(v):
    if v is None:
        return "flat"
    return "up" if v >= 0 else "down"


def _simplify_segment(seg):
    """Collapse to the primary bucket — all Luxury sub-segments (jewellery /
    watches / eyewear) become 'Luxury', and likewise Sportswear/Footwear ->
    Sportswear, Fashion-Ecommerce -> Fashion, etc. (text before the first - or /)."""
    import re
    s = (seg or "").strip()
    if not s:
        return s
    return re.split(r"[-/]", s)[0].strip() or s


def _changes(series) -> dict:
    """Percentage changes from a [(date_iso, value)] series (sorted ascending).

    Returns latest + Yesterday / L7D / L30D / MTD / YTD, anchored on the latest
    date in the series (not today), so it's correct even if the data lags a day.
    """
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


def _metric_tile(label, pct):
    return (f'<div class="kpi"><div class="k-label">{label}</div>'
            f'<div class="k-val {_sign(pct)}">{_pct(pct)}</div></div>')


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
    """Shared computation for both the HTML and the JSON feed."""
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
            "ir": ir_of.get(t, ""), "segment": _simplify_segment(seg_of.get(t)),
            "price": ch["latest"], "ccy": ccy_of.get(t, ""),
            "yday": ch["yesterday"], "l7d": ch["l7d"], "ytd": ch["ytd"],
            "fullcap": caps[t] / ff / 1e9, "float": ff * 100,
            "raw": caps[t] / total_cap * 100, "wt": weights[t] * 100,
        })
    idx_metrics = _changes([(d, lv) for d, lv in series])
    return series, recs, idx_metrics, name_of, ranked


def build_html() -> str:
    weekly, audit, consts, unsched, prices = _read_workbook()
    series, recs, m, name_of, ranked = _prepare(weekly, audit, consts, prices)
    latest = m["latest"] or 0.0

    sel_prices = {t: [[d, p] for d, p in prices.get(t, [])] for t in ranked
                  if prices.get(t)}
    sel_names = {t: name_of.get(t, t) for t in sel_prices}
    idx_series = [[d, lv] for d, lv in series]
    sel_order = sorted((t for t in ranked if t in sel_prices),
                       key=lambda t: (name_of.get(t, t) or "").lower())
    default_t = next((t for t in ranked if t in sel_prices), None)
    options = "".join(
        f'<option value="{t}"{" selected" if t == default_t else ""}>'
        f'{html.escape(str(name_of.get(t, t)))}</option>' for t in sel_order)

    kpis = (
        f'<div class="kpi hero"><div class="k-label">Index level</div>'
        f'<div class="k-val">{_fmt(latest)}</div>'
        f'<div class="k-sub">base {series[0][0] if series else "—"} = 1000 · '
        f'as of {series[-1][0] if series else "—"}</div></div>'
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
.kpi.hero {{ grid-column:span 1; }}
.k-label {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
.k-val {{ font-size:24px; font-weight:650; margin-top:4px; letter-spacing:-.02em; }}
.k-sub {{ font-size:11px; color:var(--muted); margin-top:2px; }}
.up {{ color:var(--up); }} .down {{ color:var(--down); }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:18px 20px; margin:16px 0; }}
.card h2 {{ margin:0 0 12px; font-size:15px; font-weight:600; }}
.chart {{ width:100%; height:auto; }}
.grid {{ stroke:var(--line); stroke-width:1; }}
.baseline {{ stroke:var(--muted); stroke-width:1; stroke-dasharray:4 4; opacity:.7; }}
.axis {{ fill:var(--muted); font-size:11px; }}
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
  <div class="sub">Market-cap weighted · float-adjusted · USD — daily, base {series[0][0] if series else ''} = 1000</div>
</header>
{'<div class="kpis">' + kpis + '</div>'}
<div class="card"><h2>Index level (base 1000)</h2>{_line_chart(series)}</div>
<div class="card"><h2>All {len(recs)} constituents</h2>
{_constituents_table(recs)}
<div class="legend"><b>Index wt</b> is the float-adjusted market-cap share.
<b>Mkt cap</b> is full size (shares × price); <b>Float</b> the tradable share —
e.g. Zara's larger cap but low float is why Uniqlo's index weight is higher.
Price · Yday · L7D · YTD are the local share price.</div>
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
    "<b style='color:var(--accent)'>"+NAMES[t]+"</b>: rebased 1000 -> "+last.toFixed(1)+
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
    weekly, audit, consts, unsched, prices = _read_workbook()
    series, recs, m, name_of, ranked = _prepare(weekly, audit, consts, prices)
    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds"),
        "base_date": series[0][0] if series else None,
        "base_level": 1000.0,
        "latest_date": series[-1][0] if series else None,
        "latest_level": m["latest"],
        "index_change": {"yesterday": _r(m["yesterday"]), "l7d": _r(m["l7d"]),
                         "l30d": _r(m["l30d"]), "mtd": _r(m["mtd"]),
                         "ytd": _r(m["ytd"])},
        "n_constituents": len(recs),
        "index": [[d, lv] for d, lv in series],
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


def _r(x, nd=2):
    return round(x, nd) if isinstance(x, (int, float)) else None


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
