"""SQLite journal: every decision, order, fill, trade, equity snapshot and day."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, name TEXT, started_at TEXT, mode TEXT, feed TEXT, brain TEXT, model TEXT,
  starting_capital REAL, config_json TEXT, status TEXT DEFAULT 'active', ended_at TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY, run_id INTEGER, ts TEXT, symbol TEXT, action TEXT, allocation_pct REAL,
  confidence REAL, stop_loss_pct REAL, target_pct REAL, reason TEXT, market_view TEXT, brain TEXT,
  outcome TEXT
);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY, run_id INTEGER, ts TEXT, decision_id INTEGER, symbol TEXT, side TEXT, qty INTEGER,
  quote_price REAL, fill_price REAL, slippage_cost REAL, charges REAL, charges_json TEXT,
  trigger TEXT, status TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY, run_id INTEGER, symbol TEXT, qty INTEGER, entry_ts TEXT, entry_price REAL,
  exit_ts TEXT, exit_price REAL, gross_pnl REAL, charges REAL, net_pnl REAL, exit_reason TEXT,
  hold_minutes REAL, entry_reason TEXT, side TEXT DEFAULT 'LONG'
);
CREATE TABLE IF NOT EXISTS positions (
  run_id INTEGER, symbol TEXT, qty INTEGER, avg_price REAL, stop REAL, target REAL, opened_at TEXT,
  entry_charges REAL, entry_reason TEXT, last_price REAL, PRIMARY KEY (run_id, symbol)
);
CREATE TABLE IF NOT EXISTS account (
  run_id INTEGER PRIMARY KEY, cash REAL, realized REAL, charges REAL, slippage REAL
);
CREATE TABLE IF NOT EXISTS equity (
  run_id INTEGER, ts TEXT, cash REAL, holdings REAL, equity REAL, realized REAL, unrealized REAL, charges REAL
);
CREATE TABLE IF NOT EXISTS days (
  run_id INTEGER, date TEXT, day_no INTEGER, start_equity REAL, end_equity REAL, pnl REAL, trades INTEGER,
  wins INTEGER, losses INTEGER, charges REAL, decisions INTEGER, insights TEXT, PRIMARY KEY (run_id, date)
);
CREATE TABLE IF NOT EXISTS desk (
  run_id INTEGER, ts TEXT, agent TEXT, status TEXT, ms INTEGER, summary TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS usage (
  run_id INTEGER, ts TEXT, paid_by TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER, list_usd REAL
);
CREATE TABLE IF NOT EXISTS notes (
  run_id INTEGER, ts TEXT, kind TEXT, text TEXT
);
CREATE INDEX IF NOT EXISTS ix_equity_run ON equity(run_id, ts);
CREATE INDEX IF NOT EXISTS ix_usage_run ON usage(run_id, ts);
CREATE INDEX IF NOT EXISTS ix_desk_run ON desk(run_id, ts);
CREATE INDEX IF NOT EXISTS ix_dec_run ON decisions(run_id, ts);
"""


class Journal:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Several agents (threads) share one database: WAL + a generous busy timeout keep writers from colliding.
        self.conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.executescript(SCHEMA)
        if "side" not in {r["name"] for r in self.conn.execute("PRAGMA table_info(trades)")}:
            try:  # journals made before short selling
                self.conn.execute("ALTER TABLE trades ADD COLUMN side TEXT DEFAULT 'LONG'")
            except sqlite3.OperationalError:
                pass  # another agent's thread added it first

    def x(self, sql: str, params=()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    def q(self, sql: str, params=()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params=()) -> dict | None:
        r = self.conn.execute(sql, params).fetchone()
        return dict(r) if r else None

    def add_usage(self, run_id: int, u: dict) -> None:
        """One Claude call, stamped with this computer's local time."""
        self.x("INSERT INTO usage VALUES (?,?,?,?,?,?,?)",
               (run_id, datetime.now().isoformat(timespec="seconds"), u.get("paid_by", ""), u.get("model", ""),
                u.get("input_tokens", 0), u.get("output_tokens", 0), u.get("list_usd")))

    def new_run(self, name, mode, feed, brain, model, capital, cfg, started_at) -> int:
        cur = self.x(
            "INSERT INTO runs(name, started_at, mode, feed, brain, model, starting_capital, config_json) VALUES (?,?,?,?,?,?,?,?)",
            (name, started_at, mode, feed, brain, model, capital, json.dumps(cfg, default=str)),
        )
        self.x("INSERT INTO account VALUES (?,?,?,?,?)", (cur.lastrowid, capital, 0, 0, 0))
        return cur.lastrowid

    def active_run(self, mode: str) -> dict | None:
        return self.one("SELECT * FROM runs WHERE status='active' AND mode=? ORDER BY id DESC LIMIT 1", (mode,))

    def latest_run(self) -> dict | None:
        return self.one("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
