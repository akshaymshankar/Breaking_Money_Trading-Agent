# 🥊 Claude vs Gemini: The ₹1 Lakh AI Trading Smackdown

> Two AIs. ₹1,00,000 of virtual money each. The same market days, the same trading skills, the same rules.
> One championship belt made of pure cardboard. **Paper trading only. No real money, ever.**

This is the official (and slightly unhinged) guide to the battle mode of the Breaking Money AI Trading Arena.

---

## 1. What is this?

The Arena normally lets one AI (Claude) trade a virtual ₹1 lakh on live NSE prices. Battle mode puts **two** AIs in the
ring at once:

| Corner | Fighter | Nickname | Fighting style (according to the crowd) |
|---|---|---|---|
| 🧡 Orange | **Claude** | *The Zen Monk of Margin* | Reads the whole chart twice, sips filter kaapi, says HOLD a lot |
| ✨ Blue | **Gemini** | *The Twin-Turbo Hype Machine* | Opens 47 tabs, high-fives itself (it is twins), checks every stock at once |

Both replay the same past trading days, bar by bar, and make every BUY / SHORT / SELL / HOLD call themselves. At the
final bell, whoever has the bigger portfolio wins the belt. 🏆

---

## 2. Two flavours of battle

| | **Real battle** | **Demo battle** |
|---|---|---|
| Who makes the calls | Claude and Gemini, for real | The Arena's rules bot, playing both corners in two styles |
| AI calls / cost | Yes (your Claude login + your Gemini key) | None, free |
| Page stamp | 🟢 **REAL HEAD-TO-HEAD** | 🟡 **ILLUSTRATIVE DEMO** + a watermark on every screen |
| Can you say the numbers in a reel? | Yes (it's still paper money, say so) | Only as "demo" / "example". They are **not** what Claude or Gemini made |

The page always tells you which one you are looking at. Please don't crop the stamp out. The stamp is load-bearing. 🧱

---

## 3. Start a battle

### From the control panel (easiest)

1. Double-click `START-TRADING.bat` (or run `python -m arena gui`). The panel opens at http://127.0.0.1:8787.
2. In the **🥊 Claude vs Gemini battle** box:
   - **Rounds (days)**: how many past trading days to replay. 5 is a good fight. Each day is about 25 decisions per AI.
   - **Gemini API key**: paste a key from https://aistudio.google.com/apikey and press **Save key**.
     It is written only to the `.env` file on your PC. (Already logged in to the Gemini CLI? You can skip the key.)
3. Press **Start real battle**. Grab a coffee. Claude thinks for ~20–40 s per call through the Claude Code login, so a
   5-round battle takes a while. Watch the Activity log for 🥊 lines.
4. When it finishes, open the **🥊 Claude vs Gemini** tab.

Want a preview first? Press **Build demo battle**. It finishes in a minute and is clearly stamped as a demo.

### From the command line

```bash
python -m arena battle                 # real battle, last 5 trading days
python -m arena battle --days 10       # longer war
python -m arena battle --demo          # illustrative demo, no AI calls
python -m arena battle --source synthetic --demo   # fully offline practice prices
```

The page is written to `reports/battle.html`, and every battle is also archived in `reports/battle/`.

### Which models fight?

- **Claude corner**: whatever `brain` your `config.toml` uses (`claude-cli` = your Claude Code login, or `claude-api` with
  `model = "..."`). If your brain is set to `rules`, the battle still drafts Claude.
- **Gemini corner**: `gemini-api` when `GEMINI_API_KEY` is set, otherwise the Gemini CLI if it's installed.
  The newest stable Gemini *Pro* model your key can use is picked automatically. To pin one, add
  `gemini_model = "gemini-2.5-pro"` (or any name) under `[strategy]` in `config.toml`.

The page shows the exact brain and model for each corner.

---

## 4. The Official Rulebook (how we kept it fair)

1. **Same wallet.** ₹1,00,000 each. No top-ups, no loans from amma.
2. **Same market, no time machine.** Both replay the same days from the same 5-minute bars. At each moment an AI sees only
   prices up to that minute. The future is locked in a steel almirah.
3. **Same skills, same prompt.** Identical system prompt, identical market snapshot (trend, RSI, VWAP, momentum, volume,
   opening range, own positions and P&L), identical JSON answer format, identical risk profile from `config.toml`.
4. **The AI decides, the robot referee executes.** The AI says *what* and *why*. The paper engine decides *how many*
   shares, checks cash, and fills the order a bit worse than the quote (spread + slippage + size impact), one bar after
   the call.
5. **Real Indian charges.** Brokerage, STT, exchange fees, SEBI fee, stamp duty and GST on every trade. All P&L shown is
   **after** charges.
6. **No safety net.** The rules-bot fallback is switched off for both corners. If an AI times out or answers nonsense,
   that turn is skipped and shown as a *brain-freeze* on its card.
7. **Secret stocks.** Every stock gets a snack codename (Biryani 🍛, Filter Kaapi ☕, Masala Dosa 🥞…). Tickers and
   company names are scrubbed from the AIs' reasons too, so nobody runs off to copy trades.
8. **Scoring.** Highest final portfolio value wins the belt. A separate **⚖️ Risk crown** goes to the best return per unit
   of pain (return ÷ max drawdown). Because highest profit ≠ best strategy. *Risk-um mukkiyam!*
9. **Zero real money.** No order code exists, broker order URLs are blocked by an allow-list, and the program refuses to
   start if paper mode is switched off. Gemini's API is allowed only for its model list and text generation.

---

## 5. Reading the battle page

| Section | What it shows |
|---|---|
| 🎬 Buttons at the top | **Reel mode** blurs every number. Then **Reveal Claude**, **Reveal Gemini**, **Reveal winner** unblur one section at a time with a counting-up animation. Made for screen recording. Tip: open the page with `#reel` at the end of the URL to start blurred. |
| Fighter cards | Final value, P&L, HP bar (50 = break-even, each 1% = 5 HP), trades, win rate, max drawdown, charges, HOLD count, short sells, average thinking time, brain-freezes, and that fighter's own P&L graph. |
| 💥 Best punch / 🤕 Worst bruise | Best and worst trade, with the AI's own reason (stock names hidden). |
| 📈 Face to face | Both P&L lines on one chart. R1, R2… are the rounds (trading days). |
| 🏆 Verdict | Winner, margin, and the Risk crown. |
| 🔔 Round by round | Each day's P&L for both, the round winner, and ringside commentary. |
| 🎙️ Numbers for your reel script | Ready-to-paste values for `[CLAUDE VALUE]`, `[CLAUDE P&L]`, `[GEMINI VALUE]`, `[GEMINI P&L]`, `[WINNER]`, in Indian digit format (₹1,04,512.30). |
| 🧾 Full fight log | Every trade from both corners. |
| 📖 Rulebook | The rules above, printed on the page. |

---

## 6. Recording the reel (suggested flow)

| Script beat | On screen |
|---|---|
| 0–5 s Hook | Hero banner "CLAUDE vs GEMINI", Reel mode on |
| 5–13 s The experiment | Scroll the Tale of the Tape and the Rulebook (same capital, same data, same rules) |
| 13–20 s Claude reveal | Press **Reveal Claude**: the value counts up, Claude's P&L graph is right below |
| 20–27 s Gemini reveal | Press **Reveal Gemini** |
| 27–35 s Winner + insight | Press **Reveal winner**: belt, margin, then point at the ⚖️ Risk crown line |
| 35–40 s CTA | Footer: "Paper trading only" |

---

## 7. FAQ

**Is this a fair benchmark of Claude vs Gemini?** It's a fun, fair *fight*: same data, same prompt, same rules. It is
not a scientific benchmark. A handful of days is a tiny sample, and the same AI can make different calls on a re-run.

**Why replay past days instead of live?** So both AIs see exactly the same prices at exactly the same moments. Live, they
would answer at slightly different times and get different fills.

**Does the battle touch my live 15-day challenge?** No. It uses its own database (`data/battle.db`) and its own report
folder (`reports/battle/`).

**What does a battle cost?** Claude through your Claude Code login uses your plan's usage. Gemini uses your API key's
quota (Google AI Studio has a free tier with rate limits; long battles can hit them, which shows as brain-freezes).

**Can I stop a battle?** Yes, press **■ Stop**. No page is written for an unfinished fight.

---

*Breaking Money · AI Trading Arena. Paper trading only. Virtual money, modelled slippage and real Indian charges.
No real orders are ever placed. Not investment advice.*
