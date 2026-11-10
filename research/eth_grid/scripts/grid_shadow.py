#!/usr/bin/env python3
"""Binance ETHUSDT public read-only, close-signal/next-open virtual spot grid.

This is an economic feasibility screen, NOT an execution-quality Binance grid backtest.
No auth, secret reading, account actions or non-GET requests. No hyperparameter search.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = 'https://data-api.binance.vision'
INTERVAL_MS = 5 * 60 * 1000
STEPS = ((0.005, 0.10), (0.01, 0.15), (0.02, 0.20))
COSTS = ((0.001, 0.0005), (0.002, 0.001))  # per-side fee, adverse execution


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def fetch_json(path, parameters=None):
    params = ('?' + urllib.parse.urlencode(parameters)) if parameters else ''
    url = BASE + path + params
    request = urllib.request.Request(url, headers={'User-Agent': 'binance-grid-shadow-readonly/1.0'})
    with urllib.request.urlopen(request, timeout=20) as r:
        if r.status != 200:
            raise RuntimeError(f'HTTP {r.status}')
        raw = r.read(2_000_000)
    return json.loads(raw)


def download(start_ms, end_ms):
    rows, cursor = [], start_ms
    while cursor < end_ms:
        batch = fetch_json('/api/v3/klines', {'symbol': 'ETHUSDT', 'interval': '5m',
                                              'startTime': cursor, 'endTime': end_ms - 1, 'limit': 1000})
        if not isinstance(batch, list) or not batch:
            break
        for c in batch:
            ts = int(c[0])
            if ts < cursor or ts >= end_ms:
                raise RuntimeError('KLINE_ORDER_ERROR')
            rows.append({'t': ts, 'o': float(c[1]), 'c': float(c[4])})
        next_cursor = rows[-1]['t'] + INTERVAL_MS
        if next_cursor <= cursor:
            raise RuntimeError('NON_ADVANCING_CURSOR')
        cursor = next_cursor
        time.sleep(0.18)  # bounded read-only public requests
    if len(rows) < 3:
        raise RuntimeError('INSUFFICIENT_KLINES')
    if len(set(v['t'] for v in rows)) != len(rows):
        raise RuntimeError('DUPLICATE_KLINES')
    for i, c in enumerate(rows):
        if c['o'] <= 0 or c['c'] <= 0 or not math.isfinite(c['o']) or not math.isfinite(c['c']):
            raise RuntimeError('INVALID_PRICE')
        if i and c['t'] - rows[i-1]['t'] != INTERVAL_MS:
            raise RuntimeError('GAPPED_KLINES_STOP')
    return rows


def index_at(price, anchor, step, n):
    steps = math.log(price / anchor) / math.log1p(step)
    return max(-n, min(n, math.floor(steps) if steps >= 0 else math.ceil(steps)))


def simulate(rows, eth, step, range_fraction, fee, adverse):
    if not (eth > 0 and 0 < step < range_fraction < 1 and 0 <= fee < 1 and 0 <= adverse < 1):
        raise ValueError('INVALID_ARGUMENT')
    p0 = rows[0]['c']; p_last = rows[-1]['c']
    starting = eth * p0
    # Same costly initial ETH -> USDT conversion is applied to grid and static 50/50.
    cash = (starting / 2) * (1 - fee - adverse)
    inventory = eth / 2
    static_cash, static_eth = cash, inventory
    n = math.ceil(range_fraction / step)
    notion = starting * 0.5 / n
    prev_index, trades, skipped, fees_usdt = 0, 0, 0, (starting / 2) * (fee + adverse)
    out_range = 0
    max_drawdown = 0.0
    peak = starting
    for i in range(1, len(rows)):
        prevclose = rows[i-1]['c']; current_open = rows[i]['o']
        target = index_at(prevclose, p0, step, n)
        if abs(math.log(prevclose / p0)) > math.log1p(step) * n:
            out_range += 1
        diff = target - prev_index
        if diff:
            for _ in range(abs(diff)):
                if diff < 0:  # buy ETH on decline
                    gross = min(notion, cash / (1 + fee))
                    if gross < notion * 0.1:
                        skipped += 1
                        continue
                    received = gross * (1 - adverse) / current_open
                    cost = gross * (1 + fee)
                    inventory += received; cash -= cost
                    fees_usdt += gross * (fee + adverse)
                else:  # sell ETH into rally
                    qty = min(notion / current_open, inventory)
                    if qty * current_open < notion * 0.1:
                        skipped += 1
                        continue
                    inventory -= qty; cash += qty * current_open * (1 - fee - adverse)
                    fees_usdt += qty * current_open * (fee + adverse)
                trades += 1
        prev_index = target
        equity = cash + inventory * rows[i]['c']
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, 1 - equity / peak)
    grid_end = cash + inventory * p_last
    static_end = static_cash + static_eth * p_last
    hodl_end = eth * p_last
    return {'grid_total_usdt': round(grid_end, 5), 'hodl_usdt': round(hodl_end, 5),
            'static_50_50_usdt': round(static_end, 5),
            'grid_minus_hodl_usdt': round(grid_end-hodl_end, 5),
            'grid_minus_static_usdt': round(grid_end-static_end, 5),
            'grid_return_pct': round(100 * (grid_end/starting-1), 4),
            'grid_max_drawdown_pct': round(100*max_drawdown,4),
            'eth_end': round(inventory,8), 'cash_end': round(cash,5),
            'trades': trades, 'skipped_for_insufficient_inventory': skipped,
            'outside_range_bars':out_range, 'estimated_allin_cost_usdt':round(fees_usdt,5)}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--eth',type=float,default=1.0,help='Virtual ETH capital; 1.0 for scale-free normalized run')
    p.add_argument('--days',type=int,default=30)
    p.add_argument('--start-ms',type=int,default=None,help='Override start; for prospective post-freeze run')
    p.add_argument('--end-ms',type=int,default=None)
    p.add_argument('--out',required=True)
    a=p.parse_args()
    server_ms=int(fetch_json('/api/v3/time')['serverTime'])
    end_ms=(a.end_ms or server_ms)//INTERVAL_MS*INTERVAL_MS
    start_ms=(a.start_ms or end_ms-a.days*86400000)//INTERVAL_MS*INTERVAL_MS
    if not 0 < end_ms-start_ms <= 90 * 86400000 or a.eth <= 0:
        raise SystemExit('BAD_WINDOW_OR_CAPITAL')
    rows=download(start_ms,end_ms)
    digest=hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest()
    result={'purpose':'read_only_grid_economic_screen_not_binance_execution_backtest',
            'as_of_utc':utc(server_ms),'requested_start_utc':utc(start_ms),
            'sample_start_utc':utc(rows[0]['t']),'sample_end_utc':utc(rows[-1]['t']),
            'freeze_forward_start_ms':rows[-1]['t']+INTERVAL_MS,
            'candles':len(rows),'market_sha256':digest,'symbol':'ETHUSDT','interval':'5m',
            'eth_virtual_start':a.eth,'start_price':rows[0]['c'],'end_price':rows[-1]['c'],
            'method':'previous 5m close signal, next bar open virtual market execution; no intrabar highs/lows; no leverage; freeze outside range',
            'variants':[]}
    for step, rng in STEPS:
        for fee, slip in COSTS:
            result['variants'].append({'step_pct':step*100,'range_pct':rng*100,
                'fee_pct_per_side':fee*100,'slippage_pct_per_side':slip*100,
                **simulate(rows,a.eth,step,rng,fee,slip)})
    output=Path(a.out);output.parent.mkdir(parents=True,exist_ok=True)
    tmp=output.with_suffix(output.suffix+'.tmp')
    fd=os.open(str(tmp),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f: json.dump(result,f,ensure_ascii=False,indent=2)
    os.replace(tmp,output)
    os.chmod(output,0o600)
    print(json.dumps({'status':'OK','output':str(output),'candles':len(rows),
        'forward_freeze_start_ms':result['freeze_forward_start_ms'],
        'market_sha256':digest,'variants':len(result['variants'])}))

if __name__=='__main__': main()
