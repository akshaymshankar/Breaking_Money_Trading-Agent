"""Exchanges the arena can paper-trade: US (NYSE/Nasdaq) and India (NSE).

A Market bundles everything that differs between exchanges: timezone,
session hours, currency, Yahoo ticker suffix, tick size and default
risk timings. Everything else (engine, runner, brain) is market-agnostic.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Market:
    code: str
    name: str
    exchange: str
    tz_name: str
    open: time
    close: time
    currency: str
    symbol: str
    yahoo_suffix: str
    tick: float
    square_off: str
    cutoff: str
    locale: str
    watchlist: tuple = field(default_factory=tuple)
    index_symbol: str = ""

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)

    @property
    def tz_abbr(self) -> str:
        return self.now().strftime("%Z") or self.tz_name

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def is_trading_day(self, d: date, holidays: set[str]) -> bool:
        return d.weekday() < 5 and d.isoformat() not in holidays

    def is_open(self, ts: datetime, holidays: set[str]) -> bool:
        ts = ts.astimezone(self.tz)
        return self.is_trading_day(ts.date(), holidays) and self.open <= ts.time() < self.close

    def next_open(self, ts: datetime, holidays: set[str]) -> datetime:
        ts = ts.astimezone(self.tz)
        d = ts.date()
        if ts.time() >= self.open:
            d += timedelta(days=1)
        while not self.is_trading_day(d, holidays):
            d += timedelta(days=1)
        return datetime.combine(d, self.open, tzinfo=self.tz)

    def money(self, x: float, sign: bool = False, decimals: int = 2) -> str:
        s = f"{abs(x):,.{decimals}f}"
        if sign:
            return ("+" if x >= 0 else "−") + self.symbol + s
        return ("−" if x < 0 else "") + self.symbol + s


US = Market(
    code="US", name="United States", exchange="NYSE · Nasdaq", tz_name="America/New_York",
    open=time(9, 30), close=time(16, 0), currency="USD", symbol="$", yahoo_suffix="",
    tick=0.01, square_off="15:50", cutoff="15:20", locale="en-US",
    watchlist=("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "JPM", "SPY"),
    index_symbol="^GSPC",
)

IN = Market(
    code="IN", name="India", exchange="NSE", tz_name="Asia/Kolkata",
    open=time(9, 15), close=time(15, 30), currency="INR", symbol="₹", yahoo_suffix=".NS",
    tick=0.05, square_off="15:15", cutoff="14:45", locale="en-IN",
    watchlist=("RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "BHARTIARTL", "ITC", "LT", "AXISBANK"),
    index_symbol="^NSEI",
)

MARKETS: dict[str, Market] = {"US": US, "IN": IN}


def market_of(cfg: dict) -> Market:
    return MARKETS.get(str(cfg.get("market", {}).get("region", "IN")).upper(), IN)


def apply_market(cfg: dict, region: str | None = None) -> dict:
    """Resolve region-specific settings (watchlist, holidays, session timings) in place."""
    m = cfg.setdefault("market", {})
    if region:
        m["region"] = region.upper()
    st = cfg.setdefault("strategy", {})
    # First resolution: the un-suffixed keys in config.toml are the India (NSE) values. Stash them
    # under explicit _in keys so re-resolving for another region never mixes markets.
    if "watchlist_in" not in m:
        m["watchlist_in"] = list(m.get("watchlist") or IN.watchlist)
        m["holidays_in"] = list(m.get("holidays", []))
        m["feed_in"] = m.get("feed", "yahoo")
        st["square_off_time_in"] = st.get("square_off_time", IN.square_off)
        st["no_new_entries_after_in"] = st.get("no_new_entries_after", IN.cutoff)
    mk = market_of(cfg)
    m["region"] = mk.code
    key = mk.code.lower()
    m["watchlist"] = list(m.get(f"watchlist_{key}") or mk.watchlist)
    m["holidays"] = list(m.get(f"holidays_{key}", []))
    m["feed"] = m.get("feed_in", "yahoo") if mk.code == "IN" else "yahoo"  # Upstox is NSE only
    if m["feed"] not in ("yahoo", "upstox"):
        m["feed"] = "yahoo"
    st["square_off_time"] = st.get(f"square_off_time_{key}", mk.square_off)
    st["no_new_entries_after"] = st.get(f"no_new_entries_after_{key}", mk.cutoff)
    return cfg


def for_region(cfg: dict, region: str) -> dict:
    return apply_market(copy.deepcopy(cfg), region)
