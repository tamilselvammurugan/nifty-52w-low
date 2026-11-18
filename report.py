"""
Client screening report: Quality score (fundamentals + technicals) and
returns (day / week / month / overall) for stocks touching support and
stocks near their 52-week low.

Labels are "Strong setup / Watch / Weak" - a screening aid, NOT buy/sell advice.
"""

import math
import datetime as dt
from pathlib import Path

import pandas as pd
import yfinance as yf

DISCLAIMER = ("This is an automated screening report for information and education only. "
              "It is not investment advice or a recommendation to buy or sell any security. "
              "Scores are rule-based and do not predict future prices. Past performance does not "
              "guarantee future results. Please do your own research or consult a SEBI-registered "
              "adviser before investing.")


# ---------------- returns ----------------
def pct(a, b):
    try:
        return (a - b) / b
    except ZeroDivisionError:
        return float("nan")


def returns_block(d: pd.DataFrame) -> dict:
    c = d["Close"]
    last = float(c.iloc[-1])

    def back(n):
        return float(c.iloc[-1 - n]) if len(c) > n else float("nan")

    out = {
        "1D": pct(last, back(1)),
        "1W": pct(last, back(5)),
        "1M": pct(last, back(21)),
        "3M": pct(last, back(63)),
        "6M": pct(last, back(126)),
        "1Y": pct(last, float(c.iloc[0])),
        "From 52W high": pct(last, float(d["High"].max())),
    }
    # last 5 sessions day-wise
    days = c.pct_change().tail(5)
    out["days"] = [(i.strftime("%d %b"), float(v)) for i, v in days.items()]
    # last 4 weeks week-wise (Fri close to Fri close)
    wk = c.resample("W-FRI").last().pct_change().dropna().tail(4)
    out["weeks"] = [("Wk " + i.strftime("%d %b"), float(v)) for i, v in wk.items()]
    # last 6 months month-wise
    mo = c.resample("ME").last().pct_change().dropna().tail(6)
    out["months"] = [(i.strftime("%b"), float(v)) for i, v in mo.items()]
    return out


# ---------------- technicals ----------------
def rsi(close: pd.Series, n=14) -> float:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn
    return float(100 - 100 / (1 + rs.iloc[-1]))


def tech_score(d: pd.DataFrame, sup_row) -> tuple[int, dict]:
    pts, why = 0, {}
    status = sup_row.get("Status") if sup_row is not None else None
    if status == "Holding":
        pts += 15; why["Support"] = "Held (+15)"
    elif status == "Broke today":
        why["Support"] = "Broke (0)"
    else:
        why["Support"] = "Not at support (0)"

    held = sup_row.get("Times held") if sup_row is not None else None
    if isinstance(held, (int, float)) and not (isinstance(held, float) and math.isnan(held)):
        h = int(held)
        p = 10 if h >= 4 else 7 if h == 3 else 4 if h == 2 else 0
        pts += p; why["Level strength"] = f"Held {h}x (+{p})"
    elif sup_row is not None and sup_row.get("Type") == "200-day avg":
        pts += 5; why["Level strength"] = "200-day avg (+5)"

    r = rsi(d["Close"])
    p = 10 if 30 <= r <= 45 else 7 if r < 30 else 5 if r <= 55 else 0
    pts += p; why["RSI"] = f"{r:.0f} (+{p})"

    vol = float(d["Volume"].iloc[-1]); avg = float(d["Volume"].tail(21).iloc[:-1].mean() or 1)
    green = float(d["Close"].iloc[-1]) > float(d["Open"].iloc[-1])
    ratio = vol / avg if avg else 0
    p = 10 if green and ratio >= 1.2 else 5 if green and ratio >= 1.0 else 0
    pts += p; why["Volume"] = f"{ratio:.1f}x avg, {'green' if green else 'red'} day (+{p})"

    sma200 = float(d["Close"].tail(200).mean())
    above = float(d["Close"].iloc[-1]) > sma200
    p = 5 if above else 0
    pts += p; why["Trend"] = f"{'Above' if above else 'Below'} 200-day avg (+{p})"
    return pts, why


# ---------------- fundamentals ----------------
def fund_score(symbol: str) -> tuple[int, dict]:
    try:
        info = yf.Ticker(f"{symbol}.NS").info or {}
    except Exception:
        info = {}
    pts, why = 0, {}

    def g(k):
        v = info.get(k)
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)

    roe = g("returnOnEquity")
    if roe is not None:
        p = 15 if roe >= .18 else 10 if roe >= .12 else 5 if roe >= .08 else 0
        pts += p; why["ROE"] = f"{roe*100:.1f}% (+{p})"
    else:
        why["ROE"] = "n/a"

    de = g("debtToEquity")  # Yahoo gives this in %, e.g. 45 = 0.45x
    sector = (info.get("sector") or "")
    if sector == "Financial Services":
        pts += 5; why["Debt/Equity"] = "Financial co. - not scored (+5)"
    elif de is not None:
        p = 10 if de <= 50 else 6 if de <= 100 else 3 if de <= 200 else 0
        pts += p; why["Debt/Equity"] = f"{de/100:.2f}x (+{p})"
    else:
        why["Debt/Equity"] = "n/a"

    eg = g("earningsGrowth")
    if eg is not None:
        p = 10 if eg >= .15 else 6 if eg >= .05 else 3 if eg > 0 else 0
        pts += p; why["Profit growth (YoY)"] = f"{eg*100:.1f}% (+{p})"
    else:
        why["Profit growth (YoY)"] = "n/a"

    rg = g("revenueGrowth")
    if rg is not None:
        p = 8 if rg >= .12 else 5 if rg >= .05 else 2 if rg > 0 else 0
        pts += p; why["Sales growth (YoY)"] = f"{rg*100:.1f}% (+{p})"
    else:
        why["Sales growth (YoY)"] = "n/a"

    pm = g("profitMargins")
    if pm is not None:
        p = 7 if pm >= .15 else 4 if pm >= .08 else 2 if pm > 0 else 0
        pts += p; why["Profit margin"] = f"{pm*100:.1f}% (+{p})"
    else:
        why["Profit margin"] = "n/a"

    pe = g("trailingPE")
    why["PE"] = f"{pe:.1f}" if pe else "n/a"
    return pts, why


def label(score):
    if score >= 70:
        return "Strong setup"
    if score >= 50:
        return "Watch"
    return "Weak"


# ---------------- build ----------------
def build(data, cons: pd.DataFrame, near_low: pd.DataFrame, sup: pd.DataFrame) -> pd.DataFrame:
    syms = list(dict.fromkeys(list(sup["Symbol"]) + list(near_low["Symbol"])))
    sup_map = {row["Symbol"]: row for row in sup.to_dict("records")} if len(sup) else {}
    names = dict(zip(cons["Symbol"], cons["Company"]))
    seg = dict(zip(cons["Symbol"], cons["Segment"]))
    near = set(near_low["Symbol"])

    rows = []
    for s in syms:
        try:
            d = data[f"{s}.NS"].dropna(subset=["Close"])
        except KeyError:
            continue
        if len(d) < 60:
            continue
        srow = sup_map.get(s)
        t, twhy = tech_score(d, srow)
        f, fwhy = fund_score(s)
        ret = returns_block(d)
        score = t + f
        rows.append({
            "Symbol": s, "Company": names.get(s, s), "Segment": seg.get(s, ""),
            "LTP": round(float(d["Close"].iloc[-1]), 2),
            "Score": score, "Fundamental": f, "Technical": t, "Label": label(score),
            "Support": srow.get("Support") if srow else None,
            "Support status": srow.get("Status") if srow else "",
            "Near 52W low": s in near,
            **{k: ret[k] for k in ["1D", "1W", "1M", "3M", "6M", "1Y", "From 52W high"]},
            "_days": ret["days"], "_weeks": ret["weeks"], "_months": ret["months"],
            "_fwhy": fwhy, "_twhy": twhy,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("Score", ascending=False).reset_index(drop=True)


# ---------------- HTML ----------------
def _pc(v, bold_big=True):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return '<td class="n">–</td>'
    if v <= -0.05:
        bg, fg, b = "#f5b5b5", "#7f1010", "font-weight:700"
    elif v < 0:
        bg, fg, b = "#fde2e1", "#9b1c1c", ""
    elif v > 0:
        bg, fg, b = "#dcf5e3", "#126b34", ""
    else:
        bg, fg, b = "transparent", "inherit", ""
    return f'<td class="n" style="background:{bg};color:{fg};{b}">{v*100:+.1f}%</td>'


def _chip(lbl):
    c = {"Strong setup": ("#dcf5e3", "#126b34"), "Watch": ("#fff0dc", "#9a4b00"),
         "Weak": ("#eee", "#555")}[lbl]
    return f'<span class="chip" style="background:{c[0]};color:{c[1]}">{lbl}</span>'


def html(df: pd.DataFrame, now: dt.datetime) -> str:
    summary = []
    for _, r in df.iterrows():
        summary.append(f"""<tr>
<td><a href="#{r.Symbol}">{r.Company}</a><div class="sym">{r.Symbol} · {r.Segment}</div></td>
<td class="n"><b>{r.Score}</b>/100</td><td>{_chip(r.Label)}</td>
<td class="n">{r.Fundamental}/50</td><td class="n">{r.Technical}/50</td>
<td class="n">{r.LTP:,.2f}</td>
{_pc(r['1D'])}{_pc(r['1W'])}{_pc(r['1M'])}{_pc(r['3M'])}{_pc(r['6M'])}{_pc(r['1Y'])}{_pc(r['From 52W high'])}
</tr>""")

    def pills(title, items):
        cells = []
        for a, b in items:
            if b is None or (isinstance(b, float) and math.isnan(b)):
                cells.append(f'<div class="pill"><span>{a}</span><b>–</b></div>')
                continue
            cls = "dn2" if b <= -0.05 else "dn" if b < 0 else "up" if b > 0 else ""
            cells.append(f'<div class="pill {cls}"><span>{a}</span><b>{b*100:+.1f}%</b></div>')
        return f'<div class="prow"><div class="ptitle">{title}</div><div class="pills">{"".join(cells)}</div></div>'

    cards = []
    for _, r in df.iterrows():
        f = "".join(f"<li><b>{k}:</b> {v}</li>" for k, v in r._fwhy.items())
        t = "".join(f"<li><b>{k}:</b> {v}</li>" for k, v in r._twhy.items())
        sup = (f"Support ₹{r.Support:,.2f} — {r['Support status']}" if r.Support else
               "Not at a support level today")
        tags = " · ".join(x for x in [sup, "Near 52-week low" if r['Near 52W low'] else ""] if x)
        overall = [(k, r[k]) for k in ["1D", "1W", "1M", "3M", "6M", "1Y"]]
        cards.append(f"""
<div class="card" id="{r.Symbol}">
 <div class="head"><div><h3>{r.Company}</h3><div class="sym">{r.Symbol} · {r.Segment} · ₹{r.LTP:,.2f}</div></div>
 <div class="score"><span>{r.Score}</span><small>/100</small><div>{_chip(r.Label)}</div></div></div>
 <div class="meta">{tags} · <b>From 52W high: {r['From 52W high']*100:+.1f}%</b></div>
 <div class="grid">
  <div><h4>Fundamentals {r.Fundamental}/50</h4><ul>{f}</ul></div>
  <div><h4>Technicals {r.Technical}/50</h4><ul>{t}</ul></div>
 </div>
 <h4>Performance</h4>
 {pills("Overall", overall)}
 {pills("Day-wise", r._days)}
 {pills("Week-wise", r._weeks)}
 {pills("Month-wise", r._months)}
</div>""")

    strong = (df.Label == "Strong setup").sum()
    watch = (df.Label == "Watch").sum()
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Daily Screening Report — {now:%d %b %Y}</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;color:#1f2328;background:#f6f7f9}}
.wrap{{max-width:1100px;margin:auto;padding:18px}}
header{{background:#14213d;color:#fff;padding:20px 18px}} header h1{{margin:0;font-size:22px}}
header .sub{{opacity:.8;font-size:13px;margin-top:4px}}
.kpis{{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}}
.kpi{{background:#fff;border-radius:10px;padding:12px 16px;box-shadow:0 1px 3px #0001;min-width:120px}}
.kpi b{{font-size:22px;display:block}}
.box{{background:#fff;border-radius:10px;box-shadow:0 1px 3px #0001;padding:12px;margin:14px 0;overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{padding:6px 8px;border-bottom:1px solid #eee;text-align:left;white-space:nowrap}}
th{{background:#f3f3f3}} .n{{text-align:right}} .sym{{color:#888;font-size:11px}}
.chip{{padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600}}
.card{{background:#fff;border-radius:10px;box-shadow:0 1px 3px #0001;padding:14px 16px;margin:14px 0}}
.card .head{{display:flex;justify-content:space-between;align-items:flex-start;gap:10px}}
.card h3{{margin:0;font-size:17px}} .card h4{{margin:12px 0 6px;font-size:13px;color:#14213d}}
.score{{text-align:right;font-size:13px;flex-shrink:0}} .score>span{{font-size:28px;font-weight:700}}
.meta{{font-size:12px;color:#555;margin-top:4px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}} .grid3{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
ul{{margin:0;padding-left:18px;font-size:13px;line-height:1.6}}
.mini td{{border:none;padding:4px 6px;font-size:12px}} .lbl{{color:#666;font-size:11px}}
.disc{{font-size:11px;color:#666;line-height:1.5}}
.prow{{margin:6px 0}} .ptitle{{font-size:11px;color:#666;margin-bottom:3px}}
.pills{{display:flex;flex-wrap:wrap;gap:5px}}
.pill{{background:#f3f3f3;border-radius:6px;padding:4px 7px;min-width:52px;text-align:center}}
.pill span{{display:block;font-size:10px;color:#666}} .pill b{{font-size:12px}}
.pill.up{{background:#dcf5e3;color:#126b34}} .pill.dn{{background:#fde2e1;color:#9b1c1c}}
.pill.dn2{{background:#f5b5b5;color:#7f1010}}
.score small{{font-size:12px;color:#666}} .score div{{margin-top:2px}}
.box table td:first-child,.box table th:first-child{{position:sticky;left:0;background:#fff;z-index:1;white-space:normal;min-width:130px}}
.box table th:first-child{{background:#f3f3f3}}
.swipe{{display:none;font-size:11px;color:#888;margin:0 0 4px}}
@media(max-width:700px){{.wrap{{padding:10px}} .kpis{{display:grid;grid-template-columns:1fr 1fr}}
 .kpi{{min-width:0}} .swipe{{display:block}} .card{{padding:12px}} .score>span{{font-size:24px}}
 header h1{{font-size:19px}} .box{{padding:6px}}}}
@media(max-width:700px){{.grid,.grid3{{grid-template-columns:1fr}}}}
</style></head><body>
<header><h1>Daily Stock Screening Report</h1>
<div class="sub">Nifty LargeMidcap 250 · stocks at support or near 52-week low · {now:%A, %d %b %Y, %I:%M %p} IST</div></header>
<div class="wrap">
<div class="kpis">
 <div class="kpi"><b>{len(df)}</b>stocks screened</div>
 <div class="kpi"><b style="color:#126b34">{strong}</b>Strong setup (70+)</div>
 <div class="kpi"><b style="color:#9a4b00">{watch}</b>Watch (50–69)</div>
 <div class="kpi"><b style="color:#555">{len(df)-strong-watch}</b>Weak (&lt;50)</div>
</div>
<div class="box"><p class="swipe">← Swipe the table sideways to see all columns · tap a company for details</p><table>
<tr><th>Company</th><th class="n">Score</th><th>Label</th><th class="n">Fund.</th><th class="n">Tech.</th><th class="n">LTP ₹</th>
<th class="n">1D</th><th class="n">1W</th><th class="n">1M</th><th class="n">3M</th><th class="n">6M</th><th class="n">1Y</th><th class="n">From 52W high</th></tr>
{''.join(summary)}
</table></div>
<p class="disc"><b>How the score works:</b> Fundamentals (50) = ROE, debt/equity, profit growth, sales growth, profit margin.
Technicals (50) = support held, how often the level held before, RSI, volume on the day, position vs 200-day average.
70+ Strong setup · 50–69 Watch · below 50 Weak. Data: NSE index lists, Yahoo Finance.</p>
{''.join(cards)}
<p class="disc"><b>Disclaimer:</b> {DISCLAIMER}</p>
</div></body></html>"""
