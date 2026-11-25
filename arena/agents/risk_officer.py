"""Risk Officer: the last check before the paper engine. Plain rules, no AI.

Gets one decision, returns (approved decision or None, reason). It may shrink a
trade or refuse it; it never makes one bigger and never blocks an exit.
"""
from __future__ import annotations

from ..engine import short_setting
from .emotion import clamp

ENTRIES = ("BUY", "SHORT")
EXITS = ("SELL", "COVER")


def review(d: dict, ctx: dict, st: dict, market_code: str) -> tuple[dict | None, str]:
    act = d.get("action")
    if act not in ENTRIES:
        return d, ""
    if not ctx.get("entries_allowed"):
        return None, "entries closed"
    conf = clamp(d.get("confidence"), 0, 1)
    if conf < st.get("min_confidence", 0):
        return None, f"confidence below {st['min_confidence']}"
    regime = (ctx.get("regime") or {}).get("label", "neutral")
    if act == "BUY":
        if regime == "panic":
            return {**d, "allocation_pct": clamp(d.get("allocation_pct"), 0, 100) / 2}, "panic market: size halved"
        return d, ""
    return _short(d, ctx, st, market_code, conf)


def _short(d: dict, ctx: dict, st: dict, market_code: str, conf: float) -> tuple[dict | None, str]:
    r = ctx.get("regime") or {}
    snap = (ctx.get("snapshot") or {}).get(d.get("symbol")) or {}
    if "off" in (short_setting(st, "short_mode"), r.get("short_mode")):
        return None, "short selling is switched off"
    if st.get("product") != "intraday":
        return None, "shorts are intraday only"
    if not r.get("shorts_allowed"):
        return None, f"shorts only open in a bear market (now {r.get('label', 'unknown')}, score {r.get('score', 0)})"
    need = short_setting(st, "short_min_confidence")
    if conf < need:
        return None, f"confidence below {need} for a short"
    if market_code == "US" and (snap.get("chg_pct") or 0) <= -10:
        return None, "down 10% or more today: US short-sale restriction (Rule 201)"
    if snap.get("rsi14") is not None and snap["rsi14"] < 25:
        return None, f"RSI {snap['rsi14']} is washed out: high risk of a squeeze"
    tilt = ((ctx.get("intel") or {}).get("symbol_tilts") or {}).get(d.get("symbol"), 0)
    if tilt >= 0.6:
        return None, f"news on this stock is strongly positive ({tilt:+.1f})"
    if sum(p.get("side") == "SHORT" for p in ctx.get("positions", [])) >= short_setting(st, "max_shorts"):
        return None, "max open shorts reached"
    out = dict(d)
    cap = short_setting(st, "max_short_position_pct")
    notes = []
    if clamp(d.get("allocation_pct"), 0, 100) > cap:
        notes.append(f"size cut to {cap:g}%")
    out["allocation_pct"] = min(clamp(d.get("allocation_pct"), 0, 100), cap)
    if r.get("label") == "panic":  # late shorts in a panic get squeezed: half size
        out["allocation_pct"] /= 2
        notes.append("panic market: size halved")
    stop = clamp(d.get("stop_loss_pct"), 0, 100) or short_setting(st, "short_default_stop_pct")
    if stop > short_setting(st, "short_max_stop_pct"):
        stop = short_setting(st, "short_max_stop_pct")
        notes.append(f"stop tightened to {stop:g}%")
    out["stop_loss_pct"] = stop
    out["target_pct"] = clamp(d.get("target_pct"), 0, 50) or short_setting(st, "short_default_target_pct")
    return out, ", ".join(notes)
