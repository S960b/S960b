#!/usr/bin/env python3
"""Финальная сверка funding carry (AAVE, ARKM) по замечаниям ревизии.

Что делает:
- заново тянет fundingRates(500) + spot/perp 8H klines (без lookahead);
- точный q = N0/S0 без округления до расчёта;
- net = funding_USDT + price_PnL(q*(S_T-S_0)+q*(F_0-F_T)) - 4 комиссии;
- оба знаменателя: C0 = q*(S0+F0) и C_deployed = spot_cost + cfut_deployed;
- margin: min cfut по наблюдаемой траектории (ретроспектива), coverage_multiple
  = min(E_t/MM_t) при cfut_deployed; survival=unverified (не прогноз);
- неперекрывающиеся 30d/60d окна с датами входа/выхода и остатком периода;
- E_max = net до неучтённых издержек (спред/слайпедж/синхронизация);
- флаги unverified: единицы fundingRate, tier MMR, фактический тариф.
Только публичные данные. Без ключа, без ордеров.
"""
import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = 'https://api.pionex.com'
UA = 'Mozilla/5.0'
OUT = Path('/home/kali/safetrade-research/data/pionex')
N0 = 200.0
FEE_SPOT = 5e-4      # 5 bps на ногу spot (предположение, тариф владельца unverified)
FEE_PERP = 5e-4      # 5 bps на ногу perp
MMR = 0.005          # 0.5% — АКТУАЛЬНЫЙ tier AAVE НЕ подтверждён (см. ревизию)
STEP = {'AAVE_USDT': 0.01, 'ARKM_USDT': 1.0}   # baseStep (шаг количества)


def get(path, params=None):
    url = BASE + path + ('?' + urllib.parse.urlencode(params) if params else '')
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except Exception:
        return 0, {}


def fetch_all(sym):
    perp = sym + '_PERP'
    code, body = get('/api/v1/market/fundingRates', {'symbol': perp, 'limit': 500})
    time.sleep(0.12)
    d = body.get('data') if code == 200 else {}
    rates = (d.get('rates') or []) if isinstance(d, dict) else (d or [])
    fund = sorted(
        ({'t': int(x['fundingTime']), 'r': float(x['fundingRate'])}
         for x in rates if 'fundingTime' in x and 'fundingRate' in x),
        key=lambda z: z['t'])
    t1 = fund[-1]['t']
    out = {'sym': sym, 'fund': fund, 'mark': {}, 'spot': {}}
    for typ, key, sm in (('PERP', 'mark', perp), ('SPOT', 'spot', sym)):
        code, body = get('/api/v1/market/klines',
                         {'symbol': sm, 'interval': '8H', 'limit': 500,
                          'type': typ, 'endTime': t1})
        time.sleep(0.12)
        ks = body.get('data', {}).get('klines', []) if code == 200 else []
        out[key] = {int(k['time']): float(k['close']) for k in ks if 'time' in k}
    return out


def close_at(klines, t, interval_h=8):
    cand = [o for o in klines if o + interval_h * 3600 * 1000 <= t]
    return klines[max(cand)] if cand else None


def run(sym):
    d = fetch_all(sym)
    rows = []
    for x in d['fund']:
        F = close_at(d['mark'], x['t'])
        S = close_at(d['spot'], x['t'])
        if F is not None and S is not None:
            rows.append({'t': x['t'], 'r': x['r'], 'F': F, 'S': S})
    if len(rows) < 2:
        return None
    S0, F0 = rows[0]['S'], rows[0]['F']
    q = N0 / S0            # точный q, без округления до расчёта
    q_actual = None
    # исполнимый шаг (baseStep): q должен быть кратным шагу
    st = STEP[sym]
    q_step = max(st, st * math.floor(q / st))
    fees = q * (S0 * FEE_SPOT + F0 * FEE_PERP +
                rows[-1]['S'] * FEE_SPOT + rows[-1]['F'] * FEE_PERP)
    funding = sum(q * x['F'] * x['r'] for x in rows)
    price_pnl = q * ((rows[-1]['S'] - S0) + (F0 - rows[-1]['F']))
    net = funding + price_pnl - fees
    C0 = q * (S0 + F0)
    # margin (ретроспектива по 8H, survival=unverified)
    upnl = [q * (F0 - x['F']) for x in rows]
    fcum = []
    acc = 0.0
    for i, x in enumerate(rows):
        acc += q * x['F'] * x['r']
        fcum.append(acc)
    fee_in = q * F0 * FEE_PERP
    need = [max(0.0, abs(q) * rows[i]['F'] * MMR) - (fcum[i] + upnl[i] - fee_in)
            for i in range(len(rows))]
    min_cfut = max(need) if need else 0.0
    cfut = min_cfut * 1.20
    cov = []
    for i in range(len(rows)):
        MM = max(0.0, abs(q) * rows[i]['F'] * MMR)
        E = cfut + fcum[i] + upnl[i] - fee_in
        cov.append(E / MM if MM > 0 else float('inf'))
    worst_i = cov.index(min(cov))
    spot_cost = q * S0
    C_deployed = spot_cost + cfut
    # неперекрывающиеся окна
    def windows(k_days):
        k = k_days * 3
        res, i = [], 0
        while i + k <= len(rows):
            seg = rows[i:i + k]
            fu = sum(q * x['F'] * x['r'] for x in seg)
            pp = q * ((seg[-1]['S'] - seg[0]['S']) + (seg[0]['F'] - seg[-1]['F']))
            fe = q * (seg[0]['S'] * FEE_SPOT + seg[0]['F'] * FEE_PERP +
                      seg[-1]['S'] * FEE_SPOT + seg[-1]['F'] * FEE_PERP)
            res.append({'start': seg[0]['t'], 'end': seg[-1]['t'],
                        'net_usdt': round(fu + pp - fe, 6)})
            i += k
        return res, (len(rows) - i)
    w30, r30 = windows(30)
    w60, r60 = windows(60)
    return {
        'symbol': sym, 'q': q, 'q_step_floor': q_step, 'baseStep': st,
        'S0': S0, 'F0': F0, 'ST': rows[-1]['S'], 'FT': rows[-1]['F'],
        'n_periods': len(rows),
        't_start': rows[0]['t'], 't_end': rows[-1]['t'],
        'funding_usdt': round(funding, 6),
        'price_pnl_usdt': round(price_pnl, 6),
        'fees_usdt': round(fees, 6),
        'net_usdt': round(net, 6),
        'C0_usdt': round(C0, 4),
        'net_bps_on_C0': round(net / C0 * 1e4, 3),
        'spot_cost_usdt': round(spot_cost, 4),
        'min_cfut_usdt': round(min_cfut, 4),
        'cfut_deployed_usdt': round(cfut, 4),
        'C_deployed_usdt': round(C_deployed, 4),
        'net_bps_on_deployed': round(net / C_deployed * 1e4, 3),
        'max_adverse_short_usdt': round(-min(upnl), 4),
        'coverage_multiple_min': round(min(cov), 3),   # min(E/MM) при cfut_deployed
        'worst_t': rows[worst_i]['t'], 'worst_F': rows[worst_i]['F'],
        'E_max_usdt': round(net, 6),   # бюджет неучтённых издержек = net
        'w30': w30, 'w30_remain': r30,
        'w60': w60, 'w60_remain': r60,
    }


def main():
    res = []
    for sym in ('AAVE_USDT', 'ARKM_USDT'):
        r = run(sym)
        if r:
            res.append(r)
        print('.', end='', flush=True)
    print()
    dst = OUT / 'final_check.json'
    dst.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print('WROTE', dst)
    for r in res:
        print(f"\n== {r['symbol']}")
        print(f"  период {r['t_start']}..{r['t_end']}, n={r['n_periods']}, q={r['q']:.6f} (шаг {r['baseStep']}, floor={r['q_step_floor']})")
        print(f"  S0={r['S0']} ST={r['ST']} F0={r['F0']} FT={r['FT']}")
        print(f"  funding={r['funding_usdt']} pricePnL={r['price_pnl_usdt']} fees={r['fees_usdt']} net={r['net_usdt']}")
        print(f"  C0={r['C0_usdt']} net_bps_C0={r['net_bps_on_C0']} | C_deployed={r['C_deployed_usdt']} net_bps_deployed={r['net_bps_on_deployed']}")
        print(f"  min_cfut={r['min_cfut_usdt']} deployed={r['cfut_deployed_usdt']} adverse={r['max_adverse_short_usdt']} coverage_multiple={r['coverage_multiple_min']}")
        print(f"  E_max={r['E_max_usdt']} (net до неучтённых издержек)")
        print(f"  окна 30d: {[(w['start'], w['end'], w['net_usdt']) for w in r['w30']]} остаток={r['w30_remain']} периодов")
        print(f"  окна 60d: {[(w['start'], w['end'], w['net_usdt']) for w in r['w60']]} остаток={r['w60_remain']}")


if __name__ == '__main__':
    main()