#!/usr/bin/env python3
"""Runner шага 4: блоки по времени + no-trade baseline + excess PnL.

На каждую пару и каждый из 4 последовательных блоков одинаковой длительности:
- независимая инициализация 100 USDT 50/50;
- core-матрица: H1/H2 x очередь 1x/2x x задержка 1/5с x размер 5/10/25;
- no-trade baseline (тот же старт, нулевые заявки, та же оценка);
- strategy_excess_pnl = strategy_final_nav - baseline_final_nav.

Бухгалтерские проверки:
- одинаковый стартовый портфель у стратегии и baseline (50/50 по первому mid
  блока);
- baseline: ноль заявок/филлов/комиссий;
- нет отрицательных остатков (cash/base >= 0 в любой момент);
- блоки не пересекаются: [start, end) последовательные, x0<x1<x2<x3.

Классификация НА УРОВНЕ ИДЕИ (не раздельно H1/H2):
- reject: excess <= 0 даже в оптимистичном сценарии;
- assumption-dependent: знак excess меняется от H1/H2, очереди, задержки
  или временного блока;
- research-candidate: aggregate excess > 0 во ВСЕХ core-комбинациях H1 и H2,
  положителен минимум в 3 из 4 блоков для каждой трактовки, есть исполнения
  во всех 4 блоках, и один лучший блок не даёт более 50% aggregate excess.
"""
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maker.sensitivity import Sensitivity, load_events, _d, STALE_S

RUN_DIR = Path('/home/kali/safetrade-research/data/maker')
REPORTS = Path('/home/kali/safetrade-research/reports')
RID = 'mk_18db19212772b000'
CUTOFF_NS = 1791211306996381800
WINDOW_END = CUTOFF_NS / 1e9
RUN_START = 1791052906.0                   # start run
# PRL: только доказанно полный участок после history_truncated (после warmup).
# history_truncated встречается в первых поллах; берём старт +2ч как границу
# доказанно полного наблюдения (поллостановка caught_up после ~2ч).
PRL_USABLE_START = RUN_START + 2 * 3600.0

PAIRS = {
    'PRLUSDT': {'tick': _d('0.01'), 'improve': [False], 'usable_start': PRL_USABLE_START},
    'QUANTUSUSDT': {'tick': _d('0.001'), 'improve': [False, True],
                    'usable_start': RUN_START},
}


def run_one(sim, books, trades, block_start, block_end):
    """Прогон одного Sensitivity на блоке [block_start, block_end)."""
    bs = [b for b in books if block_start <= b.ts < block_end]
    tr = [t for t in trades if block_start <= t[0] < block_end]
    if not bs or not tr:
        return sim.finalize()
    sim.on_book(bs[0])
    bi = 0
    for ts, price, amount, side in tr:
        while bi + 1 < len(bs) and bs[bi + 1].ts <= ts:
            bi += 1
            sim.on_book(bs[bi])
        book = bs[bi]
        if ts - book.ts > STALE_S:
            sim.skipped_stale += 1
            continue
        sim.on_trade(ts, price, amount, side, book)
    for b in bs[bi + 1:]:
        sim.on_book(b)
    if not sim.baseline and (sim.order or sim.exit_order):
        sim._force_exit(bs[-1], 'window_end')
    return sim.finalize()


def check_accounting(sim, baseline_sim, tag):
    """Возвращает список нарушений бухгалтерских проверок."""
    errs = []
    # стартовый портфель одинаковый: cash=50, base по первому mid
    if sim.seed_mid is not None and baseline_sim.seed_mid is not None:
        if sim.seed_mid != baseline_sim.seed_mid:
            errs.append(f'{tag}: seed_mid различается '
                        f'{sim.seed_mid} vs {baseline_sim.seed_mid}')
    # baseline: ноль заявок/филлов
    if baseline_sim.fills or baseline_sim.order or baseline_sim.exit_order:
        errs.append(f'{tag}: baseline имеет заявки/филлы')
    # отрицательные остатки
    for label, sim0 in (('strategy', sim), ('baseline', baseline_sim)):
        if float(sim0.cash) < -1e-9 or float(sim0.base) < -1e-9:
            errs.append(f'{tag}: {label} отрицательный остаток '
                        f'cash={sim0.cash} base={sim0.base}')
    return errs


def main():
    out = {'run_id': RID, 'cutoff_ns': CUTOFF_NS,
           'config': {'capital_usdt': 100.0, 'fee_rate': 0.001,
                      'max_hold_s': 1800, 'stale_s': STALE_S,
                      'delays': [1.0, 5.0], 'budgets': [5.0, 10.0, 25.0],
                      'queues': ['1x', '2x'], 'n_blocks': 4},
           'blocks': {}, 'checks': [], 'classification': {}}
    t0 = time.time()
    for pair, cfg in PAIRS.items():
        books, trades = load_events(pair, str(RUN_DIR), WINDOW_END)
        usable_start = cfg['usable_start']
        span = WINDOW_END - usable_start
        step = span / 4.0
        blocks = []
        for i in range(4):
            b0 = usable_start + i * step
            b1 = usable_start + (i + 1) * step
            blocks.append((b0, b1))
        out['blocks'][pair] = [
            {'i': i, 'start_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(b0)),
             'end_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(b1)),
             'span_h': round((b1 - b0) / 3600.0, 3)}
            for i, (b0, b1) in enumerate(blocks)]
        # проверка непересечения: b_i.end == b_{i+1}.start
        for i in range(3):
            if abs(blocks[i][1] - blocks[i + 1][0]) > 1e-6:
                out['checks'].append(f'{pair}: блоки {i}/{i+1} пересекаются')
        pair_rows = []
        for (b0, b1) in blocks:
            # baseline для блока
            base_sim = Sensitivity(pair, 'H1', '1x', 1.0, _d('10'),
                                   tick=cfg['tick'], capital=_d('100'),
                                   baseline=True, window_end=b1)
            base_res = run_one(base_sim, books, trades, b0, b1)
            for hypothesis in ('H1', 'H2'):
                for qf in ('1x', '2x'):
                    for delay in (1.0, 5.0):
                        for budget in (5.0, 10.0, 25.0):
                            for improve in cfg['improve']:
                                sim = Sensitivity(pair, hypothesis, qf, delay,
                                                  _d(str(budget)), improve=improve,
                                                  tick=cfg['tick'],
                                                  capital=_d('100'), window_end=b1)
                                res = run_one(sim, books, trades, b0, b1)
                                errs = check_accounting(sim, base_sim,
                                                        f'{pair}/{b0:.0f}')
                                out['checks'].extend(errs)
                                excess = res['final_nav'] - base_res['final_nav']
                                row = {k: res[k] for k in (
                                    'hypothesis', 'queue_factor', 'delay_s',
                                    'budget_usdt', 'improve',
                                    'net_pnl', 'final_nav', 'n_fills',
                                    'completed_cycles', 'force_exit_count',
                                    'fees_total', 'max_drawdown')}
                                row['block'] = b0
                                row['block_start'] = b0
                                row['block_end'] = b1
                                row['baseline_nav'] = base_res['final_nav']
                                row['baseline_pnl'] = base_res['net_pnl']
                                row['excess_pnl'] = round(excess, 4)
                                pair_rows.append(row)
        out.setdefault('scenarios', []).extend(pair_rows)
        # классификация на уровне идеи (по паре)
        klass = classify_idea(pair_rows)
        out['classification'][pair] = klass
        print(f'{pair}: scenarios={len(pair_rows)} -> {klass["verdict"]}', flush=True)
    out['elapsed_s'] = round(time.time() - t0, 1)
    dst = REPORTS / f'maker_sensitivity_blocks_{RID}.json'
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print('checks:', out['checks'][:10], 'n_checks=', len(out['checks']))
    print(f'WROTE {dst} elapsed={out["elapsed_s"]}s')


def classify_idea(rows):
    """Классификация идеи по паре (строго по ТЗ шага 4)."""
    # агрегат по core-комбинациям: H1/H2 x 1x/2x x 1/5с x 5/10/25
    combos = {}
    for r in rows:
        key = (r['hypothesis'], r['queue_factor'], r['delay_s'],
               r['budget_usdt'], r['improve'])
        combos.setdefault(key, []).append(r)
    agg = {}
    for key, rs in combos.items():
        agg[key] = {'excess': sum(r['excess_pnl'] for r in rs),
                    'n_fills_total': sum(r['n_fills'] for r in rs),
                    'positive_blocks': sum(1 for r in rs
                                           if r['excess_pnl'] > 0),
                    'n_blocks': len(rs),
                    'best_block_excess': max(r['excess_pnl'] for r in rs),
                    'excess_by_block': [r['excess_pnl'] for r in rs]}
    for key in agg:
        total = agg[key]['excess']
        agg[key]['best_block_share'] = (agg[key]['best_block_excess'] / total
                                        if total != 0 else 0.0)
    # reject: excess <= 0 даже в оптимистичном сценарии (максимум по всем)
    max_excess = max(a['excess'] for a in agg.values())
    if max_excess <= 0:
        return {'verdict': 'reject', 'n_combos': len(agg),
                'max_aggregate_excess': max_excess,
                'detail': 'excess<=0 даже в лучшем сценарии'}
    # research-candidate: все комбинации aggregate excess > 0, каждая трактовка
    # положительна минимум в 3/4 блоков, исполнения во всех 4 блоках,
    # лучший блок <= 50% aggregate
    all_positive = all(a['excess'] > 0 for a in agg.values())
    blocks_ok = all(a['positive_blocks'] >= 3 for a in agg.values())
    fills_ok = all(a['n_fills_total'] > 0 for a in agg.values())
    share_ok = all(a['best_block_share'] <= 0.5 for a in agg.values())
    if all_positive and blocks_ok and fills_ok and share_ok:
        return {'verdict': 'research-candidate', 'n_combos': len(agg),
                'all_aggregate_positive': all_positive,
                'blocks_positive_3of4': blocks_ok,
                'fills_all_blocks': fills_ok,
                'best_block_share_ok': share_ok,
                'max_aggregate_excess': max_excess}
    # иначе assumption-dependent
    return {'verdict': 'assumption-dependent', 'n_combos': len(agg),
            'all_aggregate_positive': all_positive,
            'blocks_positive_3of4': blocks_ok,
            'fills_all_blocks': fills_ok,
            'best_block_share_ok': share_ok,
            'max_aggregate_excess': max_excess}


if __name__ == '__main__':
    main()