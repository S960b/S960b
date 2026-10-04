"""Opportunity-анализ гипотетических maker-котировок (ревью ba9f1cb).

МОДЕЛЬ (offline, БЕЗ ордеров; opportunity, НЕ fill; portfolio не моделируется):

Политика котировок (фиксированная до оценки):
- на каждом валидном снимке пары выставляется buy и sell на бюджет B;
- buy:  best_bid + tick, если НЕ пересекает ask (improved), иначе best_bid (queued);
  sell: best_ask - tick, если НЕ пересекает bid (improved), иначе best_ask (queued);
- ЗАПРЕТ self-cross: buy < sell; при пересечении улучшенных сторон sell -> queued;
- qty: Decimal, округление ВНИЗ по amount_precision; стоимость <= budget;
  qty < min_amount => котировка отклонена (причина в отчёте);
- TTL: активность = [created_at, min(created_at+ttl, замена след. снимком, cutoff)).

Сторона сделки (контракт):
- направленные opportunity ТОЛЬКО если manifest подтверждает
  trade_side_semantics == 'aggressor' И trade_side_status подтверждён
  (не 'unverified', не пустой). Иначе — только price matches (диагностика) и
  side_unverified-сценарий, без направленного счёта/экономики.

Касание/очередь (статическая FIFO-гипотеза, явно объявлена):
- buy-котировка: встречная сторона sell; очередь = объём bids по цене >= buy_px;
  sell-котировка: встречная сторона buy; очередь = объём asks по цене <= sell_px;
- состояние на КАЖДУЮ активную котировку: queue_remaining, order_remaining;
  каждая встречная сделка (положительный объём, подтверждённая сторона,
  уникальный trade-id) сначала гасит queue_remaining, остаток — потенциальный
  объём нашей заявки, ограниченный order_remaining:
  potential = min(max(0, trade_volume - queue_remaining), order_remaining);
  order_remaining уменьшается; заявка не может получить больше своей qty;
- допущение: сделка на цене ниже buy_px (выше sell_px) трактуется как
  проходящая наш уровень после погашения очереди на уровне; это допущение,
  не восстановленная книга; помечено в policy.model_assumptions;
- price match (уникальный trade-id пересёк цену) — диагностика, считается
  отдельно и независимо от directed; один trade-id — одно пересечение;
- потенциальный объём 0 НЕ порождает касание/выход/экономику.

Экономика выхода (только для potential_qty > 0):
- exit по стакану ПОСЛЕ касания: первый валидный снимок >= ts сделки с
  задержкой <= max_exit_delay_s (по умолчанию 60с); иначе unobserved;
- maker->taker: VWAP продажи potential_qty по ВСЕМ bid-уровням; полный выход
  только если filled >= potential_qty (точность Decimal); иначе partial/none;
- net_pnl = exit_notional - entry_notional - fee_entry - fee_exit по
  ПОТЕНЦИАЛЬНОМУ объёму (не номиналу заявки);
- forced_exit_mt_bps = signed cost = -net_pnl_bps: прибыльный выход даёт
  ОТРИЦАТЕЛЬНЫЙ cost (не abs!); loss_only = max(0, -net) отдельно не средним;
- maker->maker не моделируется (отдельная лимитка, поток, неизвестное
  исполнение) — не выдумываем;
- комиссии: maker=taker=0.001 (10 bps), источник в policy.

Статусы: data_quality / model_assumptions / decision_ready / reasons —
invalid_model НЕ задаётся безусловно; decision_ready=False всегда (это
сценарная модель, не реальный портфель).
"""
import argparse
import glob
import hashlib
import json
import os
import subprocess
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
              'taker=0.001, market_id=any; поля maker_fee/taker_fee ордеров '
              'аккаунта = 0.001; taker-исполнения аккаунта: 10.00 bps')
MAX_DELAY_FRAC = 0.25
DEFAULT_TTL_S = 20.0
DEFAULT_MAX_EXIT_DELAY_S = 60.0
CONFIRMED_SIDE_STATUSES = frozenset({'confirmed', 'verified', 'fixture_verified'})


def _analyzer_provenance():
    """Identify this source file; do not reuse the collector's manifest commit."""
    source = os.path.realpath(__file__)
    with open(source, 'rb') as fh:
        source_hash = hashlib.sha256(fh.read()).hexdigest()
    info = {'analyzer_commit': 'unknown', 'analyzer_dirty': None,
            'analyzer_source_sha256': source_hash}
    git = ['git', '-C', os.path.dirname(source)]
    try:
        tracked = subprocess.run(git + ['ls-files', '--error-unmatch', os.path.basename(source)],
                                 capture_output=True, text=True, timeout=2)
        if tracked.returncode == 0:
            head = subprocess.run(git + ['rev-parse', 'HEAD'],
                                  capture_output=True, text=True, timeout=2)
            if head.returncode == 0 and head.stdout.strip():
                info['analyzer_commit'] = head.stdout.strip()
            dirty = subprocess.run(git + ['diff', '--quiet', 'HEAD', '--', os.path.basename(source)],
                                   capture_output=True, timeout=2)
            if dirty.returncode in (0, 1):
                info['analyzer_dirty'] = dirty.returncode == 1
    except (OSError, subprocess.TimeoutExpired):
        pass  # A copied deployment still has an exact source hash.
    return info


def _qty_for_budget(pair, price, budget):
    """qty = floor(budget/price) по шагу количества; None если < min_amount."""
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
    """VWAP по bid-уровням (все уровни). (avg_px, filled) или (None, filled)."""
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
                       depth_tick_s=None, max_exit_delay_s=DEFAULT_MAX_EXIT_DELAY_S):
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

    sem = manifest.get('trade_side_semantics')
    st = manifest.get('trade_side_status')
    side_confirmed = (sem == 'aggressor' and isinstance(st, str)
                      and st in CONFIRMED_SIDE_STATUSES)
    side_desc = (f'semantics={sem!r} status={st!r} -> '
                 + ('confirmed(aggressor)' if side_confirmed
                    else 'UNVERIFIED: только price matches + side_unverified'))
    out = {
        'run_id': run_id, 'started_utc': manifest.get('started_utc'),
        'cutoff_ns': cutoff_ns,
        'window_h': round(window_h, 4),
        'schema_version': 3,
        **_analyzer_provenance(),
        'collector_commit': manifest.get('code_commit') or 'unknown',
        'source_manifest': f'{run_id}_manifest.json',
        'generated_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'policy': {
            'side': 'maker buy на best_bid+tick (или очереди), sell на best_ask-tick (или очереди), buy<sell',
            'ttl_s': ttl_s, 'max_exit_delay_s': max_exit_delay_s,
            'budgets': list(budgets), 'horizons': list(horizons),
            'queue': 'статическая FIFO-гипотеза: встречный объём гасит queue_remaining '
                     '(отдельно buy/sell), остаток potential <= order_remaining; '
                     'НЕ подтверждённый fill; цена вне уровня — допущение о прохождении',
            'side_contract': side_desc,
            'fee_maker_bps': float(FEE * Decimal('1e4')),
            'fee_taker_bps': float(FEE * Decimal('1e4')),
            'fee_source': FEE_SOURCE,
        },
        'data_quality': {
            'bad_lines': {'depth': bad_d, 'trades': bad_t},
            'side_confirmed': side_confirmed,
        },
        'model_assumptions': [
            'статическая FIFO-очередь на уровне котировки; исчезновение/добавление '
            'чужих заявок неизвестно',
            'сделка глубже уровня котировки считается проходящей наш уровень после '
            'погашения очереди уровня (не восстановленная книга)',
            'выход после касания по первому снимку в пределах max_exit_delay_s; '
            'наблюдаемая цена REST-снимка не доказывает исполнимость',
            'сторона агрессора используется ТОЛЬКО при подтверждённом контракте',
            'комиссии maker=taker=10 bps в quote-валюте; частичные/нулевые входы '
            'не смешиваются с номинальной заявкой',
            'сценарий, не портфель: повторные полные входы независимы; доход '
            'портфеля и real fills не моделируются',
        ],
        'decision_ready': False,
        'reasons': ['сценарная модель opportunity; требуется подтверждение '
                    'стороны, реальные fill/очередь/доход не доказаны'],
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
            used = set()          # trade-id в направленных opportunity
            used_match = set()    # trade-id в price matches (диагностика)
            bres = {'n_quotes': 0, 'n_rejected': 0, 'rejected_reasons': {},
                    'n_price_match': 0, 'n_buy_match': 0, 'n_sell_match': 0,
                    'n_touch': 0, 'n_buy_touch': 0, 'n_sell_touch': 0,
                    'potential_qty': [], 'queue_ahead_buy': [], 'queue_ahead_sell': [],
                    'markout': {}, 'markout_sell': {},
                    'unobserved': {}, 'unobserved_sell': {},
                    'forced_exit_mt_bps_raw': [], 'net_pnl_mt_bps_raw': [],
                    'exit_qty_full': 0, 'exit_qty_partial': 0, 'exit_qty_none': 0,
                    'exit_qty_unobserved': 0, 'n_zero_potential': 0,
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
                if buy_improved and sell_improved and buy_px >= sell_px:
                    sell_px = best_ask
                    sell_improved = False
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
                # отдельные очереди: buy — по bids, sell — по asks (ba9f1cb п.2)
                q_buy = sum(Decimal(str(q)) for px, q in bids if Decimal(str(px)) >= buy_px - Decimal('1e-9'))
                q_sell = sum(Decimal(str(q)) for px, q in asks if Decimal(str(px)) <= sell_px + Decimal('1e-9'))
                bres['queue_ahead_buy'].append(float(q_buy))
                bres['queue_ahead_sell'].append(float(q_sell))
                # состояние котировки в течение жизни (ba9f1cb п.3)
                q_rem_buy, ord_rem_buy = q_buy, qty
                q_rem_sell, ord_rem_sell = q_sell, qty
                for t in trades:
                    ts = t['ts']
                    if not (rcv - 1e-9 <= ts < expires):
                        continue
                    tpx_d = Decimal(str(t['price']))
                    tamt = t['amount']
                    tid = t['id']
                    side = t['side']
                    # --- buy-котировка: встречная сторона = sell
                    if tpx_d <= buy_px + Decimal('1e-9'):
                        if tid not in used_match:
                            used_match.add(tid)
                            bres['n_price_match'] += 1
                            bres['n_buy_match'] += 1
                        if (side_confirmed and tamt > 0 and side == 'sell'
                                and tid not in used):
                            used.add(tid)
                            audit_context = {
                                'trade_id': tid, 'trade_timestamp': ts,
                                'trade_side': side, 'trade_price': t['price'],
                                'trade_amount': tamt, 'created_at': rcv, 'expires_at': expires,
                                'queue_before': str(q_rem_buy),
                                'order_remaining_before': str(ord_rem_buy),
                            }
                            # FIFO: сначала очередь, остаток — нам, лимит наша qty
                            consume = min(Decimal(str(tamt)), q_rem_buy)
                            q_rem_buy -= consume
                            avail = Decimal(str(tamt)) - consume
                            potential = min(avail, ord_rem_buy)
                            if potential > 0:
                                ord_rem_buy -= potential
                                bres['n_touch'] += 1
                                bres['n_buy_touch'] += 1
                                bres['potential_qty'].append(float(potential))
                                audit_context.update(queue_after=str(q_rem_buy),
                                                     order_remaining_after=str(ord_rem_buy))
                                _record_exit(bres, potential, qty, buy_px, ts, snaps, idx,
                                             horizons, 'buy', max_exit_delay_s=max_exit_delay_s,
                                             audit_context=audit_context)
                            else:
                                bres['n_zero_potential'] += 1
                    # --- sell-котировка: встречная сторона = buy
                    elif tpx_d >= sell_px - Decimal('1e-9'):
                        if tid not in used_match:
                            used_match.add(tid)
                            bres['n_price_match'] += 1
                            bres['n_sell_match'] += 1
                        if (side_confirmed and tamt > 0 and side == 'buy'
                                and tid not in used):
                            used.add(tid)
                            audit_context = {
                                'trade_id': tid, 'trade_timestamp': ts,
                                'trade_side': side, 'trade_price': t['price'],
                                'trade_amount': tamt, 'created_at': rcv, 'expires_at': expires,
                                'queue_before': str(q_rem_sell),
                                'order_remaining_before': str(ord_rem_sell),
                            }
                            consume = min(Decimal(str(tamt)), q_rem_sell)
                            q_rem_sell -= consume
                            avail = Decimal(str(tamt)) - consume
                            potential = min(avail, ord_rem_sell)
                            if potential > 0:
                                ord_rem_sell -= potential
                                bres['n_touch'] += 1
                                bres['n_sell_touch'] += 1
                                bres['potential_qty'].append(float(potential))
                                audit_context.update(queue_after=str(q_rem_sell),
                                                     order_remaining_after=str(ord_rem_sell))
                                _record_exit_sell(bres, potential, qty, sell_px, ts, snaps,
                                                  idx, horizons, 'sell',
                                                  audit_context=audit_context)
                            else:
                                bres['n_zero_potential'] += 1
            bres['forced_exit_mt_bps'] = _avg(bres['forced_exit_mt_bps_raw'])
            bres['net_pnl_mt_bps'] = _avg(bres['net_pnl_mt_bps_raw'])
            bres['queue_ahead_buy_avg'] = _avg(bres['queue_ahead_buy'])
            bres['queue_ahead_sell_avg'] = _avg(bres['queue_ahead_sell'])
            bres['potential_qty_avg'] = _avg(bres['potential_qty'])
            o['by_budget'][str(int(B))] = bres
        out['pairs'].append(o)
    return out


def _exit_snapshot(snaps, idx, ts, max_delay_s):
    """Первый валидный снимок с t >= ts и задержкой <= max_delay_s."""
    j = idx
    while j < len(snaps) and snaps[j][0] < ts:
        j += 1
    if j < len(snaps):
        delay = snaps[j][0] - ts
        if delay <= max_delay_s + 1e-9:
            return j, delay
    return None, None


def _record_exit(bres, potential, nominal_qty, entry_px, ts, snaps, idx,
                 horizons, side, max_exit_delay_s=DEFAULT_MAX_EXIT_DELAY_S,
                 audit_context=None):
    """Условный maker->taker exit после касания, ПО ПОТЕНЦИАЛЬНОМУ объёму.
    Signed cost: forced_exit_mt_bps = -net_pnl_bps (прибыль -> отрицательный cost)."""
    audit = {
        'quote_idx': idx, 'side': side, 'entry_price': float(entry_px),
        'qty': float(nominal_qty), 'potential_qty': float(potential),
        'exit_qty': None, 'exit_snapshot_ts': None,
        'exit_snapshot_delay_s': None, 'exit_status': 'unobserved',
        **(audit_context or {}),
    }
    bres['audit'].append(audit)
    # markout от цены котировки (диагностика, только для направленных)
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
                round((mf - float(entry_px)) / float(entry_px) * 1e4, 2))
        else:
            bres['unobserved'][str(H)] = bres['unobserved'].get(str(H), 0) + 1
    # exit: первый будущий снимок в пределах max_exit_delay_s
    j, delay = _exit_snapshot(snaps, idx, ts, max_exit_delay_s)
    if j is None:
        bres['exit_qty_unobserved'] += 1
        bres['forced_exit_mt_bps_raw'].append(None)
        return
    _, bj, _aj = snaps[j]
    audit.update(exit_snapshot_ts=snaps[j][0], exit_snapshot_delay_s=round(delay, 3))
    avg_px, filled = _taker_exit(bj, potential)
    audit['exit_qty'] = float(filled)
    if avg_px is None:
        audit['exit_status'] = 'none'
        bres['exit_qty_none'] += 1
        bres['forced_exit_mt_bps_raw'].append(None)
        return
    if filled >= float(potential) - 1e-12:
        audit['exit_status'] = 'full'
        bres['exit_qty_full'] += 1
        entry_notional = float(potential) * float(entry_px)
        exit_notional = float(potential) * avg_px
        fee_entry = float(FEE) * entry_notional
        fee_exit = float(FEE) * exit_notional
        net_pnl = exit_notional - entry_notional - fee_entry - fee_exit
        net_bps = net_pnl / entry_notional * 1e4
        # SIGNED cost: прибыльный выход -> отрицательный расход (ba9f1cb п.5)
        bres['forced_exit_mt_bps_raw'].append(round(-net_bps, 2))
        bres['net_pnl_mt_bps_raw'].append(round(net_bps, 2))
    else:
        audit['exit_status'] = 'partial'
        bres['exit_qty_partial'] += 1
        bres['forced_exit_mt_bps_raw'].append(None)


def _record_exit_sell(bres, potential, nominal_qty, entry_px, ts, snaps, idx,
                      horizons, side, audit_context=None):
    """Sell-диагностика: markout симметрично; exit не считается (bid-глубина
    не применима к продаже; maker->maker не моделируется)."""
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
                round((float(entry_px) - mf) / float(entry_px) * 1e4, 2))
        else:
            bres['unobserved_sell'][str(H)] = bres['unobserved_sell'].get(str(H), 0) + 1
    bres['audit'].append({
        'quote_idx': idx, 'side': side, 'entry_price': float(entry_px),
        'qty': float(nominal_qty), 'potential_qty': float(potential),
        'exit_qty': None,
        'exit_snapshot_delay_s': None, 'exit_status': 'not_modelled',
        **(audit_context or {}),
    })


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
    print(f"opp {run_id}: window_h={res['window_h']} "
          f"side={res['policy']['side_contract']} decision_ready={res['decision_ready']}")
    for p in res['pairs']:
        for B in ('5', '10'):
            b = p['by_budget'].get(B, {})
            mk = {h: (round(sum(v)/len(v), 2) if v else None) for h, v in b.get('markout', {}).items()}
            print(f"  {p['symbol']:<13} B={B}USDT quotes={b.get('n_quotes')} "
                  f"match={b.get('n_price_match')} touch={b.get('n_touch')} "
                  f"(buy={b.get('n_buy_touch')} sell={b.get('n_sell_touch')}) "
                  f"pot_sum={round(sum(b.get('potential_qty', [])), 4)} "
                  f"exit_full={b.get('exit_qty_full')} "
                  f"exit_mt={b.get('forced_exit_mt_bps')} net_pnl={b.get('net_pnl_mt_bps')}")
    print(f"  -> {jout}")


if __name__ == '__main__':
    main()
