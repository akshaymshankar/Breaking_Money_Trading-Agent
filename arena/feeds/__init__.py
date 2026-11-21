from __future__ import annotations

from pathlib import Path

import pandas as pd

from .base import BarReplayFeed, PriceFeed, Quote, to_ist_index


def make_live_feed(cfg: dict) -> PriceFeed:
    from ..markets import market_of
    mk = market_of(cfg)
    name = cfg["market"]["feed"]
    if name == "upstox" and mk.code == "IN":
        from .upstox import UpstoxFeed
        return UpstoxFeed(cache_dir=cfg["paths"]["data_dir"])
    if name == "yahoo":
        from .yahoo import YahooFeed
        return YahooFeed(mk)
    raise ValueError(f"Unknown live feed '{name}'. Use 'upstox' or 'yahoo'.")


def history_dir(cfg: dict) -> Path:
    return Path(cfg["paths"]["data_dir"]) / "history"


def save_history(cfg: dict, bars: dict[str, pd.DataFrame]) -> list[Path]:
    d = history_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    paths = []
    for sym, df in bars.items():
        p = d / f"{sym}.csv"
        if p.exists():  # merge with what we already have so history accumulates
            old = load_csv(p)
            df = pd.concat([old, df])
            df = df[~df.index.duplicated(keep="last")].sort_index()
        df.to_csv(p, index_label="ts")
        paths.append(p)
    return paths


def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, index_col="ts")
    df.index = pd.to_datetime(df.index, utc=True)
    return to_ist_index(df)


def load_history(cfg: dict, symbols: list[str]) -> dict[str, pd.DataFrame]:
    d = history_dir(cfg)
    out = {}
    for s in symbols:
        p = d / f"{s}.csv"
        if p.exists():
            out[s] = load_csv(p)
    return out
