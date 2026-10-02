"""Replay diagnostics using the same quality policy as analysis and paper."""
import math
from .oracle import prepare_mid_column
from simulator.paper import OrderBookReplay


def replay_check(df, symbol):
    prepared = prepare_mid_column(df[df.canonical_symbol==symbol].copy())
    rep = {'symbol': symbol, 'exchanges': {}, 'ok': True, 'execution_ready': False}
    if len(prepared[['run_id', 'boot_id']].drop_duplicates()) != 1:
        raise ValueError('Replay one run/boot at a time')
    for ex in sorted(prepared.exchange.unique()):
        part = prepared[prepared.exchange==ex]
        selected = part[part._selected]
        valid = sum(math.isfinite(float(x)) for x in selected._mid)
        last = selected.iloc[-1]._mid if not selected.empty else None
        final_valid = last is not None and math.isfinite(float(last))
        row = {'events': len(part), 'snapshots': int((part.event_type=='book_snapshot').sum()),
               'deltas': int((part.event_type=='book_delta').sum()), 'primary_observations': len(selected),
               'valid_observations': valid, 'final_valid': final_valid,
               'mid': float(last) if final_valid else None}
        if ex == 'safetrade':
            replay = OrderBookReplay(part.to_dict('records'))
            book = replay.asof_ns(replay.record_end_ns)
            row['execution_ready'] = book is not None
            row['unsynchronized_deltas_excluded'] = int(((part.event_type=='book_delta') & ~part._selected).sum())
            rep['execution_ready'] = book is not None
        rep['exchanges'][ex] = row
        rep['ok'] = rep['ok'] and final_valid
    return rep
