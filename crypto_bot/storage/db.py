"""SQLite persistence: cycles, positions/trades, LLM usage, and bot state.

SQLite is a single file (data/bot_<mode>.db), needs no server, and survives
restarts - so open positions, today's trade count, the drawdown peak and the
paper balance are all remembered if the bot crashes or the VPS reboots.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    engine TEXT,
    last_price REAL,
    snapshot_json TEXT,
    llm_raw TEXT,
    decision_json TEXT,
    parse_error TEXT,
    risk_json TEXT,
    action TEXT,
    order_json TEXT
);
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL,              -- open | closed
    amount REAL NOT NULL,
    entry_price REAL NOT NULL,
    entry_quote REAL NOT NULL,         -- USDT spent incl. fees
    entry_fee REAL NOT NULL,
    stop_loss_pct REAL NOT NULL,
    take_profit_pct REAL NOT NULL,
    stop_loss_price REAL NOT NULL,
    take_profit_price REAL NOT NULL,
    opened_at TEXT NOT NULL,
    entry_order_id TEXT,
    entry_reason TEXT,
    closed_at TEXT,
    exit_price REAL,
    exit_quote REAL,                   -- USDT received after fees
    exit_fee REAL,
    exit_order_id TEXT,
    exit_reason TEXT,
    pnl REAL,
    pnl_pct REAL
);
CREATE INDEX IF NOT EXISTS ix_positions_status ON positions(status, symbol);
CREATE TABLE IF NOT EXISTS llm_usage (
    day TEXT PRIMARY KEY,
    calls INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime] = None) -> str:
    return (dt or utcnow()).isoformat(timespec="seconds")


def day_of(dt: Optional[datetime] = None) -> str:
    return (dt or utcnow()).strftime("%Y-%m-%d")


@dataclass
class Position:
    id: int
    symbol: str
    status: str
    amount: float
    entry_price: float
    entry_quote: float
    entry_fee: float
    stop_loss_pct: float
    take_profit_pct: float
    stop_loss_price: float
    take_profit_price: float
    opened_at: str
    entry_order_id: Optional[str] = None
    entry_reason: Optional[str] = None
    closed_at: Optional[str] = None
    exit_price: Optional[float] = None
    exit_quote: Optional[float] = None
    exit_fee: Optional[float] = None
    exit_order_id: Optional[str] = None
    exit_reason: Optional[str] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None

    def unrealized_pnl(self, price: float, fee_pct: float = 0.1) -> float:
        """Approximate P&L if we sold now (includes an estimated exit fee)."""
        return self.amount * price * (1 - fee_pct / 100) - self.entry_quote


def _json(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    return json.dumps(obj, default=str, ensure_ascii=False)


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            # WAL + NORMAL: crash-safe for our purposes and much faster than full fsync per write.
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    # ------------------------------------------------------------- kv state
    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: Any) -> None:
        self._exec("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, str(value)))

    def get_float(self, key: str, default: float) -> float:
        v = self.get(key)
        try:
            return float(v) if v is not None else default
        except ValueError:
            return default

    # --------------------------------------------------------------- cycles
    def log_cycle(self, *, symbol: str, engine: str, last_price: Optional[float], snapshot: Any = None,
                  llm_raw: Optional[str] = None, decision: Any = None, parse_error: Optional[str] = None,
                  risk: Any = None, action: str = "NONE", order: Any = None, ts: Optional[str] = None) -> int:
        cur = self._exec(
            "INSERT INTO cycles(ts, symbol, engine, last_price, snapshot_json, llm_raw, decision_json, parse_error, "
            "risk_json, action, order_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ts or iso(), symbol, engine, last_price, _json(snapshot), llm_raw, _json(decision), parse_error,
             _json(risk), action, _json(order)),
        )
        return int(cur.lastrowid)

    def first_and_last_price(self, symbol: str) -> tuple[Optional[float], Optional[float], Optional[str], Optional[str]]:
        q = "SELECT last_price, ts FROM cycles WHERE symbol=? AND last_price IS NOT NULL ORDER BY id {} LIMIT 1"
        a = self.conn.execute(q.format("ASC"), (symbol,)).fetchone()
        b = self.conn.execute(q.format("DESC"), (symbol,)).fetchone()
        if not a or not b:
            return None, None, None, None
        return a["last_price"], b["last_price"], a["ts"], b["ts"]

    def symbols_seen(self) -> list[str]:
        return [r["symbol"] for r in self.conn.execute("SELECT DISTINCT symbol FROM cycles")]

    # ------------------------------------------------------------ positions
    def open_position(self, *, symbol: str, amount: float, entry_price: float, entry_quote: float, entry_fee: float,
                      sl_pct: float, tp_pct: float, sl_price: float, tp_price: float, order_id: Optional[str],
                      reason: str, opened_at: Optional[str] = None) -> Position:
        cur = self._exec(
            "INSERT INTO positions(symbol, status, amount, entry_price, entry_quote, entry_fee, stop_loss_pct, "
            "take_profit_pct, stop_loss_price, take_profit_price, opened_at, entry_order_id, entry_reason) "
            "VALUES (?, 'open', ?,?,?,?,?,?,?,?,?,?,?)",
            (symbol, amount, entry_price, entry_quote, entry_fee, sl_pct, tp_pct, sl_price, tp_price,
             opened_at or iso(), order_id, reason),
        )
        return self.get_position(int(cur.lastrowid))  # type: ignore[return-value]

    def close_position(self, pos_id: int, *, exit_price: float, exit_quote: float, exit_fee: float,
                       order_id: Optional[str], reason: str, closed_at: Optional[str] = None,
                       amount_sold: Optional[float] = None) -> Position:
        pos = self.get_position(pos_id)
        assert pos is not None
        # If only part of the position could be sold, the P&L is pro-rated on what was sold.
        frac = 1.0 if not amount_sold or pos.amount <= 0 else min(1.0, amount_sold / pos.amount)
        basis = pos.entry_quote * frac
        pnl = exit_quote - basis
        pnl_pct = pnl / basis * 100 if basis else 0.0
        self._exec(
            "UPDATE positions SET status='closed', closed_at=?, exit_price=?, exit_quote=?, exit_fee=?, "
            "exit_order_id=?, exit_reason=?, pnl=?, pnl_pct=? WHERE id=?",
            (closed_at or iso(), exit_price, exit_quote, exit_fee, order_id, reason, pnl, pnl_pct, pos_id),
        )
        return self.get_position(pos_id)  # type: ignore[return-value]

    def partial_close(self, pos_id: int, *, amount_sold: float, exit_price: float, exit_quote: float,
                      exit_fee: float, order_id: Optional[str], reason: str) -> tuple[Position, Position]:
        """A sell only partly filled: book the sold part as its own closed trade
        and keep the remainder open with a proportionally reduced cost basis."""
        pos = self.get_position(pos_id)
        assert pos is not None and 0 < amount_sold < pos.amount
        frac = amount_sold / pos.amount
        sold = self.open_position(
            symbol=pos.symbol, amount=amount_sold, entry_price=pos.entry_price, entry_quote=pos.entry_quote * frac,
            entry_fee=pos.entry_fee * frac, sl_pct=pos.stop_loss_pct, tp_pct=pos.take_profit_pct,
            sl_price=pos.stop_loss_price, tp_price=pos.take_profit_price, order_id=pos.entry_order_id,
            reason=(pos.entry_reason or "") + " [split]", opened_at=pos.opened_at,
        )
        # The split row reuses opened_at; mark it so trades_opened_on() does not double count.
        self._exec("UPDATE positions SET entry_order_id=COALESCE(entry_order_id,'') || ':split' WHERE id=?", (sold.id,))
        closed = self.close_position(sold.id, exit_price=exit_price, exit_quote=exit_quote, exit_fee=exit_fee,
                                     order_id=order_id, reason=reason + " (partial fill)")
        self._exec("UPDATE positions SET amount=?, entry_quote=?, entry_fee=? WHERE id=?",
                   (pos.amount - amount_sold, pos.entry_quote * (1 - frac), pos.entry_fee * (1 - frac), pos_id))
        return closed, self.get_position(pos_id)  # type: ignore[return-value]

    def get_position(self, pos_id: int) -> Optional[Position]:
        row = self.conn.execute("SELECT * FROM positions WHERE id=?", (pos_id,)).fetchone()
        return Position(**dict(row)) if row else None

    def open_positions(self, symbol: Optional[str] = None) -> list[Position]:
        if symbol:
            rows = self.conn.execute("SELECT * FROM positions WHERE status='open' AND symbol=?", (symbol,))
        else:
            rows = self.conn.execute("SELECT * FROM positions WHERE status='open'")
        return [Position(**dict(r)) for r in rows]

    def closed_positions(self) -> list[Position]:
        rows = self.conn.execute("SELECT * FROM positions WHERE status='closed' ORDER BY closed_at, id")
        return [Position(**dict(r)) for r in rows]

    def realized_pnl_total(self) -> float:
        return float(self.conn.execute("SELECT COALESCE(SUM(pnl),0) s FROM positions WHERE status='closed'").fetchone()["s"])

    def realized_pnl_on(self, day: str) -> float:
        r = self.conn.execute("SELECT COALESCE(SUM(pnl),0) s FROM positions WHERE status='closed' AND substr(closed_at,1,10)=?",
                              (day,)).fetchone()
        return float(r["s"])

    def trades_opened_on(self, day: str) -> int:
        r = self.conn.execute(
            "SELECT COUNT(*) c FROM positions WHERE substr(opened_at,1,10)=? "
            "AND COALESCE(entry_order_id,'') NOT LIKE '%:split'", (day,)).fetchone()
        return int(r["c"])

    # ------------------------------------------------------------ llm usage
    def add_llm_usage(self, day: str, input_tokens: int, output_tokens: int, cost: float) -> None:
        self._exec(
            "INSERT INTO llm_usage(day, calls, input_tokens, output_tokens, cost_usd) VALUES (?,1,?,?,?) "
            "ON CONFLICT(day) DO UPDATE SET calls=calls+1, input_tokens=input_tokens+excluded.input_tokens, "
            "output_tokens=output_tokens+excluded.output_tokens, cost_usd=cost_usd+excluded.cost_usd",
            (day, input_tokens, output_tokens, cost),
        )

    def llm_usage(self, day: str) -> dict[str, float]:
        r = self.conn.execute("SELECT * FROM llm_usage WHERE day=?", (day,)).fetchone()
        return dict(r) if r else {"day": day, "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    def llm_usage_all(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM llm_usage ORDER BY day")]

    # --------------------------------------------------------------- events
    def log_event(self, level: str, message: str) -> None:
        self._exec("INSERT INTO events(ts, level, message) VALUES (?,?,?)", (iso(), level, message))
