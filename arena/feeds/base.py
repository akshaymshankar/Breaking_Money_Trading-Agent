from __future__ import annotations

import time as _time
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from ..clock import IST, now_ist
from ..markets import IN, Market


@dataclass
class Quote:
    symbol: str
    ltp: float
    open: float
    high: float
    low: float
    prev_close: float
    volume: float          # cumulative volume today
    ts: datetime
    bar_volume: float = 0  # volume of the latest bar, used to cap simulated fill size

    @property
    def change_pct(self) -> float:
        return (self.ltp / self.prev_close - 1) * 100 if self.prev_close else 0.0


CANDLE_COLS = ["open", "high", "low", "close", "volume"]


class PriceFeed:
    """Interface every market-data adapter implements."""

    name = "base"
    live = True
    market: Market = IN

    def now(self) -> datetime:
        return self.market.now()

    def sleep(self, seconds: float) -> None:
        _time.sleep(seconds)

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        raise NotImplementedError

    def candles(self, symbol: str, interval_min: int = 5, days: int = 3) -> pd.DataFrame:
        """OHLCV bars indexed by IST timestamp, oldest first, nothing after now()."""
        raise NotImplementedError

    def finished(self) -> bool:
        return False


class BarReplayFeed(PriceFeed):
    """Replays 1-minute or 5-minute bars on a virtual clock.

    Only data at or before the virtual clock is ever exposed, so a strategy can
    never see a future price. Used for historical replay and the offline demo.
    """

    name = "replay"
    live = False

    def __init__(self, bars: dict[str, pd.DataFrame], start: datetime | None = None, end: datetime | None = None,
                 market: Market = IN):
        self.market = market
        self.bars = {s: df.sort_index() for s, df in bars.items() if not df.empty}
        if not self.bars:
            raise ValueError("No bars to replay.")
        all_ts = sorted(set().union(*[set(df.index) for df in self.bars.values()]))
        self.timeline = [t for t in all_ts if (start is None or t >= start) and (end is None or t <= end)]
        if not self.timeline:
            raise ValueError("No bars inside the requested replay window.")
        self._i = 0
        self._step = self._infer_step()

    def _infer_step(self) -> timedelta:
        df = next(iter(self.bars.values()))
        diffs = pd.Series(df.index).diff().dropna()
        return diffs.min() if len(diffs) else timedelta(minutes=1)

    def now(self) -> datetime:
        return self.timeline[min(self._i, len(self.timeline) - 1)] + self._step  # bar close time

    def sleep(self, seconds: float) -> None:
        target = self.now() + timedelta(seconds=max(seconds, 1))
        while self._i < len(self.timeline) - 1 and self.timeline[self._i] + self._step < target:
            self._i += 1
            if self.timeline[self._i].date() != self.timeline[self._i - 1].date():
                break  # stop at day boundaries so the runner can close the day
        if self._i >= len(self.timeline) - 1 and self.timeline[-1] + self._step < target:
            self._i = len(self.timeline)  # exhausted

    def finished(self) -> bool:
        return self._i >= len(self.timeline)

    def _visible(self, symbol: str) -> pd.DataFrame:
        df = self.bars.get(symbol)
        if df is None:
            return pd.DataFrame(columns=CANDLE_COLS)
        cur = self.timeline[min(self._i, len(self.timeline) - 1)]
        return df.loc[:cur]

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        out = {}
        for s in symbols:
            df = self._visible(s)
            if df.empty:
                continue
            last = df.iloc[-1]
            day = df[df.index.date == df.index[-1].date()]
            prev = df[df.index.date < df.index[-1].date()]
            prev_close = float(prev["close"].iloc[-1]) if len(prev) else float(day["open"].iloc[0])
            out[s] = Quote(
                symbol=s, ltp=float(last["close"]), open=float(day["open"].iloc[0]),
                high=float(day["high"].max()), low=float(day["low"].min()), prev_close=prev_close,
                volume=float(day["volume"].sum()), ts=df.index[-1].to_pydatetime(), bar_volume=float(last["volume"]),
            )
        return out

    def candles(self, symbol: str, interval_min: int = 5, days: int = 3) -> pd.DataFrame:
        df = self._visible(symbol)
        if df.empty:
            return df
        dates = sorted(set(df.index.date))[-days:]
        df = df[pd.Index(df.index.date).isin(dates)]
        return resample(df, interval_min, self.market.open.minute)


def resample(df: pd.DataFrame, interval_min: int, offset_min: int = 15) -> pd.DataFrame:
    if df.empty:
        return df
    rule = f"{interval_min}min"
    out = df.resample(rule, origin="start_day", offset=f"{offset_min % interval_min}min", label="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna(subset=["close"])


def to_ist_index(df: pd.DataFrame, tz=IST) -> pd.DataFrame:
    idx = pd.DatetimeIndex(df.index)
    idx = idx.tz_localize(tz) if idx.tz is None else idx.tz_convert(tz)
    df = df.copy()
    df.index = idx
    return df
