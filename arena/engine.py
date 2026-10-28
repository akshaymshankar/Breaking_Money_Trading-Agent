"""Deterministic paper-trading engine.

Claude proposes; this code disposes. It sizes orders, checks cash and risk
limits, simulates a realistic fill (spread + slippage + size cap), charges
Indian fees, and journals everything. No real order is ever sent anywhere.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime

from . import charges as charges_mod
from .db import Journal
from .feeds.base import Quote
from .safety import PAPER_ONLY

TICK = 0.05


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
    def value(self) -> float:
        return self.qty * (self.last_price or self.avg_price)

    @property
    def unrealized(self) -> float:
        return self.qty * ((self.last_price or self.avg_price) - self.avg_price)


def round_tick(p: float, up: bool) -> float:
    n = p / TICK
    return round((math.ceil(n - 1e-9) if up else math.floor(n + 1e-9)) * TICK, 2)


class PaperBroker:
    def __init__(self, journal: Journal, run_id: int, cfg: dict):
        assert PAPER_ONLY, "paper-only constant was tampered with"
        self.j, self.run_id, self.cfg = journal, run_id, cfg
        self.ex = cfg["execution"]
        self.st = cfg["strategy"]
        self.product = self.st["product"]
        self.rates = cfg.get("charges", {})
        acct = journal.one("SELECT * FROM account WHERE run_id=?", (run_id,))
        self.cash, self.realized = acct["cash"], acct["realized"]
        self.total_charges, self.total_slippage = acct["charges"], acct["slippage"]
        self.positions: dict[str, Position] = {
            r["symbol"]: Position(**{k: r[k] for k in Position.__dataclass_fields__})
            for r in journal.q("SELECT * FROM positions WHERE run_id=?", (run_id,))
        }

    # ---- state -------------------------------------------------------
    @property
    def holdings_value(self) -> float:  # shorts count negative (shares owed back)
        return sum(p.value for p in self.positions.values())

    @property
    def unrealized(self) -> float:
        return sum(p.unrealized for p in self.positions.values())

    @property
    def equity(self) -> float:
        return self.cash + self.holdings_value

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
            return round_tick(q.ltp * (1 + bps / 1e4), up=True)
        return round_tick(q.ltp * (1 - bps / 1e4), up=False)

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
            reason: str, decision_id: int | None = None, short: bool = False) -> tuple[bool, str]:
        """Open a position. short=True sells first and buys back later (intraday only);
        a short is stored with negative qty so equity and P&L stay cash + qty * price."""
        s = q.symbol
        side = "SELL" if short else "BUY"
        if short and self.product != "intraday":
            return self._reject(ts, decision_id, s, side, q, "shorting is intraday only")
        if s in self.positions:
            return self._reject(ts, decision_id, s, side, q, "already holding")
        if len(self.positions) >= self.st["max_positions"]:
            return self._reject(ts, decision_id, s, side, q, "max open positions reached")
        alloc_pct = min(max(alloc_pct, 0), self.st["max_position_pct"])
        # Margin: each open position locks |value| / leverage of equity, long or short.
        # Intraday (MIS) may use leverage; delivery is always 1x.
        lev = max(1.0, float(self.st.get("leverage", 1))) if self.product == "intraday" else 1.0
        free = self.equity - sum(abs(p.value) for p in self.positions.values()) / lev
        budget = min(self.equity * alloc_pct / 100 * lev, free * lev * 0.995)
        est = self.fill_price(side, q, 1)
        qty = min(int(budget // est), self.max_qty_by_liquidity(q))
        if qty < 1:
            return self._reject(ts, decision_id, s, side, q, "not enough cash for one share")
        fill = self.fill_price(side, q, qty)
        min_val = self.st.get("min_trade_value", 0)
        if min_val and qty * fill < min_val:
            return self._reject(ts, decision_id, s, side, q, f"trade below the Rs {min_val:,} minimum for this profile")
        chg = charges_mod.compute(side, qty, fill, self.product, self.rates)
        while qty > 0 and qty * fill / lev + chg.total > free:
            qty -= 1
            chg = charges_mod.compute(side, qty, fill, self.product, self.rates)
        if qty < 1:
            return self._reject(ts, decision_id, s, side, q, "not enough cash after charges")
        sign = -1 if short else 1
        self.cash -= sign * qty * fill + chg.total
        self.total_charges += chg.total
        self.total_slippage += self._order(ts.isoformat(), decision_id, s, side, qty, q.ltp, fill, chg, "ai", "filled", reason[:200])
        self.positions[s] = Position(
            symbol=s, qty=sign * qty, avg_price=fill, stop=round(fill * (1 - sign * stop_pct / 100), 2),
            target=round(fill * (1 + sign * target_pct / 100), 2), opened_at=ts.isoformat(),
            entry_charges=chg.total, entry_reason=reason[:300], last_price=q.ltp,
        )
        self._save_positions()
        return True, f"{'SHORT' if short else 'BUY'} {qty} {s} @ {fill:.2f} (quote {q.ltp:.2f}, charges {chg.total:.2f})"

    def sell(self, ts: datetime, q: Quote, trigger: str, reason: str = "", decision_id: int | None = None) -> tuple[bool, str]:
        s = q.symbol
        p = self.positions.get(s)
        if not p:
            return self._reject(ts, decision_id, s, "SELL", q, "no position (long-only engine)")
        qty = abs(p.qty)
        side = "BUY" if p.qty < 0 else "SELL"  # a short is closed by buying back
        fill = self.fill_price(side, q, qty)
        chg = charges_mod.compute(side, qty, fill, self.product, self.rates)
        self.cash += (fill if p.qty > 0 else -fill) * qty - chg.total
        self.total_charges += chg.total
        self.total_slippage += self._order(ts.isoformat(), decision_id, s, side, qty, q.ltp, fill, chg, trigger, "filled", reason[:200])
        gross = (fill - p.avg_price) * p.qty
        net = gross - p.entry_charges - chg.total
        self.realized += net
        hold = (ts - datetime.fromisoformat(p.opened_at)).total_seconds() / 60
        self.j.x(
            "INSERT INTO trades(run_id, symbol, qty, entry_ts, entry_price, exit_ts, exit_price, gross_pnl, charges, net_pnl, exit_reason, hold_minutes, entry_reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.run_id, s, p.qty, p.opened_at, p.avg_price, ts.isoformat(), fill, round(gross, 2),
             round(p.entry_charges + chg.total, 2), round(net, 2), trigger, round(hold, 1), p.entry_reason),
        )
        del self.positions[s]
        self._save_positions()
        return True, f"{'COVER' if p.qty < 0 else 'SELL'} {qty} {s} @ {fill:.2f} ({trigger}) net {net:+.2f}"

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
            sign = 1 if p.qty > 0 else -1
            if sign * (q.ltp - p.stop) <= 0:
                msgs.append(self.sell(ts, q, "stop_loss", f"price {q.ltp} hit stop {p.stop}")[1])
            elif sign * (q.ltp - p.target) >= 0:
                msgs.append(self.sell(ts, q, "target", f"price {q.ltp} hit target {p.target}")[1])
        return msgs

    def square_off_all(self, ts: datetime, quotes: dict[str, Quote], trigger: str) -> list[str]:
        return [self.sell(ts, quotes[s], trigger, trigger)[1] for s in list(self.positions) if s in quotes]

    def snapshot(self, ts: datetime) -> None:
        self.j.x("INSERT INTO equity VALUES (?,?,?,?,?,?,?,?)",
                 (self.run_id, ts.isoformat(), round(self.cash, 2), round(self.holdings_value, 2),
                  round(self.equity, 2), round(self.realized, 2), round(self.unrealized, 2), round(self.total_charges, 2)))
