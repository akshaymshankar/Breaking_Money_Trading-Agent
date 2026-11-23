"""Regime Analyst: is this a bull, neutral, bear or panic market right now?

Deterministic. Scores from -100 (very bearish) to +100 (very bullish) using price
facts only: the watchlist's breadth, and, in a live session, the index and VIX.
News and emotion are shown next to it but never decide it, so a scary headline
on its own cannot open the door to short selling.
"""
from __future__ import annotations

import logging

from .emotion import clamp

log = logging.getLogger("arena")

VIX_SYMBOL = {"US": "^VIX", "IN": "^INDIAVIX"}
BEAR, BULL = -25, 25


def gauges(region: str) -> dict:
    """Index trend and VIX from daily bars (live sessions only; cached 10 minutes)."""
    from .. import marketdata
    from ..markets import MARKETS
    mk = MARKETS[region]

    def fetch():
        import yfinance as yf
        out: dict = {"index": marketdata.NAMES.get(mk.index_symbol, mk.index_symbol)}
        df = yf.Ticker(mk.index_symbol).history(period="1y", interval="1d", auto_adjust=False)
        c = df["Close"].dropna() if df is not None and not df.empty else []
        if len(c) >= 60:
            last = float(c.iloc[-1])
            out.update(last=round(last, 2), sma20=round(float(c.iloc[-20:].mean()), 2), sma50=round(float(c.iloc[-50:].mean()), 2),
                       sma200=round(float(c.iloc[-200:].mean()), 2) if len(c) >= 200 else None,
                       ret_1d=round((last / float(c.iloc[-2]) - 1) * 100, 2), ret_5d=round((last / float(c.iloc[-6]) - 1) * 100, 2),
                       off_high_pct=round((last / float(c.max()) - 1) * 100, 2))
        try:
            v = yf.Ticker(VIX_SYMBOL[region]).history(period="1mo", interval="1d", auto_adjust=False)["Close"].dropna()
            if len(v) >= 2:
                out.update(vix=round(float(v.iloc[-1]), 2), vix_chg_pct=round((float(v.iloc[-1]) / float(v.iloc[-2]) - 1) * 100, 1))
        except Exception as e:
            log.info("VIX unavailable: %s", str(e)[:100])
        return out

    return marketdata._cached(("gauges", region), 600, fetch)


def breadth(snapshot: dict) -> dict:
    """What the watchlist itself is doing. Works in replay too: it only sees current bars."""
    rows = [s for s in snapshot.values() if "sma9" in s]
    chg = [s["chg_pct"] for s in snapshot.values() if s.get("chg_pct") is not None]
    if not chg:
        return {"n": 0}
    out = {"n": len(chg), "avg_chg_pct": round(sum(chg) / len(chg), 2), "falling_pct": round(100 * sum(c < 0 for c in chg) / len(chg))}
    if rows:
        out["below_vwap_pct"] = round(100 * sum(not r["above_vwap"] for r in rows) / len(rows))
        out["downtrend_pct"] = round(100 * sum(r["sma9"] < r["sma21"] for r in rows) / len(rows))
    return out


def assess(snapshot: dict, g: dict | None = None) -> dict:
    g = g or {}
    b = breadth(snapshot)
    parts: list[tuple[str, float, float]] = []  # (what, weight, score -1..1)
    if b["n"]:
        parts.append(("watchlist move", 20, clamp(b["avg_chg_pct"] / 1.5, -1, 1)))
        parts.append(("stocks falling", 10, (50 - b["falling_pct"]) / 50))
    if "below_vwap_pct" in b:
        parts.append(("price vs VWAP", 15, (50 - b["below_vwap_pct"]) / 50))
        parts.append(("short-term trend", 15, (50 - b["downtrend_pct"]) / 50))
    if g.get("last"):
        parts.append(("index vs 20-day", 12, clamp((g["last"] / g["sma20"] - 1) * 100 / 2, -1, 1)))
        parts.append(("20-day vs 50-day", 10, clamp((g["sma20"] / g["sma50"] - 1) * 100 / 2, -1, 1)))
        parts.append(("index 5-day move", 10, clamp(g["ret_5d"] / 3, -1, 1)))
    if g.get("vix"):
        parts.append(("VIX", 8, clamp((20 - g["vix"]) / 10, -1, 1)))
    total = sum(w for _, w, _ in parts)
    score = round(100 * sum(w * s for _, w, s in parts) / total) if total else 0
    # Panic needs a deep bearish score and a shock, not just a bad score.
    shock = (g.get("vix") or 0) >= 30 or (g.get("vix_chg_pct") or 0) >= 25 or (b.get("avg_chg_pct") or 0) <= -2.5
    label = "panic" if score <= -50 and shock else "bear" if score <= BEAR else "bull" if score >= BULL else "neutral"
    top = sorted(parts, key=lambda p: -abs(p[1] * p[2]))[:3]
    why = ", ".join(f"{n} {'+' if s > 0 else '−' if s < 0 else ''}{abs(round(100 * w * s / total))}" for n, w, s in top) if total else "no data yet"
    return {"label": label, "score": score, "why": why, "breadth": b}


def shorts_open(label: str, mode: str) -> bool:
    return mode == "always" or (mode == "bear_only" and label in ("bear", "panic"))
