#!/usr/bin/env python3
"""Margin-survivability + cost audit (пересмотр).

Единый масштаб: spot-покупка = N0 USDT (номинал), q = N0/S0 базового актива.
Воспроизводим по 8H-траектории equity короткого perp:
  E_t = C_fut + funding_to_t + short_upnl_t - fee_fut_in
  short_upnl_t = q*(F_0 - F_t); funding_to_t = Σ q*mark*r (short получает)
  fee_fut_in = q*F_0*f_p; fee_fut_out = q*F_T*f_p (в конце)
Ликвидация если E_t < MM_t = max(0, notional_t*MMR), notional=|q|*F_t.
Ищем минимальный C_fut : E_t >= MM_t во все моменты; cfut_deployed = C_fut+20%.
C_deployed = spot_cost + cfut_deployed.
net = funding_total + price_pnl - fees_spot(2) - fees_perp(2), fee по номиналам.
xStocks (AMZNX/AAPLX): fee spot=10 bps, perp=5 bps (taker консерв.);
corporate profit distribution (с 2026-09-03 long получает/short платит) —
НЕ включена в fundingRate, публичных данных по обеим сторонам нет
=> для подтверждаемой доходности xStocks исключаются (research-only).
"""
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = 'https://api.pionex.com'
UA = 'Mozilla/5.0'
OUT = Path('/home/kali/safetrade-research/data/pionex')
N0 = 200.0
MMR_STRESS = [0.005, 0.01, 0.02]

CAND = {
    'AAVE_USDT': {'fee_spot_bps': 5.0, 'fee_perp_bps': 5.0, 'xstock': False},
    'ARKM_USDT': {'fee_spot_bps': 5.0, 'fee_perp_bps': 5.0, 'xstock': False},
    'AMZNX_USDT': {'fee_spot_bps': 10.0, 'fee_perp_bps': 5.0, 'xstock': True},
    'AAPLX_USDT': {'fee_spot_bps': 10.0, 'fee_perp_bps': 5.0, 'xstock': True},
}


def get(path, params=None):
    url = BASE + path + ('?' + urllib.parse.urlencode(params) if params else '')
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError:
        return 999, {}
    except Exception as e:
        return 0, {'exception': repr(e)}


def fetch_series(perp):
    code, body = get('/api/v1/market/fundingRates', {'symbol': perp, 'limit': 500})
    time.sleep(0.12)
    d = body.get('data') if code == 200 else {}
    rates = (d.get('rates') or []) if isinstance(d, dict) else (d or [])
    fund = [{'t': int(x['fundingTime']), 'r': float(x['fundingRate'])}
            for x in rates if 'fundingTime' in x and 'fundingRate' in x]
    fund.sort(key=lambda z: z['t'])
    t1 = fund[-1]['t']
    code, body = get('/api/v1/market/klines',
                     {'symbol': perp, 'interval': '8H', 'limit': 500,
                      'type': 'PERP', 'endTime': t1})
    time.sleep(0.12)
    ks = body.get('data', {}).get('klines', []) if code == 200 else []
    mark = {int(k['time']): float(k['close']) for k in ks if 'time' in k}
    out = []
    for x in fund:
        cand = [o for o in mark if o + 8 * 3600 * 1000 <= x['t']]
        if not cand:
            continue
        out.append({'t': x['t'], 'r': x['r'], 'F': mark[max(cand)]})
    return out


def fetch_spot_klines(sym, end_ms):
    code, body = get('/api/v1/market/klines',
                     {'symbol': sym, 'interval': '8H', 'limit': 500,
                      'type': 'SPOT', 'endTime': end_ms})
    time.sleep(0.12)
    ks = body.get('data', {}).get('klines', []) if code == 200 else []
    return {int(k['time']): float(k['close']) for k in ks if 'time' in k}


def main():
    results = []
    for sym, cfg in CAND.items():
        s = fetch_series(sym + '_PERP')
        if len(s) < 2:
            continue
        S0 = json.loads((OUT / 'carry_analysis_v2.json').read_text()) \
            ['results']['spot5.0_perp5.0'] or []
        s0map = {r['symbol']: r for r in
                 json.loads((OUT / 'carry_analysis_v2.json').read_text())
                 ['results']['spot5.0_perp5.0']}
        S0 = s0map[sym]['S0']
        q = N0 / S0
        fee_s = cfg['fee_spot_bps'] * 1e-4
        fee_p = cfg['fee_perp_bps'] * 1e-4
        F0, FT = s[0]['F'], s[-1]['F']
        # spot-траектория: последняя закрытая SPOT свеча до каждого t
        spot_k = fetch_spot_klines(sym, s[-1]['t'])
        ST = max((o for o in spot_k if o + 8 * 3600 * 1000 <= s[-1]['t']), default=None)
        ST = spot_k[ST] if ST is not None else S0
        funding = []
        acc = 0.0
        for x in s:
            acc += q * x['F'] * x['r']
            funding.append(acc)
        upnl = [q * (F0 - x['F']) for x in s]     # perp-нога (short)
        funding_total = acc
        price_pnl = q * ((ST - S0) + (F0 - FT))   # ОБЕ ноги: spot + perp
        fees = q * (S0 * fee_s + F0 * fee_p + FT * fee_s + FT * fee_p)
        net = funding_total + price_pnl - fees
        mmr_data = {}
        for mmr in MMR_STRESS:
            fee_in = q * F0 * fee_p
            need = [max(0.0, abs(q) * s[i]['F'] * mmr) - (funding[i] + upnl[i] - fee_in)
                    for i in range(len(s))]
            min_cfut = max(need) if need else 0.0
            # ratio при cfut_deployed (с запасом)
            cfut = min_cfut * 1.20
            ratios = []
            for i in range(len(s)):
                MM = max(0.0, abs(q) * s[i]['F'] * mmr)
                E = cfut + funding[i] + upnl[i] - fee_in
                ratios.append(E / MM if MM > 0 else float('inf'))
            worst_i = ratios.index(min(ratios))
            mmr_data[f'mmr{mmr:.3f}'] = {
                'min_cfut': round(max(0.0, min_cfut), 4),
                'cfut_deployed(+20%)': round(cfut, 4),
                'min_margin_ratio': round(min(ratios), 3),
                'worst_t': s[worst_i]['t'], 'worst_F': round(s[worst_i]['F'], 4),
            }
        m = mmr_data['mmr0.005']
        spot_cost = q * S0
        C_deployed = spot_cost + m['cfut_deployed(+20%)']
        net_bps_deployed = net / C_deployed * 1e4
        results.append({
            'symbol': sym, 'q': round(q, 6), 'S0': S0,
            'F0': round(F0, 4), 'FT': round(FT, 4),
            'xstock': cfg['xstock'],
            'spot_cost_usdt': round(spot_cost, 2),
            'net_usdt': round(net, 4),
            'max_adverse_short_usdt': round(-min(upnl), 4),
            'mmr': mmr_data,
            'C_deployed(mmr0.5%)': round(C_deployed, 2),
            'net_bps_on_deployed': round(net_bps_deployed, 3),
        })
        print('.', end='', flush=True)
    print()
    dst = OUT / 'margin_audit.json'
    dst.write_text(json.dumps({'N0': N0, 'MMR_STRESS': MMR_STRESS, 'results': results},
                              ensure_ascii=False, indent=1))
    print('WROTE', dst)
    print()
    print('{:<12}{:<8}{:<10}{:<10}{:<10}{:<10}{:<14}{:<14}'.format(
        'symbol', 'q', 'S0', 'F0->FT', 'net_nsdt', 'adverse', 'C_deployed', 'net_bps'))
    for r in results:
        print('{:<12}{:<8}{:<10}{:<10}{:<10}{:<10}{:<14}{:<14}'.format(
            r['symbol'], r['q'], r['S0'], f"{r['F0']}->{r['FT']}",
            r['net_usdt'], r['max_adverse_short_usdt'],
            r['C_deployed(mmr0.5%)'], r['net_bps_on_deployed']))


if __name__ == '__main__':
    main()