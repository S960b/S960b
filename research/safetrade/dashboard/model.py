"""Shared quote preparation for Streamlit; bounded history from one recorder session."""
import json
import math
import os
import sqlite3
import time

import duckdb
import pandas as pd

from analysis.book import parse_bids_asks, payload_dict
from analysis.oracle import prepare_mid_column, Oracle


def read_state(path):
    if not os.path.exists(path):
        return {}, pd.DataFrame(), None
    with sqlite3.connect('file:'+path+'?mode=ro', uri=True) as con:
        state = {k: json.loads(v) for k, v in con.execute('SELECT key, value FROM ui_state')}
        run = con.execute('SELECT run_id, status, started_utc FROM runs ORDER BY started_utc DESC LIMIT 1').fetchone()
        health = pd.read_sql_query('SELECT * FROM health ORDER BY id DESC LIMIT 200', con)
    if not health.empty:
        health = health.drop_duplicates(['exchange', 'symbol'])
    return state, health, run


def recent_history(directory, symbol, run_id=None, window_s=600, limit=50000):
    files = [os.path.join(root, f) for root, _, names in os.walk(directory)
             for f in names if f.endswith('.parquet') and not f.startswith('.')]
    if not files:
        return pd.DataFrame()
    with duckdb.connect() as con:
        if run_id is None:
            row = con.execute('SELECT run_id, min(recv_utc_ns) FROM read_parquet(?, union_by_name=true) '
                              'GROUP BY run_id ORDER BY 2 DESC LIMIT 1', [files]).fetchone()
            run_id = row[0]
        end = con.execute('SELECT max(recv_monotonic_ns) FROM read_parquet(?, union_by_name=true) '
                          'WHERE run_id=? AND canonical_symbol=?', [files, run_id, symbol]).fetchone()[0]
        if end is None:
            return pd.DataFrame()
        df = con.execute('SELECT * FROM read_parquet(?, union_by_name=true) WHERE run_id=? '
                         'AND canonical_symbol=? AND recv_monotonic_ns>=? ORDER BY recv_monotonic_ns DESC LIMIT ?',
                         [files, run_id, symbol, int(end-window_s*1e9), limit]).df()
    return parse_bids_asks(df.sort_values('recv_monotonic_ns', kind='stable'))


def quote_table(df, cfg, now_utc_ns=None):
    now = time.time_ns() if now_utc_ns is None else now_utc_ns
    if df.empty:
        return pd.DataFrame(), None, 0
    prepared = prepare_mid_column(df.copy())
    rows = []
    oracle = Oracle(cfg['sources'], cfg['fitness']['max_bbo_age_s'], 3)
    for ex in cfg['sources']+[cfg['target']]:
        part = prepared[(prepared.exchange==ex) & prepared._selected]
        if part.empty:
            rows.append({'Биржа': ex, 'Статус': 'нет данных'}); continue
        ev = part.iloc[-1]
        m = ev._mid
        valid = m is not None and math.isfinite(float(m)) and m > 0
        age = max(0.0, (now-int(ev.recv_utc_ns))/1e9)
        payload = payload_dict(ev.to_dict())
        bid, ask = payload.get('bid_price'), payload.get('ask_price')
        if ev.event_type == 'book_snapshot' and valid:
            from analysis.book import OrderBook
            ob = OrderBook(); ob.apply(ev.to_dict())
            bid, ask = ob.best_bid(), ob.best_ask()
        if not valid:
            bid = ask = None
        freshness = cfg['fitness']['max_bbo_age_s'] if ex in cfg['sources'] else cfg['fitness']['max_book_age_s']
        status = 'непригодно' if not valid else 'устарело' if age>freshness else 'свежее наблюдение'
        if 'rest_provisional' in (ev.quality_flags or []):
            status += '; только REST'
        rows.append({'Биржа': ex, 'Bid': float(bid) if bid is not None else None,
                     'Ask': float(ask) if ask is not None else None, 'Mid': float(m) if valid else None,
                     'Спред, bps': (float(ask)-float(bid))/float(m)*1e4 if valid and bid is not None and ask is not None else None,
                     'Возраст, с': round(age, 2), 'Статус': status})
        if ex in cfg['sources']:
            oracle.feed(ex, int(ev.recv_utc_ns), float(m) if valid else None)
    r, n, _ = oracle.mid_at(now)
    return pd.DataFrame(rows), r, n
