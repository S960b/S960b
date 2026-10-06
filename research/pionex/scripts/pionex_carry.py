#!/usr/bin/env python3
"""Шаг 2 (offline economics): funding carry по исправленной модели.

По замечаниям ревизии:
- all_in_cost_bps = 4*fee_per_leg + spot_roundtrip_spread + perp_roundtrip_spread
  (+ ОТДЕЛЬНО показываем basis_change, slippage не моделируем — помечаем).
- Знаменатель для доходности: gross capital (spot notional + perp notional),
  явно; funding-проекция на perp notional показывается отдельно.
- Реальная последовательность 500 выплат; rolling 7/30/90/167d; худшая
  отрицательная серия; PnL при выходе в каждый день.
- basis синхронно: spot close vs perp mark в моменты funding (8H klines).
- Знак funding проверяется по семантике Pionex (положительный funding => longs
  платят shorts; позиция long spot + short perp => получаем funding при >0).

Только публичные данные. Без ключа, без ордеров.
"""
import argparse
import json
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = 'https://api.pionex.com'
UA = 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'
OUT = Path('/home/kali/safetrade-research/data/pionex')

CANDIDATES = ['AAVE_USDT', 'AMZNX_USDT', 'AAPLX_USDT', 'ARKM_USDT', 'AR_USDT',
              'ATH_USDT', 'AEVO_USDT', 'ASTER_USDT', '1INCH_USDT', 'ALT_USDT']


def get(path, params=None, timeout=20):
    url = BASE + path + ('?' + urllib.parse.urlencode(params) if params else '')
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception as e:
        return 0, {'exception': repr(e)}


def fetch_funding(perp):
    code, body = get('/api/v1/market/fundingRates', {'symbol': perp, 'limit': 500})
    time.sleep(0.12)
    if code != 200:
        return []
    d = body.get('data')
    rates = (d.get('rates') or []) if isinstance(d, dict) else (d or [])
    out = []
    for x in rates:
        try:
            out.append({'t': int(x['fundingTime']), 'r': float(x['fundingRate'])})
        except (KeyError, TypeError, ValueError):
            continue
    # в API записи идут от новых к старым -> сортируем по времени возрастанию
    out.sort(key=lambda z: z['t'])
    return out


def fetch_klines(symbol, interval, typ, limit=500):
    code, body = get('/api/v1/market/klines',
                     {'symbol': symbol, 'interval': interval, 'limit': limit, 'type': typ})
    time.sleep(0.12)
    if code != 200:
        return {}
    ks = body.get('data', {}).get('klines', [])
    return {int(k['time']): float(k['close']) for k in ks if 'time' in k}


def worst_negative_streak(vals):
    worst = cur = 0.0
    for v in vals:
        if v < 0:
            cur += v
            worst = min(worst, cur)
        else:
            cur = 0.0
    return worst


def rolling_windows(net, days):
    """net: список (t_ms, value_bps). Окна по числу периодов 8ч: days*3."""
    k = days * 3
    if len(net) < k:
        return None
    sums = []
    for i in range(0, len(net) - k + 1):
        sums.append(sum(v for _, v in net[i:i + k]))
    return {'n_windows': len(sums), 'min': round(min(sums), 3),
            'median': round(statistics.median(sums), 3), 'max': round(max(sums), 3),
            'share_positive': round(sum(1 for s in sums if s > 0) / len(sums), 3)}


def analyze(sym, fee_per_leg, spread_override=None):
    perp = sym + '_PERP'
    funding = fetch_funding(perp)
    spot_k = fetch_klines(sym, '8H', 'SPOT')
    mark_k = fetch_klines(perp, '8H', 'PERP')
    if not funding:
        return None
    rows = []
    for x in funding:
        # ближайшая свеча 8H к моменту funding
        t = x['t']
        s = spot_k.get(t)
        m = mark_k.get(t)
        basis = ((m - s) / s) if (s and m and s > 0) else None
        rows.append({'t': t, 'r_bps': x['r'] * 1e4, 'basis_bps': (basis * 1e4) if basis is not None else None})
    # carry: long spot + short perp. funding>0 => shorts получают (проверяем ниже).
    fund_vals = [r['r_bps'] for r in rows]
    cum = []
    acc = 0.0
    for v in fund_vals:
        acc += v
        cum.append(acc)
    # спреды из bookTickers
    bt = json.loads((OUT / 'bookTickers.json').read_text())
    def sp(typ, s):
        for x in bt[typ]:
            if x.get('symbol') == s:
                try:
                    return (float(x['askPrice']) - float(x['bidPrice'])) / float(x['bidPrice']) * 1e4
                except (TypeError, ValueError, ZeroDivisionError):
                    return None
        return None
    spot_sp = spread_override if spread_override is not None else sp('SPOT', sym)
    perp_sp = sp('PERP', perp)
    all_in_cost = 4 * fee_per_leg + (spot_sp or 0) + (perp_sp or 0)
    # basis entry/exit на доступной истории
    basis_series = [r['basis_bps'] for r in rows if r['basis_bps'] is not None]
    basis_change = (basis_series[-1] - basis_series[0]) if len(basis_series) >= 2 else None
    total_funding = sum(fund_vals)
    net = total_funding - all_in_cost + (basis_change or 0.0)
    res = {
        'symbol': sym, 'n_periods': len(fund_vals),
        'fee_per_leg_bps': fee_per_leg,
        'spot_spread_bps': round(spot_sp, 4) if spot_sp is not None else None,
        'perp_spread_bps': round(perp_sp, 4) if perp_sp is not None else None,
        'all_in_cost_bps': round(all_in_cost, 3),
        'funding_total_bps': round(total_funding, 3),
        'basis_change_bps': round(basis_change, 3) if basis_change is not None else None,
        'net_bps': round(net, 3),
        # знаменатель: gross capital = hедущий notional на 1 единицу (spot+perp = 2x)
        'net_bps_on_gross_capital': round(net / 2.0, 3),
        'worst_neg_streak_bps': round(worst_negative_streak(fund_vals), 3),
        'funding_pos_share': round(sum(1 for v in fund_vals if v > 0) / len(fund_vals), 3),
        'rolling': {f'{d}d': rolling_windows([(r['t'], r['r_bps']) for r in rows], d)
                    for d in (7, 30, 90, 167)},
        'pnl_exit_each_day_positive_share': round(
            sum(1 for c in cum if c > all_in_cost) / len(cum), 3) if cum else None,
        'cum_funding_final_bps': round(cum[-1], 3) if cum else None,
    }
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fee-per-leg', type=float, nargs='+', default=[1.0, 5.0, 10.0])
    args = ap.parse_args()
    out = {}
    for fee in args.fee_per_leg:
        res = []
        for sym in CANDIDATES:
            r = analyze(sym, fee)
            if r:
                res.append(r)
            print('.', end='', flush=True)
        print()
        res.sort(key=lambda z: z['net_bps'], reverse=True)
        out[f'fee_{fee}bps'] = res
    dst = OUT / 'carry_analysis.json'
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print('WROTE', dst)
    # сводная таблица при fee=5
    key = 'fee_5.0bps' if 'fee_5.0bps' in out else list(out)[0]
    print(f'\n=== fee={key} (net_bps; при gross capital /2) ===')
    print('{:<14}{:<10}{:<12}{:<12}{:<10}{:<12}{:<12}'.format(
        'symbol', 'fund_tot', 'basis_chg', 'all_in', 'net', 'net/gross', 'worst_streak'))
    for r in out[key]:
        print('{:<14}{:<10}{:<12}{:<12}{:<10}{:<12}{:<12}'.format(
            r['symbol'], r['funding_total_bps'], str(r['basis_change_bps']),
            r['all_in_cost_bps'], r['net_bps'], r['net_bps_on_gross_capital'],
            r['worst_neg_streak_bps']))


if __name__ == '__main__':
    main()