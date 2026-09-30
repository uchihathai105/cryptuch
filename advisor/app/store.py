"""SQLite storage: settings, call history (track record), ratings history, journal, Watch tab."""
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
CREATE TABLE IF NOT EXISTS watch_coins (symbol TEXT PRIMARY KEY, added_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS watch_state (
  symbol TEXT PRIMARY KEY, side TEXT NOT NULL, since INTEGER NOT NULL, since_price REAL, score REAL
);
CREATE TABLE IF NOT EXISTS watch_signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, market TEXT NOT NULL, side TEXT NOT NULL,
  strength TEXT, score REAL, state TEXT, components TEXT, rules_version TEXT,
  created_at INTEGER NOT NULL, entry REAL, stop REAL, t1 REAL, t2 REAL, cost_r REAL,
  status TEXT NOT NULL DEFAULT 'open', t1_hit INTEGER NOT NULL DEFAULT 0, t1_hit_at INTEGER,
  expired_at INTEGER, result_1h_r REAL, result_1h_price REAL,
  closed_at INTEGER, exit_price REAL, result_r REAL, outcome TEXT, final_status TEXT,
  late_win INTEGER NOT NULL DEFAULT 0, mfe_r REAL DEFAULT 0, mae_r REAL DEFAULT 0,
  checked_until INTEGER NOT NULL DEFAULT 0, last_px REAL, mirror TEXT
);
CREATE INDEX IF NOT EXISTS watch_signals_status ON watch_signals(status);
CREATE INDEX IF NOT EXISTS watch_signals_symbol ON watch_signals(symbol);
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


# ---------------------------------------------------------------- Watch tab (v1.2)
_SIGNAL_FIELDS = ("status", "t1_hit", "t1_hit_at", "expired_at", "result_1h_r", "result_1h_price", "closed_at",
                  "exit_price", "result_r", "outcome", "final_status", "late_win", "mfe_r", "mae_r",
                  "checked_until", "last_px")


def watch_coins() -> list[str]:
    return [r["symbol"] for r in _q("SELECT symbol FROM watch_coins ORDER BY added_at, symbol").fetchall()]


def watch_add(symbol: str) -> None:
    _q("INSERT OR IGNORE INTO watch_coins(symbol, added_at) VALUES(?, ?)", (symbol, int(time.time() * 1000)), commit=True)


def watch_remove(symbol: str, now_ms: int) -> None:
    """Stop watching. Open signals are set aside as 'removed' and left out of the statistics."""
    with _lock:
        _db.execute("DELETE FROM watch_coins WHERE symbol=?", (symbol,))
        _db.execute("DELETE FROM watch_state WHERE symbol=?", (symbol,))
        _db.execute("UPDATE watch_signals SET status='closed', final_status='removed', closed_at=?, "
                    "outcome='Removed from the watchlist' WHERE symbol=? AND status!='closed'", (now_ms, symbol))
        _db.commit()


def watch_state_get(symbol: str) -> dict | None:
    r = _q("SELECT * FROM watch_state WHERE symbol=?", (symbol,)).fetchone()
    return dict(r) if r else None


def watch_state_set(symbol: str, side: str, since: int, since_price: float, score: float) -> None:
    _q("INSERT INTO watch_state(symbol, side, since, since_price, score) VALUES(?,?,?,?,?) "
       "ON CONFLICT(symbol) DO UPDATE SET side=excluded.side, since=excluded.since, "
       "since_price=excluded.since_price, score=excluded.score", (symbol, side, since, since_price, score), commit=True)


def watch_state_score(symbol: str, score: float) -> None:
    _q("UPDATE watch_state SET score=? WHERE symbol=?", (score, symbol), commit=True)


def _signal_row(r) -> dict:
    d = dict(r)
    for k in ("components", "mirror"):
        d[k] = json.loads(d[k]) if d.get(k) else None
    return d


def watch_active_signal(symbol: str) -> dict | None:
    r = _q("SELECT * FROM watch_signals WHERE symbol=? AND status!='closed' ORDER BY created_at DESC LIMIT 1",
           (symbol,)).fetchone()
    return _signal_row(r) if r else None


def watch_last_closed_at(symbol: str) -> int | None:
    r = _q("SELECT closed_at FROM watch_signals WHERE symbol=? AND status='closed' AND final_status!='removed' "
           "ORDER BY closed_at DESC LIMIT 1", (symbol,)).fetchone()
    return r["closed_at"] if r else None


def watch_signal_open(symbol: str, market: str, st: dict, mirror: dict, info: dict) -> int:
    cur = _q("""INSERT INTO watch_signals(symbol, market, side, strength, score, state, components, rules_version,
                created_at, entry, stop, t1, t2, cost_r, status, checked_until, last_px, mirror)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
             (symbol, market, st["side"], info["strength"], info["score"], info["state"], json.dumps(info["contrib"]),
              C.RULES_VERSION, st["created_at"], st["entry"], st["stop"], st["t1"], st["t2"], st["cost_r"],
              "open", st["checked_until"], st["last_px"], json.dumps(mirror)), commit=True)
    return cur.lastrowid


def watch_signal_save(signal_id: int, st: dict, mirror: dict) -> None:
    keys = ", ".join(f"{k}=?" for k in _SIGNAL_FIELDS)
    _q(f"UPDATE watch_signals SET {keys}, mirror=? WHERE id=?",
       (*[st.get(k) for k in _SIGNAL_FIELDS], json.dumps(mirror), signal_id), commit=True)


def watch_signal_state(row: dict) -> dict:
    """Signal row -> the plain dict app.watch.advance() works on."""
    return {k: row.get(k) for k in ("side", "entry", "stop", "t1", "t2", "created_at", "cost_r", *_SIGNAL_FIELDS)}


def watch_open_signals() -> list[dict]:
    return [_signal_row(r) for r in _q("SELECT * FROM watch_signals WHERE status!='closed' ORDER BY created_at DESC")]


def watch_history(limit: int = 100) -> list[dict]:
    return [_signal_row(r) for r in _q(
        "SELECT * FROM watch_signals WHERE status='closed' AND final_status!='removed' "
        "ORDER BY closed_at DESC LIMIT ?", (limit,))]


def watch_all_signals() -> list[dict]:
    """Every signal that has a 1 h result, oldest first (for the statistics)."""
    return [_signal_row(r) for r in _q(
        "SELECT * FROM watch_signals WHERE result_1h_r IS NOT NULL ORDER BY created_at")]


def watch_recent_results(symbol: str | None, limit: int) -> list[float]:
    """1 h results of the latest signals (one coin, or all coins), newest first."""
    where, args = "result_1h_r IS NOT NULL AND (final_status IS NULL OR final_status!='removed')", []
    if symbol:
        where += " AND symbol=?"
        args.append(symbol)
    rows = _q(f"SELECT result_1h_r FROM watch_signals WHERE {where} ORDER BY created_at DESC LIMIT ?", (*args, limit))
    return [r["result_1h_r"] for r in rows]
