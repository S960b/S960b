#!/usr/bin/env python3
"""Семантика публичного side (этап ревью 6d01aae, ограниченный ~1ч).

Гипотезы:
- A (aggressor): публичная сделка price≈best_ask => side='buy'; price≈best_bid
  => side='sell' (агрессор покупает по ask / продаёт по bid).
- P (passive/maker): наоборот: price≈best_ask => side='sell' (лимитный sell
  стоял на ask), price≈best_bid => side='buy'.

Для каждой публичной сделки из собранных JSONL берём ближайший depth-снимок
(не позже сделки, в пределах max_age), сравниваем цену с best_bid/best_ask с
допуском tick. Считаем согласованность обеих гипотез. Это КОСВЕННАЯ
диагностика: REST-снимки запаздывают, цена может уйти; допуск и возраст
ограничены. Результат не выдаётся за подтверждённый контракт.
"""
import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from maker.util import parse_iso_utc, valid_book

TICK = {'PRLUSDT': Decimal('0.01'), 'QUANTUSUSDT': Decimal('0.001'),
        'LTCUSDT': Decimal('0.00001')}
MAX_AGE_S = 30.0   # снимок не старше 30с до сделки


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-id', default='mk_18db19212772b000')
    ap.add_argument('--data-dir', default=None)
    args = ap.parse_args()
    base = args.data_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                         'data', 'maker')
    run_id = args.run_id
    depth, trades = [], []
    with open(os.path.join(base, f'{run_id}_depth.jsonl')) as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                depth.append(json.loads(ln))
            except ValueError:
                pass
    with open(os.path.join(base, f'{run_id}_trades.jsonl')) as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                trades.append(json.loads(ln))
            except ValueError:
                pass
    # снимки по паре: (ts, bids, asks)
    snaps = defaultdict(list)
    for r in depth:
        if not isinstance(r, dict) or not r.get('pair'):
            continue
        ok, bb, ba = valid_book(r.get('bids'), r.get('asks'))
        if ok:
            snaps[r['pair']].append(((r.get('t') or 0) / 1e9, r['bids'], r['asks']))
    for pair in snaps:
        snaps[pair].sort(key=lambda x: x[0])
    agg = defaultdict(lambda: {'aggr_ok': 0, 'passive_ok': 0, 'indet': 0, 'no_snap': 0,
                               'examples': [], 'total': 0})
    for poll in trades:
        if not isinstance(poll, dict) or not poll.get('pair'):
            continue
        pair = poll['pair']
        tick = TICK.get(pair, Decimal('0.01'))
        sp = snaps.get(pair, [])
        if not sp:
            continue
        for t in poll.get('trades', []):
            if not isinstance(t, dict):
                continue
            ts = parse_iso_utc(t.get('created_at'))
            if ts is None:
                continue
            try:
                px = Decimal(str(t['price']))
                side = str(t.get('side'))
            except Exception:
                continue
            # ближайший снимок <= ts, возраст <= MAX_AGE
            j = None
            for i in range(len(sp) - 1, -1, -1):
                if sp[i][0] <= ts + 1e-9:
                    if ts - sp[i][0] <= MAX_AGE_S:
                        j = i
                    break
            agg[pair]['total'] += 1
            if j is None:
                agg[pair]['no_snap'] += 1
                continue
            _, bids, asks = sp[j]
            best_bid = max((Decimal(str(x[0])) for x in bids), default=None)
            best_ask = min((Decimal(str(x[0])) for x in asks), default=None)
            if best_bid is None or best_ask is None:
                agg[pair]['indet'] += 1
                continue
            near_bid = abs(px - best_bid) <= tick
            near_ask = abs(px - best_ask) <= tick
            if near_bid and side == 'sell':
                agg[pair]['aggr_ok'] += 1
            elif near_ask and side == 'buy':
                agg[pair]['aggr_ok'] += 1
            elif near_bid and side == 'buy':
                agg[pair]['passive_ok'] += 1
            elif near_ask and side == 'sell':
                agg[pair]['passive_ok'] += 1
            else:
                agg[pair]['indet'] += 1
            if len(agg[pair]['examples']) < 3:
                agg[pair]['examples'].append(
                    {'ts': t.get('created_at'), 'price': str(px), 'side': side,
                     'bid': str(best_bid), 'ask': str(best_ask)})
    print(f'run={run_id} max_age_s={MAX_AGE_S} | косвенная диагностика side:')
    print(f'{"pair":<14} {"total":>6} {"aggr_ok":>8} {"passive_ok":>10} {"indet":>6} {"no_snap":>8}')
    for pair in sorted(agg):
        a = agg[pair]
        tot = a['total'] or 1
        print(f'{pair:<14} {a["total"]:>6} {a["aggr_ok"]:>8} {a["passive_ok"]:>10} '
              f'{a["indet"]:>6} {a["no_snap"]:>8}   '
              f'aggr={a["aggr_ok"]/tot*100:.1f}% passive={a["passive_ok"]/tot*100:.1f}%')
        for e in a['examples']:
            print('   ', e)
    # итоговая сводка
    for pair in sorted(agg):
        a = agg[pair]
        classified = a['aggr_ok'] + a['passive_ok']
        if classified > 0:
            ratio = a['aggr_ok'] / classified
            verdict = ('aggressor-consistent' if ratio >= 0.9
                       else 'passive-consistent' if ratio <= 0.1
                       else 'AMBIGUOUS')
        else:
            verdict = 'no_classified'
        print(f'{pair}: вывод = {verdict} (aggr {a["aggr_ok"]}/{classified}') 


if __name__ == '__main__':
    main()