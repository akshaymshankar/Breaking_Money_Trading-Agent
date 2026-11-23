---
name: short-seller
description: >-
  Paper short selling for bearish markets in the Trading Arena. Checks the
  market regime first, then looks for three setups (breakdown, failed bounce,
  relative weakness), sizes the short with a stop, and plans the cover. Use
  when the user says "find me a short", "should we short", "what is weak
  today", "bear market strategy", "plan a short on TSLA", "when do we cover",
  or asks why a SHORT was refused. Paper trading only; not financial advice.
argument-hint: "[US|IN] [tickers]"
---

# short-seller

A short makes money when a price falls. You sell borrowed shares now and buy
them back later. The gain stops at 100 %, the loss has no ceiling, and bear
markets have the sharpest rallies. So this skill is strict: no bear market, no
short; no stop, no short; flat by the close.

Everything here is virtual. The arena has no code that can reach a broker.

## Step 1: is the door open?

Run this from the project folder (Windows: `.venv\Scripts\python.exe`, Mac and Linux: `.venv/bin/python`):

```bash
.venv/Scripts/python.exe -m arena intel --region US
```

Read the `Regime` and `Shorts` lines.

| regime | score | what to do |
| --- | --- | --- |
| bull | +25 or more | no shorts |
| neutral | -24 to +24 | no shorts |
| bear | -25 or less | shorts open |
| panic | -50 or less, plus a shock (VIX 30+, VIX up 25 %+, or list down 2.5 %+) | shorts open at half size; often too late |

The regime comes from prices only. Scary headlines on their own never open the
door. If it is closed, say so, give the score and the reason, and stop.

## Step 2: the three setups

| setup | what must be true | cover when |
| --- | --- | --- |
| **Breakdown** | new day low, volume 1.2x+, below VWAP, SMA9 under SMA21, RSI 30-45 | price closes back above the broken low |
| **Failed bounce** | already down, weak rally into VWAP on falling volume, then turns down | price holds above VWAP for two bars |
| **Relative weakness** | weakest 1 or 2 names while most of the list falls, and weaker than the index | it stops lagging the index |

Skip it when any of these is true:

- RSI under 25, or the mood index under about 12. The fear is spent; squeezes start here.
- US stock down 10 % or more today (short-sale restriction, Rule 201).
- Strong good news on that stock (tilt +0.6 or more).
- Earnings before the close. Check with `/trader-news`.

## Step 3: plan it before entering

- **Stop:** above the failed level, 0.6 % to 2 % away. Never wider.
- **Target:** at least 1.5 times the stop distance.
- **Size:** at most 10 % of equity per short, 2 shorts, 20 % in total.
- **Margin:** the sale proceeds and the same amount again stay blocked. No leverage.
- **Costs:** spread, slippage, fees and a borrow fee. Skip it if the target does not clearly beat them.

## Step 4: hand it to the desk

You do not trade. Write the plan in this form and stop:

```
SHORT <symbol>  setup: <which>  regime: <label> <score>
entry ~<price>  stop <price> (<%>)  target <price> (<%>)  size <% of equity>
why: <numbers used>      wrong if: <the one thing that ends the idea>
```

The running agent reads the same rules. Its SHORT calls go to the Risk Officer
(`arena/agents/risk_officer.py`), then the paper engine (`arena/engine.py`).
To let an agent do it, spawn **Bear Hunter** on the Agents page.

## "Why was my SHORT refused?"

The reason is in the journal (Journal page, Outcome column). Usual ones: the
regime is not bearish, confidence under 0.65, two shorts already open, RSI
washed out, or the product is set to delivery (shorts are intraday only).

## What it never does

- Never shorts without a regime check and a stop.
- Never averages up into a losing short.
- Never loosens a limit in `config.toml` on its own. The user decides that.
- Never calls simulated results real returns.
