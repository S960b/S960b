"""Opportunity-анализ гипотетических maker-котировок (ревью 2467cb2).

МОДЕЛЬ (всё offline, БЕЗ ордеров; opportunity, НЕ fill):

Политика котировок (фиксированная до оценки):
- на каждом валидном снимке пары выставляется buy и sell на бюджет B;
- buy:  best_bid + tick, если НЕ пересекает ask (improved), иначе best_bid (queued);
  sell: best_ask - tick, если НЕ пересекает bid (improved), иначе best_ask (queued);
- ЗАПРЕТ self-cross: buy < sell обязателен; если улучшение обеих сторон даёт
  buy >= sell — sell остаётся queued (n_improved <= 1 на снимок);
- qty: Decimal, округление ВНИЗ по amount_precision (шагу количества);
  стоимость qty*price <= budget; при qty < min_amount котировка отклонена
  (в отчёт с причиной); НЕ увеличиваем qty до минимума;
- TTL котировки: min(created_at + ttl, момент замены следующим снимком, cutoff);
  интервал активности [created_at, expires_at).

Касание и opportunity (единая дедупликация по trade-id):
- price match: уникальный trade-id пересёк цену активной котировки
  (buy: trade.price <= buy_px; sell: trade.price >= sell_px) — диагностический факт;
- directed opportunity: ПОЛОЖИТЕЛЬНЫЙ объём сделки И сторона агрессора
  подтверждена; для buy-котировки встречная сторона = sell, для sell = buy;
  без подтверждённого side — только price match (side_unverified), без счёта;
- queue scenario (явная статическая FIFO-гипотеза): встречный объём сначала
  погашает объём впереди (queue_ahead), остаток — потенциальный объём нашей
  заявки: potential_qty = max(0, trade_volume - queue_ahead); partial/full
  относительно our_qty; исчезновение/добавление чужих заявок неизвестно —
  это НЕ подтверждённый fill;
- один trade-id засчитывается один раз: markout/unobserved пишутся единожды.

Экономика выхода (после opportunity, условно):
- exit оценивается по стакану ПОСЛЕ касания (ближайший валидный снимок >= ts
  с ограничением задержки), не по стакану до котировки;
- maker->taker: VWAP продажи всей нашей qty по bid-уровням (все уровни, не
  только best); при нехватке всего bid-стакана — exit_qty < qty (не полный);
- maker->maker: НЕ моделируется как рыночная продажа; остается None/не
  подтверждён до контракта отдельной лимитной котировки (явно помечен);
- net_pnl = exit_notional - entry_notional - fee_entry - fee_exit;
  cost_bps = 10000 * (entry_px - exit_vwap) * qty + fee* (в валюте) /
             entry_notional  — ПОЛОЖИТЕЛЬНАЯ стоимость потери;
  поля forced_exit_*_bps — положительный расход; net_pnl_*_bps — со знаком.
- комиссии: maker=taker=0.001 (10 bps), источник в отчёте (fee_source).
"""
import argparse
import glob
import json
import os
import sys
import time
from collections import defaultdict
from decimal import Decimal, ROUND_DOWN

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from maker.util import parse_iso_utc, valid_book

TICK_DEFAULT = {'PRLUSDT': Decimal('0.01'), 'QUANTUSUSDT': Decimal('0.001'),
                'LTCUSDT': Decimal('0.00001')}
MIN_AMOUNT = {'PRLUSDT': Decimal('2'), 'QUANTUSUSDT': Decimal('0.01'),
              'LTCUSDT': Decimal('0.0001')}
AMOUNT_PREC = {'PRLUSDT': Decimal('0.0001'), 'QUANTUSUSDT': Decimal('0.001'),
               'LTCUSDT': Decimal('0.00001')}
FEE = Decimal('0.001')          # maker = taker = 10 bps (подтверждено API)
FEE_SOURCE = ('GET /api/v2/trade/public/trading_fees (2026-10-04): maker=0.001 '
              'taker=0.001, market_id=any; подтверждено сделками аккаунта (10 bps)')
MAX_DELAY_FRAC = 0.25
DEFAULT_TTL_S = 20.0


def _qty_for_budget(pair, price, budget, fee_bps=FEE):
    """qty = floor(budget/price) по шагу количества; None если < min_amount.
    Цена/бюджет — Decimal; стоимость qty*price <= budget (округление вниз)."""
    step = AMOUNT_PREC.get(pair)
    if step is None:
        return None
    raw = Decimal(str(budget)) / Decimal(str(price))
    qty = (raw / step).to_integral_value(rounding=ROUND_DOWN) * step
    if qty < MIN_AMOUNT.get(pair, Decimal('0')):
        return None
    return qty


def _round_amount(pair, qty):
    """Совместимая обёртка: округление вниз по шагу (тесты ревью 2467cb2)."""
    step = AMOUNT_PREC.get(pair)
    if step is None:
        return None
    q = (Decimal(str(qty)) / step).to_integral_value(rounding=ROUND_DOWN) * step
    if q <= 0:
        return None
    return float(q)


def _mid(bids, asks):
    bb = max(float(x[0]) for x in bids)
    ba = min(float(x[0]) for x in asks)
    return (bb + ba) / 2.0


def _taker_exit(bids, qty):
    """VWAP всей qty по bid-уровням (все уровни). Возвращает (avg_px, filled) или (None, filled)."""
    filled = Decimal('0')
    cost = Decimal('0')
    for px, q in sorted(bids, key=lambda x: -Decimal(str(x[0]))):
        need = qty - filled
        if need <= 0:
            break
        take = min(need, Decimal(str(q)))
        cost += take * Decimal(str(px))
        filled += take
    if filled <= 0:
        return None, filled
    return float(cost / filled), float(filled)


def opportunity_report(run_dir, run_id, cutoff_ns=None, budgets=(5.0, 10.0),
                       horizons=(10, 30, 60, 300), tick_size=None, ttl_s=DEFAULT_TTL_S,
                       depth_tick_s=None):
    # совместимость: ревью-фикстуры передают depth_tick_s (прежнее имя TTL)
    if depth_tick_s is not None:
        ttl_s = depth_tick_s
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
        cutoff_ns = int(round(cutoff_ts * 1e9))
    else:
        cutoff_ts = float(cutoff_ns) / 1e9
    window_h = (cutoff_ts - started_ts) / 3600.0

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
            try:
                tamt = float(t.get('amount') or 0)
            except (TypeError, ValueError):
                tamt = 0.0
            tr_by[r['pair']].append({'ts': ts, 'price': tpx, 'amount': tamt,
                                     'id': t['id'], 'side': t.get('side')})

    out = {
        'run_id': run_id, 'started_utc': manifest.get('started_utc'),
        'cutoff_ns': cutoff_ns,
        'window_h': round(window_h, 4),
        'policy': {
            'side': 'maker buy на best_bid+tick (или очереди), sell на best_ask-tick (или очереди), buy<sell',
            'ttl_s': ttl_s,
            'budgets': list(budgets), 'horizons': list(horizons),
            'queue': 'статическая FIFO-гипотеза: встречный объём гасит queue_ahead, '
                     'остаток potential_qty; НЕ подтверждённый fill',
            'side_semantics': manifest.get('trade_side_semantics', 'unverified'),
            'side_status': manifest.get('trade_side_status', 'unverified'),
            'fee_maker_bps': float(FEE * Decimal('1e4')), 'fee_taker_bps': float(FEE * Decimal('1e4')),
            'fee_source': FEE_SOURCE,
            'invalid_model': False,
        },
        'bad_lines': {'depth': bad_d, 'trades': bad_t},
        'pairs': [],
    }
    for pair in sorted(set(list(snaps_by) + list(tr_by))):
        snaps = snaps_by.get(pair, [])
        trades = sorted(tr_by.get(pair, []), key=lambda x: x['ts'])
        o = {'symbol': pair, 'n_snaps': len(snaps), 'n_trades': len(trades)}
        tick = (tick_size or {}).get(pair)
        if tick is None:
            tick = TICK_DEFAULT.get(pair)
        tick_d = Decimal(str(tick)) if tick is not None else Decimal('0.01')
        o['tick_size'] = float(tick_d)
        o['by_budget'] = {}
        for B in budgets:
            used = set()          # trade-id, засчитанные в направленные opportunity
            used_match = set()    # trade-id, засчитанные в price match (markout)
            bres = {'n_quotes': 0, 'n_rejected': 0, 'rejected_reasons': {},
                    'n_price_match': 0, 'n_buy_match': 0, 'n_sell_match': 0,
                    'n_touch': 0, 'n_buy_touch': 0, 'n_sell_touch': 0,
                    'potential_qty': [], 'queue_ahead': [], 'markout': {},
                    'markout_sell': {}, 'unobserved': {}, 'unobserved_sell': {},
                    'forced_exit_mt_bps_raw': [], 'net_pnl_mt_bps_raw': [],
                    'exit_qty_full': 0, 'exit_qty_partial': 0, 'exit_qty_none': 0,
                    'n_improved': 0, 'n_queued': 0, 'audit': []}
            for idx, (rcv, bids, asks) in enumerate(snaps):
                best_bid = max(Decimal(str(x[0])) for x in bids)
                best_ask = min(Decimal(str(x[0])) for x in asks)
                buy_px = best_bid + tick_d
                buy_improved = buy_px < best_ask - Decimal('1e-9')
                if not buy_improved:
                    buy_px = best_bid
                sell_px = best_ask - tick_d
                sell_improved = sell_px > best_bid + Decimal('1e-9')
                if not sell_improved:
                    sell_px = best_ask
                # запрет self-cross: если улучшенные стороны пересеклись — sell в очередь
                if buy_improved and sell_improved and buy_px >= sell_px:
                    sell_px = best_ask
                    sell_improved = False
                # активность истекает: TTL или замена следующим снимком
                expires = min(rcv + ttl_s,
                              snaps[idx + 1][0] if idx + 1 < len(snaps) else cutoff_ts)
                qty = _qty_for_budget(pair, buy_px, Decimal(str(B)))
                if qty is None:
                    bres['n_rejected'] += 1
                    bres['rejected_reasons']['below_min'] = bres['rejected_reasons'].get('below_min', 0) + 1
                    continue
                bres['n_quotes'] += 2
                bres['n_improved'] += (1 if buy_improved else 0) + (1 if sell_improved else 0)
                bres['n_queued'] += (0 if buy_improved else 1) + (0 if sell_improved else 1)
                q_ahead = sum(Decimal(str(q)) for px, q in bids if Decimal(str(px)) >= buy_px - Decimal('1e-9'))
                bres['queue_ahead'].append(float(q_ahead))
                # направленные opportunity: только активные котировки
                for t in trades:
                    ts = t['ts']
                    if not (rcv - 1e-9 <= ts < expires):
                        continue
                    tpx_d = Decimal(str(t['price']))
                    tamt = t['amount']
                    tid = t['id']
                    side = t['side']
                    # buy-котировка: встречная сторона = sell
                    if tpx_d <= buy_px + Decimal('1e-9'):
                        # price match — диагностический факт, независимо от стороны/объёма
                        if tid not in used_match:
                            used_match.add(tid)
                            bres['n_price_match'] += 1
                            bres['n_buy_match'] += 1
                        # directed: положительный объём И подтверждённая встречная сторона
                        if tamt > 0 and side == 'sell' and tid not in used:
                            used.add(tid)
                            bres['n_touch'] += 1
                            bres['n_buy_touch'] += 1
                            # потенциальный объём нашей заявки: остаток после очереди
                            potential = max(Decimal('0'),
                                            Decimal(str(tamt)) - q_ahead)
                            bres['potential_qty'].append(float(min(potential, qty)))
                            # markout от цены котировки buy_px
                            for H in horizons:
                                target = ts + H
                                tol = max(5.0, H * MAX_DELAY_FRAC)
                                j = idx
                                while j < len(snaps) and snaps[j][0] < target - tol:
                                    j += 1
                                if j < len(snaps) and abs(snaps[j][0] - target) <= tol:
                                    _, bj, aj = snaps[j]
                                    mf = _mid(bj, aj)
                                    bres['markout'].setdefault(str(H), []).append(
                                        round((mf - float(buy_px)) / float(buy_px) * 1e4, 2))
                                else:
                                    bres['unobserved'][str(H)] = bres['unobserved'].get(str(H), 0) + 1
                            # условный выход ПОСЛЕ касания: ближайший снимок >= ts
                            j = idx
                            while j < len(snaps) and snaps[j][0] < ts:
                                j += 1
                            if j < len(snaps):
                                _, bj, _aj = snaps[j]
                                avg_px, filled = _taker_exit(bj, qty)
                                if avg_px is not None and filled >= float(qty) - 1e-9:
                                    bres['exit_qty_full'] += 1
                                    entry_notional = float(qty) * float(buy_px)
                                    exit_notional = float(qty) * avg_px
                                    fee_entry = float(FEE) * entry_notional
                                    fee_exit = float(FEE) * exit_notional
                                    net_pnl = exit_notional - entry_notional - fee_entry - fee_exit
                                    net_bps = net_pnl / entry_notional * 1e4
                                    cost_bps = -net_bps if net_pnl < 0 else net_bps
                                    bres['forced_exit_mt_bps_raw'].append(round(cost_bps, 2))
                                    bres['net_pnl_mt_bps_raw'].append(round(net_bps, 2))
                                elif avg_px is not None:
                                    bres['exit_qty_partial'] += 1
                                    bres['forced_exit_mt_bps_raw'].append(None)
                                else:
                                    bres['exit_qty_none'] += 1
                                    bres['forced_exit_mt_bps_raw'].append(None)
                            else:
                                bres['exit_qty_none'] += 1
                                bres['forced_exit_mt_bps_raw'].append(None)
                            bres['audit'].append({
                                'quote_idx': idx, 'side': 'buy', 'price': float(buy_px),
                                'qty': float(qty), 'created_at': rcv, 'expires_at': expires,
                                'trade_id': tid, 'trade_side': side, 'trade_price': t['price'],
                                'trade_amount': tamt,
                            })
                    # sell-котировка: встречная сторона = buy
                    elif tpx_d >= sell_px - Decimal('1e-9'):
                        if tid not in used_match:
                            used_match.add(tid)
                            bres['n_price_match'] += 1
                            bres['n_sell_match'] += 1
                        if tamt > 0 and side == 'buy' and tid not in used:
                            used.add(tid)
                            bres['n_touch'] += 1
                            bres['n_sell_touch'] += 1
                            potential = max(Decimal('0'),
                                            Decimal(str(tamt)) - q_ahead)
                            bres['potential_qty'].append(float(min(potential, qty)))
                            for H in horizons:
                                target = ts + H
                                tol = max(5.0, H * MAX_DELAY_FRAC)
                                j = idx
                                while j < len(snaps) and snaps[j][0] < target - tol:
                                    j += 1
                                if j < len(snaps) and abs(snaps[j][0] - target) <= tol:
                                    _, bj, aj = snaps[j]
                                    mf = _mid(bj, aj)
                                    bres['markout_sell'].setdefault(str(H), []).append(
                                        round((float(sell_px) - mf) / float(sell_px) * 1e4, 2))
                                else:
                                    bres['unobserved_sell'][str(H)] = bres['unobserved_sell'].get(str(H), 0) + 1
                            bres['audit'].append({
                                'quote_idx': idx, 'side': 'sell', 'price': float(sell_px),
                                'qty': float(qty), 'created_at': rcv, 'expires_at': expires,
                                'trade_id': tid, 'trade_side': side, 'trade_price': t['price'],
                                'trade_amount': tamt,
                            })
            # агрегаты
            bres['forced_exit_mt_bps'] = _avg(bres['forced_exit_mt_bps_raw'])
            bres['net_pnl_mt_bps'] = _avg(bres['net_pnl_mt_bps_raw'])
            bres['queue_ahead_avg'] = _avg(bres['queue_ahead'])
            bres['potential_qty_avg'] = _avg(bres['potential_qty'])
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
    print(f"opp {run_id}: window_h={res['window_h']} (invalid_model=False)")
    for p in res['pairs']:
        for B in ('5', '10'):
            b = p['by_budget'].get(B, {})
            mk = {h: (round(sum(v)/len(v), 2) if v else None) for h, v in b.get('markout', {}).items()}
            print(f"  {p['symbol']:<13} B={B}USDT quotes={b.get('n_quotes')} rejected={b.get('n_rejected')} "
                  f"match={b.get('n_price_match')} touch={b.get('n_touch')} (buy={b.get('n_buy_touch')} sell={b.get('n_sell_touch')}) "
                  f"improved={b.get('n_improved')} queued={b.get('n_queued')} "
                  f"markout_bps={mk} exit_mt={b.get('forced_exit_mt_bps')} net_pnl={b.get('net_pnl_mt_bps')}")
    print(f"  -> {jout}")


if __name__ == '__main__':
    main()