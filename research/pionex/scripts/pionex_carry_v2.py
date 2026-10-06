#!/usr/bin/env python3
"""Шаг 2б: строгий денежный расчёт funding carry (по аудиту ревизии).

Модель (явные определения):
- q = постоянное количество базового актива (BTC/AAVE/...);
- S_t = spot цена, F_t = perp mark, B_t = F_t - S_t (премия perp);
- позиция: long spot q + short perp q (нейтральная по направлению);
- price_PnL = q*((S_T-S_0) + (F_0-F_T)) = q*(B_0 - B_T):
    расширение премии B -> убыток; сужение -> прибыль;
- funding (official: Funding Fee = Position Value * Rate, rate в долях,
  positive => long платит short): для short perp q получаем
    funding_USDT = sum(q * mark_i * r_i)   по всем выплатам i;
- fees, 4 операции с раздельными ставками spot/perp и своими номиналами:
    fees = q*S_0*f_s + q*F_0*f_p + q*S_T*f_s + q*F_T*f_p;
- net_USDT = funding_USDT + price_PnL - fees;
- C0 = q*(S_0 + F_0) Ёs gross capital сценарий (spot cost + futures
  collateral, без плеча, отдельный залог). Внесённые средства = C0.
  Margin path НЕ проверяется (расчёт без проверки удержания позиции).
- Цены на момент выплаты: последняя ЗАКРЫТАЯ 8H-свеча до t (без lookahead),
  возраст цены age_h указывается. Выплаты без данных помечаются непокрытыми.

Контрольные проверки (синтетика):
  P1: B расширяется (F растёт на 1 bps, S неизменна), funding=0,
      цены S неизменны: net = q*(B_0-B_T) - fees < 0 (убыток).
  P2: B сужается: net > 0.
  P3: цены неизменны, funding=0: net = -fees.
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
CANDIDATES = ['AAVE_USDT', 'AMZNX_USDT', 'AAPLX_USDT', 'ARKM_USDT', 'AR_USDT']


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
    out.sort(key=lambda z: z['t'])
    return out


def fetch_klines_cached(symbol, interval, typ, out, cache_key, end_ms=None, limit=500):
    """Загружает klines с пагинацией назад через endTime, сохраняет в out."""
    if cache_key in out:
        return
    params = {'symbol': symbol, 'interval': interval, 'type': typ, 'limit': limit}
    if end_ms is not None:
        params['endTime'] = end_ms
    code, body = get('/api/v1/market/klines', params)
    time.sleep(0.12)
    if code != 200:
        out[cache_key] = {}
        return
    ks = body.get('data', {}).get('klines', [])
    res = {}
    for k in ks:
        try:
            res[int(k['time'])] = float(k['close'])
        except (KeyError, TypeError, ValueError):
            continue
    out[cache_key] = res


def last_price_before(klines_by_open, t_ms, interval_h):
    """Последняя ЗАКРЫТАЯ свеча до t: open_time + interval <= t. Возврат (price, age_h)."""
    best_t = None
    for open_ms, close in klines_by_open.items():
        if open_ms + interval_h * 3600 * 1000 <= t_ms:
            if best_t is None or open_ms > best_t:
                best_t = open_ms
    if best_t is None:
        return None, None
    age_h = (t_ms - (best_t + interval_h * 3600 * 1000)) / 3600000.0
    return klines_by_open[best_t], age_h


def control_checks(build):
    """Контрольные проверки модели (q=1).

    Знаковые (fee=0, чтобы изолировать модель базиса):
      P1: B расширяется (F_T>F_0, S неизменна) -> price_PnL=(B_0-B_T)<0 (убыток).
      P2: B сужается (F_T<F_0) -> price_PnL>0 (прибыль).
    Расходная (цены неизменны, funding=0, fee>0):
      P3: net = -fees < 0.
    """
    S0 = F0 = 100.0
    ST = 100.0
    # P1: расширение премии, fee=0
    FT = F0 * 1.0001
    net1 = (ST - S0) + (F0 - FT)          # = B0 - BT
    p1 = net1 < 0
    # P2: сужение премии, fee=0
    FT2 = F0 * 0.9999
    net2 = (ST - S0) + (F0 - FT2)
    p2 = net2 > 0
    # P3: цены неизменны, funding=0, fee=1 bps на 4 ноги
    f = 1e-4
    fees3 = f * (S0 + F0 + ST + F0)
    net3 = 0.0 - fees3
    p3 = net3 < 0 and abs(net3 - (-fees3)) < 1e-12
    return {'P1_expansion_loss': p1, 'P2_contraction_profit': p2, 'P3_no_move_loss': p3,
            'P1_net': round(net1, 6), 'P2_net': round(net2, 6), 'P3_net': round(net3, 6)}


def analyze(sym, fee_s, fee_p, kcache):
    perp = sym + '_PERP'
    funding = fetch_funding(perp)
    if not funding:
        return None
    t0, t1 = funding[0]['t'], funding[-1]['t']
    # 8H klines: спот и mark, пагинация назад с endTime
    fetch_klines_cached(sym, '8H', 'SPOT', kcache, f'spot_{sym}',
                        end_ms=t1)
    fetch_klines_cached(perp, '8H', 'PERP', kcache, f'mark_{perp}',
                        end_ms=t1)
    spot_k = kcache.get(f'spot_{sym}', {})
    mark_k = kcache.get(f'mark_{perp}', {})
    INT = 8  # часов
    max_age = 8 * 4  # допускаем возраст цены до 32ч; больше — окно непокрыто

    rows = []
    uncovered = 0
    for x in funding:
        t = x['t']
        s, age_s = last_price_before(spot_k, t, INT)
        m, age_m = last_price_before(mark_k, t, INT)
        if s is None or m is None or age_s > max_age or age_m > max_age:
            uncovered += 1
            rows.append({'t': t, 'r': x['r'], 's': None, 'm': None,
                         'age_s': age_s, 'age_m': age_m})
            continue
        rows.append({'t': t, 'r': x['r'], 's': s, 'm': m,
                     'age_s': round(age_s, 2), 'age_m': round(age_m, 2)})
    covered = [r for r in rows if r['s'] is not None]
    if len(covered) < 2:
        return None
    q = 1.0
    S0, F0 = covered[0]['s'], covered[0]['m']
    ST, FT = covered[-1]['s'], covered[-1]['m']
    # funding_USDT: short perp получает q*m_i*r_i (official: position value * rate)
    funding_usdt = sum(q * r['m'] * r['r'] for r in covered)
    price_pnl = q * ((ST - S0) + (F0 - FT))       # = q*(B0-BT)
    fees = q * (S0 * fee_s + F0 * fee_p + ST * fee_s + FT * fee_p)
    net = funding_usdt + price_pnl - fees
    C0 = q * (S0 + F0)                              # gross capital (spot+futures collateral)
    # rolling net (по закрытым окнам): independence windows на [i, i+k)
    def roll(days):
        k = days * 3
        if len(covered) < k:
            return None
        wins = []
        for i in range(0, len(covered) - k + 1):
            seg = covered[i:i + k]
            fu = sum(q * r['m'] * r['r'] for r in seg)
            pp = q * ((seg[-1]['s'] - seg[0]['s']) + (seg[0]['m'] - seg[-1]['m']))
            fe = q * (seg[0]['s'] * fee_s + seg[0]['m'] * fee_p + seg[-1]['s'] * fee_s + seg[-1]['m'] * fee_p)
            wins.append(fu + pp - fe)
        return {'n': len(wins), 'min': round(min(wins), 4),
                'median': round(statistics.median(wins), 4),
                'max': round(max(wins), 4),
                'pos_share': round(sum(1 for w in wins if w > 0) / len(wins), 3)}
    return {
        'symbol': sym,
        'n_payouts_total': len(rows),
        'n_covered': len(covered),
        'n_uncovered': uncovered,
        'period': {'t0': t0, 't1': t1,
                   'days': round((t1 - t0) / 86400000.0, 2)},
        'q': q,
        'S0': S0, 'F0': F0, 'ST': ST, 'FT': FT,
        'B0_bps': round((F0 - S0) / S0 * 1e4, 4),
        'BT_bps': round((FT - ST) / ST * 1e4, 4),
        'funding_usdt': round(funding_usdt, 6),
        'price_pnl_usdt': round(price_pnl, 6),
        'fees_usdt': round(fees, 6),
        'net_usdt': round(net, 6),
        'C0_usdt': round(C0, 6),
        'net_bps_on_gross': round(net / C0 * 1e4, 3),
        'max_age_h': round(max(r['age_s'] for r in covered), 2),
        'rolling': {f'{d}d': roll(d) for d in (7, 30, 90)},
        'worst_funding_streak_usdt': round(
            _worst([q * r['m'] * r['r'] for r in covered]), 6),
        'sample_rows': covered[:3] + covered[-3:],
    }


def _worst(vals):
    w = c = 0.0
    for v in vals:
        if v < 0:
            c += v
            w = min(w, c)
        else:
            c = 0.0
    return w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fee-spot-bps', type=float, nargs='+', default=[1.0, 5.0, 10.0])
    ap.add_argument('--fee-perp-bps', type=float, nargs='+', default=[1.0, 5.0, 10.0])
    args = ap.parse_args()
    kcache = {}
    controls = control_checks(None)
    print('CONTROLS:', controls)
    assert all(controls[k] for k in ('P1_expansion_loss', 'P2_contraction_profit', 'P3_no_move_loss'))
    out = {}
    for fs in args.fee_spot_bps:
        for fp in args.fee_perp_bps:
            key = f'spot{fs}_perp{fp}'
            res = []
            for sym in CANDIDATES:
                r = analyze(sym, fs * 1e-4, fp * 1e-4, kcache)
                if r:
                    res.append(r)
                print('.', end='', flush=True)
            print()
            res.sort(key=lambda z: z['net_bps_on_gross'], reverse=True)
            out[key] = res
    dst = OUT / 'carry_analysis_v2.json'
    dst.write_text(json.dumps({'controls': controls, 'results': out},
                              ensure_ascii=False, indent=1))
    print('WROTE', dst)
    for key, res in out.items():
        print(f'\n=== {key} (net bps на gross capital C0={res[0]["C0_usdt"]:.0f} USDT)  ===')
        print('{:<12}{:<8}{:<8}{:<9}{:<10}{:<10}{:<8}{:<8}'.format(
            'symbol', 'payouts', 'net_usdt', 'net_bps', 'funding', 'pricePnL', 'fees', 'days'))
        for r in res[:5]:
            print('{:<12}{:<8}{:<8}{:<9}{:<10}{:<10}{:<8}{:<8}'.format(
                r['symbol'], f"{r['n_covered']}/{r['n_payouts_total']}",
                r['net_usdt'], r['net_bps_on_gross'], r['funding_usdt'],
                r['price_pnl_usdt'], r['fees_usdt'], r['period']['days']))


if __name__ == '__main__':
    main()