# Breaking Money · AI Trading Arena

**Learn how AI agents work by giving Claude virtual money. It paper-trades US stocks on current prices and Indian stocks on prices more than 30 days old, and writes a reason for every call.**

The Arena runs on your own computer. It watches prices and charts, reads world news and the market's mood, asks **Claude** for BUY / SELL / SHORT / COVER / HOLD on each decision, fills those calls in a realistic paper-trading simulator (spread, slippage and real fees), and shows your P&L, win rate and Claude's reasoning. You can also run a whole team of Claude agents side by side, each with its own style, and rank them on a leaderboard.

> ⚠️ **Education only. Paper trading only. Not investment advice.** Breaking Money is **not registered with SEBI**. The app uses virtual money and **cannot place real orders**: broker order URLs and trading SDKs are blocked, and it refuses to start if `paper_only` is switched off. Its BUY and SELL calls are simulation outputs, not tips. Simulated results are not real returns. SEBI has warned the public about paper-trading apps (PR No. 27/2024). Read [DISCLAIMER.md](DISCLAIMER.md) before you use it.

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

- **Market open?** Press **Start trading** and Claude begins paper trading US stocks. Its calls and reasons appear under *Claude's reasoning*, and its buys and sells are marked on the chart.
- **Market closed?** Start arms Claude for the next open. To see it work right away, press **Practice on the last session**, which replays the most recent real trading day.
- **Several agents?** Open **Agents** → **Spawn squad of 3**, or pick a style (Momentum, Mean Reversion, Breakout, Risk Guardian, Index). Each one is its own Claude, with its own money and watchlist.

The sidebar card at the bottom left shows whether Claude is connected. If something is missing, the card's button changes to **Install Claude Code** or **Sign in with Claude**, so you can fix it without leaving the app.

**Markets:** United States (NYSE / Nasdaq, 9:30 to 16:00 New York time) is the default. India (NSE) is **practice only**: switch to it at the top of the Trade screen and press **Start practice** to replay a session that is more than 30 days old. There are no live Indian sessions, because SEBI sets a 30-day lag on price data used for education. The delay is built in and is not a setting.

### Keep it running by itself (Windows)

Settings → **Always on**:

- **Auto-run** starts the main US agent for every US session (about 19:00 or 20:00 to 01:30 or 02:30 India time) and one India practice replay every trading day from 09:20 IST, on an older session it has not replayed yet. India has no live sessions: its prices are always more than 30 days old. If the agent stops on an error, it starts again, and the day's loss limit carries on. When a challenge finishes its days, the next session opens a new virtual account. Auto-run is off by default.
- **Start with Windows** opens the app, minimised, when you sign in, and opens it again if it crashes. While plugged in it asks Windows not to sleep by itself. It cannot run while the computer is off, asleep, signed out or its lid is closed (unless you changed what closing the lid does).
- **Stop always wins.** A market you stop stays stopped until its next session. Closing the minimised window ends everything.

Auto-run uses your Claude plan every day. The spending lock still applies: when your limit is reached, or Claude is signed out, the free rules decide instead and the sidebar says so. The app opens once only: a second copy would overwrite the first one's trades, so it opens the running one instead.

### Other ways to let Claude decide

The app uses your Claude Code login when it finds one. **Automatic never spends API credits**: if an `ANTHROPIC_API_KEY` is saved on your computer, the app hides it from Claude Code, so calls go to your Claude plan. The sidebar card shows who pays.

**Spending lock (on by default).** The app refuses any Claude call that your Claude plan does not cover, including the API key option, and trades with the free rules instead. It also stops asking Claude once 80% of your plan's 5-hour or weekly limit is used, or as soon as Claude reports paid usage credits. Change both in Settings. The lock covers this app only, and the account setting for usage credits at claude.ai is yours to keep off.

| Option | Cost | How |
|---|---|---|
| **Claude Code** (recommended) | Included with Claude Pro / Max | Done for you by the launcher above. |
| **Anthropic API key** | Pay per use | Open the `.env` file in the app folder and paste `ANTHROPIC_API_KEY=...` from [console.anthropic.com](https://console.anthropic.com). Then in Settings switch the spending lock off and choose **Claude API key (pay per use)**. |
| **Built-in rules** | Free | Used automatically when Claude Code is not set up, or when a Claude call fails. No AI, a simple momentum strategy to compare against. |

Each agent asks Claude once per decision interval (every 15 minutes by default), so three agents use your plan's limits about three times as fast. Each agent's card shows its Claude calls and tokens for today. Pick **Haiku** in Settings for the lightest usage.

### Run your own practice

1. Open **Settings**: starting capital, number of days, risk profile (**Balanced** or **Aggressive**) and your watchlist.
2. Press **Start**. Leave the computer on and awake during market hours. Stop any time; Start again resumes with the same cash and positions.
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
 prices ──► Runner (every 30 s) ──► stop-loss / target / square-off checks
                    │
                    └─(every 15 min)─► agent desk (news, mood, regime) ─► Claude: BUY / SELL / SHORT / COVER / HOLD + reason
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
- **Long and short.** BUY opens a long and SELL closes it. SHORT sells a stock Claude expects to fall and COVER buys it back. Shorts are intraday only and, by default, allowed only while the desk reads a bear or panic market.

## The agent desk

Every decision runs through six agents in a row. Each has one job and hands plain data to the next. The AI never touches the account directly.

| # | Agent | Job | AI? |
|---|---|---|---|
| 1 | News Scout | Reads world, market and per-stock headlines (BBC, Al Jazeera, NPR, CNBC, MarketWatch, Economic Times, Mint, Yahoo Finance) | no |
| 2 | Emotion Analyst | Turns them into a 0-100 fear/greed mood, the main emotions, and a lean on each stock | Claude, plus word lists as a backup |
| 3 | Regime Analyst | Calls the market bull, neutral, bear or panic from **prices only** (breadth, VWAP, trend, index, VIX) | no |
| 4 | Strategist | Makes BUY / SELL / SHORT / COVER / HOLD calls with reasons | Claude |
| 5 | Risk Officer | Approves, shrinks or refuses each entry. Never blocks an exit | no |
| 6 | Executor | Paper fills, fees, margin, stops, journal | no |

- **News and mood are shared.** They are read once per market every 15 minutes and used by every agent in the fleet. With Claude Code connected, that is one extra Claude call per refresh.
- **Headlines are treated as untrusted text.** They are cleaned, shortened, labelled as evidence, and never followed as instructions.
- **Replays never read news.** Today's headlines would leak the future into old prices, so replays and the demo use the price-based regime only.
- The Trade page shows a **Market mood** card: the mood gauge, the regime, the headlines and what each agent did in the last cycle. The Journal keeps every step.
- To see it from the command line: `python -m arena intel --region US` (add `--claude` for Claude's reading).

### Short selling (paper)

| Rule | Default (in `config.toml` under `[strategy]`) |
|---|---|
| When | `short_mode = "bear_only"`: only while the regime is bear or panic. `"always"` or `"off"` are the other choices. |
| Holding | Intraday only. Every short is covered by square-off time. |
| Size | At most 10% of equity per short, 2 shorts, 20% in total. Half size in a panic. |
| Stops | Required, at most 2% away. Hit the stop and the engine covers. |
| Margin | The sale proceeds plus 100% more stay blocked (no leverage). Below 30% maintenance, the engine covers the worst short by itself. |
| Costs | Spread, slippage, fees on both sides, plus a 3% a year borrow fee for the time held. |
| Refused when | The regime is not bearish, confidence is under 0.65, RSI is under 25 (squeeze risk), there is strongly positive news on that stock, or a US stock is down 10% or more today (short-sale restriction). |

Spawn **Bear Hunter** on the Agents page for a short-selling agent, or **Crowd Reader** for one that trades emotion confirmed by price.

### Choose each agent's skills

Open the **Skills** page, pick an agent, and switch skills on or off. A skill that is on is triggered in that agent's next decision, even while it is running. Choose **Defaults for new agents** to set the starting selection, or pick skills in the **New agent** form.

| Group | Skills | When they are triggered |
|---|---|---|
| Desk agents | News Scout, Emotion Analyst, Short Seller | Before every decision |
| Research | Chart Scanner, Stock News, Entry Timer, Earnings Analyst, Congress Tracker | Before Claude decides; the findings go to Claude. **Run now** shows one straight away |
| Trade checks | Exit Planner, Time Machine, Budget Manager, Risk Manager | On each new trade Claude calls. They can refuse it, shrink it or change its stop and target, never make it bigger |
| Open in Claude Code | World Mood, Short Planner, Price Alerts, Socratic Tutor, Risk Reviewer | Opens a conversation in its own window |

Research and trade checks use US data, so they work for US agents in live sessions. Each one shows what it last found. The Earnings Analyst needs `SEC_USER_AGENT` in `.env`, because the SEC asks automated readers for a contact. The Regime Analyst, Risk Officer and Executor are always on.

### Skills for Claude Code

Open this folder in Claude Code and these skills are there (see `AGENTS.md`):

- `/world-mood`: scan the news and read the crowd's emotion.
- `/short-seller`: check the regime, find a short setup, plan the stop and the cover.
- The **day-trader** pack (`/trader-scanner`, `/trader-news`, `/trader-entry`, `/trader-exit`, `/trader-timemachine`, `/trader-risk`, `/trader-budget`, `/trader-alerts`, `/trader-analyst`, `/trader-pelosi`) from github.com/Jakeschincariol/day-trader-skill, MIT. Its broker skill is left out, because this project never connects a broker.
- `/socratic-tutor` from github.com/janestreet/agent_skills, MIT: say "teach me short selling" and it quizzes you to mastery.

## Market data

Live US sessions always use `yahoo`.

| Feed | Cost | Setup | Notes |
|---|---|---|---|
| `yahoo` (default) | Free | None | Unofficial (yfinance), for personal use. Prices can lag a little. Also used by `fetch` for replay history. |
| `upstox` | Free API | Upstox account + an app at upstox.com/developer | Only for `fetch --source upstox` (India history). Token expires daily: run `python -m arena upstox-login`. Only market-data endpoints are used. |

Indian prices from any feed are cut off so that the last 30 days never reach the screen, the replays or Claude (`arena/compliance.py`).

We do not support Zerodha Kite for this, because Kite's terms restrict virtual/mock-trading use of its data. We also do not scrape the NSE website, because NSE's terms prohibit that. Before you publish results from any feed, check that its terms allow your use.

## Commands

| Command | What it does |
|---|---|
| `python -m arena gui` | Control panel in your browser: Start/Stop, settings, live log and dashboard. `START-TRADING.bat` opens it. |
| `python -m arena demo [--days 15] [--brain rules\|claude-api]` | Full challenge on synthetic prices. Safe to run any time, offline. |
| `python -m arena always` | Keeps the app running and opens it again if it crashes. |
| `python -m arena autostart on\|off\|status` | Starts it when you sign in to Windows. |
| `python -m arena check` | Proves order URLs and broker SDKs are blocked, then tests prices and Claude. |
| `python -m arena run [--new] [--name "..."]` | Live US session. Resumes the active run unless you pass `--new`. Refuses India. |
| `python -m arena fetch [--source yahoo\|upstox] [--days 30]` | Saves past 5-minute bars to `data/history/` for replay. |
| `python -m arena replay [--days 10 \| --start YYYY-MM-DD --end YYYY-MM-DD]` | Runs the agent through history on a virtual clock. It never sees a future price. |
| `python -m arena compete --brains rules,claude-api --days 10` | Several strategies, same prices, same capital, leaderboard. |
| `python -m arena report [--ai]` | Rebuilds the results page. `--ai` adds Claude's coach review. |
| `python -m arena status` | Lists all runs with equity and P&L. |
| `python -m arena intel [--region US] [--claude] [--json]` | World news, crowd mood and market regime right now, and whether shorts are open. |

## Practise for several days

1. Run `python -m arena check` first. It proves that order URLs are blocked and tests prices and Claude.
2. US: `python -m arena run --new --name "My practice"`. It stops on its own after `challenge_days` trading days.
3. India: `python -m arena fetch`, then `python -m arena replay --days 10`. Every session is more than 30 days old.
4. Read `reports/results.html` for what the agents did and why. If you share it, keep the disclaimers on it, and do not present simulated profit as a result anyone can expect.

To try a different style, edit `notes` under `[strategy]`. Claude reads it on every decision.

## Results page

`reports/results.html` is one self-contained page. It includes portfolio value, net P&L after charges, win rate, profit factor, max drawdown, charges and slippage paid, and a Sharpe estimate. It also shows the day-by-day challenge tracker, the equity curve, daily P&L bars, key insights, day-by-day notes, open positions, Claude's latest market view, every BUY/SELL call with Claude's reason and the engine's result, P&L by stock and by exit reason, the trade log, and a leaderboard of all runs.

## Cost of running Claude

With Claude Code signed in to your own Claude Pro or Max plan, calls use your plan's limits and the spending lock refuses anything else. One agent deciding every 15 minutes makes about 25 calls a day plus one news reading per refresh. To use less, raise `decision_minutes`, pick Haiku, or switch World news off. Nothing is billed unless you turn the lock off and choose an API key yourself.

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
  compliance.py  India price delay: withholds the last 30 days
  autopilot.py   auto-run: starts sessions by itself (off by default)
  always.py      one copy only, restart after a crash, start with Windows
  safety.py      real-money block (allow-list, SDK import block, paper_only lock)
  engine.py      virtual account, fills, stops, square-off
  charges.py     US regulatory fees and Indian charges (edit rates in config.toml)
  brain.py       Claude API / Claude CLI / rules baseline (the Strategist)
  skills.py      the skill switches: what is on for each agent, and the locked-down runner for skill tools
  agents/        the desk: news_scout, emotion, regime, risk_officer, and desk.py, which runs them
  runner.py      market loop, used for live, replay and demo
  insights.py    analytics + plain-English observations + Claude coach review
  report.py      the one-page results dashboard
  feeds/         yahoo, upstox, replay, synthetic
config.toml      all settings
data/arena.db    SQLite journal (runs, decisions, orders, trades, equity, days)
tests/           python -m unittest discover tests
AGENTS.md        how the agents fit together, and the rules for changing them
.claude/         Claude Code skills (world-mood, short-seller, day-trader pack, socratic-tutor) and subagents
```

## Limits of V1

- Fills are modelled. The engine does not see order-book depth, so very large or illiquid orders are only approximated (`max_participation` caps size at 5% of a bar's volume).
- Stops are checked on each poll (every 30 s by default), not tick by tick. A fast gap can fill below the stop, which is realistic.
- The machine must stay on during market hours. Run it on an always-on PC or a small cloud VM for an unattended 15-day challenge.
- Charge rates are a typical discount-broker schedule. Update `[charges]` if rates change.
- Shorting is simplified. It assumes every stock can be borrowed. It does not model hard-to-borrow lists, broker blocks on intraday shorts, circuit limits or share recalls. A real short can lose more than its stop in a gap.
- The mood index is a reading of headlines, not a forecast. Feeds can be down, late or one-sided; the card says which ones answered.

## Licence and disclaimer

MIT licensed, see [LICENSE](LICENSE). Education and paper trading only. Not investment advice. Breaking Money is not registered with SEBI. Simulated results do not predict real results. See [DISCLAIMER.md](DISCLAIMER.md).
