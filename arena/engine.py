"""Deterministic paper-trading engine.

Claude proposes; this code disposes. It sizes orders, checks cash and risk
limits, simulates a realistic fill (spread + slippage + size cap), charges
Indian or US fees, and journals everything. No real order is ever sent anywhere.

Short positions are stored with a negative quantity. Their sale proceeds are
held as collateral together with the short margin, so a short never gives the
account extra money to spend.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime

from . import charges as charges_mod
from .db import Journal
from .feeds.base import Quote
from .markets import market_of
from .safety import PAPER_ONLY

TICK = 0.05

# Used when a run was created before short selling existed (its saved config lacks these keys).
SHORT_DEFAULTS = {
    "short_mode": "bear_only",        # off | bear_only | always
    "short_margin_pct": 100.0,        # cash blocked on top of the sale proceeds (100 = no leverage)
    "short_maintenance_pct": 30.0,    # below this the engine covers by itself (margin call)
    "short_borrow_annual_pct": 3.0,   # stock-borrow fee, charged for the time held
    "max_short_position_pct": 10.0,   # % of equity per short
    "max_shorts": 2,
    "max_short_exposure_pct": 20.0,   # % of equity, all shorts together
    "short_min_confidence": 0.65,
    "short_max_stop_pct": 2.0,
    "short_default_stop_pct": 1.2,
    "short_default_target_pct": 2.0,
}


def short_setting(st: dict, key: str):
    return st.get(key, SHORT_DEFAULTS[key])


@dataclass
class Position:
    symbol: str
    qty: int
    avg_price: float
    stop: float
    target: float
    opened_at: str
    entry_charges: float
    entry_reason: str = ""
    last_price: float = 0.0

    @property
    def is_short(self) -> bool:
        return self.qty < 0

    @property
    def side(self) -> str:
        return "SHORT" if self.qty < 0 else "LONG"

    @property
    def unrealized_pct(self) -> float:
        if not self.avg_price:
            return 0.0
        move = ((self.last_price or self.avg_price) / self.avg_price - 1) * 100
        return -move if self.is_short else move

    @property
    def value(self) -> float:
        return self.qty * (self.last_price or self.avg_price)

    @property
    def unrealized(self) -> float:
        return self.qty * ((self.last_price or self.avg_price) - self.avg_price)


def round_tick(p: float, up: bool, tick: float = TICK) -> float:
    n = p / tick
    return round((math.ceil(n - 1e-9) if up else math.floor(n + 1e-9)) * tick, 2)


class PaperBroker:
    def __init__(self, journal: Journal, run_id: int, cfg: dict):
        assert PAPER_ONLY, "paper-only constant was tampered with"
        self.j, self.run_id, self.cfg = journal, run_id, cfg
        self.ex = cfg["execution"]
        self.st = cfg["strategy"]
        self.product = self.st["product"]
        self.market = market_of(cfg)
        self.tick = self.market.tick
        self.rates = cfg.get("charges_us", {}) if self.market.code == "US" else cfg.get("charges", {})
        acct = journal.one("SELECT * FROM account WHERE run_id=?", (run_id,))
        self.cash, self.realized = acct["cash"], acct["realized"]
        self.total_charges, self.total_slippage = acct["charges"], acct["slippage"]
        self.positions: dict[str, Position] = {
            r["symbol"]: Position(**{k: r[k] for k in Position.__dataclass_fields__})
            for r in journal.q("SELECT * FROM positions WHERE run_id=?", (run_id,))
        }

    # ---- state -------------------------------------------------------
    @property
    def holdings_value(self) -> float:
        return sum(p.value for p in self.positions.values())

    @property
    def unrealized(self) -> float:
        return sum(p.unrealized for p in self.positions.values())

    @property
    def equity(self) -> float:
        return self.cash + self.holdings_value

    @property
    def short_value(self) -> float:
        """What it would cost right now to buy back every short."""
        return sum(-p.value for p in self.positions.values() if p.is_short)

    @property
    def buying_power(self) -> float:
        """Cash free to use: short proceeds and short margin stay blocked."""
        return self.cash - self.short_value * (1 + short_setting(self.st, "short_margin_pct") / 100)

    def mark(self, quotes: dict[str, Quote]) -> None:
        for s, p in self.positions.items():
            if s in quotes:
                p.last_price = quotes[s].ltp
        self._save_positions()

    def _save_positions(self) -> None:
        self.j.x("DELETE FROM positions WHERE run_id=?", (self.run_id,))
        for p in self.positions.values():
            self.j.conn.execute(
                "INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?)",
                (self.run_id, p.symbol, p.qty, p.avg_price, p.stop, p.target, p.opened_at,
                 p.entry_charges, p.entry_reason, p.last_price),
            )
        self.j.x("UPDATE account SET cash=?, realized=?, charges=?, slippage=? WHERE run_id=?",
                 (self.cash, self.realized, self.total_charges, self.total_slippage, self.run_id))

    # ---- fills -------------------------------------------------------
    def fill_price(self, side: str, q: Quote, qty: int) -> float:
        """Price we would realistically get: cross half the spread, add slippage,
        and add market impact that grows with the order's share of bar volume."""
        bps = self.ex["spread_bps"] / 2 + self.ex["slippage_bps"]
        if q.bar_volume > 0:
            bps += self.ex["impact_bps_per_pct_of_volume"] * 100 * qty / q.bar_volume
        if side == "BUY":
            return round_tick(q.ltp * (1 + bps / 1e4), up=True, tick=self.tick)
        return round_tick(q.ltp * (1 - bps / 1e4), up=False, tick=self.tick)

    def max_qty_by_liquidity(self, q: Quote) -> int:
        if q.bar_volume <= 0:
            return 10**9
        return max(1, int(q.bar_volume * self.ex["max_participation"]))

    def _order(self, ts, decision_id, symbol, side, qty, quote, fill, chg, trigger, status, note=""):
        slip = abs(fill - quote) * qty
        self.j.x(
            "INSERT INTO orders(run_id, ts, decision_id, symbol, side, qty, quote_price, fill_price, slippage_cost, charges, charges_json, trigger, status, note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.run_id, ts, decision_id, symbol, side, qty, quote, fill, round(slip, 2),
             chg.total if chg else 0, json.dumps(chg.as_dict()) if chg else None, trigger, status, note),
        )
        return slip

    def buy(self, ts: datetime, q: Quote, alloc_pct: float, stop_pct: float, target_pct: float,
            reason: str, decision_id: int | None = None) -> tuple[bool, str]:
        s = q.symbol
        if s in self.positions:
            return self._reject(ts, decision_id, s, "BUY", q, "already holding")
        if len(self.positions) >= self.st["max_positions"]:
            return self._reject(ts, decision_id, s, "BUY", q, "max open positions reached")
        alloc_pct = min(max(alloc_pct, 0), self.st["max_position_pct"])
        power = self.buying_power
        budget = min(self.equity * alloc_pct / 100, power * 0.995)
        est = self.fill_price("BUY", q, 1)
        qty = min(int(budget // est), self.max_qty_by_liquidity(q))
        if qty < 1:
            return self._reject(ts, decision_id, s, "BUY", q, "not enough cash for one share")
        fill = self.fill_price("BUY", q, qty)
        chg = charges_mod.compute("BUY", qty, fill, self.product, self.rates, self.market.code)
        while qty > 0 and qty * fill + chg.total > power:
            qty -= 1
            chg = charges_mod.compute("BUY", qty, fill, self.product, self.rates, self.market.code)
        if qty < 1:
            return self._reject(ts, decision_id, s, "BUY", q, "not enough cash after charges")
        self.cash -= qty * fill + chg.total
        self.total_charges += chg.total
        self.total_slippage += self._order(ts.isoformat(), decision_id, s, "BUY", qty, q.ltp, fill, chg, "ai", "filled", reason[:200])
        self.positions[s] = Position(
            symbol=s, qty=qty, avg_price=fill, stop=round(fill * (1 - stop_pct / 100), 2),
            target=round(fill * (1 + target_pct / 100), 2), opened_at=ts.isoformat(),
            entry_charges=chg.total, entry_reason=reason[:300], last_price=q.ltp,
        )
        self._save_positions()
        return True, f"BUY {qty} {s} @ {fill:.2f} (quote {q.ltp:.2f}, charges {chg.total:.2f})"

    def sell(self, ts: datetime, q: Quote, trigger: str, reason: str = "", decision_id: int | None = None) -> tuple[bool, str]:
        s = q.symbol
        p = self.positions.get(s)
        if not p:
            return self._reject(ts, decision_id, s, "SELL", q, "no long position to sell")
        if p.is_short:
            return self._reject(ts, decision_id, s, "SELL", q, "this is a short: use COVER")
        qty = p.qty
        fill = self.fill_price("SELL", q, qty)
        chg = charges_mod.compute("SELL", qty, fill, self.product, self.rates, self.market.code)
        self.cash += qty * fill - chg.total
        self.total_charges += chg.total
        self.total_slippage += self._order(ts.isoformat(), decision_id, s, "SELL", qty, q.ltp, fill, chg, trigger, "filled", reason[:200])
        gross = (fill - p.avg_price) * qty
        net = gross - p.entry_charges - chg.total
        self.realized += net
        hold = (ts - datetime.fromisoformat(p.opened_at)).total_seconds() / 60
        self._record_trade(ts, p, qty, fill, gross, p.entry_charges + chg.total, net, trigger, hold)
        return True, f"SELL {qty} {s} @ {fill:.2f} ({trigger}) net {net:+.2f}"

    def _record_trade(self, ts, p: Position, qty, fill, gross, charges, net, trigger, hold) -> None:
        self.j.x(
            "INSERT INTO trades(run_id, symbol, qty, entry_ts, entry_price, exit_ts, exit_price, gross_pnl, charges, net_pnl, exit_reason, hold_minutes, entry_reason, side) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.run_id, p.symbol, qty, p.opened_at, p.avg_price, ts.isoformat(), fill, round(gross, 2),
             round(charges, 2), round(net, 2), trigger, round(hold, 1), p.entry_reason, p.side),
        )
        del self.positions[p.symbol]
        self._save_positions()

    # ---- short side --------------------------------------------------
    def short(self, ts: datetime, q: Quote, alloc_pct: float, stop_pct: float, target_pct: float,
              reason: str, decision_id: int | None = None) -> tuple[bool, str]:
        """Sell borrowed (virtual) shares. The risk officer has already vetted the call;
        these are the hard limits that hold whatever the caller asked for."""
        s = q.symbol

        def no(why: str) -> tuple[bool, str]:
            return self._reject(ts, decision_id, s, "SHORT", q, why)

        if short_setting(self.st, "short_mode") == "off":
            return no("short selling is switched off")
        if self.product != "intraday":
            return no("shorts are intraday only")
        if s in self.positions:
            return no("already have a position in this stock")
        if len(self.positions) >= self.st["max_positions"]:
            return no("max open positions reached")
        if sum(p.is_short for p in self.positions.values()) >= short_setting(self.st, "max_shorts"):
            return no("max open shorts reached")
        if not (stop_pct > 0 and target_pct > 0):
            return no("a short needs a stop and a target")
        stop_pct = min(stop_pct, short_setting(self.st, "short_max_stop_pct"))
        target_pct = min(target_pct, 50.0)
        alloc = min(max(alloc_pct, 0), short_setting(self.st, "max_short_position_pct"))
        room = self.equity * short_setting(self.st, "max_short_exposure_pct") / 100 - self.short_value
        # Proceeds P raise cash by P and block P + margin, so each unit of short uses margin% of buying power.
        margin = max(short_setting(self.st, "short_margin_pct"), 1.0) / 100
        budget = max(0.0, min(self.equity * alloc / 100, room, self.buying_power * 0.995 / margin))
        est = self.fill_price("SELL", q, 1)
        qty = min(int(budget // est), self.max_qty_by_liquidity(q)) if est > 0 else 0
        if qty < 1:
            return no("not enough margin for one share")
        fill = self.fill_price("SELL", q, qty)
        chg = charges_mod.compute("SELL", qty, fill, self.product, self.rates, self.market.code)
        self.cash += qty * fill - chg.total
        self.total_charges += chg.total
        self.total_slippage += self._order(ts.isoformat(), decision_id, s, "SHORT", qty, q.ltp, fill, chg, "ai", "filled", reason[:200])
        self.positions[s] = Position(
            symbol=s, qty=-qty, avg_price=fill, stop=round(fill * (1 + stop_pct / 100), 2),
            target=round(fill * (1 - target_pct / 100), 2), opened_at=ts.isoformat(),
            entry_charges=chg.total, entry_reason=reason[:300], last_price=q.ltp,
        )
        self._save_positions()
        return True, f"SHORT {qty} {s} @ {fill:.2f} (quote {q.ltp:.2f}, charges {chg.total:.2f})"

    def cover(self, ts: datetime, q: Quote, trigger: str, reason: str = "", decision_id: int | None = None) -> tuple[bool, str]:
        """Buy the borrowed shares back."""
        s = q.symbol
        p = self.positions.get(s)
        if not p or not p.is_short:
            return self._reject(ts, decision_id, s, "COVER", q, "no short to cover")
        qty = -p.qty
        fill = self.fill_price("BUY", q, qty)
        chg = charges_mod.compute("BUY", qty, fill, self.product, self.rates, self.market.code)
        hold = (ts - datetime.fromisoformat(p.opened_at)).total_seconds() / 60
        borrow = round(qty * p.avg_price * short_setting(self.st, "short_borrow_annual_pct") / 100 * max(hold, 0) / (365 * 1440), 2)
        self.cash -= qty * fill + chg.total + borrow
        self.total_charges += chg.total + borrow
        self.total_slippage += self._order(ts.isoformat(), decision_id, s, "COVER", qty, q.ltp, fill, chg, trigger, "filled", reason[:200])
        gross = (p.avg_price - fill) * qty
        net = gross - p.entry_charges - chg.total - borrow
        self.realized += net
        self._record_trade(ts, p, qty, fill, gross, p.entry_charges + chg.total + borrow, net, trigger, hold)
        return True, f"COVER {qty} {s} @ {fill:.2f} ({trigger}) net {net:+.2f}"

    def close(self, ts: datetime, q: Quote, trigger: str, reason: str = "", decision_id: int | None = None) -> tuple[bool, str]:
        """Flatten a position, whichever side it is on."""
        p = self.positions.get(q.symbol)
        return (self.cover if p and p.is_short else self.sell)(ts, q, trigger, reason, decision_id)

    def _reject(self, ts, decision_id, symbol, side, q, why):
        self._order(ts.isoformat(), decision_id, symbol, side, 0, q.ltp, 0, None, "ai", "rejected", why)
        return False, f"{side} {symbol} rejected: {why}"

    # ---- risk rules --------------------------------------------------
    def check_exits(self, ts: datetime, quotes: dict[str, Quote]) -> list[str]:
        msgs = []
        for s in list(self.positions):
            q = quotes.get(s)
            if not q:
                continue
            p = self.positions[s]
            hit_stop = q.ltp >= p.stop if p.is_short else q.ltp <= p.stop
            hit_target = q.ltp <= p.target if p.is_short else q.ltp >= p.target
            if hit_stop:
                msgs.append(self.close(ts, q, "stop_loss", f"price {q.ltp} hit stop {p.stop}")[1])
            elif hit_target:
                msgs.append(self.close(ts, q, "target", f"price {q.ltp} hit target {p.target}")[1])
        return msgs + self._margin_calls(ts, quotes)

    def _margin_calls(self, ts: datetime, quotes: dict[str, Quote]) -> list[str]:
        """Cover the worst short first while collateral is under the maintenance level."""
        msgs, need = [], 1 + short_setting(self.st, "short_maintenance_pct") / 100
        while self.short_value > 0 and self.cash < self.short_value * need:
            shorts = [p for p in self.positions.values() if p.is_short and p.symbol in quotes]
            if not shorts:
                break
            worst = min(shorts, key=lambda p: p.unrealized)
            msgs.append(self.cover(ts, quotes[worst.symbol], "margin_call", "short collateral below maintenance")[1])
        return msgs

    def square_off_all(self, ts: datetime, quotes: dict[str, Quote], trigger: str) -> list[str]:
        return [self.close(ts, quotes[s], trigger, trigger)[1] for s in list(self.positions) if s in quotes]

    def snapshot(self, ts: datetime) -> None:
        self.j.x("INSERT INTO equity VALUES (?,?,?,?,?,?,?,?)",
                 (self.run_id, ts.isoformat(), round(self.cash, 2), round(self.holdings_value, 2),
                  round(self.equity, 2), round(self.realized, 2), round(self.unrealized, 2), round(self.total_charges, 2)))
