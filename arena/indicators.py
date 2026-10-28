"""Compact per-symbol market snapshot for the decision prompt."""
from __future__ import annotations

import pandas as pd

from .feeds.base import Quote


def rsi(close: pd.Series, n: int = 14) -> float:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, 1e-9)
    return float((100 - 100 / (1 + rs)).iloc[-1])


def snapshot(q: Quote, bars: pd.DataFrame) -> dict:
    """bars: 5-minute OHLCV, oldest first."""
    snap = {
        "ltp": round(q.ltp, 2), "chg_pct": round(q.change_pct, 2),
        "day_open": round(q.open, 2), "day_high": round(q.high, 2), "day_low": round(q.low, 2),
    }
    if bars is None or len(bars) < 22:
        return snap
    c = bars["close"]
    today = bars[bars.index.date == bars.index[-1].date()]
    tp = (today["high"] + today["low"] + today["close"]) / 3
    vwap = float((tp * today["volume"]).sum() / max(today["volume"].sum(), 1))
    tr = pd.concat([bars["high"] - bars["low"], (bars["high"] - c.shift()).abs(), (bars["low"] - c.shift()).abs()], axis=1).max(axis=1)
    sma9, sma21 = c.rolling(9).mean(), c.rolling(21).mean()
    # The newest bar is usually still forming, so compare the last completed bar with the 20 before it.
    vol_avg = bars["volume"].iloc[-22:-2].mean()
    snap.update({
        "sma9": round(float(sma9.iloc[-1]), 2), "sma21": round(float(sma21.iloc[-1]), 2),
        "sma9_cross": "up" if sma9.iloc[-2] <= sma21.iloc[-2] and sma9.iloc[-1] > sma21.iloc[-1]
        else "down" if sma9.iloc[-2] >= sma21.iloc[-2] and sma9.iloc[-1] < sma21.iloc[-1] else "none",
        "rsi14": round(rsi(c), 1),
        "vwap": round(vwap, 2), "above_vwap": bool(q.ltp > vwap),
        "atr_pct": round(float(tr.rolling(14).mean().iloc[-1] / c.iloc[-1] * 100), 2),
        "mom_30m_pct": round(float((c.iloc[-1] / c.iloc[-7] - 1) * 100), 2),
        "vol_ratio": round(float(bars["volume"].iloc[-2] / vol_avg), 2) if vol_avg else None,
        # Opening range = first 30 minutes (09:15-09:45). Breakouts above it are a well-known intraday setup.
        **_opening_range(today),
        "last6_closes": [round(float(x), 2) for x in c.iloc[-6:]],
    })
    return snap


def _opening_range(today: pd.DataFrame) -> dict:
    t = today.index.time
    first = today[[(x.hour, x.minute) < (9, 45) for x in t]]
    if len(first) < 2 or len(first) == len(today):
        return {"or_high": None, "or_low": None}
    return {"or_high": round(float(first["high"].max()), 2), "or_low": round(float(first["low"].min()), 2)}
