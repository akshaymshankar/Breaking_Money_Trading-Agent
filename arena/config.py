from __future__ import annotations

import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path = ROOT / ".env") -> None:
    """Minimal .env reader (KEY=value lines). Existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def set_env_value(key: str, value: str, path: Path = ROOT / ".env") -> None:
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    lines = [l for l in lines if not l.startswith(key + "=")] + [f"{key}={value}"]
    path.write_text("\n".join(lines) + "\n")
    os.environ[key] = value


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> dict:
    """overrides: {"section": {"key": value}} applied before profile/market resolution."""
    p = Path(path) if path else ROOT / "config.toml"
    with open(p, "rb") as f:
        cfg = tomllib.load(f)
    for section, vals in (overrides or {}).items():
        cfg.setdefault(section, {}).update({k: v for k, v in vals.items() if v is not None})
    for k, v in cfg.get("paths", {}).items():  # paths are relative to the project folder
        if not Path(v).is_absolute():
            cfg["paths"][k] = str(ROOT / v)
    from .markets import apply_market
    return apply_market(apply_profile(cfg))


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
}


def apply_profile(cfg: dict) -> dict:
    st = cfg["strategy"]
    st.setdefault("min_confidence", 0.0)
    st.setdefault("profile", "balanced")
    for k, v in PROFILES.get(st["profile"], {}).items():
        st[k] = v
    return cfg
