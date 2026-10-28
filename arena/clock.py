"""NSE market calendar helpers (IST)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    IST = ZoneInfo("Asia/Kolkata")
except ZoneInfoNotFoundError:  # Windows without the tzdata package; India has no DST
    IST = timezone(timedelta(hours=5, minutes=30), "IST")
OPEN = time(9, 15)
CLOSE = time(15, 30)


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def now_ist() -> datetime:
    return datetime.now(IST)


def is_trading_day(d: date, holidays: set[str]) -> bool:
    return d.weekday() < 5 and d.isoformat() not in holidays


def is_market_open(ts: datetime, holidays: set[str]) -> bool:
    return is_trading_day(ts.date(), holidays) and OPEN <= ts.timetz().replace(tzinfo=None) < CLOSE


def next_open(ts: datetime, holidays: set[str]) -> datetime:
    d = ts.date()
    if ts.timetz().replace(tzinfo=None) >= OPEN:
        d += timedelta(days=1)
    while not is_trading_day(d, holidays):
        d += timedelta(days=1)
    return datetime.combine(d, OPEN, tzinfo=IST)


def trading_days(start: date, end: date, holidays: set[str]) -> list[date]:
    out, d = [], start
    while d <= end:
        if is_trading_day(d, holidays):
            out.append(d)
        d += timedelta(days=1)
    return out
