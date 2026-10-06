#!/usr/bin/env python3
"""Shortlist Pionex (шаг 1): funding carry — статистика и fee break-even.

Для каждого рынка (spot+perp): funding-история (500 записей, ~8ч интервал),
доля положительных, средний/медиана в bps, и break-even по комиссиям при
стресс 0/1/5/10 bps на ногу (4 ноги за круг: spot entry, perp entry,
spot exit, perp exit).
Публичный REST, без ключа.
"""
import json
import statistics
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = 'https://api.pionex.com'
UA = 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'
OUT = Path('/home/kali/safetrade-research/data/pionex')


def get(path, params=None, timeout=20):
    url = BASE + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception as e:
        return 0, {'exception': repr(e)}


def main():
    report = json.load(open(OUT / 'report.json'))
    rows = []
    for m in report['selected_markets']:
        sym, perp = m['symbol'], m['perp_symbol']
        code, body = get('/api/v1/market/fundingRates', {'symbol': perp, 'limit': 500})
        time.sleep(0.12)
        if code != 200:
            continue
        fr = body.get('data')
        rates = (fr.get('rates') or fr.get('fundingRates') or []) if isinstance(fr, dict) else (fr or [])
        vals = []
        for x in rates:
            try:
                vals.append(float(x.get('fundingRate')))
            except (TypeError, ValueError):
                continue
        if not vals:
            continue
        bps = [v * 1e4 for v in vals]          # fundingRate доля -> bps за 8ч
        n = len(bps)
        pos_share = sum(1 for v in bps if v > 0) / n
        mean_bps = statistics.fmean(bps)
        med_bps = statistics.median(bps)
        daily = mean_bps * 3                  # 3 периода/день
        row = {
            'symbol': sym, 'perp': perp, 'n': n,
            'funding_mean_bps_8h': round(mean_bps, 4),
            'funding_median_bps_8h': round(med_bps, 4),
            'funding_pos_share': round(pos_share, 3),
            'funding_bps_day': round(daily, 4),
            'spot_spread_bps': m.get('spot_spread_bps'),
            'perp_spread_bps': m.get('perp_spread_bps'),
        }
        # break-even по комиссиям: сколько дней funding покрывает 4 ноги
        for fee in (0, 1, 5, 10):
            cost = 4 * fee
            row[f'breakeven_days_fee{fee}bps'] = (round(cost / daily, 2)
                                                  if daily > 0 else None)
        rows.append(row)
        print('.', end='', flush=True)
    print()
    # сортировка: по funding_bps_day убыв., затем по спреду
    rows.sort(key=lambda r: (r['funding_bps_day'] if r['funding_bps_day'] > 0 else -999),
              reverse=True)
    dst = OUT / 'shortlist_funding.json'
    dst.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    print(f'WROTE {dst} ({len(rows)} рынков)')
    print()
    hdr = ('symbol', 'fund_bps/8h', 'pos%', 'bps/day', 'spot_sp', 'perp_sp',
           'be1d', 'be5d', 'be10d')
    print('{:<14}{:<12}{:<7}{:<9}{:<9}{:<9}{:<8}{:<8}{:<8}'.format(*hdr))
    for r in rows[:20]:
        print('{:<14}{:<12}{:<7}{:<9}{:<9}{:<9}{:<8}{:<8}{:<8}'.format(
            r['symbol'], r['funding_mean_bps_8h'], r['funding_pos_share'],
            r['funding_bps_day'], str(r['spot_spread_bps']), str(r['perp_spread_bps']),
            str(r['breakeven_days_fee1bps']), str(r['breakeven_days_fee5bps']),
            str(r['breakeven_days_fee10bps'])))


if __name__ == '__main__':
    main()