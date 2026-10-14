#!/usr/bin/env python3
"""Bybit public-data regime study for Donchian 1h; offline paper simulation.
No auth, no POST, no orders. Selection on training ONLY, chronological OOS.
Strategy model is explicitly an approximation of the forward paper runner.
"""
import argparse, datetime as dt, hashlib, json, math, statistics, time
import urllib.parse, urllib.request
from pathlib import Path

BASE="https://api.bybit.com/v5/market/kline"
CFG={"BTCUSDT":{"timeout":96},"SOLUSDT":{"timeout":48},"ETHUSDT":{"timeout":72}}
HOUR=3600000
def get(sym,days):
    end=int(time.time()*1000)//HOUR*HOUR
    first=end-days*24*HOUR; cursor=end; bars={}
    while cursor>first:
        q=urllib.parse.urlencode(dict(category="linear",symbol=sym,interval="60",end=cursor,limit=1000))
        for n in range(6):
            try:
                req=urllib.request.Request(BASE+"?"+q,headers={"User-Agent":"regime-study-public-get/1.0"})
                with urllib.request.urlopen(req,timeout=25) as f: data=json.load(f)
                if data.get("retCode")==0:break
                if data.get("retCode")!=10006:raise RuntimeError(str(data)[:250])
            except OSError:
                if n==5:raise
            time.sleep(min(25,2**n))
        else:raise RuntimeError("retries exhausted")
        raw=data["result"].get("list",[])
        if not raw:break
        oldest=min(int(r[0]) for r in raw)
        for r in raw:
            t=int(r[0])
            if first<=t and t+HOUR<=end:
                bars[t]=dict(t=t,o=float(r[1]),h=float(r[2]),l=float(r[3]),c=float(r[4]),v=float(r[5]))
        if oldest<=first or len(raw)<1000:break
        if oldest>=cursor:raise RuntimeError("pagination failed")
        cursor=oldest-1;time.sleep(.15)
    arr=[bars[t] for t in sorted(bars)]
    gaps=sum(arr[i]["t"]-arr[i-1]["t"]!=HOUR for i in range(1,len(arr)))
    return arr,gaps

def sma(xs):return sum(xs)/len(xs)
def atr(rows,i,n=14):
    if i<n:return None
    tr=[]
    for j in range(i-n+1,i+1):
        b=rows[j];prev=rows[j-1]["c"]
        tr.append(max(b["h"]-b["l"],abs(b["h"]-prev),abs(b["l"]-prev)))
    return sma(tr)

def candidate(rows,i,regime):
    # At close of candle i, lookback excludes candle i.
    if i<120:return (0,None)
    b=rows[i];past=rows[i-20:i]
    hi=max(x["h"] for x in past);lo=min(x["l"] for x in past)
    side=1 if b["c"]>hi else -1 if b["c"]<lo else 0
    if not side:return (0,None)
    volratio=b["v"]/max(1e-12,sma([x["v"] for x in rows[i-20:i]]))
    atrval=atr(rows,i)
    atrpct=atrval/b["c"] if b["c"]>0 else 0
    trend=sma([x["c"] for x in rows[i-48:i]])-sma([x["c"] for x in rows[i-120:i]])
    filters={
        "baseline":True,
        "trend48_120": side*trend>0,
        "volume_1_5":volratio>=1.5,
        "atr_pct_0_75":atrpct>=0.0075,
        "trend_and_volume":side*trend>0 and volratio>=1.5,
        "trend_and_atr":side*trend>0 and atrpct>=0.0075
    }
    if not filters[regime]:return (0,None)
    return (side,atrval)

def run(rows,regime,timeout,cost_bps=6.5,slip_bps=3,begin=120,end=None):
    """Conservative OHLC model.
    Signal at close i, fill at next candle open. Active stop applied from entry
    candle onward, gap through stop at worse open; opposite signal close exits at
    NEXT bar open. Timeout exits at NEXT open. A trade crossing split boundary
    is excluded by separately running train/test slices with 120-bar warmup.
    """
    if end is None:end=len(rows)
    trades=[];i=max(begin,120)
    while i+1<end:
        side, a=candidate(rows,i,regime)
        if not side:i+=1;continue
        entry_idx=i+1;entry=rows[entry_idx]["o"]*(1+side*slip_bps/10000)
        if entry<=0 or a is None or a<=0:i+=1;continue
        stop=entry-side*1.5*a
        exit_idx=None;px=None;reason=None
        last=min(end-1,entry_idx+timeout-1)
        for j in range(entry_idx,last+1):
            b=rows[j]
            stopped=b["l"]<=stop if side>0 else b["h"]>=stop
            if stopped:
                px=min(stop,b["o"]) if side>0 else max(stop,b["o"])
                reason="stop";exit_idx=j;break
            # Exit on opposite signal, but execution at next available open.
            if j+1<end:
                hi=max(x["h"] for x in rows[j-20:j])
                lo=min(x["l"] for x in rows[j-20:j])
                opposite=b["c"]<lo if side>0 else b["c"]>hi
                if opposite:
                    exit_idx=j+1;px=rows[exit_idx]["o"];reason="opposite";break
        if exit_idx is None:
            if last+1>=end:break # censor unfinished trades, not hypothetical win/loss
            exit_idx=last+1;px=rows[exit_idx]["o"];reason="timeout"
        px*=1-side*slip_bps/10000
        # Floating PnL on unit notional, after 2 sided model costs.
        gross=side*(px/entry-1)
        net=gross-2*cost_bps/10000
        trades.append(dict(entry_time=rows[entry_idx]["t"],exit_time=rows[exit_idx]["t"],
                           side=side,entry=entry,exit=px,net=net,reason=reason))
        i=exit_idx+1
    return trades

def stats(trades):
    r=[x["net"] for x in trades];n=len(r)
    if n==0:return {"n":0}
    pos=sum(max(v,0) for v in r);neg=-sum(min(v,0) for v in r)
    eq=peak=1.;dd=0.
    for v in r:
        eq*=max(0,1+v);peak=max(eq,peak);dd=max(dd,1-eq/peak)
    return dict(n=n,mean_pct=round(100*statistics.mean(r),5),
                win_rate=round(sum(v>0 for v in r)/n,4),
                pf=round(pos/neg,4) if neg else None,
                compounded_pct=round((eq-1)*100,4),max_dd_pct=round(dd*100,4),
                exits={reason:sum(t["reason"]==reason for t in trades) for reason in ("stop","timeout","opposite")})
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--days",type=int,default=180)
    p.add_argument("--cost-bps-per-side",type=float,default=6.5)
    p.add_argument("--slippage-bps-per-side",type=float,default=3)
    p.add_argument("--out",type=Path,required=True)
    z=p.parse_args()
    if not 90<=z.days<=365 or min(z.cost_bps_per_side,z.slippage_bps_per_side)<0:
        p.error("invalid days or cost")
    z.out.mkdir(parents=True,exist_ok=True)
    opts=("baseline","trend48_120","volume_1_5","atr_pct_0_75","trend_and_volume","trend_and_atr")
    result=dict(timestamp_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
      warning="OHLC model is NOT the live paper runner: no real order book, fees/funding timing, portfolio interactions or live-risk gate; independent candidate-only study.",
      days=z.days,cost_bps=z.cost_bps_per_side,slippage_bps=z.slippage_bps_per_side,results=[])
    for sym,meta in CFG.items():
      try:
        rows,gaps=get(sym,z.days)
        rec=dict(symbol=sym,bars=len(rows),gaps=gaps)
        if gaps or len(rows)<1200:raise ValueError("Missing bars / insufficient coverage; fail closed")
        cut=int(.7*len(rows));train=rows[:cut]
        # Important: validation warmup from pre-cut past is allowed as known prices,
        # but trading is allowed ONLY from cut onward.
        holdout=rows[cut-120:]
        train_data={}
        for name in opts:
            tr=run(train,name,meta["timeout"],z.cost_bps_per_side,z.slippage_bps_per_side)
            train_data[name]=stats(tr)
        eligible=[(m["mean_pct"],m["n"],name) for name,m in train_data.items()
                  if m["n"]>=20 and name!="baseline"]
        eligible.sort(reverse=True)
        selected=eligible[0][2] if eligible else None
        rec["train"]=train_data
        rec["train_selected"]=selected
        rec["oos"]={}
        for name in ("baseline",selected) if selected else ("baseline",):
            ts=run(holdout,name,meta["timeout"],z.cost_bps_per_side,z.slippage_bps_per_side)
            rec["oos"][name]=stats(ts)
            fn=z.out/(sym+"_"+name+"_oos.json")
            fn.write_text(json.dumps(ts,indent=2),encoding="utf8")
        rec["train_from"]=rows[0]["t"];rec["train_to"]=train[-1]["t"]
        rec["oos_from"]=rows[cut]["t"];rec["oos_to"]=rows[-1]["t"]
        result["results"].append(rec)
      except Exception as e:result["results"].append(dict(symbol=sym,error=repr(e)))
    (z.out/"report.json").write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf8")
    hashes={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in z.out.iterdir() if f.is_file()}
    (z.out/"manifest.json").write_text(json.dumps(hashes,indent=2),encoding="utf8")
    print(json.dumps(result,ensure_ascii=False))
if __name__=="__main__":main()
