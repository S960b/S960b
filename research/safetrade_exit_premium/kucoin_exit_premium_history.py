#!/usr/bin/env python3
"""Historical withdrawal-asset premium research: KuCoin vs Bybit SPOT.
Public endpoints only. Current withdrawal fees are NOT historical fee evidence.
"""
import argparse,datetime as dt,hashlib,json,statistics,time,urllib.parse,urllib.request
from pathlib import Path
KU="https://api.kucoin.com"; BY="https://api.bybit.com"
COINS=("BTC","ETH","LTC","DOGE","TRX","SOL","XRP","USDT")
def request(host,path,query=None):
    url=host+path+("?" + urllib.parse.urlencode(query) if query else "")
    for attempt in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"withdraw-premium-study-readonly/1.0","Accept":"application/json"}),timeout=22) as f:
                obj=json.load(f)
            return obj
        except Exception:
            if attempt==4:raise
            time.sleep(min(20,2**attempt))
def kucoin(sym,start,end):
    res={}; chunk=1400*3600
    for lo in range(start,end,chunk):
        hi=min(end,lo+chunk)
        o=request(KU,"/api/v1/market/candles",{"symbol":sym,"type":"1hour","startAt":lo,"endAt":hi})
        if o.get("code")!="200000":raise RuntimeError("kucoin: "+str(o)[:200])
        for r in o.get("data",[]):
            try:
                t=int(r[0]);v=float(r[6]);c=float(r[2])
                if start<=t<end and c>0:res[t]=(c,v)
            except (IndexError,ValueError,TypeError):pass
        time.sleep(.12)
    return res
def bybit(sym,start,end):
    res={}; current=end*1000
    while current>start*1000:
        o=request(BY,"/v5/market/kline",{"category":"spot","symbol":sym,"interval":"60","end":current,"limit":1000})
        if o.get("retCode")!=0:raise RuntimeError("bybit "+str(o)[:200])
        rows=o["result"].get("list",[])
        if not rows:break
        oldest=min(int(r[0]) for r in rows)
        for r in rows:
            t=int(r[0])//1000
            if start<=t<end:res[t]=(float(r[4]),float(r[6]) if len(r)>6 else None)
        if oldest<=start*1000:break
        if oldest>=current:raise RuntimeError("pagination stuck")
        current=oldest-1;time.sleep(.15)
    return res
def stat(v):
    if not v:return None
    s=sorted(v)
    return {"median":round(statistics.median(s),4),"min":round(s[0],4),"max":round(s[-1],4),
            "p95":round(s[int(.95*(len(s)-1))],4),"n":len(s)}
def fee_snapshot(coin):
    o=request(KU,"/api/v3/currencies/"+coin)
    if o.get("code")!="200000":return {"error":"API response "+str(o.get("code"))}
    d=o.get("data") or {}
    chains=[]
    for ch in d.get("chains") or []:
        chains.append({k:ch.get(k) for k in ("chainName","chainId","withdrawalMinFee","withdrawFeeRate","withdrawalMinSize","isWithdrawEnabled","isDepositEnabled")})
    return {"coin":coin,"chains":chains}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--days",type=int,default=90)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args()
    if not 7<=args.days<=180:p.error("days must be 7..180")
    out=args.out;out.mkdir(parents=True,exist_ok=True)
    now=int(time.time());end=(now//3600)*3600;start=end-args.days*86400
    report={"at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"window_utc":[start,end],
            "warning":"KuCoin fee status is CURRENT ONLY. Cannot infer historical fee levels or causation. Close-to-close premium is NOT executable spread.",
            "coins":{}}
    for coin in COINS:
        row={"current_fee_snapshot":None,"market":None}
        try:row["current_fee_snapshot"]=fee_snapshot(coin)
        except Exception as e:row["fee_error"]=repr(e)
        if coin=="USDT":
            report["coins"][coin]=row;continue
        try:
            local=kucoin(coin+"-USDT",start,end)
            benchmark=bybit(coin+"USDT",start,end)
            common=sorted(set(local)&set(benchmark))
            observations=[]
            for t in common:
                lc,lv=local[t];bc,_=benchmark[t]
                if bc<=0:continue
                prem=100*(lc/bc-1)
                observations.append({"ts":t,"kucoin_close":lc,"bybit_spot_close":bc,
                                     "kucoin_quote_volume":lv,"premium_pct":round(prem,6)})
            vals=[x["premium_pct"] for x in observations]
            row["market"]={"kucoin_bars":len(local),"bybit_bars":len(benchmark),
                "matched":len(common),"coverage":round(len(common)/(args.days*24),4),
                "prem_pct":stat(vals),
                "hours_prem_gt_0_5_pct":sum(v>.5 for v in vals),
                "hours_prem_gt_1_pct":sum(v>1 for v in vals),
                "hours_discount_lt_neg1_pct":sum(v< -1 for v in vals),
                "extremes":sorted(observations,key=lambda x:-abs(x["premium_pct"]))[:12],
                "interpretation":"PRELIMINARY" if len(common)>=args.days*20 else "INSUFFICIENT"}
            (out/(coin+"_matched.json")).write_text(json.dumps(observations,indent=2),encoding="utf8")
        except Exception as e:row["market_error"]=repr(e)
        report["coins"][coin]=row
    pth=out/"report.json";pth.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf8")
    hashes={f.name:{"sha256":hashlib.sha256(f.read_bytes()).hexdigest(),"bytes":f.stat().st_size}
            for f in out.iterdir() if f.is_file()}
    (out/"manifest.json").write_text(json.dumps(hashes,indent=2),encoding="utf8")
    print(json.dumps({"report":str(pth),"results":report["coins"]},ensure_ascii=False))
if __name__=="__main__":main()
