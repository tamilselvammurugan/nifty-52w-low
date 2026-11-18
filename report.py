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


# ---------------- index membership / links ----------------
TV_LAYOUT = "xmMqv7cK"   # user's TradingView chart layout
INDEX_LISTS = {
    "Nifty 50": "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
    "Nifty Next 50": "https://www.niftyindices.com/IndexConstituent/ind_niftynext50list.csv",
    "Nifty 200": "https://www.niftyindices.com/IndexConstituent/ind_nifty200list.csv",
    "Nifty 500": "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
}
TABS = ["All", "Nifty 50", "Nifty Next 50", "Nifty 100", "Midcap 150", "Nifty 200", "Nifty 500"]
INDEX_NAMES = TABS[1:]
# tab: (label, index filter, include all constituents?)
MAIN_TABS = ["All", "Nifty 50", "Nifty Next 50", "Nifty 100", "Midcap 150", "Nifty 200", "Nifty 500"]
TIERS = ["Nifty 50", "Nifty Next 50", "Nifty 200", "Nifty 500"]


def index_tier(idx: set) -> tuple[int, str]:
    for i, t in enumerate(TIERS):
        if t in idx:
            return i, t
    if "Midcap 150" in idx or "Nifty 100" in idx:
        return 3, "Nifty 500"
    return 9, "Not in Nifty 500"


def index_membership(cons: pd.DataFrame) -> dict:
    """symbol -> set of index names."""
    import io, requests
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}
    mem = {s: set() for s in cons["Symbol"]}
    for s, seg in zip(cons["Symbol"], cons["Segment"]):
        mem[s].add("Nifty 100" if seg == "Nifty 100" else "Midcap 150" if seg == "Midcap 150" else seg)
    for name, url in INDEX_LISTS.items():
        try:
            r = requests.get(url, headers=hdr, timeout=30); r.raise_for_status()
            for sym in pd.read_csv(io.StringIO(r.text))["Symbol"]:
                mem.setdefault(sym, set()).add(name)
        except Exception as e:
            print(f"Index list {name} failed: {e}")
    return mem


def tv_url(symbol: str) -> str:
    tv = symbol.replace("&", "_").replace("-", "_")
    return f"https://www.tradingview.com/chart/{TV_LAYOUT}/?symbol=NSE%3A{tv}"


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
_INFO = {}


def get_info(symbol: str) -> dict:
    if symbol not in _INFO:
        import time
        info = {}
        for attempt in range(3):
            try:
                info = yf.Ticker(f"{symbol}.NS").info or {}
                break
            except Exception:
                time.sleep(2 + attempt * 3)
        _INFO[symbol] = info
        time.sleep(0.15)
    return _INFO[symbol]


def fund_score(symbol: str) -> tuple[int, dict]:
    info = get_info(symbol)
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
def build(data, cons: pd.DataFrame, near_low: pd.DataFrame, sup: pd.DataFrame, mem: dict = None,
          extra=None, watch=None, core=None, targets=None) -> pd.DataFrame:
    targets = targets or {}
    mem = mem or {}
    watch = list(watch or [])
    screened = list(dict.fromkeys(list(sup["Symbol"]) + list(near_low["Symbol"])))
    scr_set = set(screened)
    syms = list(dict.fromkeys(screened + watch + list(extra or [])))
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
            "Symbol": s,
            "Company": (names.get(s) if isinstance(names.get(s), str) and names.get(s) else
                        get_info(s).get("longName") or get_info(s).get("shortName") or s),
            "Segment": seg.get(s) or "Other NSE",
            "_watch": s in watch,
            "_targets": targets.get(s, []),
            "_dlow": round(float(d["Low"].iloc[-1]), 2),
            "LTP": round(float(d["Close"].iloc[-1]), 2),
            "Score": score, "Fundamental": f, "Technical": t, "Label": label(score),
            "Support": srow.get("Support") if srow else None,
            "Support status": srow.get("Status") if srow else "",
            "Near 52W low": s in near,
            "_hit": s in scr_set,
            "_screened": s in scr_set and (core is None or s in core),
            "_tier": index_tier(mem.get(s, set())),
            "Indices": ", ".join(t for t in INDEX_NAMES if t in mem.get(s, set())),
            "_idx": mem.get(s, set()),
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
        return '<td class="n" data-v="">–</td>'
    if v <= -0.05:
        bg, fg, b = "#f5b5b5", "#7f1010", "font-weight:700"
    elif v < 0:
        bg, fg, b = "#fde2e1", "#9b1c1c", ""
    elif v > 0:
        bg, fg, b = "#dcf5e3", "#126b34", ""
    else:
        bg, fg, b = "transparent", "inherit", ""
    return f'<td class="n" data-v="{v:.6f}" style="background:{bg};color:{fg};{b}">{v*100:+.1f}%</td>'


def _chip(lbl):
    c = {"Strong setup": ("#dcf5e3", "#126b34"), "Watch": ("#fff0dc", "#9a4b00"),
         "Weak": ("#eee", "#555")}[lbl]
    return f'<span class="chip" style="background:{c[0]};color:{c[1]}">{lbl}</span>'


def html(df: pd.DataFrame, now: dt.datetime) -> str:
    def srow(r):
        return f"""<tr data-s="{r.Symbol}" data-q="{(r.Company + ' ' + r.Symbol).lower()}" data-ltp="{r.LTP}" data-low="{r._dlow}">
<td data-v="{r.Company}"><button class="star" data-s="{r.Symbol}" title="Add to My stocks">☆</button><a href="#{r.Symbol}">{r.Company}</a><div class="sym">{r.Symbol} · {r.Segment}</div></td>
<td class="n" data-v="{r.LTP}"><b>{r.LTP:,.2f}</b></td>
<td data-v="{r._tier[0]}"><span class="ix ix{r._tier[0]}">{r._tier[1]}</span></td>
<td><a class="tv" href="{tv_url(r.Symbol)}" target="_blank" rel="noopener">📈 Chart</a></td>
<td class="n" data-v="{r.Score}"><b>{r.Score}</b>/100</td><td data-v="{r.Score}">{_chip(r.Label)}</td>
<td class="n" data-v="{r.Fundamental}">{r.Fundamental}/50</td><td class="n" data-v="{r.Technical}">{r.Technical}/50</td>
{_pc(r['1D'])}{_pc(r['1W'])}{_pc(r['1M'])}{_pc(r['3M'])}{_pc(r['6M'])}{_pc(r['1Y'])}{_pc(r['From 52W high'])}
</tr>"""

    cols = [("Company", "t"), ("LTP ₹", "n"), ("Index", "n"), ("Chart", ""), ("Score", "n"), ("Label", "n"), ("Fund.", "n"),
            ("Tech.", "n"), ("1D", "n"), ("1W", "n"), ("1M", "n"), ("3M", "n"), ("6M", "n"), ("1Y", "n"),
            ("From 52W high", "n")]
    head = "<thead><tr>" + "".join(
        f'<th class="{"n " if k == "n" and c not in ("Label",) else ""}{"s" if k else ""}" data-k="{k}" data-c="{i}">{c}<i></i></th>'
        for i, (c, k) in enumerate(cols)) + "</tr></thead>"
    tab_btns, tab_panels, sub_bars = [], [], []
    scr = df[df["_screened"]]

    import json as _json
    import html as _h

    def panel(pid, sub, note, hidden=True, empty="No stocks here today."):
        syms = _h.escape(_json.dumps(list(sub["Symbol"])))
        return (f'<div class="panel" id="{pid}"{" hidden" if hidden else ""} data-syms="{syms}" '
                f'data-empty="{_h.escape(empty)}"><p class="swipe">{note} · tap a column '
                f'title to sort · ← swipe sideways</p><table>{head}<tbody></tbody></table></div>')

    for i, tab in enumerate(MAIN_TABS):
        tid = "t" + str(i)
        if tab == "All":
            tab_btns.append(f'<button class="tab on" data-t="{tid}">All <small>{len(scr)}</small></button>')
            tab_panels.append(panel(tid, scr, f"All {len(scr)} stocks at support or near 52-week low", hidden=False))
            continue
        members = df[df["_idx"].apply(lambda x, t=tab: t in x)]
        hits = df[df["_hit"]]
        top = hits[hits["_idx"].apply(lambda x, t=tab: t in x)].head(50)
        tab_btns.append(f'<button class="tab" data-t="{tid}a" data-sub="{tid}">{tab} <small>{len(members)}</small></button>')
        sub_bars.append(f'<div class="subtabs" id="{tid}" hidden>'
                        f'<button class="stab on" data-t="{tid}a">{tab} <small>{len(members)}</small></button>'
                        f'<button class="stab" data-t="{tid}b">Top by score <small>{len(top)}</small></button></div>')
        tab_panels.append(panel(f"{tid}a", members, f"All {len(members)} {tab} stocks, scored"))
        tab_panels.append(panel(f"{tid}b", top, f"Top {len(top)} {tab} stocks at support / near 52W low, by score (max 50)",
                                empty=f"No {tab} stock is at support or near its 52-week low today."))

    watch_syms = list(df[df["_watch"]]["Symbol"])
    tab_btns.insert(0, f'<button class="tab mytab" data-t="tmy">⭐ My stocks <small id="mycount">{len(watch_syms)}</small></button>')
    my_cols = cols[:2] + [("Target 1", "n"), ("Target 2", "n"), ("Target 3", "n")] + cols[2:]
    my_head = "<thead><tr>" + "".join(
        f'<th class="{"n " if k == "n" and c not in ("Label",) else ""}{"s" if k else ""}" data-k="{k}" data-c="{i}">{c}<i></i></th>'
        for i, (c, k) in enumerate(my_cols)) + "</tr></thead>"
    tab_panels.append(f'<div class="panel" id="tmy" hidden><p class="swipe">Your watchlist · tap a target to set or edit it · '
                      f'tap ☆/⭐ to add or remove a stock · saved on this device</p><table data-shift="3">{my_head}<tbody></tbody></table>'
                      f'<p class="empty" id="myempty" hidden>No stocks yet. Search above and tap ☆ to add.</p></div>')
    tab_panels.append(f'<div class="panel" id="tsearch" hidden><p class="swipe" id="snote">Search results</p><table>{head}<tbody></tbody></table></div>')
    univ = "".join(srow(r) for _, r in df.iterrows())
    tab_panels.append(f'<table id="univ" hidden><tbody>{univ}</tbody></table>')
    sort_opts = "".join(f'<option value="{v}">{t}</option>' for v, t in [
        ("4:d", "Score: high → low"), ("4:a", "Score: low → high"),
        ("14:a", "Biggest fall from 52W high"), ("10:a", "1M: biggest fall"), ("10:d", "1M: biggest gain"),
        ("9:a", "1W: biggest fall"), ("8:a", "1D: biggest fall"), ("8:d", "1D: biggest gain"),
        ("11:a", "3M: biggest fall"), ("13:a", "1Y: biggest fall"), ("13:d", "1Y: biggest gain"),
        ("6:d", "Fundamentals: high → low"), ("7:d", "Technicals: high → low"),
        ("2:a", "Index: Nifty 50 first"), ("1:d", "Price: high → low"), ("1:a", "Price: low → high"),
        ("0:a", "Company A → Z")])
    summary_html = (f'<script>const WATCH_DEFAULT={_json.dumps(watch_syms)};'
                    f'const TGT_DEFAULT={_json.dumps({r.Symbol: r._targets for _, r in df.iterrows() if r._targets})};</script>'
                    f'<div class="modal" id="tgm" hidden><form class="mbox" id="tgf"><h3 id="tgt">Set targets</h3>'
                    f'<p class="sym" id="tgsub"></p>'
                    f'<label>Target 1 ₹<input type="number" step="any" inputmode="decimal" id="tg1"></label>'
                    f'<label>Target 2 ₹<input type="number" step="any" inputmode="decimal" id="tg2"></label>'
                    f'<label>Target 3 ₹<input type="number" step="any" inputmode="decimal" id="tg3"></label>'
                    f'<div class="mbtn"><button type="button" id="tgclr">Clear</button><button type="button" id="tgx">Cancel</button>'
                    f'<button type="submit" class="pri">Save</button></div></form></div>'
                    f'<div class="search"><input id="q" type="search" placeholder="🔍 Search any stock (name or symbol)…" autocomplete="off"></div>'
                    f'<div class="tabs">{"".join(tab_btns)}</div>{"".join(sub_bars)}'
                    f'<div class="sortbar">Sort by <select id="sortsel">{sort_opts}</select></div>'
                    f'<div class="box">{"".join(tab_panels)}</div>')

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
 <div class="meta">{tags} · <b>From 52W high: {r['From 52W high']*100:+.1f}%</b>{(" · " + r.Indices) if r.Indices else ""}</div>
 <a class="tvbtn" href="{tv_url(r.Symbol)}" target="_blank" rel="noopener">📈 View full chart on TradingView</a>
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

    scr_all = df[df["_screened"]]
    strong = (scr_all.Label == "Strong setup").sum()
    watch = (scr_all.Label == "Watch").sum()
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
.swipe{{font-size:11px;color:#888;margin:2px 4px 6px}}
.tabs{{display:flex;gap:6px;overflow-x:auto;margin:14px 0 0;padding-bottom:2px;-webkit-overflow-scrolling:touch}}
.tab{{border:0;background:#e6e9ef;color:#14213d;padding:8px 12px;border-radius:18px;font-size:13px;font-weight:600;white-space:nowrap;cursor:pointer}}
.tab small,.stab small{{opacity:.7;font-weight:500}}
.subtabs{{display:flex;gap:4px;margin:8px 0 0;background:#e6e9ef;border-radius:10px;padding:3px;width:max-content;max-width:100%}}
.stab{{border:0;background:none;color:#14213d;padding:7px 14px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap}}
.stab.on{{background:#fff;box-shadow:0 1px 3px #0002}}
[hidden]{{display:none!important}} .tab.on{{background:#14213d;color:#fff}}
.sortbar{{margin:8px 2px 0;font-size:13px;color:#444}}
.search input{{width:100%;box-sizing:border-box;font-size:16px;padding:11px 14px;border:1px solid #ccd;border-radius:22px;margin-top:14px;background:#fff}}
.star{{border:0;background:none;font-size:17px;line-height:1;padding:0 6px 0 0;cursor:pointer;color:#b58900;vertical-align:-1px}}
.star.on{{color:#e0a800}} .tab.mytab{{background:#fff4cc;color:#7a5d00}} .tab.mytab.on{{background:#e0a800;color:#fff}}
.empty{{font-size:13px;color:#666;padding:10px}}
.warn{{background:#fff4cc;color:#7a5d00;border-radius:10px;padding:10px 12px;font-size:13px;margin:12px 0 0}}
.tg{{white-space:nowrap;cursor:pointer;font-size:12px;line-height:1.25}} .tg small{{display:block;font-size:10.5px}}
.tg.hit{{background:#dcf5e3;color:#126b34;font-weight:700}} .tg.near{{background:#fff0dc;color:#9a4b00}} .tg.far{{color:#444}}
.tg.none{{color:#999}} .tg.none span{{border:1px dashed #bbb;border-radius:8px;padding:2px 7px}}
.modal{{position:fixed;inset:0;background:#0007;display:flex;align-items:center;justify-content:center;z-index:50}}
.mbox{{background:#fff;border-radius:14px;padding:18px;width:min(320px,88vw)}} .mbox h3{{margin:0;font-size:17px}}
.mbox label{{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:14px;margin:10px 0}}
.mbox input{{width:130px;font-size:16px;padding:8px;border:1px solid #ccc;border-radius:8px}}
.mbtn{{display:flex;gap:8px;justify-content:flex-end;margin-top:14px}} .mbtn button{{border:0;border-radius:8px;padding:9px 14px;font-size:14px;background:#eee;cursor:pointer}}
.mbtn .pri{{background:#14213d;color:#fff}}
.ix{{font-size:11px;font-weight:600;padding:2px 7px;border-radius:9px;white-space:nowrap}}
.ix0{{background:#dbe7ff;color:#1d3f8f}} .ix1{{background:#e5dcff;color:#4b2a8f}}
.ix2{{background:#d9f2ef;color:#0f5f56}} .ix3{{background:#eceff3;color:#3b4554}} .ix9{{background:#fff;color:#888;border:1px solid #ddd}}
.sortbar select{{font-size:13px;padding:5px 8px;border-radius:8px;border:1px solid #ccc;background:#fff;margin-left:4px}}
.sortbar+.box{{margin-top:8px}}
th.s{{cursor:pointer;user-select:none}} th.s:hover{{background:#e6e9ef}}
th i{{font-style:normal;font-size:10px;margin-left:3px;color:#14213d}}
th.asc i:after{{content:"▲"}} th.desc i:after{{content:"▼"}}
.tv{{font-size:12px;text-decoration:none;white-space:nowrap}}
.tvbtn{{display:inline-block;margin-top:8px;padding:7px 12px;border-radius:8px;background:#14213d;color:#fff;text-decoration:none;font-size:13px;font-weight:600}}
@media(max-width:700px){{.wrap{{padding:10px}} .kpis{{display:grid;grid-template-columns:1fr 1fr}}
 .kpi{{min-width:0}} .card{{padding:12px}} .score>span{{font-size:24px}}
 header h1{{font-size:19px}} .box{{padding:6px}}}}
@media(max-width:700px){{.grid,.grid3{{grid-template-columns:1fr}}}}
</style></head><body>
<header><h1>Daily Stock Screening Report</h1>
<div class="sub">Nifty LargeMidcap 250 · stocks at support or near 52-week low · {now:%A, %d %b %Y, %I:%M %p} IST</div></header>
<div class="wrap">
<div class="kpis">
 <div class="kpi"><b>{len(scr_all)}</b>stocks screened</div>
 <div class="kpi"><b style="color:#126b34">{strong}</b>Strong setup (70+)</div>
 <div class="kpi"><b style="color:#9a4b00">{watch}</b>Watch (50–69)</div>
 <div class="kpi"><b style="color:#555">{len(scr_all)-strong-watch}</b>Weak (&lt;50)</div>
</div>
{summary_html}
<p class="disc"><b>How the score works:</b> Fundamentals (50) = ROE, debt/equity, profit growth, sales growth, profit margin.
Technicals (50) = support held, how often the level held before, RSI, volume on the day, position vs 200-day average.
70+ Strong setup · 50–69 Watch · below 50 Weak. Data: NSE index lists, Yahoo Finance.</p>
{''.join(cards)}
<p class="disc"><b>Disclaimer:</b> {DISCLAIMER}</p>
</div>
<script>
function sortTable(tbl,c,dir){{
 const tb=tbl.tBodies[0], rows=[...tb.rows], th=tbl.tHead.rows[0].cells[c], num=th.dataset.k==='n';
 rows.sort((a,b)=>{{
  let x=a.cells[c]?.dataset.v??'', y=b.cells[c]?.dataset.v??'';
  if(num){{ x=x===''?NaN:+x; y=y===''?NaN:+y;
   if(isNaN(x)) return 1; if(isNaN(y)) return -1; return dir==='a'?x-y:y-x; }}
  return dir==='a'?x.localeCompare(y):y.localeCompare(x);
 }});
 rows.forEach(r=>tb.appendChild(r));
 [...tbl.tHead.rows[0].cells].forEach(h=>h.classList.remove('asc','desc'));
 th.classList.add(dir==='a'?'asc':'desc');
}}
let SORT=[4,'d'];
function sget(k,d){{let v=null;try{{v=localStorage.getItem(k)}}catch(e){{}}
 if(v==null){{const m=document.cookie.match(new RegExp('(?:^|; )'+k+'=([^;]*)'));if(m)v=decodeURIComponent(m[1]);}}
 try{{return v==null?d:JSON.parse(v)}}catch(e){{return d}}}}
function sset(k,o){{const v=JSON.stringify(o);try{{localStorage.setItem(k,v)}}catch(e){{}}
 try{{document.cookie=k+'='+encodeURIComponent(v)+';max-age=31536000;path=/;SameSite=Lax'}}catch(e){{}}}}
function storeCheck(){{let ok=false;try{{localStorage.setItem('_t','1');ok=localStorage.getItem('_t')==='1';}}catch(e){{}}
 if(!ok){{const n=document.createElement('p');n.className='warn';
  n.textContent='⚠️ This browser is not saving your stars/targets (private mode or an in-app browser). Open the link in Chrome or Safari, or ask to add them to the permanent list.';
  document.querySelector('.search').before(n);}}}}
const mapC=(t,c)=>(+(t.dataset.shift||0)&&c>=2)?c+ +t.dataset.shift:c;
function sortAll(c,dir){{SORT=[c,dir];document.querySelectorAll('.panel table').forEach(t=>{{if(t.tBodies[0].rows[0]?.cells.length>1)sortTable(t,mapC(t,c),dir)}});}}
const UNIV=[...document.querySelectorAll('#univ tbody tr')];
function getWatch(){{const add=sget('w_add',[]),rem=sget('w_rem',[]);
 return new Set([...WATCH_DEFAULT.filter(s=>!rem.includes(s)),...add]);}}
function setWatch(sym,on){{let add=sget('w_add',[]),rem=sget('w_rem',[]);
 add=add.filter(x=>x!==sym);rem=rem.filter(x=>x!==sym);
 if(on&&!WATCH_DEFAULT.includes(sym))add.push(sym); if(!on&&WATCH_DEFAULT.includes(sym))rem.push(sym);
 sset('w_add',add);sset('w_rem',rem);
 renderMy(); paintStars();}}
const BYSYM={{}};
function fill(id,rows,tg){{const tb=document.querySelector('#'+id+' tbody');tb.innerHTML='';
 rows.forEach(r=>{{const n=r.cloneNode(true); if(tg)addTargets(n); tb.appendChild(n);}});
 const t=tb.closest('table'); if(tb.rows.length)sortTable(t,mapC(t,SORT[0]),SORT[1]); bindStars(tb);}}
function getTg(){{return sget('tg',{{}});}}
function tgFor(s){{const o=getTg();return o[s]!==undefined?o[s]:(TGT_DEFAULT[s]||[]);}}
const inr=v=>'₹'+(+v).toLocaleString('en-IN',{{maximumFractionDigits:2}});
function tcell(t,ltp,low){{const td=document.createElement('td');td.className='tg';
 if(!t){{td.classList.add('none');td.dataset.v='';td.innerHTML='<span>+ set</span>';return td;}}
 if(low<=t){{td.classList.add('hit');td.dataset.v='-1';td.innerHTML=inr(t)+'<small>✅ Reached</small>';return td;}}
 const d=(ltp-t)/ltp;td.dataset.v=d.toFixed(6);td.classList.add(d<=0.03?'near':'far');
 td.innerHTML=inr(t)+'<small>'+(d*100).toFixed(1)+'% away</small>';return td;}}
function addTargets(tr){{const t=tgFor(tr.dataset.s),ltp=+tr.dataset.ltp,low=+tr.dataset.low,first=tr.cells[1];
 [2,1,0].forEach(i=>{{const td=tcell(t[i],ltp,low);td.onclick=()=>openTg(tr.dataset.s);first.after(td);}});}}
function openTg(s){{const t=tgFor(s),r=BYSYM[s];document.getElementById('tgm').hidden=false;document.getElementById('tgm').dataset.s=s;
 document.getElementById('tgt').textContent='Targets · '+s;
 document.getElementById('tgsub').textContent='Current price '+inr(r.dataset.ltp)+' · reached when the price falls to the target';
 [1,2,3].forEach(i=>document.getElementById('tg'+i).value=t[i-1]||'');}}
function saveTg(s,v){{const o=getTg();o[s]=v;sset('tg',o);document.getElementById('tgm').hidden=true;renderMy();}}
function renderMy(){{const w=getWatch();const rows=UNIV.filter(r=>w.has(r.dataset.s));fill('tmy',rows,true);
 document.getElementById('mycount').textContent=rows.length;document.getElementById('myempty').hidden=rows.length>0;}}
function paintStars(){{const w=getWatch();document.querySelectorAll('.star').forEach(b=>{{const on=w.has(b.dataset.s);b.textContent=on?'⭐':'☆';b.classList.toggle('on',on);}});}}
function bindStars(root){{root.querySelectorAll('.star').forEach(b=>b.onclick=e=>{{e.preventDefault();setWatch(b.dataset.s,!getWatch().has(b.dataset.s));}});}}
function show(id){{
 const sub=id.match(/^(t\\d+)[ab]$/); const main=sub?sub[1]:null;
 document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',(x.dataset.sub||x.dataset.t)===(main||id)));
 document.querySelectorAll('.subtabs').forEach(b=>b.hidden=b.id!==main);
 document.querySelectorAll('.stab').forEach(x=>x.classList.toggle('on',x.dataset.t===id));
 document.querySelectorAll('.panel').forEach(p=>p.hidden=p.id!==id);}}
let lastTab='t0';
document.getElementById('q').oninput=e=>{{const q=e.target.value.trim().toLowerCase();
 if(!q){{show(lastTab);return;}}
 const rows=UNIV.filter(r=>r.dataset.q.includes(q)).slice(0,50); fill('tsearch',rows);
 document.getElementById('snote').textContent=rows.length?`${{rows.length}} match(es) for "${{q}}" · tap ☆ to add to My stocks`:`No match for "${{q}}". Only the Top 250 + your watchlist are in this report.`;
 show('tsearch'); paintStars();}};
document.querySelectorAll('th.s').forEach(th=>th.onclick=()=>{{
 const dir=th.classList.contains('desc')?'a':th.classList.contains('asc')?'d':(th.dataset.k==='n'?'d':'a');
 sortTable(th.closest('table'),+th.dataset.c,dir); document.getElementById('sortsel').value='';
}});
document.getElementById('tgf').onsubmit=e=>{{e.preventDefault();const m=document.getElementById('tgm');
 saveTg(m.dataset.s,[1,2,3].map(i=>{{const v=parseFloat(document.getElementById('tg'+i).value);return isNaN(v)?null:v}}));}};
document.getElementById('tgclr').onclick=()=>saveTg(document.getElementById('tgm').dataset.s,[]);
document.getElementById('tgx').onclick=()=>document.getElementById('tgm').hidden=true;
document.getElementById('sortsel').onchange=e=>{{const [c,d]=e.target.value.split(':'); sortAll(+c,d);}};
document.querySelectorAll('.tab,.stab').forEach(b=>b.onclick=()=>{{lastTab=b.dataset.t;document.getElementById('q').value='';show(b.dataset.t);}});
UNIV.forEach(r=>BYSYM[r.dataset.s]=r);
document.querySelectorAll('.panel[data-syms]').forEach(p=>{{const rows=JSON.parse(p.dataset.syms).map(s=>BYSYM[s]).filter(Boolean);
 if(rows.length)fill(p.id,rows); else p.querySelector('tbody').innerHTML='<tr><td colspan="15">'+p.dataset.empty+'</td></tr>';}});
bindStars(document);renderMy();sortAll(4,'d');paintStars();storeCheck();
</script></body></html>"""
