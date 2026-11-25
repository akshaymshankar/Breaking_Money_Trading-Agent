"""Upstox market-data adapter (read-only).

Uses the user's own Upstox account and only the instrument-list,
historical-candle and login endpoints, through SafeHTTP. There is no order code here and
SafeHTTP refuses order URLs anyway.

Access tokens expire daily. Run `python -m arena upstox-login` each morning,
or paste a token into UPSTOX_ACCESS_TOKEN in your .env file.
"""
from __future__ import annotations

import gzip
import json
import os
import time as _time
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote as urlquote

import pandas as pd

from .. import compliance
from ..clock import now_ist
from ..markets import IN
from ..safety import SafeHTTP
from .base import CANDLE_COLS, PriceFeed, Quote, resample, to_ist_index

API = "https://api.upstox.com"
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"


class UpstoxFeed(PriceFeed):
    name = "upstox"
    live = True

    def __init__(self, access_token: str | None = None, cache_dir: str = "data"):
        self.token = access_token or os.environ.get("UPSTOX_ACCESS_TOKEN", "")
        if not self.token:
            raise RuntimeError("UPSTOX_ACCESS_TOKEN is not set. Run `python -m arena upstox-login` first.")
        self.http = SafeHTTP()
        self.cache_dir = Path(cache_dir)
        self._keys: dict[str, str] = {}
        self._candle_cache: dict[str, tuple[float, pd.DataFrame]] = {}

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    # ---- instruments -------------------------------------------------
    def instrument_key(self, symbol: str) -> str:
        if not self._keys:
            self._load_instruments()
        if symbol not in self._keys:
            raise KeyError(f"{symbol} not found in Upstox NSE equity instruments")
        return self._keys[symbol]

    def _load_instruments(self) -> None:
        path = self.cache_dir / "upstox_nse_instruments.json"
        if not path.exists() or _time.time() - path.stat().st_mtime > 86400:
            r = self.http.get(INSTRUMENTS_URL)
            r.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(gzip.decompress(r.content))
        for row in json.loads(path.read_text()):
            if row.get("segment") == "NSE_EQ" and row.get("instrument_type") == "EQ":
                self._keys[row["trading_symbol"]] = row["instrument_key"]

    # ---- quotes ------------------------------------------------------
    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        raise compliance.DelayedMarket(compliance.NOTE)  # live Indian prices are never used

    # ---- candles -----------------------------------------------------
    def _fetch_candles(self, url: str) -> pd.DataFrame:
        r = self.http.get(url, headers=self._headers)
        r.raise_for_status()
        rows = r.json().get("data", {}).get("candles", [])
        if not rows:
            return pd.DataFrame(columns=CANDLE_COLS)
        df = pd.DataFrame([row[:6] for row in rows], columns=["ts"] + CANDLE_COLS)
        df.index = pd.to_datetime(df.pop("ts"))
        return compliance.clip(to_ist_index(df.sort_index()), IN)

    def minute_bars(self, symbol: str, days: int = 3) -> pd.DataFrame:
        key = urlquote(self.instrument_key(symbol), safe="")
        today = now_ist().date()
        frm = today - timedelta(days=days + 4)
        hist = self._fetch_candles(f"{API}/v3/historical-candle/{key}/minutes/1/{today.isoformat()}/{frm.isoformat()}")
        intra = self._fetch_candles(f"{API}/v3/historical-candle/intraday/{key}/minutes/1")
        df = pd.concat([hist, intra])
        return df[~df.index.duplicated(keep="last")].sort_index()

    def candles(self, symbol: str, interval_min: int = 5, days: int = 3) -> pd.DataFrame:
        cached = self._candle_cache.get(symbol)
        if cached and _time.time() - cached[0] < 60:
            df = cached[1]
        else:
            df = self.minute_bars(symbol, days)
            self._candle_cache[symbol] = (_time.time(), df)
        if df.empty:
            return df
        dates = sorted(set(df.index.date))[-days:]
        return resample(df[pd.Index(df.index.date).isin(dates)], interval_min)

    def history(self, symbols: list[str], days: int = 30) -> dict[str, pd.DataFrame]:
        return {s: self.minute_bars(s, days) for s in symbols}


def login_flow(api_key: str, api_secret: str, redirect_uri: str) -> str:
    """Interactive OAuth: open URL, paste the `code` from the redirect, get a token."""
    url = (f"{API}/v2/login/authorization/dialog?response_type=code&client_id={urlquote(api_key)}"
           f"&redirect_uri={urlquote(redirect_uri, safe='')}")
    print("\n1) Open this URL and log in to Upstox:\n   " + url)
    print("2) After login you are redirected. Copy the `code=` value from the address bar.")
    code = input("Paste code: ").strip()
    r = SafeHTTP().post(f"{API}/v2/login/authorization/token", data={
        "code": code, "client_id": api_key, "client_secret": api_secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    }, headers={"Accept": "application/json"})
    r.raise_for_status()
    return r.json()["access_token"]
