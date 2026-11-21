"""Synthetic market for the offline demo and tests. Never used for real results."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from ..clock import trading_days
from ..markets import IN, Market
from .base import BarReplayFeed

BASE_PRICES = {
    "RELIANCE": 1450, "TCS": 3400, "HDFCBANK": 1700, "INFY": 1500, "ICICIBANK": 1400,
    "SBIN": 820, "BHARTIARTL": 1900, "ITC": 420, "LT": 3600, "AXISBANK": 1150,
    "AAPL": 230, "MSFT": 430, "NVDA": 140, "AMZN": 200, "GOOGL": 175, "META": 580,
    "TSLA": 250, "AMD": 150, "JPM": 220, "SPY": 580,
}


def synth_bars(symbols: list[str], days: list[date], seed: int = 7, market: Market = IN) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    out = {}
    for k, sym in enumerate(symbols):
        price = BASE_PRICES.get(sym, 500 + 100 * k)
        rows, idx = [], []
        for d in days:
            drift = rng.normal(0, 0.00003)       # per-minute drift for the day (trend regime)
            vol = rng.uniform(0.0008, 0.0018)    # per-minute volatility
            price *= 1 + rng.normal(0, 0.006)    # overnight gap
            start = datetime.combine(d, market.open, tzinfo=market.tz)
            minutes = int((datetime.combine(d, market.close) - datetime.combine(d, market.open)).total_seconds() // 60)
            for m in range(minutes):
                o = price
                c = o * (1 + drift + rng.normal(0, vol))
                h = max(o, c) * (1 + abs(rng.normal(0, vol / 3)))
                l = min(o, c) * (1 - abs(rng.normal(0, vol / 3)))
                u = 1.8 - 1.4 * np.sin(np.pi * m / minutes)  # U-shaped intraday volume
                v = int(rng.lognormal(9, 0.5) * u)
                rows.append((round(o, 2), round(h, 2), round(l, 2), round(c, 2), v))
                idx.append(start + timedelta(minutes=m))
                price = c
        out[sym] = pd.DataFrame(rows, index=pd.DatetimeIndex(idx), columns=["open", "high", "low", "close", "volume"])
    return out


class SimFeed(BarReplayFeed):
    name = "sim"

    def __init__(self, symbols: list[str], n_days: int = 10, start: date | None = None, holidays: set[str] = frozenset(),
                 seed: int = 7, market: Market = IN):
        start = start or date(2026, 10, 5)
        days = trading_days(start, start + timedelta(days=n_days * 2 + 7), set(holidays))[:n_days]
        super().__init__(synth_bars(symbols, days, seed, market), market=market)
