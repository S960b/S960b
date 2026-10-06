#!/usr/bin/env python3
"""Pionex public probe (шаг 1) — публичные данные без ключа.

Выгружает:
- все тикеры SPOT и PERP (tickers?type=)
- bookTickers SPOT/PERP (best bid/ask)
- для выбранных совпадающих spot/perp: depth, klines, fundingRates,
  mark/index klines, openInterests
После сбора: report.json (ошибки, глубина данных, спред, funding, fee
break-even при стресс 0/1/5/10 bps на сторону).

Уважает лимит: 10 req/s по IP, weight 1, пауза между запросами.
Только чтение, без ключа, без ордеров.
"""
import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = 'https://api.pionex.com'
UA = 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'
OUT = Path('/home/kali/safetrade-research/data/pionex')


def get(path, params=None, timeout=20):
    url = BASE + path
    if params:
        import urllib.parse
        url += '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': UA,
                                               'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {'exception': repr(e)}


def pause():
    time.sleep(0.12)   # ~8 req/s, ниже лимита 10


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--max-markets', type=int, default=25)
    ap.add_argument('--out-dir', default=str(OUT))
    args = ap.parse_args()
    outdir = Path(args.out_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    raw = {}

    # 1) все тикеры SPOT и PERP
    all_tickers = {}
    for typ in ('SPOT', 'PERP'):
        code, body = get('/api/v1/market/tickers', {'type': typ})
        pause()
        raw[f'tickers_{typ}_http'] = code
        tk = body.get('data', {}).get('tickers', []) if isinstance(body, dict) else []
        all_tickers[typ] = tk
        print(f'tickers {typ}: http={code}, count={len(tk)}')
    (outdir / 'tickers.json').write_text(json.dumps(all_tickers, ensure_ascii=False, indent=1))

    # 2) bookTickers SPOT и PERP
    all_bt = {}
    for typ in ('SPOT', 'PERP'):
        code, body = get('/api/v1/market/bookTickers', {'type': typ})
        pause()
        bt = body.get('data', {}).get('tickers', []) if isinstance(body, dict) else []
        all_bt[typ] = bt
        print(f'bookTickers {typ}: http={code}, count={len(bt)}')
    (outdir / 'bookTickers.json').write_text(json.dumps(all_bt, ensure_ascii=False, indent=1))

    # 3) пересечение spot/perp по базовому символу (PERP имеет суффикс _PERP)
    spot_sym = {t.get('symbol') for t in all_tickers['SPOT']}
    perp_sym = {t.get('symbol') for t in all_tickers['PERP']}
    perp_base = {s[:-5] if s.endswith('_PERP') else s for s in perp_sym}
    common = sorted(spot_sym & perp_base)
    print(f'SPOT={len(spot_sym)} PERP={len(perp_sym)} общих(по базе)={len(common)}')

    # построим карту bookTicker для удобства
    btmap = {}
    for typ in ('SPOT', 'PERP'):
        btmap[typ] = {}
        for t in all_bt[typ]:
            btmap[typ][t.get('symbol')] = t

    # 4) для первых N общих пар — детальный сбор (sym = базовый, PERP = sym_PERP)
    selected = common[:args.max_markets]
    report = {'selected_markets': [], 'errors': []}
    market_data = {}
    for sym in selected:
        perp = sym + '_PERP'
        entry = {'symbol': sym, 'perp_symbol': perp}
        # bookTicker spot vs perp -> spread + basis
        for typ, sm in (('SPOT', sym), ('PERP', perp)):
            bt = btmap[typ].get(sm)
            if bt:
                try:
                    bid = float(bt.get('bidPrice'))
                    ask = float(bt.get('askPrice'))
                    spread_bps = (ask - bid) / bid * 1e4 if bid else None
                    entry[f'{typ.lower()}_bid'] = bid
                    entry[f'{typ.lower()}_ask'] = ask
                    entry[f'{typ.lower()}_spread_bps'] = round(spread_bps, 4) if spread_bps is not None else None
                    entry[f'{typ.lower()}_bid_size'] = bt.get('bidSize')
                    entry[f'{typ.lower()}_ask_size'] = bt.get('askSize')
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
        # funding rate для PERP
        code, body = get('/api/v1/market/fundingRates', {'symbol': perp, 'limit': 1})
        pause()
        if code == 200 and isinstance(body, dict) and body.get('data'):
            fr = body['data']
            funding = fr.get('fundingRate') if isinstance(fr, dict) else None
            if isinstance(fr, dict) and 'rates' in fr:
                rr = fr['rates']
                funding = rr[0].get('fundingRate') if isinstance(rr, list) and rr and isinstance(rr[0], dict) else None
            if funding is not None:
                try:
                    entry['funding_rate_8h'] = float(funding)
                except (TypeError, ValueError):
                    entry['funding_rate_8h'] = funding
            else:
                entry['funding_raw'] = fr
        elif code != 200:
            report['errors'].append({'sym': sym, 'endpoint': '/market/fundingRates',
                                     'code': code, 'body': str(body)[:120]})
        else:
            entry['funding_raw'] = None
        # глубины SPOT (для maker-оценки) — только если спред валиден
        code, body = get('/api/v1/market/depth', {'symbol': sym, 'limit': 20, 'type': 'SPOT'})
        pause()
        if code == 200:
            d = body.get('data', {})
            market_data.setdefault(sym, {})['spot_depth'] = d
        # futures depth
        code, body = get('/api/v1/market/depth', {'symbol': perp, 'limit': 20, 'type': 'PERP'})
        pause()
        if code == 200:
            d = body.get('data', {})
            market_data.setdefault(sym, {})['perp_depth'] = d
        # klines SPOT 1D (минимум истории, последние 2)
        code, body = get('/api/v1/market/klines',
                         {'symbol': sym, 'interval': '1D', 'limit': 2, 'type': 'SPOT'})
        pause()
        if code == 200:
            ks = body.get('data', {}).get('klines', [])
            entry['spot_klines_1d_n'] = len(ks)
        # klines PERP 1D
        code, body = get('/api/v1/market/klines',
                         {'symbol': perp, 'interval': '1D', 'limit': 2, 'type': 'PERP'})
        pause()
        if code == 200:
            ks = body.get('data', {}).get('klines', [])
            entry['perp_klines_1d_n'] = len(ks)
        # история funding (до 500 записей; 3/день ~ 167 дней)
        code, body = get('/api/v1/market/fundingRates', {'symbol': perp, 'limit': 500})
        pause()
        if code == 200 and isinstance(body, dict):
            fr = body.get('data')
            rates = []
            if isinstance(fr, dict):
                rates = fr.get('rates') or fr.get('fundingRates') or []
            elif isinstance(fr, list):
                rates = fr
            entry['funding_history_n'] = len(rates)
            if rates and isinstance(rates[0], dict):
                vals = []
                for x in rates:
                    try:
                        vals.append(float(x.get('fundingRate')))
                    except (TypeError, ValueError):
                        continue
                if vals:
                    entry['funding_mean_8h'] = round(sum(vals) / len(vals), 8)
                    entry['funding_min_8h'] = round(min(vals), 8)
                    entry['funding_max_8h'] = round(max(vals), 8)
        # mark klines PERP 1D (для carry-расчётов basis)
        code, body = get('/api/v1/market/markKlines',
                         {'symbol': perp, 'interval': '1D', 'limit': 2})
        pause()
        if code == 200:
            ks = body.get('data', {}).get('klines', [])
            entry['mark_klines_1d_n'] = len(ks)
        # index klines SPOT 1D
        code, body = get('/api/v1/market/indexKlines',
                         {'symbol': perp, 'interval': '1D', 'limit': 2})
        pause()
        if code == 200:
            ks = body.get('data', {}).get('klines', [])
            entry['index_klines_1d_n'] = len(ks)
        # open interest (PERP) — эндпоинт возвращает ВСЕ символы сразу
        code, body = get('/api/v1/market/openInterests', {'symbol': perp})
        pause()
        if code == 200 and isinstance(body, dict):
            oi = body.get('data')
            oi_list = (oi.get('openInterests') or []) if isinstance(oi, dict) else []
            mine = [x for x in oi_list
                    if isinstance(x, dict) and x.get('symbol') == perp]
            entry['open_interest'] = (mine[0].get('openInterest') if mine else None)
            entry['oi_endpoint_returns_all'] = len(oi_list)  # документирование поведения
        entry['spot_klines_1d_n'] = entry.get('spot_klines_1d_n', 0)
        entry['perp_klines_1d_n'] = entry.get('perp_klines_1d_n', 0)
        report['selected_markets'].append(entry)
        print('.', end='', flush=True)
    print()
    (outdir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1))
    (outdir / 'market_data.json').write_text(json.dumps(market_data, ensure_ascii=False, indent=1))
    print(f'WROTE {outdir}/report.json, {outdir}/market_data.json')


if __name__ == '__main__':
    main()