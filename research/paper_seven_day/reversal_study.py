#!/usr/bin/env python3
"""GET-only Bybit reversal study. No keys, no orders, no capital at risk.
Out-of-sample chronological 70/30. Signal at bar close; next-bar OPEN fill.
Stop/target examined from NEXT bar after entry; adverse-first on simultaneous hit.
"""
import argparse, datetime as dt, json, math, statistics, time, urllib.parse, urllib.request
from pathlib import Path
BASE="https://api.bybit.com"
def fetch(sym, interval, days):
    now=int(time.time()*1000); since=now-days*86400000
    end=now; out={}
    while end>=since:
        args=urllib.parse.urlencode({"category":"linear","symbol":sym,"interval":str(interval),
                                      "end":end,"limit":1000})
        req=urllib.request.Request(BASE+"/v5/market/kline?"+args,
                                    headers={"User-Agent":"read-only-reversal-study/1.0"})
        for retry in range(6):
            try:
                with urllib.request.urlopen(req,timeout=25) as resp: d=json.load(resp)
                if d.get("retCode")==0: break
                if d.get("retCode")!=10006: raise RuntimeError(str(d))
            except (OSError, TimeoutError):
                if retry==5: raise
            time.sleep(min(30,2**retry))
        else: raise RuntimeError("rate limited")
        xs=d["result"]["list"]
        if not xs: break
        for x in xs:
            t=int(x[0])
            if since<=t and t+interval*60000<=now:
                out[t]=(t,*map(float,(x[1],x[2],x[3],x[4],x[5])))
        earliest=min(int(x[0]) for x in xs)
        if earliest<=since or len(xs)<1000: break
        if earliest>=end: raise RuntimeError("pagination did not advance")
        end=earliest-1
        time.sleep(.16)
    a=sorted(out.values())
    gap=sum(a[i][0]-a[i-1][0]!=interval*60000 for i in range(1,len(a)))
    return a,gap

def simulate(a, interval, threshold, hold, target, stop, cost_bps):
    trades=[]; i=4
    while i+1<len(a):
        prev=a[i-3][4]; close=a[i][4]
        move=close/prev-1 if prev else 0
        if move > -threshold: i+=1; continue
        entry_i=i+1
        entry=a[entry_i][1]    # enter at next bar open (not signal close)
        if entry<=0: i+=1; continue
        exit_i=min(entry_i+hold-1,len(a)-1)
        reason="timeout"; px=a[exit_i][4]
        for j in range(entry_i,exit_i+1):
            _,op,hi,lo,cl,_=a[j]
            # Entry bar is included. If target and stop both touch in one
            # candle, choose STOP pessimistically. Gaps filled at open.
            if lo<=entry*(1-stop):
                px=min(op,entry*(1-stop)); exit_i=j; reason="stop"; break
            if hi>=entry*(1+target):
                px=max(entry*(1+target),op) if j>entry_i else entry*(1+target)
                exit_i=j; reason="target"; break
        # Conservative two-sided taker + slippage cost, expressed in bps
        net=(px/entry-1)-2*cost_bps/10000
        trades.append({"signal_ts":a[i][0],"entry_ts":a[entry_i][0],
                       "exit_ts":a[exit_i][0],"entry":entry,"exit":px,
                       "ret_net":net,"reason":reason})
        i=exit_i+1
    return trades

def metrics(ts):
    vals=[t["ret_net"] for t in ts]; n=len(vals)
    if not n:return {"count":0,"avg_net_pct":None,"pf":None,"max_dd_pct":None}
    gains=sum(max(0,x) for x in vals); losses=-sum(min(0,x) for x in vals)
    equity=1.; peak=1.; dd=0.
    for x in vals:
        equity*=max(0.,1+x)
        peak=max(peak,equity)
        dd=max(dd,1-equity/peak)
    return {"count":n,"avg_net_pct":round(100*statistics.mean(vals),4),
            "win_fraction":round(sum(x>0 for x in vals)/n,4),
            "pf":round(gains/losses,4) if losses else None,
            "max_dd_pct":round(dd*100,4),"compounded_return_pct":round((equity-1)*100,4),
            "reason_counts":{k:sum(t["reason"]==k for t in ts) for k in ("stop","target","timeout")}}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--days",type=int,default=90)
    ap.add_argument("--out",type=Path,required=True)
    ap.add_argument("--cost-bps-per-side",type=float,default=10.)
    a=ap.parse_args()
    if not 15<=a.days<=180:ap.error("days 15..180")
    if a.cost_bps_per_side<0:ap.error("cost must be >=0")
    allres={"method":"Chronological split 70/30, no lookahead, next-open fills, stop-first ambiguous intrabar, fixed cost each side",
            "captured_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
            "days":a.days,"cost_bps_per_side":a.cost_bps_per_side,"results":[]}
    a.out.mkdir(parents=True,exist_ok=True)
    for sym in ("BTCUSDT","ETHUSDT","SOLUSDT"):
      for interval in (5,15):
        try:
            bars,gaps=fetch(sym,interval,a.days)
            if len(bars)<1000:raise ValueError("too few bars")
            # never trade across missing candles, check dataset completeness
            maxgaps=max(2, int(len(bars)*.001))
            if gaps>maxgaps:raise ValueError(f"data gaps {gaps}")
            cut=int(.7*len(bars)); train=bars[:cut]; test=bars[cut:]
            # Parameter grid declared in advance; selection only on train
            candidates=[]
            for threshold in (.005,.01,.015,.02):
              for hold in (3,6,12):
                for target,stop in ((.004,.005),(.008,.008),(.012,.01)):
                    tr=simulate(train,interval,threshold,hold,target,stop,a.cost_bps_per_side)
                    m=metrics(tr)
                    if m["count"]>=20:
                        score=m["avg_net_pct"]  # no tuning on test
                        candidates.append((score,m["count"],threshold,hold,target,stop))
            candidates.sort(reverse=True)
            selected=candidates[0] if candidates else None
            record={"symbol":sym,"interval_minutes":interval,"bars":len(bars),"gaps":gaps,
                    "train_bars":len(train),"test_bars":len(test),"candidates_eligible":len(candidates)}
            if selected:
                _,_,threshold,hold,target,stop=selected
                trained=simulate(train,interval,threshold,hold,target,stop,a.cost_bps_per_side)
                tested=simulate(test,interval,threshold,hold,target,stop,a.cost_bps_per_side)
                record.update({"parameters":{"drop_threshold":threshold,"max_hold_bars":hold,
                                             "target":target,"stop":stop},
                               "train":metrics(trained),"test":metrics(tested)})
                path=a.out/f"{sym}_{interval}m_test_trades.json"
                path.write_text(json.dumps(tested,indent=2),encoding="utf-8")
                record["test_trades_file"]=path.name
            else:record["verdict"]="INSUFFICIENT_TRAIN_TRADES"
            allres["results"].append(record)
        except Exception as exc:
            allres["results"].append({"symbol":sym,"interval_minutes":interval,"error":str(exc)})
    (a.out/"report.json").write_text(json.dumps(allres,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(allres,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
