"""Market data for the dashboard: watchlist quotes, index strip and chart candles.

Free Yahoo Finance data (personal use; may lag the exchange by a few seconds
to minutes). Results are cached briefly so many browser polls cost one fetch.
"""
from __future__ import annotations

import threading
import time as _time

import pandas as pd

from .markets import MARKETS, Market

NAMES = {
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "AMZN": "Amazon", "GOOGL": "Alphabet",
    "GOOG": "Alphabet", "META": "Meta Platforms", "TSLA": "Tesla", "AMD": "AMD", "JPM": "JPMorgan Chase",
    "SPY": "S&P 500 ETF", "QQQ": "Nasdaq-100 ETF", "NFLX": "Netflix", "AVGO": "Broadcom", "COST": "Costco",
    "V": "Visa", "MA": "Mastercard", "BAC": "Bank of America", "XOM": "Exxon Mobil", "WMT": "Walmart",
    "PLTR": "Palantir", "INTC": "Intel", "CRM": "Salesforce", "ORCL": "Oracle", "UBER": "Uber",
    "RELIANCE": "Reliance Industries", "TCS": "Tata Consultancy", "HDFCBANK": "HDFC Bank", "INFY": "Infosys",
    "ICICIBANK": "ICICI Bank", "SBIN": "State Bank of India", "BHARTIARTL": "Bharti Airtel", "ITC": "ITC",
    "LT": "Larsen & Toubro", "AXISBANK": "Axis Bank",
    "^GSPC": "S&P 500", "^IXIC": "Nasdaq", "^DJI": "Dow Jones", "^VIX": "VIX",
    "^NSEI": "Nifty 50", "^BSESN": "Sensex", "^NSEBANK": "Bank Nifty",
}

INDICES = {"US": ["^GSPC", "^IXIC", "^DJI", "^VIX"], "IN": ["^NSEI", "^BSESN", "^NSEBANK"]}

# UI interval -> (yfinance interval, period, resample minutes or None)
INTERVALS = {
    "1m": ("1m", "1d", None),
    "5m": ("5m", "5d", None),
    "15m": ("15m", "1mo", None),
    "1h": ("60m", "6mo", None),
    "1D": ("1d", "2y", None),
}

_cache: dict = {}
_lock = threading.Lock()


def _cached(key, ttl: float, fn):
    now = _time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (now, val)
    return val


def ticker(sym: str, mk: Market) -> str:
    return sym if sym.startswith("^") or "." in sym else sym + mk.yahoo_suffix


def _frame(raw: pd.DataFrame, t: str, many: bool) -> pd.DataFrame | None:
    try:
        df = raw[t] if many else raw
    except KeyError:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        lv0 = df.columns.get_level_values(0)
        df = df.droplevel(0, axis=1) if t in lv0 else df.droplevel(1, axis=1)
    df = df.rename(columns=str.lower)
    cols = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    df = df[cols].dropna(subset=["close"])
    return df if not df.empty else None


def quotes(region: str, symbols: list[str], ttl: float = 15) -> dict:
    """Last price, day change, day range and an intraday sparkline for each symbol."""
    mk = MARKETS[region]
    symbols = [s.strip().upper() for s in symbols if s.strip()]

    def fetch():
        import yfinance as yf
        tickers = [ticker(s, mk) for s in symbols]
        if not tickers:
            return {}
        raw = yf.download(tickers, period="5d", interval="5m", group_by="ticker", auto_adjust=False,
                          progress=False, threads=True, prepost=False)
        # Official closes (the last 5-minute bar misses the closing auction).
        try:
            daily = yf.download(tickers, period="10d", interval="1d", group_by="ticker", auto_adjust=False,
                                progress=False, threads=True)
        except Exception:
            daily = None
        out = {}
        for s, t in zip(symbols, tickers):
            df = _frame(raw, t, len(tickers) > 1)
            if df is None:
                continue
            idx = df.index.tz_convert(mk.tz) if df.index.tz is not None else df.index.tz_localize(mk.tz)
            df.index = idx
            last_day = idx[-1].date()
            day = df[idx.date == last_day]
            prev = df[idx.date < last_day]
            ltp = float(df["close"].iloc[-1])
            prev_close = float(prev["close"].iloc[-1]) if len(prev) else float(day["open"].iloc[0])
            dd = _frame(daily, t, len(tickers) > 1) if daily is not None and not daily.empty else None
            if dd is not None:
                ddates = [pd.Timestamp(x).date() for x in dd.index]
                before = [i for i, x in enumerate(ddates) if x < last_day]
                if before:
                    prev_close = float(dd["close"].iloc[before[-1]])
                if ddates[-1] == last_day and not mk.is_open(mk.now(), set()):
                    ltp = float(dd["close"].iloc[-1])  # session over: show the official close
            closes = day["close"].tolist()
            step = max(1, len(closes) // 48)
            out[s] = {
                "symbol": s, "name": NAMES.get(s, s), "ltp": round(ltp, 2), "prev_close": round(prev_close, 2),
                "chg": round(ltp - prev_close, 2), "chg_pct": round((ltp / prev_close - 1) * 100, 2) if prev_close else 0,
                "open": round(float(day["open"].iloc[0]), 2), "high": round(float(day["high"].max()), 2),
                "low": round(float(day["low"].min()), 2),
                "volume": int(day["volume"].sum()) if "volume" in day else 0,
                "ts": idx[-1].isoformat(), "spark": [round(float(x), 2) for x in closes[::step]] + [round(ltp, 2)],
            }
        return out

    return _cached(("q", region, tuple(symbols)), ttl, fetch)


def indices(region: str) -> dict:
    return quotes(region, INDICES.get(region, []), ttl=30)


def candles(region: str, symbol: str, interval: str = "5m") -> dict:
    """OHLCV bars for the chart. `time` is shifted to exchange-local wall time so the
    chart's axis reads in the exchange's own hours (lightweight-charts shows UTC)."""
    mk = MARKETS[region]
    yf_int, period, _ = INTERVALS.get(interval, INTERVALS["5m"])
    ttl = 20 if interval in ("1m", "5m") else 120

    def fetch():
        import yfinance as yf
        df = yf.Ticker(ticker(symbol, mk)).history(period=period, interval=yf_int, auto_adjust=False, prepost=False)
        if df is None or df.empty:
            return {"symbol": symbol, "interval": interval, "bars": []}
        df = df.rename(columns=str.lower)
        idx = df.index.tz_convert(mk.tz) if df.index.tz is not None else df.index.tz_localize(mk.tz)
        bars = []
        for ts, r in zip(idx, df.itertuples()):
            if pd.isna(r.close):
                continue
            local = int(ts.replace(tzinfo=None).timestamp()) if interval != "1D" else int(
                pd.Timestamp(ts.date()).timestamp())
            bars.append({"time": local, "open": round(float(r.open), 4), "high": round(float(r.high), 4),
                         "low": round(float(r.low), 4), "close": round(float(r.close), 4),
                         "volume": int(r.volume) if not pd.isna(r.volume) else 0})
        return {"symbol": symbol, "name": NAMES.get(symbol, symbol), "interval": interval, "tz": mk.tz_name, "bars": bars}

    return _cached(("c", region, symbol, interval), ttl, fetch)


def local_epoch(iso_ts: str, region: str) -> int:
    """Convert a journal timestamp to the same shifted epoch the chart uses."""
    mk = MARKETS[region]
    ts = pd.Timestamp(iso_ts)
    ts = ts.tz_convert(mk.tz) if ts.tzinfo else ts.tz_localize(mk.tz)
    return int(ts.replace(tzinfo=None).timestamp())
