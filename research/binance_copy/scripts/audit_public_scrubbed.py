#!/usr/bin/env python3
"""DERIVED/CENSORED public-only variant of the original Binance SPOT Copy audit.

ORIGINAL SOURCE:  /tmp/gigatool/binance_copy_audit/audit.py  (decoded from
  dialog task_d609795f9aa9af5c35cadae878e2e7c4, seq9
  cgpt_copy_replicability_20261105_v1)
ORIGINAL SHA256: 97e803f2db832bd011f532d71f585fe17477e76862d73ecbe3974fa0342aedc1

This variant is DERIVED and CENSORED: it removes all signed/private Binance
endpoints (account/apiRestrictions, account/commission), the credentials-file
loader and HMAC signing so the script is strictly PUBLIC read-only and carries
no way to reach account data or use API keys. Aggregation and public-market
depth logic is preserved verbatim from the original where it only touches
public endpoints. No key files, tokens or account responses are included.
"""
import argparse, json, statistics, sys, time
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

PUBLIC_HOST = 'https://data-api.binance.vision'
PUBLIC_PATHS = frozenset({'/api/v3/time','/api/v3/exchangeInfo','/api/v3/depth'})
SYMBOLS = ('BTCUSDT','WLDUSDT','ZROUSDT','ENAUSDT','PEPEUSDT','ZECUSDT','ADAUSDT','TIAUSDT',
           'UNIUSDT','TRUMPUSDT','PENDLEUSDT','WLFIUSDT','DOTUSDT','PUMPUSDT','XPLUSDT')
LEADER_BALANCE = Decimal('146555.43')  # User-provided snapshot, not today's live balance (preserved for scaling illustration)
EXAMPLES = (('WLFIUSDT', '12.05'), ('WLFIUSDT', '58.29'), ('BTCUSDT', '56.42'),
            ('TRUMPUSDT', '400.15'), ('WLFIUSDT', '1853.63'), ('WLFIUSDT', '1627.47'))
D = Decimal

def get_json(path, params=None, timeout=12):
    params = dict(params or {})
    if path not in PUBLIC_PATHS:
        raise ValueError('BLOCKED_NON_ALLOWLIST_PATH_PUBLIC_ONLY')
    query = urlencode(params)
    headers = {'User-Agent':'Local-Readonly-Spot-Audit/1.0-public'}
    req = Request(PUBLIC_HOST + path + ('?' + query if query else ''),headers=headers,method='GET')
    try:
        with urlopen(req,timeout=timeout) as resp:
            return json.load(resp)
    except HTTPError as e:
        raise RuntimeError(f'API_HTTP_{e.code}') from None
    except URLError:
        raise RuntimeError('API_NETWORK_ERROR') from None

def minimum_notional(symbol_info):
    for f in symbol_info.get('filters',[]):
        if f.get('filterType')=='NOTIONAL':return D(f.get('minNotional','0'))
    for f in symbol_info.get('filters',[]):
        if f.get('filterType')=='MIN_NOTIONAL':return D(f.get('minNotional','0'))
    return None

def price_slippage(depth, size, side):
    levels=depth['asks'] if side=='BUY' else depth['bids']
    if not levels: return None
    best=D(levels[0][0]); remaining=D(str(size)); base_qty=D('0'); cost=D('0')
    for px_s,q_s in levels:
        px=D(px_s); available=D(q_s)*px
        filled=min(remaining,available)
        if filled<=0: continue
        base_qty+=filled/px
        cost+=filled
        remaining-=filled
        if remaining<=0: break
    if remaining>0 or base_qty==0: return None
    vwap=cost/base_qty
    slip_bps=((vwap/best)-1 if side=='BUY' else (1-vwap/best))*D('10000')
    return round(float(slip_bps),4)

def assess(symbol_info,depth):
    best_bid=D(depth['bids'][0][0]);best_ask=D(depth['asks'][0][0]);mid=(best_bid+best_ask)/2
    result={'symbol':symbol_info['symbol'],'status':symbol_info.get('status'),
            'min_notional_usdt':str(minimum_notional(symbol_info)),
            'spread_bps':round(float((best_ask-best_bid)/mid*D('10000')),3)}
    for n in (10,100,1000):
        result[f'buy_slippage_{n}_bps']=price_slippage(depth,n,'BUY')
        result[f'sell_slippage_{n}_bps']=price_slippage(depth,n,'SELL')
    return result

def estimate_examples(symbol_metadata,capital):
    factor=D(capital)/LEADER_BALANCE
    ret=[]
    for sym,lead_quote in EXAMPLES:
        mi=minimum_notional(symbol_metadata[sym]) if sym in symbol_metadata else None
        proportion=D(lead_quote)*factor
        ret.append({'symbol':sym,'leader_fill_usdt':lead_quote,
                    'scaled_fill_usdt':str(round(proportion,4)),
                    'min_notional_usdt':str(mi) if mi is not None else None,
                    'potentially_below_minimum': (bool(proportion < mi) if mi is not None else None)})
    return ret

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--output',required=True)
    args=ap.parse_args()
    public_time=get_json('/api/v3/time')['serverTime']
    skew=int(time.time()*1000)-int(public_time)
    exchange=get_json('/api/v3/exchangeInfo')
    metadata={m['symbol']:m for m in exchange['symbols'] if m['symbol'] in SYMBOLS}
    rows=[];errors={}
    for symbol in SYMBOLS:
        info=metadata.get(symbol)
        if not info or info.get('status')!='TRADING':
            errors[symbol]='NOT_TRADING_OR_NOT_LISTED';continue
        try:
            depth=get_json('/api/v3/depth',{'symbol':symbol,'limit':100})
            rows.append(assess(info,depth))
        except (RuntimeError,KeyError,ValueError,InvalidOperation,ZeroDivisionError) as exc:
            errors[symbol]=str(exc) if str(exc).startswith('API_') else 'DEPTH_UNAVAILABLE'
    result={'as_of_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
            'variant':'DERIVED/CENSORED public-only (original private endpoints removed)',
            'clock_skew_ms':skew,'leader_balance_snapshot_usdt':str(LEADER_BALANCE),
            'warning':'Illustrative quote-fill scaling using snapshot leader balance: NOT actual Fixed Ratio behavior. Public market liquidity only; no account/fee data.',
            'markets':rows,'unavailable':errors,
            'illustrative_fills_1000usdt':estimate_examples(metadata,1000),
            'illustrative_fills_10000usdt':estimate_examples(metadata,10000)}
    import os as _os
    from pathlib import Path
    p=Path(args.output);p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=_os.open(p,_os.O_CREAT|_os.O_WRONLY|_os.O_TRUNC,0o600)
    with _os.fdopen(fd,'w') as f:json.dump(result,f,indent=2)
    print(json.dumps({'status':'OK','report_path':str(p),'market_count':len(rows),
                      'unavailable':errors,'clock_skew_ms':skew},ensure_ascii=False))

if __name__=='__main__':
    try:main()
    except Exception as exc:
        code=str(exc)
        if not code.startswith(('API_','KEY_','CLOCK_')): code='AUDIT_FAILED_NO_DETAILS'
        print(json.dumps({'status':'STOP','reason':code}));sys.exit(1)
