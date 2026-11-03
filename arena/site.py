"""Claude vs Gemini results site: a polished, single-file page built from a battle.

`payload()` turns a finished battle (the battle database) into plain data, including
a post-mortem of each corner written from its own trades. `render()` turns that data
into one self-contained HTML page with animated charts and the full documentation.

Every page carries its provenance label:
- "real"    both AIs made every call on the same replayed days
- "demo"    rule-bot stand-ins played both corners (illustrative only)
- "example" numbers supplied by hand for a dramatization (labeled on every screen)
"""
from __future__ import annotations

import json
from datetime import date, datetime

from . import insights as ins

LABELS = {
    "real": "Real head-to-head · both AIs made every call on the same replayed market days · paper money",
    "demo": "Illustrative demo · rule-based stand-ins played both corners · not real Claude or Gemini results",
    "example": "Dramatized example · illustrative figures, not the result of a real Claude vs Gemini run",
}
NAMES = {"claude": "Claude", "gemini": "Gemini"}
EXIT_TEXT = {"ai": "AI exit call", "stop_loss": "Stop-loss", "target": "Target", "square_off": "15:15 auto-exit",
             "kill_switch": "Daily loss limit"}


def _inr(x: float, sign: bool = False) -> str:
    neg = x < 0
    whole = f"{abs(x):.0f}"
    head, tail = whole[:-3], whole[-3:]
    if head:
        head = ",".join([head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1])
        whole = head + "," + tail
    return (("+" if not neg else "−") if sign else ("−" if neg else "")) + "₹" + whole


# ---- data -------------------------------------------------------------------
def payload(j, corners: dict, meta: dict, alias: dict, hide) -> dict:
    """Plain data for the page. `alias` maps tickers to codenames; `hide` scrubs names from text."""
    from .battle import corner_stats

    st = corners["claude"]["cfg"]["strategy"]
    fighters = {}
    for k in ("claude", "gemini"):
        s = corner_stats(j, corners[k]["run_id"])
        sc = corners[k]["score"]
        trades = [{
            "t_in": t["entry_ts"], "t_out": t["exit_ts"], "code": alias.get(t["symbol"], "Stock"),
            "side": "Short" if t["qty"] < 0 else "Long",
            "move": round((t["exit_price"] / t["entry_price"] - 1) * 100 * (-1 if t["qty"] < 0 else 1), 2),
            "gross": round(t["gross_pnl"], 2), "charges": round(t["charges"], 2), "net": round(t["net_pnl"], 2),
            "exit": t["exit_reason"], "hold": round(t["hold_minutes"] or 0), "value": round(abs(t["qty"]) * t["entry_price"]),
            "reason": hide(t["entry_reason"] or ""),
        } for t in s["trades"]]
        fighters[k] = {
            "name": NAMES[k], "brain": corners[k]["brain"], "model": corners[k]["model"],
            "start": s["cap"], "final": round(s["final"], 2), "pnl": round(s["pnl"], 2), "pct": round(s["pct"], 3),
            "maxdd": round(s["dd"], 2), "charges": round(s["charges"], 2), "holds": s["holds"], "decisions": s["decisions"],
            "errors": sc.errors, "calls": sc.calls, "think": round(sc.seconds / max(sc.calls, 1), 1),
            "days": [{"date": d["date"], "pnl": round(d["pnl"], 2), "trades": d["trades"]} for d in s["days"]],
            "equity": [[e["ts"], round(e["equity"] - s["cap"], 2)] for e in _thin(s["eq"])],
            "trades": trades,
        }
    return finish({
        "kind": "demo" if meta["demo"] else "real",
        "days": meta["days"], "source": meta["source"], "made": meta["made"],
        "strategy": {k: st.get(k) for k in ("profile", "decision_minutes", "max_positions", "max_position_pct", "leverage",
                                              "allow_short", "default_stop_loss_pct", "default_target_pct", "max_daily_loss_pct",
                                              "no_new_entries_after", "square_off_time", "min_confidence", "product")},
        "execution": corners["claude"]["cfg"]["execution"],
        "stand_ins": {k: corners[k]["cfg"]["strategy"]["profile"] for k in corners} if meta["demo"] else None,
        "codenames": list(alias.values()),
        "fighters": fighters,
    })


def _thin(rows: list[dict], n: int = 400) -> list[dict]:
    if len(rows) <= n:
        return rows
    step = len(rows) / n
    return [rows[int(i * step)] for i in range(n)] + [rows[-1]]


def finish(p: dict) -> dict:
    """Add derived stats, the post-mortems and the verdict. Works for real, demo and example data."""
    for k, f in p["fighters"].items():
        f["stats"] = stats(f)
    c, g = p["fighters"]["claude"], p["fighters"]["gemini"]
    p["winner"] = None if abs(c["final"] - g["final"]) < 0.5 else ("claude" if c["final"] > g["final"] else "gemini")
    for k, f in p["fighters"].items():
        other = p["fighters"]["gemini" if k == "claude" else "claude"]
        f["postmortem"] = postmortem(f, other, p)
    p["verdict"] = verdict(p)
    p["label"] = LABELS[p["kind"]]
    return p


def stats(f: dict) -> dict:
    t = f["trades"]
    wins = [x for x in t if x["net"] > 0]
    losses = [x for x in t if x["net"] <= 0]
    gw, gl = sum(x["net"] for x in wins), -sum(x["net"] for x in losses)
    exits = {}
    for x in t:
        e = exits.setdefault(x["exit"], {"n": 0, "net": 0.0})
        e["n"] += 1
        e["net"] += x["net"]
    longs = [x for x in t if x["side"] == "Long"]
    shorts = [x for x in t if x["side"] == "Short"]
    early = [x for x in t if datetime.fromisoformat(x["t_in"]).hour < 11]
    late = [x for x in t if datetime.fromisoformat(x["t_in"]).hour >= 11]
    days = f["days"]
    return {
        "n": len(t), "wins": len(wins), "losses": len(losses), "win_rate": len(wins) / len(t) * 100 if t else 0.0,
        "gross": sum(x["gross"] for x in t), "charges_trades": sum(x["charges"] for x in t),
        "avg_win": gw / len(wins) if wins else 0.0, "avg_loss": -gl / len(losses) if losses else 0.0,
        "pf": (gw / gl) if gl > 0 else None, "best": max(t, key=lambda x: x["net"]) if t else None,
        "worst": min(t, key=lambda x: x["net"]) if t else None, "exits": exits,
        "long_net": sum(x["net"] for x in longs), "short_net": sum(x["net"] for x in shorts),
        "n_long": len(longs), "n_short": len(shorts),
        "early_net": sum(x["net"] for x in early), "late_net": sum(x["net"] for x in late),
        "n_early": len(early), "n_late": len(late),
        "avg_hold": sum(x["hold"] for x in t) / len(t) if t else 0.0,
        "green_days": sum(1 for d in days if d["pnl"] > 0), "red_days": sum(1 for d in days if d["pnl"] < 0),
        "flat_days": sum(1 for d in days if d["pnl"] == 0),
        "worst_day": min(days, key=lambda d: d["pnl"]) if days else None,
        "best_day": max(days, key=lambda d: d["pnl"]) if days else None,
        "avg_value": sum(x["value"] for x in t) / len(t) if t else 0.0,
    }


def postmortem(f: dict, o: dict, p: dict) -> list[dict]:
    """Findings, most important first, each backed by numbers from this corner's own trades."""
    s, os_ = f["stats"], o["stats"]
    st = p["strategy"]
    out = []
    nm, on = f["name"], o["name"]
    lost = f["pnl"] < 0
    if s["n"] == 0:
        out.append({"title": "It never pulled the trigger",
                    "body": f"{nm} answered {f['decisions']} decision prompts and judged none of the setups good enough. "
                            "Its result is flat by design: zero trades, zero charges, zero risk taken."})
        return out
    if lost:
        out.append({"title": "Where the money went",
                    "body": f"{nm} finished at {_inr(f['final'])}, {_inr(f['pnl'], True)} ({f['pct']:+.2f}%). "
                            f"Its trades made {_inr(s['gross'], True)} before costs and paid {_inr(s['charges_trades'])} in "
                            f"brokerage, STT, exchange fees, stamp duty and GST, which leaves {_inr(s['gross'] - s['charges_trades'], True)} net."})
    sl = s["exits"].get("stop_loss")
    if sl and sl["n"]:
        share = sl["n"] / s["n"] * 100
        out.append({"title": "Stop-losses did most of the damage" if sl["net"] < 0 and share >= 30 else "Stop-losses fired",
                    "body": f"{sl['n']} of {s['n']} trades ({share:.0f}%) ended at the stop for {_inr(sl['net'], True)} combined. "
                            f"With a {st.get('default_stop_loss_pct')}% stop"
                            + (f" and {st.get('leverage')}x intraday leverage, each stop-out costs roughly "
                               f"{st.get('default_stop_loss_pct', 1) * st.get('leverage', 1):.0f}% of the account"
                               if (st.get("leverage") or 1) > 1 else "")
                            + ". Breakouts that reverse inside the first hour are the classic way this strategy bleeds."})
    if s["losses"] and s["avg_win"] and abs(s["avg_loss"]) > s["avg_win"]:
        out.append({"title": "Losers were bigger than winners",
                    "body": f"The average win was {_inr(s['avg_win'])} and the average loss {_inr(abs(s['avg_loss']))}. "
                            f"At a {s['win_rate']:.0f}% win rate that ratio cannot be profitable; the plan assumes a 2:1 target "
                            "to stop, and exits before the target (AI exits, 15:15 square-off) erased that edge."})
    elif s["losses"] and s["win_rate"] < 40:
        out.append({"title": "Too few winners",
                    "body": f"Only {s['wins']} of {s['n']} trades closed green ({s['win_rate']:.0f}%). A 2:1 reward-to-risk plan "
                            "needs roughly a 34% win rate after costs just to break even."})
    if lost and s["gross"] > 0:
        out.append({"title": "Charges turned a small profit into a loss",
                    "body": f"Before costs the trades were up {_inr(s['gross'], True)}. Charges of {_inr(s['charges_trades'])} across "
                            f"{s['n']} trades (average position {_inr(s['avg_value'])}) wiped that out. The edge per trade was too thin "
                            "to pay for STT, stamp duty and GST on every round trip."})
    elif s["charges_trades"] and lost and s["charges_trades"] >= 0.25 * abs(f["pnl"]):
        out.append({"title": "Costs were a real share of the loss",
                    "body": f"Charges were {_inr(s['charges_trades'])}, about {s['charges_trades'] / abs(f['pnl']) * 100:.0f}% of the "
                            f"total loss. Average position size was {_inr(s['avg_value'])}; every round trip pays STT on the sell side "
                            "and GST on brokerage, so frequent trading is taxed twice over."})
    if s["n_short"] and s["n_long"]:
        worse = "short" if s["short_net"] < s["long_net"] else "long"
        out.append({"title": "Direction",
                    "body": f"Longs: {s['n_long']} trades, {_inr(s['long_net'], True)}. Shorts: {s['n_short']} trades, "
                            f"{_inr(s['short_net'], True)}. The {worse} side was the weaker of the two."})
    if s["n_early"] and s["n_late"]:
        out.append({"title": "Timing",
                    "body": f"Entries before 11:00 netted {_inr(s['early_net'], True)} over {s['n_early']} trades; entries after "
                            f"11:00 netted {_inr(s['late_net'], True)} over {s['n_late']}. "
                            + ("Late entries were the weaker group: they have less time to reach the target before the 15:15 exit."
                               if s["late_net"] < s["early_net"] else "Morning entries were the weaker group: the open is the most volatile, "
                               "least predictable part of the session.")})
    if s["worst_day"] and s["worst_day"]["pnl"] < 0:
        d = date.fromisoformat(s["worst_day"]["date"]).strftime("%a %d %b")
        out.append({"title": "The worst day",
                    "body": f"{d} cost {_inr(s['worst_day']['pnl'], True)} across {s['worst_day']['trades']} trade(s). "
                            f"Green days: {s['green_days']}, red days: {s['red_days']}, flat days: {s['flat_days']}."})
    if s["n"] >= 1.5 * max(os_["n"], 1) and s["n"] - os_["n"] >= 3:
        out.append({"title": "It traded more than its opponent",
                    "body": f"{nm} took {s['n']} trades to {on}'s {os_['n']}. In a costed, leveraged system, extra trades are extra "
                            "chances to be stopped out and extra charges, unless each one carries a clear edge."})
    if f["errors"]:
        out.append({"title": "Missed turns",
                    "body": f"{f['errors']} of {f['calls']} decision calls failed or timed out and were skipped (there is no "
                            "fallback in a battle). Those turns could not open, manage or close positions."})
    return out


def verdict(p: dict) -> dict:
    c, g, w = p["fighters"]["claude"], p["fighters"]["gemini"], p["winner"]
    if not w:
        return {"title": "Draw", "points": ["Both corners finished at the same value."]}
    W, L = p["fighters"][w], p["fighters"]["gemini" if w == "claude" else "claude"]
    pts = [f"{W['name']} finished {_inr(W['final'] - L['final'])} ahead on the same capital, the same days and the same rules."]
    if W["pnl"] < 0 and L["pnl"] < 0:
        pts.append(f"Neither AI made money. {W['name']} won by losing less: {_inr(W['pnl'], True)} against {_inr(L['pnl'], True)}.")
    ws, ls = W["stats"], L["stats"]
    if W["maxdd"] < L["maxdd"]:
        pts.append(f"Smaller worst dip: {W['maxdd']:.2f}% peak-to-trough drawdown versus {L['maxdd']:.2f}%.")
    if ws["n"] < ls["n"]:
        pts.append(f"More selective: {ws['n']} trades versus {ls['n']}, and {_inr(W['charges'])} in charges versus {_inr(L['charges'])}.")
    if ws["n"] and ls["n"] and ws["win_rate"] > ls["win_rate"]:
        pts.append(f"Higher hit rate: {ws['win_rate']:.0f}% of trades closed green versus {ls['win_rate']:.0f}%.")
    pts.append("A few days of replay is a small sample. The same AI can make different calls on a re-run, so treat this as one "
               "experiment, not a ranking.")
    return {"title": f"{W['name']} wins", "points": pts}


# ---- page -------------------------------------------------------------------
def render(p: dict) -> str:
    data = json.dumps(p, default=str, ensure_ascii=False).replace("</", "<\\/")
    import html
    return TEMPLATE.replace("__LABEL__", html.escape(p["label"])).replace("__DATA__", data)


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claude vs Gemini · ₹1 Lakh Trading Experiment</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&family=Space+Grotesk:wght@500;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#07080b;--bg2:#0c0e13;--panel:#11141b;--line:#1f2430;--line2:#2a3040;--ink:#f2f4f8;--ink2:#a7afc0;--mut:#6c7489;
--c:#e8835e;--c2:#f2b39b;--g:#6f9cff;--g2:#b3c8ff;--up:#3ecf8e;--down:#ff6370;--gold:#e9c46a;
--sans:Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;--disp:"Space Grotesk",Inter,system-ui,sans-serif;--mono:"JetBrains Mono",ui-monospace,Consolas,monospace}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 var(--sans);-webkit-font-smoothing:antialiased;overflow-x:hidden}
a{color:inherit}
.wrap{max-width:1160px;margin:0 auto;padding:0 20px}
.mono{font-family:var(--mono)}.mut{color:var(--mut)}.ink2{color:var(--ink2)}.up{color:var(--up)}.down{color:var(--down)}
.label{position:sticky;top:0;z-index:50;background:rgba(7,8,11,.82);backdrop-filter:blur(10px);border-bottom:1px solid var(--line);font-size:12.5px;letter-spacing:.2px}
.label .wrap{display:flex;gap:10px;align-items:center;justify-content:space-between;padding-top:9px;padding-bottom:9px}
.pill{display:inline-flex;align-items:center;gap:8px;padding:3px 10px;border-radius:999px;border:1px solid var(--line2);font-weight:600}
.pill i{width:7px;height:7px;border-radius:50%;background:var(--gold);box-shadow:0 0 10px var(--gold)}
.pill.real i{background:var(--up);box-shadow:0 0 10px var(--up)}
nav.toc a{color:var(--ink2);text-decoration:none;margin-left:18px;font-weight:500}nav.toc a:hover{color:var(--ink)}
/* hero */
.hero{position:relative;min-height:92vh;display:grid;place-items:center;overflow:hidden;border-bottom:1px solid var(--line)}
#bg{position:absolute;inset:0;width:100%;height:100%;opacity:.55}
.hero:after{content:"";position:absolute;inset:0;background:radial-gradient(60% 50% at 25% 40%,rgba(232,131,94,.16),transparent 70%),radial-gradient(60% 50% at 75% 40%,rgba(111,156,255,.16),transparent 70%),linear-gradient(180deg,transparent 60%,var(--bg))}
.hero .in{position:relative;z-index:2;text-align:center;padding:60px 20px}
.kicker{font:600 12px/1 var(--mono);letter-spacing:3px;text-transform:uppercase;color:var(--ink2)}
.title{font:700 clamp(46px,9.5vw,128px)/.92 var(--disp);letter-spacing:-3px;margin:22px 0 10px}
.title .c{background:linear-gradient(180deg,#fff 0,var(--c) 120%);-webkit-background-clip:text;background-clip:text;color:transparent}
.title .g{background:linear-gradient(180deg,#fff 0,var(--g) 120%);-webkit-background-clip:text;background-clip:text;color:transparent}
.title .vs{display:block;font:500 clamp(14px,1.6vw,18px)/1 var(--mono);letter-spacing:8px;color:var(--mut);margin:16px 0}
.lede{max-width:640px;margin:18px auto 0;color:var(--ink2);font-size:clamp(16px,1.6vw,19px)}
.facts{display:flex;gap:10px;justify-content:center;flex-wrap:wrap;margin-top:30px}
.facts span{border:1px solid var(--line2);background:rgba(17,20,27,.6);padding:8px 14px;border-radius:10px;font:500 13px var(--mono);color:var(--ink2)}
.scroll{position:absolute;bottom:26px;left:50%;transform:translateX(-50%);z-index:2;font:500 11px var(--mono);letter-spacing:3px;color:var(--mut)}
.scroll:after{content:"";display:block;width:1px;height:38px;margin:10px auto 0;background:linear-gradient(var(--mut),transparent);animation:drip 1.8s infinite}
@keyframes drip{0%{transform:scaleY(0);transform-origin:top}50%{transform:scaleY(1);transform-origin:top}51%{transform-origin:bottom}100%{transform:scaleY(0);transform-origin:bottom}}
/* sections */
section{padding:110px 0;border-bottom:1px solid var(--line)}
.eyebrow{font:600 12px var(--mono);letter-spacing:3px;text-transform:uppercase;color:var(--mut);margin-bottom:14px}
h2{font:700 clamp(32px,4.4vw,54px)/1.05 var(--disp);letter-spacing:-1.5px;margin:0 0 18px}
h3{font:700 22px/1.25 var(--disp);margin:0 0 10px;letter-spacing:-.3px}
p{margin:0 0 14px}
.reveal{opacity:0;transform:translateY(28px);transition:opacity .9s cubic-bezier(.2,.7,.2,1),transform .9s cubic-bezier(.2,.7,.2,1)}
.reveal.on{opacity:1;transform:none}
/* scoreboard */
.score{display:grid;grid-template-columns:1fr 1fr;gap:22px;margin-top:40px}
.card{position:relative;background:linear-gradient(180deg,var(--panel),var(--bg2));border:1px solid var(--line);border-radius:22px;padding:28px;overflow:hidden}
.card.cl{--a:var(--c);--a2:var(--c2)}.card.gm{--a:var(--g);--a2:var(--g2)}
.card:before{content:"";position:absolute;inset:-1px;border-radius:22px;padding:1px;background:linear-gradient(140deg,var(--a),transparent 45%);-webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask-composite:exclude;opacity:.7}
.who{display:flex;align-items:center;gap:16px}
.emb{width:58px;height:58px;flex:none}
.who b{font:700 28px var(--disp);letter-spacing:-.5px}.who small{display:block;color:var(--mut);font:500 12px var(--mono)}
.win{position:absolute;top:24px;right:24px;font:700 11px var(--mono);letter-spacing:2px;color:#1a1405;background:var(--gold);padding:6px 10px;border-radius:999px;box-shadow:0 0 30px rgba(233,196,106,.35);opacity:0;transform:scale(.8);transition:all .6s .9s}
.on .win{opacity:1;transform:none}
.k{font:600 11.5px var(--mono);letter-spacing:1.6px;text-transform:uppercase;color:var(--mut);margin-top:26px}
.big{font:700 clamp(40px,5.2vw,64px)/1.05 var(--disp);letter-spacing:-2px;font-variant-numeric:tabular-nums}
.delta{font:700 22px var(--disp);margin-top:4px}
.spark{margin-top:20px;height:90px}
.mini{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line);border:1px solid var(--line);border-radius:14px;overflow:hidden;margin-top:22px}
.mini div{background:var(--bg2);padding:12px 14px}.mini b{display:block;font:600 18px var(--disp)}.mini span{font-size:12px;color:var(--mut)}
/* charts */
.chartwrap{background:var(--bg2);border:1px solid var(--line);border-radius:22px;padding:26px;margin-top:36px}
.legend{display:flex;gap:22px;font:500 13px var(--mono);color:var(--ink2);margin-bottom:10px;flex-wrap:wrap}
.legend i{display:inline-block;width:18px;height:3px;border-radius:2px;margin-right:8px;vertical-align:middle}
svg text{font-family:var(--mono)}
.draw{stroke-dasharray:var(--len);stroke-dashoffset:var(--len);transition:stroke-dashoffset 2.6s cubic-bezier(.45,.05,.2,1)}
.on .draw{stroke-dashoffset:0}
.bars rect{transform-box:fill-box;transform-origin:center bottom;transform:scaleY(0);transition:transform 1s cubic-bezier(.2,.7,.2,1)}
.bars rect.neg{transform-origin:center top}.on .bars rect{transform:scaleY(1)}
.tip{position:fixed;pointer-events:none;background:#0b0d12f0;border:1px solid var(--line2);border-radius:10px;padding:8px 10px;font:500 12px var(--mono);opacity:0;transition:opacity .15s;z-index:60}
/* compare table */
table{width:100%;border-collapse:collapse}
.cmp td,.cmp th{padding:15px 12px;border-bottom:1px solid var(--line);text-align:right;font-variant-numeric:tabular-nums}
.cmp th{font:600 11.5px var(--mono);letter-spacing:1.5px;text-transform:uppercase;color:var(--mut)}
.cmp td:first-child,.cmp th:first-child{text-align:left;color:var(--ink2)}
.cmp .best{color:var(--ink);font-weight:700}.cmp .best:after{content:"";display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--gold);margin-left:8px;vertical-align:middle}
/* verdict */
.verdict{display:grid;grid-template-columns:1.1fr 1fr;gap:40px;align-items:center}
.trophy{aspect-ratio:1;max-width:420px;margin:auto}
.points{list-style:none;padding:0;margin:20px 0 0;counter-reset:p}
.points li{counter-increment:p;position:relative;padding:14px 0 14px 46px;border-top:1px solid var(--line);color:var(--ink2)}
.points li:before{content:counter(p,decimal-leading-zero);position:absolute;left:0;top:14px;font:600 13px var(--mono);color:var(--gold)}
/* docs */
.docs{display:grid;grid-template-columns:240px 1fr;gap:56px;margin-top:40px}
.side{position:sticky;top:70px;align-self:start}
.side a{display:block;padding:8px 0 8px 14px;border-left:2px solid var(--line);color:var(--mut);text-decoration:none;font-size:14px;transition:all .2s}
.side a.on,.side a:hover{color:var(--ink);border-color:var(--gold)}
.doc{max-width:740px}.doc article{padding:6px 0 46px;margin-bottom:46px;border-bottom:1px solid var(--line)}
.doc article:last-child{border-bottom:0}
.doc h3{font-size:28px;margin-bottom:16px}.doc h4{font:700 17px var(--disp);margin:26px 0 8px}
.doc p,.doc li{color:var(--ink2)}.doc strong{color:var(--ink)}
.doc ul{padding-left:20px}.doc li{margin-bottom:8px}
.spec{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line);border:1px solid var(--line);border-radius:14px;overflow:hidden;margin:18px 0}
.spec div{background:var(--bg2);padding:14px 16px}.spec span{display:block;font:600 11px var(--mono);letter-spacing:1.4px;text-transform:uppercase;color:var(--mut)}.spec b{font:600 16px var(--disp)}
.flow{margin:22px 0;width:100%}
.pm{display:grid;grid-template-columns:1fr 1fr;gap:22px;margin-top:18px}
.pmcol{background:var(--bg2);border:1px solid var(--line);border-radius:18px;padding:22px}
.pmcol h4{margin-top:0;display:flex;align-items:center;gap:10px}.pmcol h4 i{width:10px;height:10px;border-radius:50%}
.find{padding:14px 0;border-top:1px solid var(--line)}.find b{display:block;color:var(--ink);margin-bottom:4px;font-size:15px}.find p{font-size:14.5px;margin:0}
.callout{border:1px solid var(--line2);border-left:3px solid var(--gold);background:var(--bg2);border-radius:12px;padding:16px 18px;margin:20px 0;color:var(--ink2)}
.gloss dt{font:600 15px var(--disp);color:var(--ink);margin-top:14px}.gloss dd{margin:4px 0 0;color:var(--ink2)}
/* ledger */
.ledger{max-height:560px;overflow:auto;border:1px solid var(--line);border-radius:16px;margin-top:30px}
.ledger table{font-size:13.5px}.ledger th{position:sticky;top:0;background:var(--panel);font:600 11px var(--mono);letter-spacing:1.4px;text-transform:uppercase;color:var(--mut);text-align:left;padding:12px}
.ledger td{padding:11px 12px;border-bottom:1px solid var(--line);vertical-align:top}.ledger td.r{text-align:right;font-variant-numeric:tabular-nums}
.ledger .why{color:var(--mut);font-size:12.5px;max-width:380px}
.tag{font:600 11px var(--mono);padding:2px 8px;border-radius:6px;border:1px solid}
.tabs{display:flex;gap:8px;margin-top:24px}.tabs button{font:600 13px var(--mono);background:transparent;color:var(--ink2);border:1px solid var(--line2);padding:8px 16px;border-radius:999px;cursor:pointer}
.tabs button.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
footer{padding:60px 0 80px;color:var(--mut);font-size:13px}
footer .wrap{display:flex;justify-content:space-between;gap:30px;flex-wrap:wrap}
.stamp{position:fixed;right:16px;bottom:16px;z-index:55;font:600 11px var(--mono);letter-spacing:1px;padding:7px 12px;border-radius:999px;background:rgba(17,20,27,.9);border:1px solid var(--line2);color:var(--gold)}
@media (max-width:900px){.score,.verdict,.pm,.docs{grid-template-columns:1fr}.side{display:none}nav.toc{display:none}.spec{grid-template-columns:1fr}section{padding:80px 0}}
@media (prefers-reduced-motion:reduce){.reveal{opacity:1;transform:none;transition:none}.draw{stroke-dashoffset:0;transition:none}.bars rect{transform:none;transition:none}}
</style></head>
<body>
<div class="label"><div class="wrap"><span class="pill" id="pill"><i></i><span id="labeltxt">__LABEL__</span></span>
<nav class="toc"><a href="#results">Results</a><a href="#race">Equity race</a><a href="#verdict">Verdict</a><a href="#docs">Documentation</a><a href="#ledger">Trade log</a></nav></div></div>

<header class="hero"><canvas id="bg"></canvas><div class="in">
<div class="kicker">Breaking Money · AI Trading Arena</div>
<div class="title"><span class="c">Claude</span><span class="vs">VERSUS</span><span class="g">Gemini</span></div>
<p class="lede">Two AI models. ₹1,00,000 of virtual capital each. The same market days, the same trading skills and the same rules. Every buy, short, sell and hold was the model's own call.</p>
<div class="facts" id="facts"></div></div><div class="scroll">SCROLL</div></header>

<section id="results"><div class="wrap">
<div class="reveal"><div class="eyebrow">01 · Final scoreboard</div><h2>Where the ₹1 lakh ended up</h2>
<p class="ink2" style="max-width:640px">Portfolio values after the final bell, net of every rupee of brokerage, STT, exchange fees, stamp duty and GST.</p></div>
<div class="score" id="score"></div></div></section>

<section id="race"><div class="wrap">
<div class="reveal"><div class="eyebrow">02 · Equity race</div><h2>Profit and loss, minute by minute</h2>
<p class="ink2" style="max-width:640px">Both curves on one trading-time axis (nights and weekends removed). The dashed line is break-even.</p></div>
<div class="chartwrap reveal" id="racebox"><div class="legend"><span><i style="background:var(--c)"></i>Claude</span><span><i style="background:var(--g)"></i>Gemini</span><span class="mut">D1, D2… = trading days</span></div><div id="racechart"></div></div>
<div class="chartwrap reveal" id="daybox"><div class="legend"><span><i style="background:var(--c)"></i>Claude</span><span><i style="background:var(--g)"></i>Gemini</span><span class="mut">Net P&amp;L per trading day</span></div><div id="daychart"></div></div>
<div class="reveal" style="margin-top:36px"><table class="cmp" id="cmp"></table></div>
</div></section>

<section id="verdict"><div class="wrap verdict">
<div class="reveal"><div class="eyebrow">03 · Verdict</div><h2 id="vtitle"></h2><ul class="points" id="vpoints"></ul></div>
<div class="reveal"><svg class="trophy" viewBox="0 0 400 400" id="trophy"></svg></div></div></section>

<section id="docs"><div class="wrap">
<div class="reveal"><div class="eyebrow">04 · Documentation</div><h2>How the experiment worked, and why the money was lost</h2>
<p class="ink2" style="max-width:700px">The full method, the trading strategy both models were given, the execution and cost model, and a trade-by-trade post-mortem of each model's result.</p></div>
<div class="docs"><aside class="side" id="side"></aside><div class="doc" id="doc"></div></div></div></section>

<section id="ledger"><div class="wrap">
<div class="reveal"><div class="eyebrow">05 · Trade log</div><h2>Every trade, both models</h2>
<p class="ink2" style="max-width:640px">Stock names are replaced with codenames so the trades cannot be copied. The reason column is the model's own explanation at entry.</p></div>
<div class="tabs" id="ltabs"></div><div class="ledger"><table id="ltable"></table></div></div></section>

<footer><div class="wrap"><div><b style="color:var(--ink)">Breaking Money · AI Trading Arena</b><br>Paper trading only. Virtual money, modelled slippage and real Indian charges.<br>No real orders are ever placed. Not investment advice.</div>
<div class="mono" id="made"></div></div></footer>
<div class="stamp" id="stamp"></div><div class="tip" id="tip"></div>

<script>
const D=__DATA__;
const F=D.fighters,C=F.claude,G=F.gemini,COL={claude:"#e8835e",gemini:"#6f9cff"};
const $=s=>document.querySelector(s);
const inr=(x,s)=>{const n=Math.round(Math.abs(x)).toString();let h=n.slice(0,-3),t=n.slice(-3);if(h)h=h.replace(/\B(?=(\d{2})+(?!\d))/g,",")+",";return (s?(x<0?"−":"+"):(x<0?"−":""))+"₹"+h+t};
const inr2=x=>{const s=Math.abs(x).toFixed(2).split(".");let h=s[0].slice(0,-3),t=s[0].slice(-3);if(h)h=h.replace(/\B(?=(\d{2})+(?!\d))/g,",")+",";return (x<0?"−":"")+"₹"+h+t+"."+s[1]};
const pct=x=>(x>=0?"+":"−")+Math.abs(x).toFixed(2)+"%";
const fmtD=d=>new Date(d+"T00:00:00").toLocaleDateString("en-IN",{day:"2-digit",month:"short"});
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"})[c]);
const cls=x=>x>0?"up":x<0?"down":"";

/* label */
$("#labeltxt").textContent=D.label;if(D.kind==="real")$("#pill").classList.add("real");
$("#stamp").textContent=D.kind==="real"?"PAPER TRADING · REAL RUN":D.kind==="demo"?"ILLUSTRATIVE DEMO":"DRAMATIZED EXAMPLE";
$("#made").innerHTML="Built "+esc(D.made)+"<br>Prices: "+esc(D.source);
const st=D.strategy;
$("#facts").innerHTML=[["Capital","₹1,00,000 each"],["Trading days",D.days.length+" ("+fmtD(D.days[0])+" – "+fmtD(D.days[D.days.length-1])+")"],["Decision cycle","every "+st.decision_minutes+" min"],["Profile",st.profile]].map(([a,b])=>`<span>${a}: <b style="color:var(--ink)">${esc(b)}</b></span>`).join("");

/* emblems: abstract monograms, not product logos */
function emblem(k){const c=COL[k];return k==="claude"?
`<svg class="emb" viewBox="0 0 60 60"><defs><radialGradient id="ec" cx=".5" cy=".5" r=".6"><stop offset="0" stop-color="${c}" stop-opacity=".55"/><stop offset="1" stop-color="${c}" stop-opacity="0"/></radialGradient></defs><circle cx="30" cy="30" r="29" fill="url(#ec)"/><circle cx="30" cy="30" r="22" fill="none" stroke="${c}" stroke-width="1.5"/><path d="M38 21a12 12 0 1 0 0 18" fill="none" stroke="#fff" stroke-width="3.4" stroke-linecap="round"/></svg>`:
`<svg class="emb" viewBox="0 0 60 60"><defs><radialGradient id="eg" cx=".5" cy=".5" r=".6"><stop offset="0" stop-color="${c}" stop-opacity=".55"/><stop offset="1" stop-color="${c}" stop-opacity="0"/></radialGradient></defs><circle cx="30" cy="30" r="29" fill="url(#eg)"/><rect x="9" y="9" width="42" height="42" rx="12" fill="none" stroke="${c}" stroke-width="1.5" transform="rotate(45 30 30)"/><path d="M40 24a12 12 0 1 0 1 9h-9" fill="none" stroke="#fff" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"/></svg>`}

/* sparkline path helper */
function series(f){return f.equity.map(e=>[e[0],e[1]])}
const dayIdx={};D.days.forEach((d,i)=>dayIdx[d]=i);
function xFrac(ts){const t=new Date(ts.replace(" ","T"));const d=ts.slice(0,10);if(!(d in dayIdx))return null;const m=t.getHours()*60+t.getMinutes();return dayIdx[d]+Math.min(Math.max((m-555)/375,0),1)}

function lineChart(el,list,W,H,opt={}){
 const L=opt.L??74,R=opt.R??18,T=opt.T??18,B=opt.B??34,nd=D.days.length;
 const pts=list.map(f=>series(f).map(([ts,v])=>[xFrac(ts),v]).filter(p=>p[0]!==null));
 let lo=Math.min(0,...pts.flat().map(p=>p[1])),hi=Math.max(0,...pts.flat().map(p=>p[1]));const pad=(hi-lo)*.12||500;lo-=pad;hi+=pad;
 const X=v=>L+v/nd*(W-L-R),Y=v=>T+(hi-v)/(hi-lo)*(H-T-B);
 let s=`<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Profit and loss over time">`;
 s+=`<defs>${list.map(f=>{const k=f.name.toLowerCase();return `<linearGradient id="fill${k}${W}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${COL[k]}" stop-opacity=".28"/><stop offset="1" stop-color="${COL[k]}" stop-opacity="0"/></linearGradient>`}).join("")}</defs>`;
 if(!opt.noaxis){for(let i=0;i<5;i++){const v=lo+(hi-lo)*i/4;s+=`<line x1="${L}" x2="${W-R}" y1="${Y(v)}" y2="${Y(v)}" stroke="#ffffff0d"/><text x="${L-10}" y="${Y(v)+4}" fill="#6c7489" font-size="11" text-anchor="end">${inr(v,1)}</text>`}
 D.days.forEach((d,i)=>{if(i)s+=`<line x1="${X(i)}" x2="${X(i)}" y1="${T}" y2="${H-B}" stroke="#ffffff10"/>`;s+=`<text x="${X(i+.5)}" y="${H-10}" fill="#6c7489" font-size="11" text-anchor="middle">D${i+1} · ${fmtD(d)}</text>`})}
 s+=`<line x1="${L}" x2="${W-R}" y1="${Y(0)}" y2="${Y(0)}" stroke="#ffffff40" stroke-dasharray="4 5"/>`;
 list.forEach((f,j)=>{const k=f.name.toLowerCase(),p=pts[j];if(!p.length)return;
  const d="M"+p.map(q=>X(q[0]).toFixed(1)+","+Y(q[1]).toFixed(1)).join("L");
  s+=`<path d="${d}L${X(p[p.length-1][0]).toFixed(1)},${Y(0)}L${X(p[0][0]).toFixed(1)},${Y(0)}Z" fill="url(#fill${k}${W})" opacity=".9"/>`;
  s+=`<path class="draw" d="${d}" fill="none" stroke="${COL[k]}" stroke-width="${opt.sw||2.4}" stroke-linejoin="round" stroke-linecap="round" style="filter:drop-shadow(0 0 8px ${COL[k]}66)"/>`;
  const e=p[p.length-1];if(!opt.noaxis)s+=`<circle cx="${X(e[0])}" cy="${Y(e[1])}" r="4.5" fill="${COL[k]}"/><text x="${Math.min(X(e[0])+8,W-R-60)}" y="${Y(e[1])-10}" fill="${COL[k]}" font-size="12" font-weight="600">${inr(e[1],1)}</text>`});
 s+="</svg>";el.innerHTML=s;
 el.querySelectorAll("path.draw").forEach(p=>{const n=p.getTotalLength();p.style.setProperty("--len",n)});
}

/* scoreboard */
function card(k){const f=F[k],s=f.stats,w=D.winner===k;
 return `<div class="card ${k==="claude"?"cl":"gm"} reveal">${w?'<div class="win">WINNER</div>':""}
 <div class="who">${emblem(k)}<div><b>${f.name}</b><small>${esc(D.kind==="demo"?"stand-in: rules bot ("+D.stand_ins[k]+")":f.brain+(f.model?" · "+f.model:""))}</small></div></div>
 <div class="k">Final portfolio value</div><div class="big" data-count="${f.final}">${inr2(f.start)}</div>
 <div class="delta ${cls(f.pnl)}">${inr(f.pnl,1)} <span style="font-weight:500;opacity:.8">(${pct(f.pct)})</span></div>
 <div class="spark" id="sp_${k}"></div>
 <div class="mini"><div><b>${s.n}</b><span>Trades</span></div><div><b>${s.n?s.win_rate.toFixed(0)+"%":"–"}</b><span>Win rate</span></div><div><b>${f.maxdd.toFixed(2)}%</b><span>Max drawdown</span></div>
 <div><b>${inr(f.charges)}</b><span>Charges paid</span></div><div><b>${s.green_days}/${D.days.length}</b><span>Green days</span></div><div><b>${f.holds}</b><span>Hold calls</span></div></div></div>`}
$("#score").innerHTML=card("claude")+card("gemini");
["claude","gemini"].forEach(k=>lineChart($("#sp_"+k),[F[k]],520,90,{noaxis:1,L:2,R:2,T:6,B:6,sw:2}));
lineChart($("#racechart"),[C,G],1100,420);

/* day bars */
(function(){const W=1100,H=300,L=74,R=18,T=20,B=40,nd=D.days.length;
 const v=[...C.days,...G.days].map(d=>d.pnl);let lo=Math.min(0,...v),hi=Math.max(0,...v);const pad=(hi-lo)*.15||300;lo-=pad;hi+=pad;
 const Y=x=>T+(hi-x)/(hi-lo)*(H-T-B),bw=(W-L-R)/nd;let s=`<svg viewBox="0 0 ${W} ${H}" width="100%" class="bars">`;
 for(let i=0;i<5;i++){const x=lo+(hi-lo)*i/4;s+=`<line x1="${L}" x2="${W-R}" y1="${Y(x)}" y2="${Y(x)}" stroke="#ffffff0d"/><text x="${L-10}" y="${Y(x)+4}" fill="#6c7489" font-size="11" text-anchor="end">${inr(x,1)}</text>`}
 s+=`<line x1="${L}" x2="${W-R}" y1="${Y(0)}" y2="${Y(0)}" stroke="#ffffff40"/>`;
 D.days.forEach((d,i)=>{[["claude",C],["gemini",G]].forEach(([k,f],j)=>{const r=f.days.find(x=>x.date===d)||{pnl:0,trades:0};
  const x=L+i*bw+bw*.18+j*bw*.33,w=bw*.3,y0=Y(0),y1=Y(r.pnl);
  s+=`<rect class="${r.pnl<0?"neg":""}" data-tip="${f.name} · ${fmtD(d)}<br>${inr(r.pnl,1)} · ${r.trades} trade(s)" x="${x}" y="${Math.min(y0,y1)}" width="${w}" height="${Math.max(Math.abs(y1-y0),1.5)}" rx="4" fill="${COL[k]}" style="transition-delay:${i*.08+j*.04}s"/>`});
  s+=`<text x="${L+i*bw+bw/2}" y="${H-12}" fill="#6c7489" font-size="11" text-anchor="middle">D${i+1} · ${fmtD(d)}</text>`});
 $("#daychart").innerHTML=s+"</svg>"})();

/* comparison */
(function(){const r=(lab,a,b,fmt,better)=>{const ba=better==null?"":(better==="hi"?a>b:a<b),bb=better==null?"":(better==="hi"?b>a:b<a);
  return `<tr><td>${lab}</td><td class="${ba?"best":""}">${fmt(a)}</td><td class="${bb?"best":""}">${fmt(b)}</td></tr>`};
 const cs=C.stats,gs=G.stats,id=x=>x,pf=x=>x==null?"–":x.toFixed(2);
 $("#cmp").innerHTML=`<tr><th>Metric</th><th style="color:var(--c)">Claude</th><th style="color:var(--g)">Gemini</th></tr>`+
 r("Final value",C.final,G.final,inr2,"hi")+r("Net P&L",C.pnl,G.pnl,x=>inr(x,1),"hi")+r("Return",C.pct,G.pct,pct,"hi")+
 r("Gross P&L before charges",cs.gross,gs.gross,x=>inr(x,1),"hi")+r("Charges paid",C.charges,G.charges,inr,"lo")+
 r("Trades",cs.n,gs.n,id,null)+r("Win rate",cs.win_rate,gs.win_rate,x=>x.toFixed(0)+"%","hi")+
 r("Average win",cs.avg_win,gs.avg_win,inr,"hi")+r("Average loss",cs.avg_loss,gs.avg_loss,x=>inr(x,1),"hi")+
 r("Profit factor",cs.pf??0,gs.pf??0,pf,"hi")+r("Max drawdown",C.maxdd,G.maxdd,x=>x.toFixed(2)+"%","lo")+
 r("Average holding time",cs.avg_hold,gs.avg_hold,x=>Math.round(x)+" min",null)+
 r("Decisions answered",C.decisions,G.decisions,id,null)+r("Failed calls",C.errors,G.errors,id,"lo")})();

/* verdict + trophy art */
$("#vtitle").textContent=D.verdict.title;$("#vpoints").innerHTML=D.verdict.points.map(p=>`<li>${esc(p)}</li>`).join("");
(function(){const w=D.winner?COL[D.winner]:"#e9c46a";let s=`<defs><radialGradient id="tg" cx=".5" cy=".45" r=".55"><stop offset="0" stop-color="${w}" stop-opacity=".5"/><stop offset="1" stop-color="${w}" stop-opacity="0"/></radialGradient>
 <linearGradient id="tm" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fff5d6"/><stop offset=".5" stop-color="#e9c46a"/><stop offset="1" stop-color="#8a6a1e"/></linearGradient></defs>
 <circle cx="200" cy="190" r="190" fill="url(#tg)"/>`;
 for(let i=0;i<36;i++){const a=i/36*Math.PI*2;s+=`<line x1="${200+Math.cos(a)*120}" y1="${190+Math.sin(a)*120}" x2="${200+Math.cos(a)*(150+(i%3)*14)}" y2="${190+Math.sin(a)*(150+(i%3)*14)}" stroke="${w}" stroke-opacity=".35" stroke-width="2"><animate attributeName="stroke-opacity" values=".1;.5;.1" dur="${2+i%5*.4}s" repeatCount="indefinite"/></line>`}
 s+=`<g><animateTransform attributeName="transform" type="translate" values="0 0;0 -8;0 0" dur="3.2s" repeatCount="indefinite"/>
 <path d="M140 110h120v40c0 48-27 82-60 82s-60-34-60-82z" fill="url(#tm)"/><path d="M140 125h-26c0 34 16 55 40 60M260 125h26c0 34-16 55-40 60" fill="none" stroke="url(#tm)" stroke-width="10" stroke-linecap="round"/>
 <rect x="188" y="230" width="24" height="34" fill="url(#tm)"/><rect x="154" y="262" width="92" height="20" rx="4" fill="url(#tm)"/><rect x="140" y="282" width="120" height="22" rx="5" fill="#2a2414" stroke="url(#tm)"/>
 <text x="200" y="298" text-anchor="middle" fill="#e9c46a" font-size="12" font-weight="600" letter-spacing="2">${D.winner?F[D.winner].name.toUpperCase():"DRAW"}</text>
 <path d="M168 128c4 38 14 62 30 74" stroke="#fff" stroke-opacity=".55" stroke-width="5" fill="none" stroke-linecap="round"/></g>`;
 $("#trophy").innerHTML=s})();

/* documentation */
(function(){const ex=D.execution,s=D.strategy,lev=s.leverage||1,n=D.days.length;
 const pmcol=k=>{const f=F[k];return `<div class="pmcol"><h4><i style="background:${COL[k]}"></i>${f.name}: ${inr(f.pnl,1)} (${pct(f.pct)})</h4>${f.postmortem.map(x=>`<div class="find"><b>${esc(x.title)}</b><p>${esc(x.body)}</p></div>`).join("")}</div>`};
 const best=k=>{const t=F[k].stats.best,w=F[k].stats.worst;if(!t)return "<p>No trades.</p>";return `<ul><li><strong>Best trade:</strong> ${esc(t.code)}, ${t.side.toLowerCase()}, ${inr(t.net,1)} (${EXIT[t.exit]||t.exit}). <em>“${esc(t.reason)}”</em></li><li><strong>Worst trade:</strong> ${esc(w.code)}, ${w.side.toLowerCase()}, ${inr(w.net,1)} (${EXIT[w.exit]||w.exit}). <em>“${esc(w.reason)}”</em></li></ul>`};
 const EXIT={ai:"AI exit call",stop_loss:"stop-loss",target:"target",square_off:"15:15 auto-exit",kill_switch:"daily loss limit"};
 window.EXIT=EXIT;
 const who=D.kind==="real"?`<p>Both corners were real models: <strong>Claude</strong> (${esc(C.brain)}${C.model?", "+esc(C.model):""}) and <strong>Gemini</strong> (${esc(G.brain)}${G.model?", "+esc(G.model):""}). There was no rule-based fallback in either corner: if a model failed to answer, that turn was skipped.</p>`:
  D.kind==="demo"?`<div class="callout"><strong>Illustrative demo.</strong> The Arena's rule-based strategy played both corners in two styles (Claude corner: ${esc(D.stand_ins.claude)}, Gemini corner: ${esc(D.stand_ins.gemini)}). No AI model was called, so these figures are not what Claude or Gemini would have made.</div>`:
  `<div class="callout"><strong>Dramatized example.</strong> The figures on this page were supplied to illustrate the format and were not produced by a real Claude vs Gemini run.</div>`;
 const A=[
 ["setup","The experiment",`<p>The question was simple: given exactly the same tools, data and money, which model trades better? Both models were placed in the Breaking Money AI Trading Arena, a paper-trading simulator for the Indian cash market, and asked to trade the same ${n} trading day(s), ${fmtD(D.days[0])} to ${fmtD(D.days[n-1])}.</p>${who}
 <div class="spec"><div><span>Starting capital</span><b>₹1,00,000 each</b></div><div><span>Market</span><b>NSE cash equities</b></div><div><span>Price data</span><b>${esc(D.source)}</b></div><div><span>Decision cycle</span><b>Every ${s.decision_minutes} minutes</b></div><div><span>Trading window</span><b>09:15 – ${esc(s.square_off_time)} IST</b></div><div><span>Stocks</span><b>${D.codenames.length} large caps (names hidden)</b></div></div>
 <h4>Controls that kept it fair</h4><ul><li><strong>Same data, no look-ahead.</strong> Each day was replayed bar by bar from 5-minute candles. At every decision the model saw only prices up to that minute.</li><li><strong>Same prompt.</strong> Identical system instructions, identical market snapshot and identical answer format for both models.</li><li><strong>Same risk rules.</strong> One profile, one set of limits, enforced by code rather than by the model.</li><li><strong>Same execution.</strong> One deterministic paper engine filled both models' orders with the same slippage and the same charges.</li></ul>`],
 ["strategy","The trading strategy",`<p>Both models received the same written playbook, the <strong>${esc(s.profile)}</strong> profile. In plain terms:</p>
 ${s.profile==="allin"?`<ul><li><strong>One position at a time</strong>, using the full account at <strong>${lev}x intraday leverage</strong>. A 1% move in the stock is roughly a ${lev}% move in the account.</li><li><strong>Long setup:</strong> an opening-range breakout. After 09:45, price breaks above the high of the first 30 minutes, with price above VWAP, the 9-bar average above the 21-bar average, RSI between 55 and 75 and volume at least 1.2x normal.</li><li><strong>Short setup:</strong> the mirror image. Price breaks below the opening-range low, below VWAP, 9-bar average below the 21-bar, RSI 25–45, volume at least 1.2x.</li><li><strong>Exits:</strong> stop about ${s.default_stop_loss_pct}% against the entry, target about ${s.default_target_pct}% in favour, and exit at once if price falls back inside the opening range or crosses VWAP against the trade.</li><li><strong>Confidence gate:</strong> only setups the model rates at ${s.min_confidence} or higher are accepted; anything weaker must be a HOLD. Skipping a whole day is allowed.</li></ul>`:
 `<ul><li>Up to ${s.max_positions} positions, each capped at ${s.max_position_pct}% of equity${lev>1?`, with ${lev}x intraday leverage`:""}.</li><li>Trend, momentum and VWAP alignment required for entries.</li><li>Stop about ${s.default_stop_loss_pct}% and target about ${s.default_target_pct}% from entry.</li>${s.min_confidence?`<li>Entries below confidence ${s.min_confidence} are rejected by the engine.</li>`:""}</ul>`}
 <h4>Hard limits enforced by the engine</h4><ul><li>No new entries after ${esc(s.no_new_entries_after)} IST; everything is squared off at ${esc(s.square_off_time)} IST.</li><li>Daily loss limit of ${s.max_daily_loss_pct}% of equity: if hit, all positions close and entries stop for the day.</li><li>The model never chooses share quantities or touches cash. It says <em>what</em> and <em>why</em>; the engine sizes and executes.</li></ul>
 ${s.profile==="allin"?`<div class="callout">The idea behind the strategy: the first 30 minutes set the day's battle lines. A decisive break out of that range on strong volume often runs; a break that fails comes back quickly, which is why the stop is tight and the exit rule is strict.</div>`:`<div class="callout">The idea behind the strategy: only trade when trend, momentum and VWAP agree, risk a fixed small amount per trade, and let winners run to about twice the risk.</div>`}`],
 ["engine","Execution and costs",`<svg class="flow" viewBox="0 0 740 120"><defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#6c7489"/></marker></defs>
 ${[["Market snapshot","prices + indicators"],["Model","BUY · SHORT · SELL · HOLD"],["Risk checks","size, cash, limits"],["Paper fill","+ slippage + charges"]].map((b,i)=>`<g transform="translate(${i*190},20)"><rect width="160" height="70" rx="12" fill="#11141b" stroke="#2a3040"/><text x="80" y="31" fill="#f2f4f8" font-size="13" font-weight="600" text-anchor="middle">${b[0]}</text><text x="80" y="51" fill="#6c7489" font-size="11" text-anchor="middle">${b[1]}</text></g>${i<3?`<line x1="${i*190+164}" y1="55" x2="${i*190+186}" y2="55" stroke="#6c7489" stroke-width="1.5" marker-end="url(#ar)"/>`:""}`).join("")}</svg>
 <ul><li><strong>Latency.</strong> Orders fill on the next bar after the decision (about ${ex.replay_latency_seconds||60} s later), because the market keeps moving while a model thinks.</li><li><strong>Fill price.</strong> Half of a ${ex.spread_bps} bps bid-ask spread, plus ${ex.slippage_bps} bps slippage, plus ${ex.impact_bps_per_pct_of_volume} bps of impact for each 1% of the bar's volume taken. No order may take more than ${Math.round(ex.max_participation*100)}% of a bar.</li><li><strong>Charges.</strong> Brokerage (0.03%, capped at ₹20 per order), STT on the sell side, NSE transaction charges, SEBI fees, stamp duty on the buy side and 18% GST on brokerage and fees, the same items as a real contract note.</li></ul>
 <div class="callout">Every profit and loss figure on this page is <strong>after</strong> all of these costs.${lev>1?" With leverage, position sizes are large, so costs per trade are large too.":""}</div>`],
 ["postmortem","Post-mortem: why money was lost",`<p>Each finding below is computed from that model's own trades: exit reasons, sizes, timing and costs. Nothing here is a guess about what the model was "thinking"; the reasons quoted are its own words at the time of entry.</p><div class="pm">${pmcol("claude")}${pmcol("gemini")}</div>
 <h4>Claude's defining trades</h4>${best("claude")}<h4>Gemini's defining trades</h4>${best("gemini")}`],
 ["winner",(D.winner?F[D.winner].name+" won":"No winner")+": what made the difference",`<ul>${D.verdict.points.map(p=>`<li>${esc(p)}</li>`).join("")}</ul>
 <div class="callout"><strong>Highest profit is not the same as best strategy.</strong> A result has to be judged with the risk taken to get it. Look at drawdown, the size of the average loss and how often the stop was hit, not just the final number.</div>`],
 ["lessons","Lessons for anyone trading this way",`<ul>${lev>1?`<li><strong>Leverage multiplies mistakes.</strong> At ${lev}x, a ${s.default_stop_loss_pct}% stop is about ${(s.default_stop_loss_pct*lev).toFixed(0)}% of the account. Two or three bad breakouts in a week is a deep hole.</li>`:`<li><strong>Small losses add up.</strong> A ${s.default_stop_loss_pct}% stop on a ${s.max_position_pct}% position is a small hit, but dozens of them in a week are not.</li>`}<li><strong>Costs are a strategy input.</strong> Every round trip pays STT, stamp duty and GST. A plan that trades often needs a bigger edge per trade.</li><li><strong>Selectivity is a skill.</strong> HOLD is a valid decision. The model that waited for cleaner setups lost less.</li><li><strong>Stops work only if winners are allowed to run.</strong> A 2:1 target-to-stop plan needs winners to reach the target; early exits destroy that maths.</li><li><strong>Small samples lie.</strong> ${n} trading day(s) is not enough to rank models. Run more days, and re-run the same days, before drawing conclusions.</li></ul>`],
 ["limits","Limitations",`<ul><li>Short sample: ${n} trading day(s). Results can change completely on a different week.</li><li>Models are not deterministic; the same prompt can produce different calls on a re-run.</li><li>Fills are modelled. Real markets can slip more on fast moves, and liquidity can vanish.</li><li>Replay data comes from ${esc(D.source)}. Check the data source's terms before publishing results.</li><li>This is paper trading. No real orders were placed and no real money was at risk.</li></ul>`],
 ["glossary","Glossary",`<dl class="gloss"><dt>Opening range</dt><dd>The high and low of the first 30 minutes of trading (09:15–09:45).</dd><dt>VWAP</dt><dd>Volume-weighted average price for the day; a common line for judging whether buyers or sellers are in control.</dd><dt>RSI</dt><dd>Relative Strength Index, a 0–100 momentum gauge. Above 70 is stretched, below 30 is washed out.</dd><dt>Stop-loss / target</dt><dd>Pre-set exit prices that cap a loss or lock in a gain.</dd><dt>Leverage</dt><dd>Trading a position bigger than your cash. 5x means ₹1 lakh controls about ₹5 lakh of stock.${lev>1?" This experiment used "+lev+"x.":" This experiment used none."}</dd><dt>Max drawdown</dt><dd>The largest fall from a peak in account value to a later low.</dd><dt>Profit factor</dt><dd>Gross profit from winners divided by gross loss from losers. Below 1 means the strategy lost money.</dd><dt>STT</dt><dd>Securities Transaction Tax, charged by the government on equity trades.</dd></dl>`]];
 $("#side").innerHTML=A.map(a=>`<a href="#d_${a[0]}">${a[1]}</a>`).join("");
 $("#doc").innerHTML=A.map(a=>`<article id="d_${a[0]}" class="reveal"><h3>${a[1]}</h3>${a[2]}</article>`).join("");
})();

/* ledger */
(function(){let cur="all";const rows=()=>[...C.trades.map(t=>({...t,k:"claude"})),...G.trades.map(t=>({...t,k:"gemini"}))].filter(t=>cur==="all"||t.k===cur).sort((a,b)=>a.t_in<b.t_in?-1:1);
 const draw=()=>{const r=rows();$("#ltable").innerHTML=`<tr><th>Model</th><th>Entry</th><th>Stock</th><th>Side</th><th style="text-align:right">Move</th><th style="text-align:right">Charges</th><th style="text-align:right">Net P&amp;L</th><th>Exit</th><th>Model's reason</th></tr>`+
  (r.length?r.map(t=>`<tr><td><span class="tag" style="color:${COL[t.k]};border-color:${COL[t.k]}55">${F[t.k].name}</span></td><td class="mono">${new Date(t.t_in.replace(" ","T")).toLocaleString("en-IN",{day:"2-digit",month:"short",hour:"2-digit",minute:"2-digit",hour12:false})}</td><td>${esc(t.code)}</td><td>${t.side}</td><td class="r ${cls(t.move)}">${pct(t.move)}</td><td class="r">${inr(t.charges)}</td><td class="r ${cls(t.net)}"><b>${inr(t.net,1)}</b></td><td>${window.EXIT[t.exit]||esc(t.exit)}</td><td class="why">${esc(t.reason)}</td></tr>`).join(""):`<tr><td colspan="9" class="mut">No trades.</td></tr>`)};
 $("#ltabs").innerHTML=[["all","All trades"],["claude","Claude"],["gemini","Gemini"]].map(([k,l])=>`<button data-k="${k}" class="${k==="all"?"on":""}">${l}</button>`).join("");
 $("#ltabs").onclick=e=>{const k=e.target.dataset.k;if(!k)return;cur=k;$("#ltabs").querySelectorAll("button").forEach(b=>b.classList.toggle("on",b.dataset.k===k));draw()};draw()})();

/* animations: reveal on scroll, count-up, tooltips, toc highlight */
function countUp(el){const to=+el.dataset.count,from=100000,t0=performance.now(),d=1800;(function f(n){const p=Math.min((n-t0)/d,1),e=1-Math.pow(1-p,4);el.textContent=inr2(from+(to-from)*e);if(p<1)requestAnimationFrame(f)})(t0)}
const io=new IntersectionObserver(es=>es.forEach(e=>{if(!e.isIntersecting)return;e.target.classList.add("on");e.target.querySelectorAll("[data-count]").forEach(countUp);io.unobserve(e.target)}),{threshold:.18});
document.querySelectorAll(".reveal").forEach(el=>io.observe(el));
const tip=$("#tip");document.addEventListener("mousemove",e=>{const t=e.target.closest("[data-tip]");if(!t){tip.style.opacity=0;return}tip.innerHTML=t.dataset.tip;tip.style.opacity=1;tip.style.left=(e.clientX+14)+"px";tip.style.top=(e.clientY+14)+"px"});
const arts=[...document.querySelectorAll(".doc article")],links=[...document.querySelectorAll(".side a")];
addEventListener("scroll",()=>{let i=0;arts.forEach((a,j)=>{if(a.getBoundingClientRect().top<160)i=j});links.forEach((l,j)=>l.classList.toggle("on",j===i))},{passive:true});

/* hero background: drifting candlesticks */
(function(){const cv=$("#bg"),cx=cv.getContext("2d");let W,H,cols=[];const dpr=Math.min(devicePixelRatio||1,2);
 function init(){W=cv.width=cv.offsetWidth*dpr;H=cv.height=cv.offsetHeight*dpr;cols=[];const n=Math.ceil(W/(16*dpr))+2;
  [0,1].forEach(side=>{let p=H*(side?.42:.58);for(let i=0;i<n;i++){const o=p,c=o+(Math.random()-.5+(side?.02:-.02))*H*.05;cols.push({side,i,o,c,h:Math.max(o,c)+Math.random()*H*.02,l:Math.min(o,c)-Math.random()*H*.02});p=c}})}
 let off=0,last=performance.now();
 function frame(n){const dt=n-last;last=n;off+=dt*.012*dpr;cx.clearRect(0,0,W,H);const step=16*dpr;
  cols.forEach(k=>{const x=((k.i*step-off)%(cols.length/2*step)+cols.length/2*step)%(cols.length/2*step)-step;
   const up=k.c<k.o,col=k.side?"111,156,255":"232,131,94",a=.18+.22*Math.sin((x/W)*Math.PI);
   cx.strokeStyle=`rgba(${col},${a})`;cx.fillStyle=`rgba(${col},${up?a:a*.35})`;cx.lineWidth=dpr;
   cx.beginPath();cx.moveTo(x+step/2,k.h);cx.lineTo(x+step/2,k.l);cx.stroke();
   const y=Math.min(k.o,k.c),h=Math.max(Math.abs(k.c-k.o),dpr);cx.fillRect(x+3*dpr,y,step-6*dpr,h);cx.strokeRect(x+3*dpr,y,step-6*dpr,h)});
  if(!matchMedia("(prefers-reduced-motion: reduce)").matches)requestAnimationFrame(frame)}
 init();addEventListener("resize",init);requestAnimationFrame(frame)})();
</script></body></html>"""
