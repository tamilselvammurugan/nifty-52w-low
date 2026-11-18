# Nifty LargeMidcap 250 – Near 52-Week Low (free, GitHub Actions)

Every weekday at **4:15 PM IST**, this finds Nifty LargeMidcap 250 stocks trading
within **5%** of their 52-week low and:

- publishes a colour-coded page on **GitHub Pages**
- saves `docs/latest.csv` and a daily copy in `docs/history/`
- sends a **Telegram** message (optional)

Cost: ₹0. Public repos get unlimited free Actions minutes; one run takes ~1–2 minutes.

## Setup (10 minutes)

1. **Create a repo** on GitHub (e.g. `nifty-52w-low`, Public) and upload all these files,
   keeping the folder `.github/workflows/scan.yml`.
2. **Turn on Pages:** repo → Settings → Pages → Source: *Deploy from a branch* →
   Branch `main`, folder `/docs` → Save.
3. **First run:** repo → Actions → *Nifty 52W low scan* → **Run workflow**.
   After ~2 minutes your page is at `https://<your-username>.github.io/nifty-52w-low/`.

### Telegram message (optional)
1. In Telegram, message **@BotFather** → `/newbot` → copy the **bot token**.
2. Send any message to your new bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `"chat":{"id": ...}`.
3. Repo → Settings → Secrets and variables → Actions → New secret:
   `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

## Change settings
- **Threshold:** edit `THRESHOLD_PCT: "5"` in `.github/workflows/scan.yml` (e.g. `"10"`).
- **Time:** edit the cron line (it's in UTC; 10:45 UTC = 4:15 PM IST).

## Colours
- % above low: red ≤1%, orange 1–3%, yellow 3–5%
- Day change: green up, light red down, dark red bold = fell 5% or more
- Blue **NEW** tag = wasn't on yesterday's list

## Touching support (all 250 stocks)
Second table on the page + `docs/support.csv`.
- **Support** = a price zone where the stock made a swing low (lowest of 5 days either side)
  at least 2 times in the past year, or the 200-day average.
- **Touching** = today's low came within 1.5% of that level.
- 🟢 **Holding** = touched and closed above (or within 1% of) support
- 🔴 **Broke today** = closed more than 1% below it
- Tune `SWING`, `ZONE`, `TOUCH`, `MIN_TOUCHES` at the top of the support section in `scan.py`.

## Notes
- Prices come from Yahoo Finance (end-of-day, may differ slightly from NSE).
- On market holidays the scheduled run skips the update.
- GitHub may start scheduled runs a few minutes late, and pauses schedules on
  repos with no activity for 60 days (this repo commits daily, so it stays active).
- Info only, not investment advice.
