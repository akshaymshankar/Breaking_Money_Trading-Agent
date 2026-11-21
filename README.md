# Breaking Money · AI Trading Arena

**Give Claude virtual money and watch it trade the real US or Indian stock market. Press Start, and Claude makes every call with a written reason.**

The Arena runs on your own computer. It watches live prices and charts, asks **Claude** for BUY / SELL / HOLD on each decision, fills those calls in a realistic paper-trading simulator (spread, slippage and real fees), and shows your P&L, win rate and Claude's reasoning. You can also run a whole team of Claude agents side by side, each with its own style, and rank them on a leaderboard.

> ⚠️ **Paper trading only. Not financial advice.** The app uses virtual money and **cannot place real orders**: broker order URLs and trading SDKs are blocked, and it refuses to start if `paper_only` is switched off. Simulated results are not real returns. Read [DISCLAIMER.md](DISCLAIMER.md).

**What you need:** a Windows, Mac or Linux computer, internet, and a **Claude Pro or Max** plan (Claude trades using your own Claude login). No broker account, no API key and no paid data.

---

## Quick start (about 5 minutes)

### 1. Download

Click the green **Code** button on this page → **Download ZIP**, then unzip it anywhere (for example your Desktop).

### 2. Double-click to start

| Windows | Mac | Linux |
|---|---|---|
| Double-click **`START-TRADING.bat`** | Right-click **`start-trading.command`** → **Open** (the first time only, so macOS lets it run) | Run `./start-trading.command` in a terminal |

The first run sets everything up for you and asks before installing anything:

1. **Python.** If you don't have Python 3.11 or newer, it offers to install it (Windows Package Manager on Windows, Homebrew on Mac) or opens python.org.
2. **App packages.** It installs everything the app needs into a private `.venv` folder inside the app, so nothing else on your computer changes. This takes 1 to 3 minutes, once.
3. **Claude Code.** If it isn't installed, it offers to install it with the official claude.ai installer. Then it opens your browser so you can **sign in with your Claude account**. It never sees or stores your password.
4. **The app.** The control panel opens in your browser.

After the first time, the same double-click opens the app in a few seconds. Keep the black window open while you use the app. Closing it quits.

### 3. Press Start

- **Market open?** Press **Start** and Claude begins trading. Its calls and reasons appear under *Claude's reasoning*, and its buys and sells are marked on the chart.
- **Market closed?** Start arms Claude for the next open. To see it work right away, press **Practice on the last session**, which replays the most recent real trading day.
- **Several agents?** Open **Agents** → **Spawn squad of 3**, or pick a style (Momentum, Mean Reversion, Breakout, Risk Guardian, Index). Each one is its own Claude, with its own money and watchlist.

The sidebar card at the bottom left shows whether Claude is connected. If something is missing, the card's button changes to **Install Claude Code** or **Sign in with Claude**, so you can fix it without leaving the app.

**Markets:** United States (NYSE / Nasdaq, 9:30 to 16:00 New York time) is the default. Switch to India (NSE, 9:15 to 15:30 IST) at the top of the Trade screen or in Settings.

### Other ways to let Claude decide

The app uses your Claude Code login when it finds one. Otherwise:

| Option | Cost | How |
|---|---|---|
| **Claude Code** (recommended) | Included with Claude Pro / Max | Done for you by the launcher above. |
| **Anthropic API key** | Pay per use | Open the `.env` file in the app folder and paste `ANTHROPIC_API_KEY=...` from [console.anthropic.com](https://console.anthropic.com). |
| **Built-in rules** | Free | Used automatically when neither of the above is set up. No AI, a simple momentum strategy to compare against. |

Each agent asks Claude once per decision interval (every 5 minutes by default), so three agents use your plan's limits about three times as fast. Pick **Haiku** in Settings for the lightest usage.

### Run your own challenge

1. Open **Settings**: starting capital, number of days, risk profile (**Balanced** or **Aggressive**) and your watchlist.
2. Press **Start**. Leave the computer on and awake during market hours. Stop any time; Start again resumes the same challenge with the same cash and positions.
3. Open **Journal** any time for the equity curve, every trade and every call Claude made, with its reason.

Want to try your own strategy idea? Write it in plain English in **Settings → Strategy notes**, for example *"Only buy breakouts above the opening-range high with volume above 1.5x average."* Claude reads it before every decision.

### Troubleshooting

- **Windows protected your PC (blue box):** click **More info** → **Run anyway**. It appears because the file came from the internet.
- **Mac says the file can't be opened:** right-click it and choose **Open**. If it still refuses, run `chmod +x start-trading.command setup.sh` in Terminal from the app folder.
- **Packages failed to install:** check your internet connection and double-click again. It repairs a broken install automatically. If it still fails, delete the `.venv` folder and try once more.
- **Claude card says "not signed in":** press **Sign in with Claude** in the sidebar, finish the sign-in in your browser, and the card turns green on its own.
- **No prices:** the market is closed or Yahoo is briefly unavailable. Use **Practice on the last session**, or try again during market hours.
- **Port already in use:** the app picks the next free port and prints the address in the black window. Close any old Arena windows first.

---

## How it works

```
 live prices ──► Runner (every 30 s) ──► stop-loss / target / square-off checks
                    │
                    └─(every 15 min)─► Claude: BUY / SELL / HOLD + reason + confidence
                                            │
                                            ▼
                              Paper engine (deterministic)
                     sizing · cash checks · risk limits · re-quote after the call
                     fill = price + half spread + slippage + size impact
                     Indian charges: brokerage, STT, NSE fee, SEBI, stamp, GST, DP
                                            │
                                            ▼
                     SQLite journal ──► results page (P&L, insights, Claude's reasons)
```

- **Claude decides, code executes.** Claude never chooses share quantities or touches cash. The engine sizes each order from `allocation_pct`, caps it at `max_position_pct`, and rejects anything that breaks a rule.
- **Realistic fills.** After Claude answers, the engine fetches a fresh price (the market moved while Claude was thinking) and fills against it with spread, slippage and impact. A "₹1,500 → ₹1,505" trade does not book a clean ₹5/share.
- **Risk rules** on every price poll: per-trade stop-loss and target, a daily loss kill switch (`max_daily_loss_pct`), no new entries after 14:45, and auto square-off at 15:15 in intraday mode.
- **Long-only V1.** BUY opens, SELL closes. Short selling is a later version.

## Market data

Set `feed` in `config.toml`.

| Feed | Cost | Setup | Notes |
|---|---|---|---|
| `yahoo` (default) | Free | None | Unofficial (yfinance), for personal use. Prices can lag a little. Also used by `fetch` for replay history. |
| `upstox` | Free API | Upstox account + an app at upstox.com/developer | Official exchange-licensed data. Token expires daily: run `python -m arena upstox-login` each morning. Only market-data endpoints are used. |

We do not support Zerodha Kite for this, because Kite's terms restrict virtual/mock-trading use of its data. We also do not scrape the NSE website, because NSE's terms prohibit that. Before you publish results from any feed, check that its terms allow your use.

## Commands

| Command | What it does |
|---|---|
| `python -m arena gui` | Control panel in your browser: Start/Stop, settings, live log and dashboard. `START-TRADING.bat` opens it. |
| `python -m arena demo [--days 15] [--brain rules\|claude-api]` | Full challenge on synthetic prices. Safe to run any time, offline. |
| `python -m arena check` | Proves order URLs and broker SDKs are blocked, then tests prices and Claude. |
| `python -m arena run [--new] [--name "..."]` | Live session. Resumes the active challenge unless you pass `--new`. |
| `python -m arena fetch [--source yahoo\|upstox] [--days 30]` | Saves past 5-minute bars to `data/history/` for replay. |
| `python -m arena replay [--days 10 \| --start YYYY-MM-DD --end YYYY-MM-DD]` | Runs the agent through history on a virtual clock. It never sees a future price. |
| `python -m arena compete --brains rules,claude-api --days 10` | Several strategies, same prices, same capital, leaderboard. |
| `python -m arena report [--ai]` | Rebuilds the results page. `--ai` adds Claude's coach review. |
| `python -m arena status` | Lists all runs with equity and P&L. |

## The 15-day Breaking Money challenge

1. Fill `holidays` in `config.toml` from NSE's holiday list, so the 15 days are counted correctly.
2. The evening before Day 1: `python -m arena check`.
3. Day 1 morning: `python -m arena run --new --name "Breaking Money Challenge"`.
4. Each evening, `reports/results.html` has that day's P&L and notes. Run `python -m arena report --ai` for Claude's coach review to use in your video.
5. The run stops on its own after 15 trading days (change `challenge_days` for a different length).

To try a different style mid-series, edit `notes` under `[strategy]` (for example, "Only trade breakouts above the opening-range high with volume above 1.5x average"). Claude reads it on every decision.

## Results page

`reports/results.html` is one self-contained page. It includes portfolio value, net P&L after charges, win rate, profit factor, max drawdown, charges and slippage paid, and a Sharpe estimate. It also shows the day-by-day challenge tracker, the equity curve, daily P&L bars, key insights, day-by-day notes, open positions, Claude's latest market view, every BUY/SELL call with Claude's reason and the engine's result, P&L by stock and by exit reason, the trade log, and a leaderboard of all runs.

## Cost of running Claude

With the defaults (10 stocks, a decision every 15 minutes, `claude-opus-5-5` at `effort = "medium"`), expect roughly 25 calls a day at a few cents each. That is a few US dollars a day. Lower it with `decision_minutes = 30`, fewer stocks, or `effort = "low"`.

## Files

```
START-TRADING.bat / start-trading.command   double-click launchers (Windows / Mac, Linux)
launch.py        first-run setup: Python env, packages, Claude Code sign-in, then opens the app
arena/
  gui.py         local web server for the control panel (http://127.0.0.1:8787)
  web/           the control panel itself (HTML, CSS, JS)
  fleet.py       multiple agents, each its own Claude Code process
  markets.py     US and India market hours, currency, ticks
  marketdata.py  watchlist quotes, index strip and chart candles
  safety.py      real-money block (allow-list, SDK import block, paper_only lock)
  engine.py      virtual account, fills, stops, square-off
  charges.py     US regulatory fees and Indian charges (edit rates in config.toml)
  brain.py       Claude API / Claude CLI / rules baseline
  runner.py      market loop, used for live, replay and demo
  insights.py    analytics + plain-English observations + Claude coach review
  report.py      the one-page results dashboard
  feeds/         yahoo, upstox, replay, synthetic
config.toml      all settings
data/arena.db    SQLite journal (runs, decisions, orders, trades, equity, days)
tests/           python -m unittest discover tests
```

## Limits of V1

- Fills are modelled. The engine does not see order-book depth, so very large or illiquid orders are only approximated (`max_participation` caps size at 5% of a bar's volume).
- Stops are checked on each poll (every 30 s by default), not tick by tick. A fast gap can fill below the stop, which is realistic.
- The machine must stay on during market hours. Run it on an always-on PC or a small cloud VM for an unattended 15-day challenge.
- Charge rates are a typical discount-broker schedule. Update `[charges]` if rates change.

## Licence and disclaimer

MIT licensed, see [LICENSE](LICENSE). Paper trading only. Not investment advice. Simulated results do not guarantee real-world results. See [DISCLAIMER.md](DISCLAIMER.md).
