#!/usr/bin/env python3
"""Пересчёт exact6h опубликованного opportunity-отчёта по контракту ba9f1cb.

Ревью: reports/opp_mk_18db19212772b000.json на main последний раз менялся в
d05b4e5 (старая схема, без audit/potential_qty/signed net). Здесь:
- cutoff = 1791074506996381800 (как указано в ревью; 104 ns от старого незначимы)
- analyzer_commit = ba9f1cb-фиксы (локально, до публикации нового коммита)
- schema_version = 3
- side не подтверждён в manifest -> только price matches + side_unverified,
  направленные opportunity = 0 (это и есть честный результат ревью).
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from maker.opportunity import opportunity_report

RUN = 'mk_18db19212772b000'
CUT = 1791074506996381800
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'maker')

if __name__ == '__main__':
    res = opportunity_report(DATA, RUN, cutoff_ns=CUT)
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'reports', f'opp_{RUN}.json')
    with open(out, 'w') as f:
        json.dump(res, f, indent=1, ensure_ascii=False)
    print(f'window_h={res["window_h"]}')
    print(f'side_contract: {res["policy"]["side_contract"]}')
    print(f'decision_ready: {res["decision_ready"]}')
    print(f'reasons: {res["reasons"]}')
    for p in res['pairs']:
        for B in ('5', '10'):
            b = p['by_budget'][B]
            print(f'{p["symbol"]} B={B}: quotes={b["n_quotes"]} '
                  f'match={b["n_price_match"]} (buy={b["n_buy_match"]} sell={b["n_sell_match"]}) '
                  f'touch={b["n_touch"]} pot_sum={round(sum(b["potential_qty"]),4)} '
                  f'exit_mt={b["forced_exit_mt_bps"]}')
    print(f'-> {out}')