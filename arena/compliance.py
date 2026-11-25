"""India price delay.

SEBI circular HO/47/17/12(11)2025-MRD-POD3/I/11107/2026 (8 May 2026, in force since 1 July 2026) sets a 30-day lag
for using market price data in education. So for Indian stocks this app shows, replays and trades on paper only with
prices from before the last 30 days. Live Indian sessions do not exist here.

`INDIA_LAG_DAYS` is a constant, not a setting, like `PAPER_ONLY` in safety.py. This lowers one risk. It does not make
the app approved by SEBI: see DISCLAIMER.md.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

import pandas as pd

from .markets import Market

INDIA_LAG_DAYS = 30

NOTE = ("India is practice only: this app uses Indian prices that are more than 30 days old. "
        "Use Practice on an older session, or the offline demo.")

# What a US session may ask for. Anything else (TCS.NS, RELIANCE.BO, ^NSEI, an ISIN) could be a live Indian price.
_US_SYMBOL = re.compile(r"[A-Z]{1,5}(\.[A-Z]|-[A-Z]{1,3})?")
_US_INDICES = {"^GSPC", "^IXIC", "^DJI", "^VIX", "^RUT", "^NDX"}


class DelayedMarket(RuntimeError):
    """Raised whenever something asks for live Indian prices."""


def delayed(market: Market | str) -> bool:
    code = market if isinstance(market, str) else market.code
    return code.upper() != "US"  # only the US is live; anything unknown counts as India


def last_day(market: Market, now: datetime | None = None) -> date | None:
    """The newest trading date whose prices may be used, or None when there is no delay."""
    if not delayed(market):
        return None
    return (now or market.now()).astimezone(market.tz).date() - timedelta(days=INDIA_LAG_DAYS + 1)


def clip(df: pd.DataFrame, market: Market, now: datetime | None = None) -> pd.DataFrame:
    """Drop every bar that is too recent. The only door Indian prices come through."""
    cut = last_day(market, now)
    if cut is None or df is None or len(df) == 0:
        return df
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is not None:
        idx = idx.tz_convert(market.tz)
    return df[idx.date <= cut]


def us_symbol(symbol: str) -> bool:
    s = symbol.strip().upper()
    return s in _US_INDICES or bool(_US_SYMBOL.fullmatch(s))


def allowed(symbols: list[str], market: Market) -> list[str]:
    """In a US session, only US symbols. In a delayed market every symbol is fine, because its prices are clipped."""
    return list(symbols) if delayed(market) else [s for s in symbols if us_symbol(s)]


def recent_run(j, run: dict | None) -> bool:
    """A saved Indian session (from before this rule) whose prices are still inside the 30 days."""
    from .markets import MARKETS
    if not run or run["mode"].split("-")[0] not in ("live", "replay"):
        return False  # the demo and tests use made-up prices
    code = str((json.loads(run["config_json"] or "{}").get("market") or {}).get("region") or "IN").upper()
    if not delayed(code):
        return False
    last = [r["t"] for r in (j.one(f"SELECT MAX({col}) t FROM {table} WHERE run_id=?", (run["id"],))
                             for table, col in (("orders", "ts"), ("decisions", "ts"), ("equity", "ts"), ("trades", "exit_ts"))) if r["t"]]
    if not last:
        return False
    mk = MARKETS["IN"]
    ts = max(pd.Timestamp(t).tz_convert(mk.tz) if pd.Timestamp(t).tzinfo else pd.Timestamp(t).tz_localize(mk.tz) for t in last)
    return ts.date() > last_day(mk)


def refuse_recent(j, run: dict | None) -> None:
    if recent_run(j, run):
        raise DelayedMarket("This saved Indian session has prices from the last 30 days, so it stays hidden until they are older.")


def refuse_live(market: Market | str) -> None:
    if delayed(market):
        raise DelayedMarket(NOTE)
