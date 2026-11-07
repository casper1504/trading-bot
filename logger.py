"""Persistent logging: structured JSON lines + SQLite. Every signal evaluation,
trade execution, API response, and system error is recorded."""
import json
import sqlite3
import time
from datetime import datetime, timezone

from config import cfg

SCHEMA = """
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER,
    level TEXT,
    category TEXT,
    msg TEXT
);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER,
    symbol TEXT,
    timeframe TEXT,
    signal TEXT,
    close REAL,
    ema_fast REAL,
    ema_slow REAL,
    rsi REAL,
    meta TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER,
    symbol TEXT,
    side TEXT,
    entry REAL,
    qty REAL,
    size_usd REAL,
    sl REAL,
    tp REAL,
    fee REAL,
    pnl REAL,
    exit_ts INTEGER,
    exit_price REAL,
    exit_reason TEXT,
    status TEXT
);
CREATE TABLE IF NOT EXISTS equity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER,
    equity REAL,
    drawdown REAL
);
CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER,
    source TEXT,
    detail TEXT,
    fatal INTEGER
);
"""


class Logger:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or cfg.log_db
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def _insert(self, table: str, **cols):
        keys = list(cols.keys())
        q = f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' * len(keys))})"
        self._conn.execute(q, [cols[k] for k in keys])
        self._conn.commit()

    def log(self, level: str, category: str, msg: str):
        ts = int(time.time() * 1000)
        line = {"ts": ts, "level": level, "category": category, "msg": msg}
        print(json.dumps(line, ensure_ascii=False))
        self._insert("logs", ts=ts, level=level, category=category, msg=msg)

    def info(self, category, msg): self.log("INFO", category, msg)
    def warn(self, category, msg): self.log("WARN", category, msg)

    def error(self, category, msg, fatal=False):
        self.log("ERROR", category, msg)
        self._insert("errors", ts=int(time.time() * 1000), source=category,
                     detail=msg, fatal=1 if fatal else 0)

    def signal(self, symbol, timeframe, signal, close, ema_fast, ema_slow, rsi, meta=None):
        self._insert("signals", ts=int(time.time() * 1000), symbol=symbol,
                     timeframe=timeframe, signal=signal, close=close,
                     ema_fast=ema_fast, ema_slow=ema_slow, rsi=rsi,
                     meta=json.dumps(meta or {}, ensure_ascii=False))

    def trade_open(self, symbol, side, entry, qty, size_usd, sl, tp, fee):
        self._insert("trades", ts=int(time.time() * 1000), symbol=symbol, side=side,
                     entry=entry, qty=qty, size_usd=size_usd, sl=sl, tp=tp,
                     fee=fee, pnl=None, exit_ts=None, exit_price=None,
                     exit_reason=None, status="OPEN")

    def equity(self, equity, drawdown):
        self._insert("equity", ts=int(time.time() * 1000), equity=equity, drawdown=drawdown)

    def close_all(self, symbol=None):
        q = "UPDATE trades SET status='CLOSED' WHERE status='OPEN'"
        args = ()
        if symbol:
            q += " AND symbol=?"
            args = (symbol,)
        self._conn.execute(q, args)
        self._conn.commit()

    def trade_closed(self, trade_id, exit_price, exit_ts, exit_reason, pnl):
        self._conn.execute(
            "UPDATE trades SET status='CLOSED', exit_price=?, exit_ts=?, exit_reason=?, pnl=? WHERE id=?",
            (exit_price, exit_ts, exit_reason, pnl, trade_id))
        self._conn.commit()

    def query(self, sql, args=()):
        cur = self._conn.execute(sql, args)
        return cur.fetchall()

    def close(self):
        self._conn.close()
