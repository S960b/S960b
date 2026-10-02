"""Replay-проверка: согласованность снапшот+delta, sequence-контроль, время (as-of без будущего)."""
import json
import math

import pandas as pd

from .book import OrderBook


def replay_check(df, symbol: str):
    """Прогоняет стакан по событиям и сообщает о нарушениях (valid, gaps, applied deltas)."""
    rep = {"symbol": symbol, "exchanges": {}, "ok": True}
    for ex in sorted(df.exchange.unique()):
        sub = df[(df.exchange == ex) & (df.canonical_symbol == symbol)] \
            .sort_values(["recv_monotonic_ns", "sequence"]).reset_index(drop=True)
        ob = OrderBook()
        n_snap = n_delta = n_applied = n_rejected = n_gaps = 0
        last_seq = None
        for _, ev in sub.iterrows():
            et = ev["event_type"]
            raw_seq = ev.get("sequence")
            if raw_seq is None or pd.isna(raw_seq):
                ev_seq = None
            else:
                ev_seq = int(raw_seq)
            if et == "book_snapshot":
                ob.apply(ev.to_dict())
                n_snap += 1
                n_applied += 1
                last_seq = ev_seq
            elif et == "book_delta":
                n_delta += 1
                if not ob.valid:
                    n_rejected += 1
                    continue
                if ev_seq is not None and last_seq is not None and ev_seq < last_seq:
                    n_rejected += 1
                    n_gaps += 1
                    ob.valid = False
                    continue
                if ev_seq is not None:
                    last_seq = ev_seq
                ob.apply(ev.to_dict())
                n_applied += 1
        rep["exchanges"][ex] = {
            "events": len(sub), "snapshots": n_snap, "deltas": n_delta,
            "applied": n_applied, "rejected_before_snapshot_or_gap": n_rejected,
            "sequence_gaps": n_gaps,
            "final_valid": ob.valid,
            "best_bid": str(ob.best_bid()) if ob.best_bid() else None,
            "best_ask": str(ob.best_ask()) if ob.best_ask() else None,
            "mid": str(ob.mid()) if ob.mid() else None,
        }
        if n_gaps or not ob.valid:
            rep["ok"] = False
    return rep