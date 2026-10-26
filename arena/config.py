from __future__ import annotations

import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path = ROOT / ".env") -> None:
    """Minimal .env reader (KEY=value lines). Existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def set_env_value(key: str, value: str, path: Path = ROOT / ".env") -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    lines = [l for l in lines if not l.startswith(key + "=")] + [f"{key}={value}"]
    path.write_text("\n".join(lines) + "\n")
    os.environ[key] = value


def load_config(path: str | Path | None = None) -> dict:
    p = Path(path) if path else ROOT / "config.toml"
    with open(p, "rb") as f:
        cfg = tomllib.load(f)
    for k, v in cfg.get("paths", {}).items():  # paths are relative to the project folder
        if not Path(v).is_absolute():
            cfg["paths"][k] = str(ROOT / v)
    return apply_profile(cfg)


# Strategy profiles. "balanced" uses config.toml as written; others override it.
PROFILES = {
    "aggressive": {
        # High stakes: few, concentrated positions with large size...
        "max_positions": 3,
        "max_position_pct": 35,
        "max_daily_loss_pct": 6.0,
        # ...but only A+ momentum breakouts, with quick profit-taking to keep the win rate high.
        "min_confidence": 0.75,
        "default_stop_loss_pct": 1.2,
        "default_target_pct": 1.2,
        "profile_notes": (
            "AGGRESSIVE PROFILE: play for high stakes. Concentrate capital into at most 3 positions at up to 35% "
            "of equity each, but ONLY on A+ momentum breakouts: price above VWAP and near the day's high, SMA9 above "
            "SMA21, RSI 55-72, strong 30-minute momentum and volume at least 1.2x average. Give confidence >= 0.75 "
            "only to such setups; anything weaker is HOLD. Take profits fast (target about equal to the stop) to keep "
            "the win rate high, and SELL immediately if momentum stalls or price loses VWAP."
        ),
    },
    "allin": {
        # Maximum stakes: one position at a time, nearly all the cash, at least Rs 50,000 per trade.
        "max_positions": 1,
        "max_position_pct": 100,
        "allow_short": True,
        "min_trade_value": 50000,
        "max_daily_loss_pct": 8.0,
        "min_confidence": 0.8,
        "default_stop_loss_pct": 1.0,
        "default_target_pct": 2.0,
        "profile_notes": (
            "ALL-IN PROFILE: one trade at a time with the full account (allocation_pct 100, Rs 50,000 minimum), so every call is a big bet. "
            "Trade the single best setup in either direction. Long: an opening-range breakout (price above or_high after 09:45) with "
            "price above VWAP, SMA9 above SMA21, RSI 55-75 and volume at least 1.2x average. Short: an opening-range breakdown "
            "(price below or_low after 09:45) with price below VWAP, SMA9 below SMA21, RSI 25-45 and volume at least 1.2x. "
            "Give confidence >= 0.8 only to such a setup; otherwise HOLD. Use a stop about 1% against entry and a target about 2% in favour. "
            "SELL at once if price moves back inside the opening range or crosses VWAP against you. Skipping a day is fine."
        ),
    },
}


def apply_profile(cfg: dict) -> dict:
    st = cfg["strategy"]
    st.setdefault("min_confidence", 0.0)
    st.setdefault("min_trade_value", 0)
    st.setdefault("profile", "balanced")
    for k, v in PROFILES.get(st["profile"], {}).items():
        st[k] = v
    return cfg
