"""
Nifty LargeMidcap 250 - stocks near their 52-week low.

Runs free on GitHub Actions. Uses:
  - niftyindices.com for the official constituent list
  - Yahoo Finance (yfinance) for 1 year of daily prices
Outputs:
  - docs/index.html            colour-coded page (GitHub Pages)
  - docs/latest.csv            today's list
  - docs/history/YYYY-MM-DD.csv
  - Telegram message (optional, if secrets are set)

Info only - not investment advice.
"""

import io
import os
import sys
import datetime as dt
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

import report

THRESHOLD = float(os.getenv("THRESHOLD_PCT", "5")) / 100  # within 5% of 52W low
DOCS = Path(__file__).parent / "docs"
HIST = DOCS / "history"
FALLBACK_LIST = Path(__file__).parent / "constituents.csv"

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NOW = dt.datetime.now(IST)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/csv,*/*",
}

LIST_URLS = {
    # Official LargeMidcap 250 list; if it fails, build it from Nifty 100 + Midcap 150
    "main": "https://www.niftyindices.com/IndexConstituent/ind_niftylargemidcap250list.csv",
    "n100": "https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv",
    "m150": "https://www.niftyindices.com/IndexConstituent/ind_niftymidcap150list.csv",
}


def fetch_csv(url: str) -> pd.DataFrame:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return pd.read_csv(io.StringIO(r.text))


def get_constituents() -> pd.DataFrame:
    """Return DataFrame with Symbol, Company, Segment."""
    try:
        n100 = fetch_csv(LIST_URLS["n100"])
        m150 = fetch_csv(LIST_URLS["m150"])
        n100["Segment"] = "Nifty 100"
        m150["Segment"] = "Midcap 150"
        df = pd.concat([n100, m150], ignore_index=True)
    except Exception as e:
        print(f"Segment lists failed ({e}); trying main list", file=sys.stderr)
        try:
            df = fetch_csv(LIST_URLS["main"])
            df["Segment"] = "LargeMidcap 250"
        except Exception as e2:
            print(f"Main list failed ({e2}); using saved constituents.csv", file=sys.stderr)
            return pd.read_csv(FALLBACK_LIST)

    df = df.rename(columns={"Company Name": "Company"})[["Symbol", "Company", "Segment"]]
    df = df.drop_duplicates("Symbol")
    df.to_csv(FALLBACK_LIST, index=False)  # keep a copy for days the site is down
    return df


def download(cons: pd.DataFrame):
    tickers = [f"{s}.NS" for s in cons["Symbol"]]
    return yf.download(tickers, period="1y", interval="1d", group_by="ticker",
                       auto_adjust=False, threads=True, progress=False)


# ---------- Support detection ----------
SWING = 5          # a swing low = lowest Low of 5 days before and 5 days after
ZONE = 0.015       # lows within 1.5% of each other = same support zone
TOUCH = 0.015      # today's Low within 1.5% of the level = "touching"
MIN_TOUCHES = 2    # zone must have held at least twice before


def support_levels(d: pd.DataFrame):
    """Return list of (level, touches, last_date) from past swing lows (excludes last 5 days)."""
    lows = d["Low"].values
    idx = d.index
    pts = []
    for i in range(SWING, len(d) - SWING):
        w = lows[i - SWING:i + SWING + 1]
        if lows[i] == w.min():
            pts.append((float(lows[i]), idx[i]))
    pts.sort()
    zones = []
    for price, date in pts:
        if zones and price <= zones[-1]["base"] * (1 + ZONE):
            z = zones[-1]
            z["prices"].append(price)
            z["dates"].append(date)
        else:
            zones.append({"base": price, "prices": [price], "dates": [date]})
    out = []
    for z in zones:
        if len(z["prices"]) >= MIN_TOUCHES:
            level = sum(z["prices"]) / len(z["prices"])
            out.append((level, len(z["prices"]), max(z["dates"])))
    return out


def support_scan(cons: pd.DataFrame, data) -> pd.DataFrame:
    rows = []
    for _, c in cons.iterrows():
        try:
            d = data[f"{c.Symbol}.NS"].dropna(subset=["Close"])
        except KeyError:
            continue
        if len(d) < 60:
            continue
        today = d.iloc[-1]
        prev_close = float(d["Close"].iloc[-2])
        low, close = float(today["Low"]), float(today["Close"])
        hist = d.iloc[:-1]  # levels built from past days only

        candidates = [(lvl, n, dt_, "Swing-low zone") for lvl, n, dt_ in support_levels(hist)]
        sma200 = float(hist["Close"].tail(200).mean()) if len(hist) >= 200 else None
        if sma200 and prev_close > sma200:
            candidates.append((sma200, None, None, "200-day avg"))

        for lvl, n, last_dt, kind in candidates:
            if lvl > prev_close:          # only levels below yesterday's close are support
                continue
            touched = low <= lvl * (1 + TOUCH)
            if not touched:
                continue
            if close >= lvl * (1 - 0.01):
                status = "Holding"        # touched and closed at/above support
            elif prev_close >= lvl:
                status = "Broke today"    # closed more than 1% below support
            else:
                continue
            rows.append({
                "Company": c.Company, "Symbol": c.Symbol, "Segment": c.Segment,
                "LTP": round(close, 2), "Day low": round(low, 2),
                "Support": round(lvl, 2), "Type": kind,
                "Times held": n if n else "-",
                "Last held": pd.Timestamp(last_dt).date().isoformat() if last_dt is not None else "-",
                "% from support": (close - lvl) / lvl,
                "Day change %": (close - prev_close) / prev_close,
                "Status": status,
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # one row per stock: the support level nearest today's close
    df["_dist"] = df["% from support"].abs()
    df = df.sort_values(["Symbol", "_dist"]).drop_duplicates("Symbol").drop(columns="_dist")
    return df.sort_values(["Status", "% from support"], ascending=[False, True]).reset_index(drop=True)


def scan(cons: pd.DataFrame, data) -> pd.DataFrame:
    rows = []
    for _, c in cons.iterrows():
        t = f"{c.Symbol}.NS"
        try:
            d = data[t].dropna(subset=["Close"])
        except KeyError:
            continue
        if len(d) < 2:
            continue
        low_idx = d["Low"].idxmin()
        low52 = float(d["Low"].min())
        high52 = float(d["High"].max())
        last = float(d["Close"].iloc[-1])
        prev = float(d["Close"].iloc[-2])
        rows.append({
            "Company": c.Company,
            "Symbol": c.Symbol,
            "Segment": c.Segment,
            "LTP": round(last, 2),
            "52W Low": round(low52, 2),
            "% above 52W low": (last - low52) / low52,
            "52W low date": pd.Timestamp(low_idx).date().isoformat(),
            "Day change %": (last - prev) / prev,
            "52W High": round(high52, 2),
            "% below 52W high": (high52 - last) / high52,
            "Data date": pd.Timestamp(d.index[-1]).date().isoformat(),
        })
    df = pd.DataFrame(rows)
    df = df[df["% above 52W low"] <= THRESHOLD].sort_values("% above 52W low")
    return df.reset_index(drop=True)


def colour_above(v):
    if v <= 0.01:
        return "#fde2e1", "#9b1c1c"   # red
    if v <= 0.03:
        return "#fff0dc", "#9a4b00"   # orange
    return "#fff8d6", "#7a5d00"       # yellow


def colour_day(v):
    if v <= -0.05:
        return "#f5b5b5", "#7f1010", True   # dark red, bold
    if v < 0:
        return "#fde2e1", "#9b1c1c", False  # light red
    if v > 0:
        return "#dcf5e3", "#126b34", False  # green
    return "transparent", "inherit", False


def support_table(sup: pd.DataFrame) -> str:
    if sup.empty:
        return "<p class='meta'>No stock touched a support level today.</p>"
    trs = []
    for _, r in sup.iterrows():
        if r.Status == "Holding":
            st = '<span style="background:#dcf5e3;color:#126b34;padding:2px 6px;border-radius:4px">🟢 Holding</span>'
        else:
            st = '<span style="background:#f5b5b5;color:#7f1010;padding:2px 6px;border-radius:4px;font-weight:700">🔴 Broke today</span>'
        dc_bg, dc_fg, bold = colour_day(r["Day change %"])
        trs.append(f"""
<tr>
  <td>{r.Company}<div class="sym">{r.Symbol}</div></td>
  <td>{st}</td>
  <td class="n">{r.LTP:,.2f}</td>
  <td class="n">{r.Support:,.2f}</td>
  <td class="n">{r['% from support']*100:+.2f}%</td>
  <td>{r.Type}</td>
  <td class="n">{r['Times held']}</td>
  <td class="n">{r['Day low']:,.2f}</td>
  <td class="n" style="background:{dc_bg};color:{dc_fg};{'font-weight:700' if bold else ''}">{r['Day change %']*100:+.2f}%</td>
  <td>{r.Segment}</td>
</tr>""")
    return f"""<div class="wrap"><table>
<tr><th>Company</th><th>Status</th><th class="n">LTP ₹</th><th class="n">Support ₹</th>
<th class="n">From support</th><th>Type</th><th class="n">Times held</th><th class="n">Day low ₹</th>
<th class="n">Day %</th><th>Segment</th></tr>
{''.join(trs)}
</table></div>
<p class="meta"><a href="support.csv">Download CSV</a> · Support = price zone where the stock bounced at least {MIN_TOUCHES} times in the past year (swing lows), or the 200-day average. "Touching" = today's low came within {TOUCH*100:.1f}% of it.</p>"""


def build_html(df: pd.DataFrame, new_syms: set, sup: pd.DataFrame) -> str:
    trs = []
    for _, r in df.iterrows():
        ab_bg, ab_fg = colour_above(r["% above 52W low"])
        dc_bg, dc_fg, bold = colour_day(r["Day change %"])
        name_style = f"color:{ab_fg};font-weight:700" if r["% above 52W low"] <= 0.01 else ""
        new = ' <span class="new">NEW</span>' if r.Symbol in new_syms else ""
        trs.append(f"""
<tr>
  <td style="{name_style}">{r.Company}{new}<div class="sym">{r.Symbol}</div></td>
  <td>{r.Segment}</td>
  <td class="n">{r.LTP:,.2f}</td>
  <td class="n">{r['52W Low']:,.2f}</td>
  <td class="n" style="background:{ab_bg};color:{ab_fg}">{r['% above 52W low']*100:.2f}%</td>
  <td>{r['52W low date']}</td>
  <td class="n" style="background:{dc_bg};color:{dc_fg};{'font-weight:700' if bold else ''}">{r['Day change %']*100:+.2f}%</td>
  <td class="n">{r['% below 52W high']*100:.1f}%</td>
</tr>""")
    data_date = df["Data date"].max() if len(df) else "-"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nifty LargeMidcap 250 - Near 52W Low</title>
<style>
 body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:16px;color:#1f2328;background:#fff}}
 h1{{font-size:20px;margin:0 0 4px}} .meta{{color:#666;font-size:13px;margin-bottom:10px}}
 .legend span{{display:inline-block;padding:2px 8px;border-radius:4px;margin:2px 4px 2px 0;font-size:12px}}
 .wrap{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;font-size:14px}}
 th,td{{padding:7px 9px;border-bottom:1px solid #eee;text-align:left;white-space:nowrap}}
 th{{background:#f3f3f3;position:sticky;top:0}} .n{{text-align:right}}
 .sym{{color:#888;font-size:11px}} .new{{background:#2563eb;color:#fff;font-size:10px;padding:1px 5px;border-radius:3px}}
</style></head><body>
<h1>Nifty LargeMidcap 250 — within {THRESHOLD*100:.0f}% of 52-week low</h1>
<div class="meta">{len(df)} stocks · Data date {data_date} · Updated {NOW:%d %b %Y, %I:%M %p} IST · Source: NSE list + Yahoo Finance · Info only, not investment advice</div>
<div class="legend">
 <span style="background:#fde2e1;color:#9b1c1c">≤1% above low</span>
 <span style="background:#fff0dc;color:#9a4b00">1–3%</span>
 <span style="background:#fff8d6;color:#7a5d00">3–5%</span>
 &nbsp;Day: <span style="background:#dcf5e3;color:#126b34">up</span>
 <span style="background:#fde2e1;color:#9b1c1c">down</span>
 <span style="background:#f5b5b5;color:#7f1010;font-weight:700">fell ≥5%</span>
</div>
<div class="wrap"><table>
<tr><th>Company</th><th>Segment</th><th class="n">LTP ₹</th><th class="n">52W Low ₹</th>
<th class="n">% above low</th><th>Low date</th><th class="n">Day %</th><th class="n">Below 52W high</th></tr>
{''.join(trs)}
</table></div>
<p class="meta"><a href="latest.csv">Download CSV</a> · <b><a href="report.html">📄 Client screening report (scores + returns)</a></b></p>
<h1 style="margin-top:28px">Touching support today — {len(sup)} stocks</h1>
<div class="meta">🟢 Holding = touched support and closed above it · 🔴 Broke today = closed more than 1% below support</div>
{support_table(sup)}
</body></html>"""


def send_telegram(df: pd.DataFrame, new_syms: set, page_url: str, sup: pd.DataFrame,
                  rep: pd.DataFrame = None):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("Telegram not configured; skipping message")
        return
    lines = [f"📉 Nifty LargeMidcap 250 – near 52W low ({NOW:%d %b})",
             f"{len(df)} stocks within {THRESHOLD*100:.0f}%"]
    if new_syms:
        lines.append("🆕 New today: " + ", ".join(sorted(new_syms)))
    lines.append("\nClosest to low:")
    for _, r in df.head(5).iterrows():
        lines.append(f"• {r.Symbol}  ₹{r.LTP:,.2f}  ({r['% above 52W low']*100:.2f}% above, day {r['Day change %']*100:+.1f}%)")
    if not sup.empty:
        hold = sup[sup.Status == "Holding"]
        broke = sup[sup.Status == "Broke today"]
        lines.append(f"\n🎯 Touching support: {len(sup)}")
        if len(hold):
            lines.append("🟢 Holding: " + ", ".join(hold.Symbol.head(15)))
        if len(broke):
            lines.append("🔴 Broke: " + ", ".join(broke.Symbol.head(15)))
    if rep is not None and not rep.empty:
        top = rep[(rep.Label == "Strong setup") & rep["_screened"]].head(5)
        if len(top):
            lines.append("\n⭐ Strong setups (score 70+):")
            for _, r in top.iterrows():
                lines.append(f"• {r.Symbol} {r.Score}/100 | 1M {r['1M']*100:+.1f}% | from 52W high {r['From 52W high']*100:+.1f}%")
        else:
            lines.append("\n⭐ No stock scored 70+ today")
    if page_url:
        lines.append(f"\n{page_url}")
        lines.append(f"📄 Report: {page_url}report.html")
    lines.append("\nScreening info only, not investment advice.")
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                  data={"chat_id": chat, "text": "\n".join(lines),
                        "disable_web_page_preview": "true"}, timeout=30)


def main():
    DOCS.mkdir(exist_ok=True)
    HIST.mkdir(exist_ok=True)

    cons = get_constituents()
    print(f"{len(cons)} constituents")
    # Nifty 500 universe for the report (screening lists below stay on LargeMidcap 250)
    try:
        n500 = fetch_csv("https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv")
        n500 = n500.rename(columns={"Company Name": "Company"})[["Symbol", "Company"]]
        n500["Segment"] = "Nifty 500"
        (Path(__file__).parent / "nifty500.csv").write_text(n500.to_csv(index=False))
    except Exception as e:
        print(f"Nifty 500 list failed ({e}); using saved copy if any")
        f500 = Path(__file__).parent / "nifty500.csv"
        n500 = pd.read_csv(f500) if f500.exists() else pd.DataFrame(columns=["Symbol", "Company", "Segment"])
    univ = pd.concat([cons, n500[~n500["Symbol"].isin(cons["Symbol"])]], ignore_index=True)

    wl_file = Path(__file__).parent / "watchlist.txt"
    watch = [l.split("#")[0].strip().upper() for l in wl_file.read_text().splitlines()] if wl_file.exists() else []
    watch = [w for w in watch if w]
    extra_rows = pd.DataFrame([{"Symbol": w, "Company": "", "Segment": "My stocks"}
                               for w in watch if w not in set(univ["Symbol"])])
    dl_cons = pd.concat([univ, extra_rows], ignore_index=True) if len(extra_rows) else univ
    data = download(dl_cons)
    df = scan(cons, data)
    sup = support_scan(cons, data)
    print(f"{len(sup)} stocks touching support")
    print(f"{len(df)} stocks within {THRESHOLD*100:.0f}% of 52W low")

    # Skip on market holidays: Yahoo's last bar is not today
    if len(df) and df["Data date"].max() < NOW.date().isoformat() and os.getenv("FORCE") != "1":
        print("No fresh data for today (holiday?) - skipping update")
        return

    prev_file = DOCS / "latest.csv"
    prev = set(pd.read_csv(prev_file)["Symbol"]) if prev_file.exists() else set()
    new_syms = set(df["Symbol"]) - prev if prev else set()

    out = df.copy()
    for col in ["% above 52W low", "Day change %", "% below 52W high"]:
        out[col] = (out[col] * 100).round(2)
    out.to_csv(prev_file, index=False)
    out.to_csv(HIST / f"{NOW:%Y-%m-%d}.csv", index=False)
    s_out = sup.copy()
    if not s_out.empty:
        for col in ["% from support", "Day change %"]:
            s_out[col] = (s_out[col] * 100).round(2)
    s_out.to_csv(DOCS / "support.csv", index=False)
    s_out.to_csv(HIST / f"{NOW:%Y-%m-%d}-support.csv", index=False)
    (DOCS / "index.html").write_text(build_html(df, new_syms, sup), encoding="utf-8")

    # Client screening report (score + returns)
    mem = report.index_membership(dl_cons)
    df_u = scan(univ, data)            # 52W-low + support across Nifty 500 for the report
    sup_u = support_scan(univ, data)
    rep = report.build(data, dl_cons, df_u, sup_u, mem, extra=list(univ["Symbol"]), watch=watch,
                       core=set(cons["Symbol"]))
    if not rep.empty:
        page = report.html(rep, NOW)
        pw = os.getenv("REPORT_PASSWORD", "")
        if pw:
            import protect
            page = protect.encrypt_page(page, pw)
        else:
            print("WARNING: REPORT_PASSWORD not set - report.html is NOT protected")
        (DOCS / "report.html").write_text(page, encoding="utf-8")
        (HIST / f"{NOW:%Y-%m-%d}-report.html").write_text(page, encoding="utf-8")
        old_csv = DOCS / "report.csv"   # full scores would bypass the password, so not published
        if old_csv.exists():
            old_csv.unlink()
    print(f"Report: {len(rep)} stocks scored")

    send_telegram(df, new_syms, os.getenv("PAGE_URL", ""), sup, rep)


if __name__ == "__main__":
    main()
