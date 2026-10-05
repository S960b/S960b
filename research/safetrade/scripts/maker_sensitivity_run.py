#!/usr/bin/env python3
"""Runner шага 3: матрица сценариев sensitivity (PRL join; QUANTUS join+improve).

Матрица: H1/H2 × очередь 0x/1x/2x × задержка 0/1/5с × размер 5/10/25 USDT.
События сливаются по времени; каждый trade использует ПОСЛЕДНИЙ известный
стакан (bisect по ts), возраст > STALE_S -> unobserved (пропуск, не будущее).
Остаток в конце окна принудительно закрывается taker.
"""
import bisect
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
CUTOFF_NS = 1791211306996381800          # start+44h
WINDOW_END = CUTOFF_NS / 1e9
# PRL: неполный ранний участок (history_truncated) исключаем из ОСНОВНОГО окна;
# полный прогон по всей паре = отдельная чувствительность. Для PRL основное
# окно начинается после последнего history_truncated poll. Упрощённо:
# PRL_MAIN_START = время перехода PRL к caught_up (см. манифест среза).
PRL_MAIN_START = 1791052906.0 + 3600.0 * 2.0   # ~2h после старта (после warmup)

PAIRS = {
    'PRLUSDT': {'improve': [False], 'tick': _d('0.01'), 'main_start': PRL_MAIN_START},
    'QUANTUSUSDT': {'improve': [False, True], 'tick': _d('0.001'), 'main_start': None},
}


def run_scenario(pair, hypothesis, qf, delay, budget, improve, tick,
                 books, trades, main_start=None):
    """Один прогон на рамках main-окна (main_start..window_end).

    События сливаются по времени: каждый стакан -> on_book (постановка/
    перестановка заявок, force-exit по удержанию); каждая сделка ->
    последний известный стакан (bisect), возраст > STALE_S -> unobserved.
    """
    sim = Sensitivity(pair, hypothesis, qf, delay, budget, improve=improve,
                      tick=tick, run_dir=str(RUN_DIR),
                      window_end=WINDOW_END)
    if main_start:
        books = [b for b in books if b.ts >= main_start]
        trades = [t for t in trades if t[0] >= main_start]
    if not books or not trades:
        return sim.finalize()
    sim.on_book(books[0])                 # seed капитала + первая заявка
    bi = 0
    for ts, price, amount, side in trades:
        while bi + 1 < len(books) and books[bi + 1].ts <= ts:
            bi += 1
            sim.on_book(books[bi])
        book = books[bi]
        if ts - book.ts > STALE_S:
            sim.skipped_stale += 1
            continue
        sim.on_trade(ts, price, amount, side, book)
    # докрутить оставшиеся стаканы (удержания, постановка) и закрыть остаток
    for b in books[bi + 1:]:
        sim.on_book(b)
    if sim.order or sim.exit_order:
        sim._force_exit(books[-1], 'window_end')
    return sim.finalize()


def main():
    out = {'run_id': RID, 'cutoff_ns': CUTOFF_NS,
           'config': {'capital_usdt': 100.0, 'fee_rate': 0.001,
                      'max_hold_s': 1800, 'stale_s': STALE_S,
                      'delay_s': [0.0, 1.0, 5.0], 'budgets': [5.0, 10.0, 25.0],
                      'queue_factors': ['0x', '1x', '2x']},
           'scenarios': [], 'classification': {}}
    t0 = time.time()
    for pair, cfg in PAIRS.items():
        books, trades = load_events(pair, str(RUN_DIR), WINDOW_END)
        print(f'{pair}: books={len(books)} trades={len(trades)}', flush=True)
        for hypothesis in ('H1', 'H2'):
            for qf in ('0x', '1x', '2x'):
                for delay in (0.0, 1.0, 5.0):
                    for budget in (5.0, 10.0, 25.0):
                        for improve in cfg['improve']:
                            r = run_scenario(pair, hypothesis, qf, delay,
                                             _d(str(budget)), improve,
                                             cfg['tick'], books, trades,
                                             main_start=cfg.get('main_start'))
                            out['scenarios'].append(r)
    # --- классификация (строго по ТЗ шага 3): research-candidate требует
    # положительный net PnL при H1 и H2, очереди 1x и 2x, задержках 1 и 5с.
    for pair in PAIRS:
        base = [s for s in out['scenarios'] if s['pair'] == pair]
        for hyp in ('H1', 'H2'):
            hs = [s for s in base if s['hypothesis'] == hyp]
            core = [s for s in hs
                    if s['queue_factor'] in ('1', '2')
                    and s['delay_s'] in (1.0, 5.0)]
            best = max((s['net_pnl'] for s in hs), default=0.0)
            worst = min((s['net_pnl'] for s in hs), default=0.0)
            core_positive = bool(core) and all(s['net_pnl'] > 0 for s in core)
            any_negative = any(s['net_pnl'] <= 0 for s in hs)
            if best <= 0:
                verdict = 'reject'
            elif any_negative:
                verdict = 'assumption-dependent'
            elif core_positive:
                verdict = 'research-candidate'
            else:
                verdict = 'assumption-dependent'
            out['classification'][f'{pair}/{hyp}'] = {
                'verdict': verdict, 'n': len(hs), 'n_core': len(core),
                'core_all_positive': core_positive,
                'best_net_pnl': best, 'worst_net_pnl': worst,
            }
    out['elapsed_s'] = round(time.time() - t0, 1)
    dst = REPORTS / f'maker_sensitivity_{RID}.json'
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f'WROTE {dst} scenarios={len(out["scenarios"])} '
          f'elapsed={out["elapsed_s"]}s')
    for k, v in out['classification'].items():
        print(f'  {k}: {v["verdict"]} (best={v["best_net_pnl"]}, '
              f'worst={v["worst_net_pnl"]})')


if __name__ == '__main__':
    main()