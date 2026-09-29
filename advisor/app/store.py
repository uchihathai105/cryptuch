"""SQLite storage: settings, call history (track record), ratings history, journal."""
from __future__ import annotations

import json
import sqlite3
import threading
import time

from . import config as C

_lock = threading.Lock()
_db = sqlite3.connect(C.DB_PATH, check_same_thread=False)
_db.row_factory = sqlite3.Row

_db.executescript("""
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL, symbol TEXT NOT NULL, market TEXT NOT NULL,
  side TEXT NOT NULL, call TEXT NOT NULL, entry REAL, stop REAL, t1 REAL, t2 REAL,
  confidence INTEGER, reason TEXT, rules_version TEXT,
  status TEXT NOT NULL DEFAULT 'active', t1_hit INTEGER NOT NULL DEFAULT 0,
  advice TEXT DEFAULT 'Hold', advice_note TEXT DEFAULT 'New call: watch for entry in the zone',
  closed_at INTEGER, outcome TEXT, exit_price REAL, result_r REAL,
  checked_until INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS calls_status ON calls(status);
CREATE TABLE IF NOT EXISTS ratings (
  day TEXT NOT NULL, symbol TEXT NOT NULL, rating TEXT NOT NULL, close REAL,
  PRIMARY KEY (day, symbol)
);
CREATE TABLE IF NOT EXISTS screener (
  day TEXT NOT NULL, symbol TEXT NOT NULL, stage TEXT NOT NULL, score INTEGER, price REAL,
  PRIMARY KEY (day, symbol)
);
CREATE TABLE IF NOT EXISTS journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at INTEGER NOT NULL,
  trade_date TEXT, symbol TEXT, market TEXT, side TEXT, entry REAL, exit REAL,
  qty REAL, followed INTEGER, notes TEXT
);
""")
_db.commit()


def _q(sql: str, args: tuple = (), commit: bool = False):
    with _lock:
        cur = _db.execute(sql, args)
        if commit:
            _db.commit()
        return cur


# ---------------------------------------------------------------- settings
def get_settings() -> dict:
    s = dict(C.DEFAULT_SETTINGS)
    for row in _q("SELECT key, value FROM settings").fetchall():
        s[row["key"]] = json.loads(row["value"])
    return s


def save_settings(values: dict) -> dict:
    allowed = set(C.DEFAULT_SETTINGS)
    for k, v in values.items():
        if k in allowed:
            _q("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (k, json.dumps(v)), commit=True)
    return get_settings()


# ---------------------------------------------------------------- calls
def active_calls() -> list[dict]:
    return [dict(r) for r in _q("SELECT * FROM calls WHERE status='active' ORDER BY created_at DESC")]


def has_active(symbol: str, market: str) -> dict | None:
    r = _q("SELECT * FROM calls WHERE status='active' AND symbol=? AND market=?", (symbol, market)).fetchone()
    return dict(r) if r else None


def open_call(symbol: str, market: str, res: dict) -> int:
    cur = _q("""INSERT INTO calls(created_at, symbol, market, side, call, entry, stop, t1, t2, confidence,
                reason, rules_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
             (int(time.time() * 1000), symbol, market, res["side"], res["call"], res["entry"], res["stop"],
              res["t1"], res["t2"], res["confidence"], res["reason"], C.RULES_VERSION), commit=True)
    return cur.lastrowid


def update_call(call_id: int, **fields) -> None:
    keys = ", ".join(f"{k}=?" for k in fields)
    _q(f"UPDATE calls SET {keys} WHERE id=?", (*fields.values(), call_id), commit=True)


def closed_calls(limit: int = 200) -> list[dict]:
    return [dict(r) for r in _q("SELECT * FROM calls WHERE status='closed' ORDER BY closed_at DESC LIMIT ?",
                                (limit,))]


def track_record() -> dict:
    rows = closed_calls(10_000)
    if not rows:
        return {"count": 0}
    wins = [r for r in rows if (r["result_r"] or 0) > 0]
    total_r = sum(r["result_r"] or 0 for r in rows)
    return {"count": len(rows), "win_rate": round(len(wins) / len(rows) * 100),
            "total_r": round(total_r, 2), "avg_r": round(total_r / len(rows), 2)}


# ---------------------------------------------------------------- ratings
def save_rating(day: str, symbol: str, rating: str, close: float) -> None:
    _q("INSERT INTO ratings(day, symbol, rating, close) VALUES(?,?,?,?) "
       "ON CONFLICT(day, symbol) DO UPDATE SET rating=excluded.rating, close=excluded.close",
       (day, symbol, rating, close), commit=True)


def rating_change(symbol: str, current: str) -> dict | None:
    """When the rating last changed and what it was before (URS F16)."""
    rows = _q("SELECT day, rating FROM ratings WHERE symbol=? ORDER BY day DESC LIMIT 400", (symbol,)).fetchall()
    for i, r in enumerate(rows):
        if r["rating"] != current:
            since = rows[i - 1]["day"] if i > 0 else None
            return {"previous": r["rating"], "since": since}
    return None


# ---------------------------------------------------------------- screener
def save_screen(day: str, rows: list[dict]) -> None:
    with _lock:
        _db.execute("DELETE FROM screener WHERE day=?", (day,))
        _db.executemany("INSERT INTO screener(day, symbol, stage, score, price) VALUES(?,?,?,?,?)",
                        [(day, r["symbol"], r["stage"], r["score"], r["price"]) for r in rows])
        _db.commit()


def previous_screen(day: str) -> dict[str, str]:
    """Stages from the most recent earlier run, to spot coins that newly turned Recovering."""
    row = _q("SELECT MAX(day) AS d FROM screener WHERE day < ?", (day,)).fetchone()
    if not row or not row["d"]:
        return {}
    return {r["symbol"]: r["stage"] for r in _q("SELECT symbol, stage FROM screener WHERE day=?", (row["d"],))}


# ---------------------------------------------------------------- journal
def journal_list() -> list[dict]:
    return [dict(r) for r in _q("SELECT * FROM journal ORDER BY trade_date DESC, id DESC")]


def journal_add(e: dict) -> None:
    _q("""INSERT INTO journal(created_at, trade_date, symbol, market, side, entry, exit, qty, followed, notes)
          VALUES(?,?,?,?,?,?,?,?,?,?)""",
       (int(time.time() * 1000), e.get("trade_date"), e.get("symbol", "").upper(), e.get("market"),
        e.get("side"), e.get("entry"), e.get("exit"), e.get("qty"), 1 if e.get("followed") else 0,
        e.get("notes", "")), commit=True)


def journal_delete(entry_id: int) -> None:
    _q("DELETE FROM journal WHERE id=?", (entry_id,), commit=True)
