"""Opportunity-анализ гипотетических maker-котировок (ревью 4a3dc86 п.3-5).

БЕЗ ордеров. На уже собранных JSONL (depth + trades) моделирует ПРИЧИННУЮ
политику котировок и считает потенциальное исполнение (opportunity), НЕ fill:

Политика (фиксированная до оценки, не подбирается по данным):
- каждые DEPTH_TICK (появлении валидного снимка пары) выставляется котировка:
  buy-maker на цене max(bid) + tick_size  (в очередь впереди лучшего bid),
  sell-maker на цене min(ask) - tick_size (в очередь впереди лучшего ask);
- размер по бюджету B: qty = B / цена, с ограничением min_amount и шагом;
- срок жизни котировки = DEPTH_TICK (по следующему снимку переставляем);
  объём конкурентов впереди на нашем уровне — из снимка (доля очереди).

Opportunity исполнения (касание):
- buy-maker: встречная СДЕЛКА с price <= нашей bid-цены (агрессор продаёт);
- sell-maker: встречная сделка с price >= нашей ask-цены;
- каждый trade ID засчитывается ОДНОЙ котировке (не повторно);
- касание = возможность исполнения, НЕ подтверждённый fill (очередь,
  частичное исполнение, приоритет неизвестны).

Markout: mid через H секунд после касания (10/30/60/300с); нет будущего
снимка в допуске => unobserved (не unchanged).

Вынужденный выход (если бы мы исполнились целиком и должны выйти):
- вариант maker->maker: продать по bid-глубине того же снимка (котировкой в
  очередь) — цена входа и выхода по уровням стакана;
- вариант maker->taker: немедленно продать по bid (taker) — худший случай;
- комиссии: maker 0.1% + maker 0.1% либо + taker 0.1% (подтверждены API).
"""
import argparse
import glob
import json
import os
import sys
import time
from collections import defaultdict
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maker.util import parse_iso_utc, valid_book

TICK_DEFAULT = {'PRLUSDT': 0.01, 'QUANTUSUSDT': 0.001, 'LTCUSDT': 0.00001}  # price_precision из markets
MIN_AMOUNT = {'PRLUSDT': 2.0, 'QUANTUSUSDT': 0.01, 'LTCUSDT': 0.0001}
AMOUNT_PREC = {'PRLUSDT': 4, 'QUANTUSUSDT': 3, 'LTCUSDT': 5}
MAX_DELAY_FRAC = 0.25
BUDGETS = (5.0, 10.0)
HORIZONS = (10, 30, 60, 300)


def _round_amount(pair, qty):
    prec = AMOUNT_PREC.get(pair, 4)
    q = round(qty, prec)
    return max(q, MIN_AMOUNT.get(pair, 0.0))


def opportunity_report(run_dir, run_id, cutoff_ns=None, budgets=BUDGETS,
                       horizons=HORIZONS, tick_size=None, depth_tick_s=20.0):
    depth, bad_d = [], 0
    tr_rows, bad_t = [], 0
    with open(os.path.join(run_dir, f'{run_id}_depth.jsonl')) as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                depth.append(json.loads(ln))
            except ValueError:
                bad_d += 1
    with open(os.path.join(run_dir, f'{run_id}_trades.jsonl')) as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                tr_rows.append(json.loads(ln))
            except ValueError:
                bad_t += 1
    manifest = {}
    mp = os.path.join(run_dir, f'{run_id}_manifest.json')
    if os.path.exists(mp):
        manifest = json.load(open(mp))
    started_ts = float(manifest.get('started_epoch') or 0.0)
    if started_ts <= 0:
        started_ts = parse_iso_utc(manifest.get('started_utc')) or 0.0
    if cutoff_ns is None:
        last = started_ts
        for r in reversed(depth):
            if isinstance(r, dict) and r.get('t'):
                last = max(last, float(r['t']) / 1e9)
                break
        cutoff_ts = last
    else:
        cutoff_ts = float(cutoff_ns) / 1e9
    window_h = (cutoff_ts - started_ts) / 3600.0

    # снимки по парам (валидные), отсортированы по времени
    snaps_by = defaultdict(list)
    for r in depth:
        if not isinstance(r, dict) or not r.get('pair'):
            continue
        rcv = (r.get('t') or 0) / 1e9
        if not (started_ts <= rcv <= cutoff_ts):
            continue
        ok, bb, ba = valid_book(r.get('bids'), r.get('asks'))
        if ok:
            snaps_by[r['pair']].append((rcv, r['bids'], r['asks']))
    # сделки по парам (уникальные id, строго в окне)
    tr_by = defaultdict(list)
    seen = set()
    for r in tr_rows:
        if not isinstance(r, dict) or not r.get('pair'):
            continue
        rcv = (r.get('t') or 0) / 1e9
        if not (started_ts <= rcv <= cutoff_ts):
            continue
        for t in r.get('trades', []):
            if not isinstance(t, dict) or 'id' not in t:
                continue
            key = (r['pair'], t['id'])
            if key in seen:
                continue
            seen.add(key)
            ts = parse_iso_utc(t.get('created_at'))
            if ts is None or not (started_ts <= ts <= cutoff_ts):
                continue
            try:
                tpx = float(t.get('price'))
            except (TypeError, ValueError):
                continue
            tr_by[r['pair']].append((ts, tpx, float(t.get('amount') or 0), t['id']))

    out = {
        'run_id': run_id, 'started_utc': manifest.get('started_utc'),
        'cutoff_ns': int(cutoff_ts * 1e9),
        'window_h': round(window_h, 4),
        'policy': {
            'side': 'maker buy на best_bid+tick, sell на best_ask-tick',
            'life': f'{depth_tick_s}с (перестановка на каждом снимке)',
            'budgets': list(budgets), 'horizons': list(horizons),
            'note': ('касание = opportunity, НЕ fill; очередь/приоритет/частичное '
                     'исполнение неизвестны; fee maker=0.1% taker=0.1% (API)'),
        },
        'bad_lines': {'depth': bad_d, 'trades': bad_t},
        'pairs': [],
    }
    for pair in sorted(set(list(snaps_by) + list(tr_by))):
        snaps = snaps_by.get(pair, [])
        trades = tr_by.get(pair, [])
        o = {'symbol': pair, 'n_snaps': len(snaps), 'n_trades': len(trades)}
        tick = (tick_size or {}).get(pair) or TICK_DEFAULT.get(pair, 0.01)
        o['tick_size'] = tick
        o['by_budget'] = {}
        for B in budgets:
            used_trade = set()   # сделка = касание один раз ДЛЯ ЭТОГО бюджета
            bres = {'n_quotes': 0, 'n_touch': 0, 'n_buy_touch': 0, 'n_sell_touch': 0,
                    'touch_trades': 0, 'markout': {}, 'unobserved': {},
                    'forced_exit_mm': [], 'forced_exit_mt': []}
            # политика: на каждом снимке котировка buy и sell на бюджет B
            for i, (rcv, bids, asks) in enumerate(snaps):
                best_bid = max(float(x[0]) for x in bids)
                best_ask = min(float(x[0]) for x in asks)
                # buy-maker: улучшаем лучший bid на tick ЕСЛИ не пересекаем ask,
                # иначе встаём в очередь на СУЩЕСТВУЮЩЕМ уровне bid (спред = 1 шаг)
                buy_px = best_bid + tick
                buy_improved = buy_px < best_ask - 1e-12
                if not buy_improved:
                    buy_px = best_bid
                # sell-maker: улучшаем лучший ask на tick, иначе очередь на ask
                sell_px = best_ask - tick
                sell_improved = sell_px > best_bid + 1e-12
                if not sell_improved:
                    sell_px = best_ask
                qty = _round_amount(pair, B / buy_px)
                bres['n_quotes'] += 2
                bres['n_improved'] = bres.get('n_improved', 0) + (1 if buy_improved else 0) + (1 if sell_improved else 0)
                bres['n_queued'] = bres.get('n_queued', 0) + (0 if buy_improved else 1) + (0 if sell_improved else 1)
                # очередь конкурентов на нашем buy-уровне: объём bid по цене >= buy_px
                q_buy = sum(float(q) for px, q in bids if float(px) >= buy_px - 1e-12)
                q_sell = sum(float(q) for px, q in asks if float(px) <= sell_px + 1e-12)
                bres.setdefault('queue_buy', []).append(q_buy)
                bres.setdefault('queue_sell', []).append(q_sell)
                # Для queued-котировки конкурирующий объём на нашем уровне:
                # в очереди мы ПОСЛЕ q_buy (при buy_px==best_bid q_buy включ. нас нет —
                # это объём чужих заявок на уровне; своя qty добавляется сверху).
                # Касание = opportunity ТОЛЬКО если конкурирующий объём < наша qty
                # (иначе очередь впереди нас съедает встречный поток — слабый сигнал).
                buy_strong = buy_improved or q_buy < qty
                sell_strong = sell_improved or q_sell < qty
                bres.setdefault('_strong', []).append((buy_strong, sell_strong))
                # противоположный поток в срок жизни котировки (до след. снимка)
                t_end = snaps[i + 1][0] if i + 1 < len(snaps) else cutoff_ts
                # встречные сделки в [rcv, t_end] на нашей цене
                for ts, tpx, tamt, tid in trades:
                    if not (rcv - 1e-9 <= ts <= t_end):
                        continue
                    # buy-maker исполняется сделкой price <= buy_px (агрессор продаёт)
                    if tpx <= buy_px + 1e-9 and buy_strong:
                        if tid not in used_trade:
                            used_trade.add(tid)
                            bres['n_buy_touch'] += 1
                            bres['touch_trades'] += 1
                        # markout: mid через H (по уникальной касательной сделке)
                        for H in horizons:
                            j = i
                            while j < len(snaps) and snaps[j][0] < ts + H - max(5.0, H * MAX_DELAY_FRAC):
                                j += 1
                            if j < len(snaps) and abs(snaps[j][0] - (ts + H)) <= max(5.0, H * MAX_DELAY_FRAC):
                                _, bj, aj = snaps[j]
                                mid_f = (max(float(x[0]) for x in bj) + min(float(x[0]) for x in aj)) / 2
                                bres['markout'].setdefault(str(H), []).append(round((mid_f - buy_px) / buy_px * 1e4, 2))
                            else:
                                bres['unobserved'][str(H)] = bres['unobserved'].get(str(H), 0) + 1
                        # вынужденный выход: продать qty (для каждой касательной сделки
                        # только один раз — по первому касанию сделки в этом снимке)
                        if tid not in [x[0] for x in bres.get('_exited', [])]:
                            bres.setdefault('_exited', []).append((tid, qty, bids, best_bid, buy_px))
                    # sell-maker: сделка price >= sell_px (агрессор покупает)
                    elif tpx >= sell_px - 1e-9 and sell_strong:
                        if tid not in used_trade:
                            used_trade.add(tid)
                            bres['n_sell_touch'] += 1
                            bres['touch_trades'] += 1
            # стоимость вынужденного выхода по сохранённым касаниям
            for tid, qty, bids, best_bid, buy_px in bres.get('_exited', []):
                filled = 0.0; avg_px = 0.0
                for px, q in sorted(bids, key=lambda x: -float(x[0])):
                    need = qty - filled
                    if need <= 0:
                        break
                    take = min(need, float(q))
                    avg_px = (avg_px * filled + take * float(px)) / (filled + take)
                    filled += take
                if filled >= qty * 0.9999:
                    cost_mm = (avg_px - buy_px) * qty
                    fee_mm = 0.001 * (buy_px * qty + avg_px * qty)
                    bres['forced_exit_mm'].append(round((cost_mm + fee_mm) / (buy_px * qty) * 1e4, 2))
                else:
                    bres['forced_exit_mm'].append(None)
                cost_mt = (best_bid - buy_px) * qty
                fee_mt = 0.001 * (buy_px * qty + best_bid * qty)
                bres['forced_exit_mt'].append(round((cost_mt + fee_mt) / (buy_px * qty) * 1e4, 2))
            # убрать служебное
            bres.pop('_exited', None)
            bres['n_touch'] = bres['n_buy_touch'] + bres['n_sell_touch']
            bres['queue_buy_avg'] = round(sum(bres.get('queue_buy', [])) / len(bres.get('queue_buy', [])), 4) if bres.get('queue_buy') else None
            bres['queue_sell_avg'] = round(sum(bres.get('queue_sell', [])) / len(bres.get('queue_sell', [])), 4) if bres.get('queue_sell') else None
            bres['forced_exit_mm_bps'] = _avg(bres['forced_exit_mm'])
            bres['forced_exit_mt_bps'] = _avg(bres['forced_exit_mt'])
            o['by_budget'][str(int(B))] = bres
        out['pairs'].append(o)
    return out


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


def main():
    ap = argparse.ArgumentParser(description='Opportunity-анализ maker-котировок')
    ap.add_argument('--run-id', default=None)
    ap.add_argument('--data-dir', default=None)
    ap.add_argument('--cutoff-ns', type=int, default=None)
    ap.add_argument('--json-out', default=None)
    ap.add_argument('--latest', action='store_true')
    args = ap.parse_args()
    base = args.data_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'maker')
    run_id = args.run_id
    if args.latest or not run_id:
        runs = sorted({os.path.basename(f).replace('_depth.jsonl', '')
                       for f in glob.glob(os.path.join(base, '*_depth.jsonl'))})
        run_id = run_id or (runs[-1] if runs else None)
    if not run_id:
        print('no run found')
        return
    res = opportunity_report(base, run_id, cutoff_ns=args.cutoff_ns)
    jout = args.json_out or os.path.join(os.path.dirname(base), 'reports',
                                         f'opp_{run_id}.json')
    os.makedirs(os.path.dirname(jout), exist_ok=True)
    with open(jout, 'w') as f:
        json.dump(res, f, indent=1, ensure_ascii=False)
    print(f"opp {run_id}: window_h={res['window_h']} (политика: {res['policy']['side']})")
    for p in res['pairs']:
        for B in ('5', '10'):
            b = p['by_budget'].get(B, {})
            mk = {h: (round(sum(v)/len(v), 2) if v else None) for h, v in b.get('markout', {}).items()}
            print(f"  {p['symbol']:<13} B={B}USDT котировок={b.get('n_quotes')} касаний={b.get('n_touch')} "
                  f"(buy={b.get('n_buy_touch')} sell={b.get('n_sell_touch')}) markout_bps={mk} "
                  f"exit_mm={b.get('forced_exit_mm_bps')}bps exit_mt={b.get('forced_exit_mt_bps')}bps")
    print(f"  -> {jout}")


if __name__ == '__main__':
    main()