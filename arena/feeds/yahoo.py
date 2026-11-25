"""Yahoo Finance via yfinance. Free, no signup, unofficial, personal use only.

Prices for NSE symbols can lag the exchange slightly. Good for getting started,
for historical replay data, and as a fallback when Upstox is not configured.
"""
from __future__ import annotations

import time as _time
from datetime import datetime

import pandas as pd

from .. import compliance
from ..markets import IN, Market
from .base import CANDLE_COLS, PriceFeed, Quote, resample, to_ist_index


class YahooFeed(PriceFeed):
    name = "yahoo"
    live = True

    def __init__(self, market: Market = IN, cache_seconds: int = 20):
        import yfinance  # noqa: F401  (fail early with a clear error if missing)

        self.market = market
        self.name = "yahoo-" + market.code.lower()
        self.suffix = market.yahoo_suffix
        self.cache_seconds = cache_seconds
        self._cache: dict[str, pd.DataFrame] = {}
        self._cache_at = 0.0
        self._cache_syms: frozenset = frozenset()

    def _download(self, symbols: list[str], period: str = "5d", interval: str = "1m") -> dict[str, pd.DataFrame]:
        import yfinance as yf

        symbols = compliance.allowed(symbols, self.market)
        if not symbols:
            return {}
        tickers = [s + self.suffix for s in symbols]
        raw = yf.download(tickers, period=period, interval=interval, group_by="ticker",
                          auto_adjust=False, progress=False, threads=True)
        out = {}
        for s, t in zip(symbols, tickers):
            try:
                df = raw[t] if len(tickers) > 1 else raw
                if isinstance(df.columns, pd.MultiIndex):
                    df = df.droplevel(0, axis=1) if t in df.columns.get_level_values(0) else df.droplevel(1, axis=1)
                df = df.rename(columns=str.lower)[CANDLE_COLS].dropna(subset=["close"])
                out[s] = compliance.clip(to_ist_index(df, self.market.tz), self.market)
            except KeyError:
                continue
        return out

    def _bars(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        key = frozenset(symbols)
        if _time.time() - self._cache_at > self.cache_seconds or not key <= self._cache_syms:
            self._cache = self._download(sorted(key | self._cache_syms))
            self._cache_syms = key | self._cache_syms
            self._cache_at = _time.time()
        return self._cache

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        bars = self._bars(symbols)
        out = {}
        for s in symbols:
            df = bars.get(s)
            if df is None or df.empty:
                continue
            last = df.iloc[-1]
            today = df.index[-1].date()
            day = df[df.index.date == today]
            prev = df[df.index.date < today]
            out[s] = Quote(
                symbol=s, ltp=float(last["close"]), open=float(day["open"].iloc[0]),
                high=float(day["high"].max()), low=float(day["low"].min()),
                prev_close=float(prev["close"].iloc[-1]) if len(prev) else float(day["open"].iloc[0]),
                volume=float(day["volume"].sum()), ts=df.index[-1].to_pydatetime(), bar_volume=float(last["volume"]),
            )
        return out

    def candles(self, symbol: str, interval_min: int = 5, days: int = 3) -> pd.DataFrame:
        df = self._bars([symbol]).get(symbol)
        if df is None or df.empty:
            return pd.DataFrame(columns=CANDLE_COLS)
        dates = sorted(set(df.index.date))[-days:]
        return resample(df[pd.Index(df.index.date).isin(dates)], interval_min, self.market.open.minute)

    def history(self, symbols: list[str], period: str = "60d", interval: str = "5m") -> dict[str, pd.DataFrame]:
        """Bulk download for replay. Yahoo keeps ~7 days of 1m and ~60 days of 5m bars."""
        return self._download(symbols, period=period, interval=interval)
