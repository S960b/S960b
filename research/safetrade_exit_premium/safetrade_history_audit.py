#!/usr/bin/env python3
"""SafeTrade historical data availability and LTC/BTC/DOGE price-premium prefilter.
Public GET only; no API keys; legacy endpoints may be obsolete.
Run: python3 safetrade_history_audit.py --days 30 --out ./safe_audit
"""
import argparse, datetime as dt, hashlib, json, statistics, time
import urllib.parse, urllib.request, urllib.error
from pathlib import Path

SAFE="https://safe.trade/api/v2/peatio/public/markets"
BYBIT="https://api.bybit.com/v5/market/kline"
PAIRS={"ltcusdt":"LTCUSDT","dogeusdt":"DOGEUSDT","btcusdt":"BTCUSDT"}
def get(url,params=None):
    uri=url+("?" + urllib.parse.urlencode(params) if params else "")
    req=urllib.request.Request(uri,headers={"User-Agent":"readonly-historical-market-audit/1.0","Accept":"application/json"})
    t=time.monotonic()
    try:
        with urllib.request.urlopen(req,timeout=18) as r:
            body=r.read(3_000_000)
            return {"ok":True,"status":r.status,"url":uri,
                    "seconds":round(time.monotonic()-t,2),
                    "data":json.loads(body)}
    except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError,ValueError) as e:
        return {"ok":False,"url":uri,"error":str(e)[:400]}
def unwrap(data):
    if isinstance(data,list):return data
    if isinstance(data,dict):
        for k in ("data","result","candles","klines","kline"):
            if isinstance(data.get(k),list):return data[k]
    return []
def normal(rows):
    """Recognize common [unix seconds,open,high,low,close,vol] and dict bars.
    Unrecognized schemas produce no bars, not guessed market evidence.
    """
    out={}
    for r in rows:
        try:
            if isinstance(r,dict):
                t=r.get("time",r.get("timestamp",r.get("at",r.get("ts"))))
                c=r.get("close",r.get("c"))
                v=r.get("volume",r.get("v",0))
            elif isinstance(r,list) and len(r)>=6:
                t=r[0];c=r[4];v=r[5]
            else:continue
            t=int(float(t));t=t//1000 if t>10_000_000_000 else t
            c=float(c);v=float(v)
            if 1500000000<t<2100000000 and c>0 and v>=0:
                out[t]={"timestamp":t,"close":c,"volume":v}
        except (TypeError,ValueError,IndexError):continue
    return out
def bybit(sym,days):
    now=int(time.time()*1000);start=now-days*86400000;end=now;out={}
    while end>=start:
        r=get(BYBIT,{"category":"spot","symbol":sym,"interval":"60","end":end,"limit":1000})
        if not r["ok"] or r["data"].get("retCode")!=0:
            return {},{"error":r.get("error",r.get("data",{}).get("retMsg","unknown"))}
        raw=r["data"]["result"]["list"]
        if not raw:break
        for x in raw:
            try:
                t=int(x[0])//1000
                if start//1000<=t and t+3600<=now//1000:
                    out[t]={"timestamp":t,"close":float(x[4]),"volume":float(x[5])}
            except (ValueError,IndexError):pass
        earliest=min(int(x[0]) for x in raw)
        if len(raw)<1000 or earliest<=start:break
        if earliest>=end:break
        end=earliest-1;time.sleep(.17)
    return out,{"bars":len(out)}
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser();p.add_argument("--days",type=int,default=30)
    p.add_argument("--out",type=Path,required=True);a=p.parse_args()
    if not 1<=a.days<=90:p.error("days must be 1..90")
    a.out.mkdir(parents=True,exist_ok=True)
    now=int(time.time());start=now-a.days*86400
    report={"captured_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
      "period_days":a.days,"status":"DATA_AVAILABILITY_FIRST",
      "limitations":["Legacy SafeTrade v2 routes may be obsolete",
       "OHLC does not prove executable prices or causality of withdrawals",
       "No historical orderbook, withdrawal fees/status, deposit/withdrawal flows or news",
       "Lack of bars is not zero premium; historical matched bars require actual trade activity"],
      "probes":{},"markets":{}}
    for tag,url in {"markets":SAFE,"tickers":SAFE+"/tickers"}.items():
        r=get(url);report["probes"][tag]={"ok":r["ok"],"status":r.get("status"),"error":r.get("error"),
                                          "type":type(r.get("data")).__name__}
        if r["ok"]:(a.out/("raw_"+tag+".json")).write_text(json.dumps(r["data"]),encoding="utf8")
    for market,sym in PAIRS.items():
        # bounded historical requests: endpoints are NOT assumed operational.
        chunks=[]; safe={}
        for period_start in range(start,now,7*86400):
            period_end=min(now,period_start+7*86400)
            r=get(SAFE+"/"+market+"/k-line",{"period":60,"time_from":period_start,"time_to":period_end})
            chunks.append({"from":period_start,"to":period_end,"ok":r["ok"],
                           "status":r.get("status"),"error":r.get("error"),
                           "returned_items":len(unwrap(r.get("data")))})
            if r["ok"]:
                safe.update(normal(unwrap(r["data"])))
            time.sleep(.18)
        ext,extstatus=bybit(sym,a.days)
        overlap=sorted(set(safe)&set(ext))
        values=[]
        for t in overlap:
            local=safe[t];benchmark=ext[t]
            premium=100*(local["close"]/benchmark["close"]-1)
            values.append({"ts":t,"safe_close":local["close"],"bybit_spot_close":benchmark["close"],
                           "premium_pct":round(premium,6),"safe_volume_base":local["volume"]})
        (a.out/(market+"_matched.json")).write_text(json.dumps(values,indent=2),encoding="utf8")
        large=[v for v in values if abs(v["premium_pct"])>=1]
        report["markets"][market]={"bybit_symbol":sym,"safe_probes":chunks,
            "safe_unique_bars":len(safe),"benchmark":extstatus,"exact_hour_overlaps":len(values),
            "coverage_fraction_of_benchmark":round(len(values)/max(1,len(ext)),4),
            "positive_premium_over_1pct":sum(v["premium_pct"]>=1 for v in values),
            "negative_premium_below_minus_1pct":sum(v["premium_pct"]<=-1 for v in values),
            "median_premium_pct":round(statistics.median(v["premium_pct"] for v in values),4) if values else None,
            "max_positive_premium_pct":round(max(v["premium_pct"] for v in values),4) if values else None,
            "largest_absolute_examples":sorted(large,key=lambda v:-abs(v["premium_pct"]))[:20],
            "validation":"INSUFFICIENT" if len(values)<72 else "PRELIMINARY_ONLY"}
    path=a.out/"report.json";path.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf8")
    files={p.name:{"sha256":sha(p),"bytes":p.stat().st_size} for p in a.out.iterdir() if p.is_file()}
    (a.out/"manifest.json").write_text(json.dumps(files,indent=2),encoding="utf8")
    print(json.dumps({"report":str(path),"manifest":str(a.out/"manifest.json"),
                      "markets":report["markets"],"probes":report["probes"]},ensure_ascii=False))
if __name__=="__main__":main()
