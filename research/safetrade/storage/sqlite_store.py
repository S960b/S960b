"""SQLite WAL-хранилище: метаданные сбора, health-снимки, состояние панели. Один писатель."""
import json
import sqlite3
import time
from datetime import datetime, timezone


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StateStore:
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._init_tables()

    def _init_tables(self):
        c = self.conn
        c.execute("""CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY, boot_id TEXT, started_utc TEXT, stopped_utc TEXT,
            sources TEXT, markets TEXT, status TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS health (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT, exchange TEXT, symbol TEXT,
            connected INTEGER, last_msg_age_s REAL, msgs INTEGER, reconnects INTEGER, errors TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS ui_state (
            key TEXT PRIMARY KEY, value TEXT, updated_utc TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT, symbol TEXT, hypothesis TEXT, L_ms INTEGER,
            signal_utc TEXT, signal_utc_ns INTEGER,
            W_s INTEGER, D_s INTEGER, H_s INTEGER,
            entry_price REAL, exit_price REAL, qty REAL, notional REAL,
            fee_bps REAL, fee_paid REAL, pnl REAL, pnl_pct REAL,
            reason TEXT, adverse_move_bps REAL, reach_raw INTEGER, reach_adj INTEGER,
            raw JSON)""")
        c.execute("""CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT, symbol TEXT, signal_utc_ns INTEGER, signal_utc TEXT,
            R0 REAL, F0 REAL, b REAL, direction TEXT, sources TEXT,
            W_s INTEGER, D_s INTEGER, L_ms INTEGER, outcome TEXT)""")
        self.conn.commit()

    # ---- runs ----
    def start_run(self, run_id, boot_id, sources, markets):
        self.conn.execute("INSERT OR REPLACE INTO runs (run_id, boot_id, started_utc, sources, markets, status) VALUES (?,?,?,?,?,?)",
                          (run_id, boot_id, utcnow_iso(), json.dumps(sources), json.dumps(markets), "running"))
        self.conn.commit()

    def stop_run(self, run_id):
        self.conn.execute("UPDATE runs SET stopped_utc=?, status=? WHERE run_id=?",
                          (utcnow_iso(), "stopped", run_id))
        self.conn.commit()

    # ---- health ----
    def save_health(self, exchange, symbol, h: dict):
        self.conn.execute(
            "INSERT INTO health (ts_utc, exchange, symbol, connected, last_msg_age_s, msgs, reconnects, errors) VALUES (?,?,?,?,?,?,?,?)",
            (utcnow_iso(), exchange, symbol, int(bool(h.get("connected"))), h.get("last_msg_age_s"),
             h.get("msgs"), h.get("reconnects"), json.dumps(h.get("errors", []))))
        self.conn.commit()

    def latest_health(self, limit=50):
        cur = self.conn.execute("SELECT * FROM health ORDER BY id DESC LIMIT ?", (limit,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    # ---- ui ----
    def set_ui(self, key, value):
        self.conn.execute("INSERT OR REPLACE INTO ui_state (key, value, updated_utc) VALUES (?,?,?)",
                          (key, json.dumps(value, ensure_ascii=False), utcnow_iso()))
        self.conn.commit()

    def get_ui(self, key, default=None):
        cur = self.conn.execute("SELECT value FROM ui_state WHERE key=?", (key,))
        row = cur.fetchone()
        return json.loads(row[0]) if row else default

    # ---- paper ----
    def save_paper_trade(self, t: dict):
        cols = ("run_id","symbol","hypothesis","L_ms","signal_utc","signal_utc_ns","W_s","D_s","H_s",
                "entry_price","exit_price","qty","notional","fee_bps","fee_paid","pnl","pnl_pct",
                "reason","adverse_move_bps","reach_raw","reach_adj","raw")
        vals = tuple(t.get(c) for c in cols)
        qs = ",".join("?" * len(cols))
        self.conn.execute(f"INSERT INTO paper_trades ({','.join(cols)}) VALUES ({qs})", vals)
        self.conn.commit()

    def all_paper_trades(self):
        cur = self.conn.execute("SELECT * FROM paper_trades ORDER BY signal_utc_ns")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def save_signal(self, s: dict):
        cols = ("run_id","symbol","signal_utc_ns","signal_utc","R0","F0","b","direction","sources",
                "W_s","D_s","L_ms","outcome")
        vals = tuple(s.get(c) for c in cols)
        qs = ",".join("?" * len(cols))
        self.conn.execute(f"INSERT INTO signals ({','.join(cols)}) VALUES ({qs})", vals)
        self.conn.commit()

    def close(self):
        self.conn.commit()
        self.conn.close()