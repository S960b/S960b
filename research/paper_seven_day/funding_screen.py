#!/usr/bin/env python3
"""Public Bybit spot/perpetual funding feasibility scan. GET-only; NO ORDERS.

This is a prefilter, not an executable arbitrage backtest: funding histories do
not establish obtainable spot/perp entry spreads or fill probability.
"""
import argparse
import datetime as dt
import json
import statistics
import time
import urllib.parse
import urllib.request

BASE = "https://api.bybit.com"
def get(path, params):
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    for attempt in range(5):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"paper-research-readonly/1.0"})
            with urllib.request.urlopen(req,timeout=20) as resp:
                obj=json.load(resp)
            if obj.get("retCode") == 0:
                return obj["result"]
            if obj.get("retCode") == 10006:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(str(obj.get("retCode"))+": "+str(obj.get("retMsg")))
        except (TimeoutError, OSError):
            if attempt == 4: raise
            time.sleep(2 ** attempt)
    raise RuntimeError("retries exhausted")

def instrument(category,symbol):
    xs=get("/v5/market/instruments-info",{"category":category,"symbol":symbol}).get("list",[])
    return xs[0] if xs else {}

def ticker(category,symbol):
    xs=get("/v5/market/tickers",{"category":category,"symbol":symbol}).get("list",[])
    return xs[0] if xs else {}

def scan(sym, days, fee_bps):
    now=int(time.time()*1000)
    start=now-days*86400000
    vals=[]
    end=now
    while end>=start:
        rs=get("/v5/market/funding/history",{
          "category":"linear","symbol":sym,"startTime":start,"endTime":end,"limit":200})
        items=rs.get("list",[])
        if not items: break
        for it in items:
            t=int(it["fundingRateTimestamp"])
            if start<=t<=now: vals.append((t,float(it["fundingRate"])))
        earliest=min(int(x["fundingRateTimestamp"]) for x in items)
        if earliest<=start or len(items)<200: break
        end=earliest-1
        time.sleep(.14)
    vals=sorted(set(vals))
    futures=ticker("linear",sym)
    spot=ticker("spot",sym)
    lininfo=instrument("linear",sym)
    spinfo=instrument("spot",sym)
    n=len(vals)
    # Positive rate: short perp receives; negative: short pays.
    net_rate=sum(r for _,r in vals)
    period_days=((vals[-1][0]-vals[0][0])/86400000) if n>=2 else 0
    # Initial+final, 2 legs, both taker at conservative assumed cost per side.
    round_trip_fee=4*fee_bps/10000
    return {
      "symbol":sym,"window_days_requested":days,"funding_events":n,
      "first_funding_utc":dt.datetime.fromtimestamp(vals[0][0]/1000,dt.timezone.utc).isoformat() if n else None,
      "last_funding_utc":dt.datetime.fromtimestamp(vals[-1][0]/1000,dt.timezone.utc).isoformat() if n else None,
      "observed_span_days":round(period_days,2),
      "positive_event_fraction":round(sum(r>0 for _,r in vals)/n,4) if n else None,
      "sum_historical_funding_pct":round(net_rate*100,5),
      "historical_funding_usd_per_100_notional":round(net_rate*100,4),
      "assumed_roundtrip_two_leg_fee_pct":round(round_trip_fee*100,4),
      "historical_funding_minus_fee_usd_per_100_notional":round((net_rate-round_trip_fee)*100,4),
      "rate_min_pct":round(min(r for _,r in vals)*100,6) if n else None,
      "rate_max_pct":round(max(r for _,r in vals)*100,6) if n else None,
      "rate_median_pct":round(statistics.median(r for _,r in vals)*100,6) if n else None,
      "spot_bid":spot.get("bid1Price"),"spot_ask":spot.get("ask1Price"),
      "perp_bid":futures.get("bid1Price"),"perp_ask":futures.get("ask1Price"),
      "spot_lot":spinfo.get("lotSizeFilter",{}),
      "perp_lot":lininfo.get("lotSizeFilter",{}),
      "warning":"NOT net executable profit. No historical basis, depth/slippage, funding forecast, capital utilization, hedge imperfections or liquidation costs."
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--days",type=int,default=30)
    p.add_argument("--symbols",nargs="+",default=["BTCUSDT","ETHUSDT","SOLUSDT"])
    p.add_argument("--fee-bps-per-leg-per-side",type=float,default=6.5)
    a=p.parse_args()
    if not 1<=a.days<=180: p.error("days must be 1..180")
    out={"captured_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
      "method":"GET-only historical funding and current spot/perp top-of-book",
      "assumptions":{"funding_notional_usd":100,"fee_bps_per_leg_per_side":a.fee_bps_per_leg_per_side},
      "results":[]}
    for sym in a.symbols:
        try: out["results"].append(scan(sym,a.days,a.fee_bps_per_leg_per_side))
        except Exception as e: out["results"].append({"symbol":sym,"error":str(e)})
        time.sleep(.2)
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
