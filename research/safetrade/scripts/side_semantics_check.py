#!/usr/bin/env python3
"""Семантика публичного side (шаг 7 ревью: дедуп, nearest-boundary, tick/max_age).

Гипотезы:
- A (aggressor): публичная сделка price≈best_ask => side='buy'; price≈best_bid
  => side='sell' (агрессор покупает по ask / продаёт по bid).
- P (passive/maker): наоборот: price≈best_ask => side='sell' (лимитный sell
  стоял на ask), price≈best_bid => side='buy'.

Для каждой публичной сделки из собранных JSONL берём ближайший depth-снимок
(не позже сделки, в пределах max_age). Изменения к предыдущей версии:
- дедупликация сделок по (pair, id); строки без id учитываются отдельно
  (missing_id) и не дедуплицируются; повторы (pair, id) => duplicates и не
  классифицируются (в т.ч. между разными poll-записями);
- выбор границы НЕЗАВИСИМО от side, строго по BBO: best_bid=max(bids),
  best_ask=min(asks); глубокие уровни не границы спреда и не учитываются
  (ложная сторона на устаревшем стакане); равенство расстояний BBO
  (в т.ч. двойное совпадение внутри тика) => ambiguous;
- side='unverified' сохраняется отдельным счётчиком (не классифицируется);
- проценты aggr/passive считаются от КЛАССИФИЦИРОВАННЫХ (aggr_ok+passive_ok);
  ambiguous/indet/no_snap/unverified в знаменатель не входят;
- tick (запасной для пар вне TICK) и max_age — параметры CLI.

Это КОСВЕННАЯ диагностика: REST-снимки запаздывают, цена может уйти; допуск и
возраст ограничены. Результат не выдаётся за подтверждённый контракт.
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from maker.util import parse_iso_utc, valid_book

TICK = {'PRLUSDT': Decimal('0.01'), 'QUANTUSUSDT': Decimal('0.001'),
        'LTCUSDT': Decimal('0.00001')}
DEFAULT_TICK = Decimal('0.01')
MAX_AGE_S = 30.0   # снимок не старше 30с до сделки

_CLS_KEY = {'aggr': 'aggr_ok', 'passive': 'passive_ok', 'ambiguous': 'ambiguous',
            'indet': 'indet', 'unverified': 'unverified'}


def nearest_boundary(px, bids, asks, tick):
    """BBO-only: только best_bid=max(bids) и best_ask=min(asks).

    Глубокие уровни не являются границей спреда и не учитываются: на
    устаревшем снимке они могут оказаться ближе цены и дать ложную сторону.
    Возвращает 'bid' | 'ask' | 'ambiguous' | 'indet'.
    Равенство расстояний BBO (в т.ч. двойное совпадение внутри тика) =>
    'ambiguous'; вне допуска от обеих границ => 'indet'.
    """
    try:
        bb = max(Decimal(str(x[0])) for x in bids)
        ba = min(Decimal(str(x[0])) for x in asks)
    except (ValueError, TypeError):
        return 'indet'
    db, da = abs(px - bb), abs(px - ba)
    if db == da:
        return 'ambiguous' if db <= tick else 'indet'
    if db < da:
        return 'bid' if db <= tick else 'indet'
    return 'ask' if da <= tick else 'indet'


def classify_trade(px, side, bids, asks, tick):
    """Классификация одной сделки.

    Граница — только BBO (best_bid=max(bids), best_ask=min(asks)), выбор
    независимо от side; затем агрессор/пассив:
    bid: sell=>aggr, buy=>passive; ask: buy=>aggr, sell=>passive.
    side='unverified' сохраняется как есть. Прочие side / вне допуска => 'indet'.
    """
    side = str(side or '')
    if side == 'unverified':
        return 'unverified'
    if side not in ('buy', 'sell'):
        return 'indet'
    nb = nearest_boundary(px, bids, asks, tick)
    if nb == 'bid':
        return 'aggr' if side == 'sell' else 'passive'
    if nb == 'ask':
        return 'aggr' if side == 'buy' else 'passive'
    return nb   # 'ambiguous' | 'indet'


def analyze(depth_rows, trade_rows, ticks=None, max_age_s=MAX_AGE_S,
            fallback_tick=None):
    """Основной конвейер: depth_rows/trade_rows — списки сырых JSON-записей.

    Возвращает agg {pair: {total, unique, missing_id, duplicates, aggr_ok,
    passive_ok, ambiguous, indet, no_snap, unverified, classified, aggr_pct,
    passive_pct, verdict, examples}}.
    """
    ticks = ticks or TICK
    fallback = fallback_tick if fallback_tick is not None else DEFAULT_TICK
    snaps = defaultdict(list)
    for r in depth_rows:
        if not isinstance(r, dict) or not r.get('pair'):
            continue
        ok, _, _ = valid_book(r.get('bids'), r.get('asks'))
        if ok:
            snaps[r['pair']].append(((r.get('t') or 0) / 1e9, r['bids'], r['asks']))
    for pair in snaps:
        snaps[pair].sort(key=lambda x: x[0])

    def _new():
        return {'total': 0, 'unique': 0, 'missing_id': 0, 'duplicates': 0,
                'aggr_ok': 0, 'passive_ok': 0, 'ambiguous': 0, 'indet': 0,
                'no_snap': 0, 'unverified': 0, 'classified': 0,
                'aggr_pct': None, 'passive_pct': None, 'verdict': 'no_classified',
                'examples': []}

    agg = defaultdict(_new)
    seen = set()
    for poll in trade_rows:
        if not isinstance(poll, dict) or not poll.get('pair'):
            continue
        pair = poll['pair']
        tick = ticks.get(pair, fallback)
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
            except Exception:
                continue
            a = agg[pair]
            a['total'] += 1
            tid = t.get('id')
            if tid is None or not str(tid).strip() or str(tid).strip().lower() == 'none':
                a['missing_id'] += 1
            else:
                key = (pair, str(tid))
                if key in seen:
                    a['duplicates'] += 1
                    continue
                seen.add(key)
                a['unique'] += 1
            # ближайший снимок <= ts, возраст <= max_age
            j = None
            for i in range(len(sp) - 1, -1, -1):
                if sp[i][0] <= ts + 1e-9:
                    if ts - sp[i][0] <= max_age_s:
                        j = i
                    break
            if j is None:
                a['no_snap'] += 1
                continue
            cls = classify_trade(px, t.get('side'), sp[j][1], sp[j][2], tick)
            a[_CLS_KEY[cls]] += 1
            if len(a['examples']) < 3:
                a['examples'].append(
                    {'ts': t.get('created_at'), 'price': str(px), 'side': str(t.get('side')),
                     'cls': cls})
    for a in agg.values():
        a['classified'] = a['aggr_ok'] + a['passive_ok']
        if a['classified'] > 0:
            a['aggr_pct'] = a['aggr_ok'] / a['classified'] * 100
            a['passive_pct'] = a['passive_ok'] / a['classified'] * 100
            ratio = a['aggr_ok'] / a['classified']
            a['verdict'] = ('aggressor-consistent' if ratio >= 0.9
                            else 'passive-consistent' if ratio <= 0.1
                            else 'AMBIGUOUS')
    return agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-id', default='mk_18db19212772b000')
    ap.add_argument('--data-dir', default=None)
    ap.add_argument('--tick', default=None,
                    help='запасной допуск для пар вне TICK (напр. 0.01)')
    ap.add_argument('--max-age', type=float, default=MAX_AGE_S,
                    help=f'макс. возраст снимка до сделки, сек (default {MAX_AGE_S})')
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
    fallback = Decimal(str(args.tick)) if args.tick else None
    agg = analyze(depth, trades, max_age_s=args.max_age, fallback_tick=fallback)
    print(f'run={run_id} max_age_s={args.max_age} | косвенная диагностика side:')
    print(f'{"pair":<14} {"total":>6} {"uniq":>5} {"dup":>4} {"miss":>5} {"aggr":>5} '
          f'{"pass":>5} {"amb":>4} {"indet":>5} {"no_snap":>7} {"unver":>6}')
    for pair in sorted(agg):
        a = agg[pair]
        pct = (f'aggr={a["aggr_pct"]:.1f}% pass={a["passive_pct"]:.1f}%'
               if a['classified'] else 'classified=0')
        print(f'{pair:<14} {a["total"]:>6} {a["unique"]:>5} {a["duplicates"]:>4} '
              f'{a["missing_id"]:>5} {a["aggr_ok"]:>5} {a["passive_ok"]:>5} '
              f'{a["ambiguous"]:>4} {a["indet"]:>5} {a["no_snap"]:>7} '
              f'{a["unverified"]:>6}   {pct}')
        for e in a['examples']:
            print('   ', e)
        print(f'{pair}: вывод = {a["verdict"]} (aggr {a["aggr_ok"]}/{a["classified"]})')


if __name__ == '__main__':
    main()