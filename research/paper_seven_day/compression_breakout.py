#!/usr/bin/env python3
"""Public Bybit volatility compression breakout research; GET only, no orders.

Non-overlapping trades, next-bar open entries, adverse-first same-bar fills,
chronological 70/30 holdout. Parameter selection ONLY on train.
"""
import argparse, datetime as dt, json, statistics, time, urllib.parse, urllib.request
from pathlib import Path
BASE="https://api.bybit.com/v5/market/kline"
def load(sym,minutes,days):
    now=int(time.time()*1000); start=now-days*86400000; end=now; bars={}
    while end>=start:
        q=urllib.parse.urlencode(dict(category="linear",symbol=sym,interval=minutes,end=end,limit=1000))
        for attempt in range(6):
            try:
                with urllib.request.urlopen(urllib.request.Request(BASE+"?"+q,headers={"User-Agent":"research-get-only"}),timeout=25) as r: x=json.load(r)
                if x.get("retCode")==0:break
                if x.get("retCode")!=10006:raise RuntimeError(str(x))
            except OSError:
                if attempt==5:raise
            time.sleep(min(30,2**attempt))
        else:raise RuntimeError("retry exhausted")
        xs=x["result"]["list"]
        if not xs:break
        for z in xs:
            t=int(z[0])
            if start<=t and t+minutes*60000<=now:
                bars[t]=[t]+[float(z[k]) for k in (1,2,3,4,5)]
        earliest=min(int(z[0]) for z in xs)
        if earliest<=start or len(xs)<1000:break
        end=earliest-1
        time.sleep(.18)
    arr=sorted(bars.values())
    gaps=sum(arr[i][0]-arr[i-1][0]!=minutes*60000 for i in range(1,len(arr)))
    return arr,gaps

def test(bars, lookback, squeeze, hold, stop, reward, cost):
    """Squeeze: mean true-range ratio of last lookback bars vs preceding lookback.
    Breakout: close > previous lookback HIGH for long, < LOW for short.
    All features computed at signal close i, entry next bar open i+1.
    """
    trades=[]; i=lookback*2
    while i+1<len(bars):
        past=bars[i-lookback:i]; older=bars[i-2*lookback:i-lookback]
        def mean_range(v):return sum((x[2]-x[3])/max(x[4],1e-12) for x in v)/len(v)
        old_r=mean_range(older); new_r=mean_range(past)
        if old_r<=0 or new_r/old_r>squeeze:
            i+=1;continue
        upper=max(x[2] for x in past);lower=min(x[3] for x in past)
        close=bars[i][4]
        side=1 if close>upper else (-1 if close<lower else 0)
        if not side:
            i+=1;continue
        entry_idx=i+1;entry=bars[entry_idx][1]
        if entry<=0:i+=1;continue
        last=min(len(bars)-1,entry_idx+hold-1);reason="timeout";exit_px=bars[last][4];ei=last
        stop_px=entry*(1-side*stop);target_px=entry*(1+side*stop*reward)
        for j in range(entry_idx,last+1):
            _,o,h,l,c,_=bars[j]
            stop_hit=(l<=stop_px if side==1 else h>=stop_px)
            take_hit=(h>=target_px if side==1 else l<=target_px)
            if stop_hit:
                exit_px=min(o,stop_px) if side==1 else max(o,stop_px)
                reason="stop";ei=j;break
            if take_hit:
                # don't claim price improvement on profitable gap
                exit_px=target_px;reason="target";ei=j;break
        pnl=side*(exit_px/entry-1)-2*cost/10000
        trades.append(dict(signal=bars[i][0],entry_ms=bars[entry_idx][0],
                           exit_ms=bars[ei][0],side=side,entry=entry,exit=exit_px,
                           pnl_net=pnl,reason=reason))
        i=ei+1
    return trades

def stats(ts):
    v=[t["pnl_net"] for t in ts]
    if not v:return dict(n=0)
    pos=sum(max(0,x) for x in v);neg=-sum(min(0,x) for x in v)
    eq=peak=1.;dd=0.
    for x in v:
        eq*=max(0,1+x);peak=max(peak,eq);dd=max(dd,1-eq/peak)
    return dict(n=len(v),avg_net_pct=round(statistics.mean(v)*100,4),
                pf=round(pos/neg,4) if neg else None,
                win_rate=round(sum(x>0 for x in v)/len(v),4),
                compounded_pct=round((eq-1)*100,4),max_dd_pct=round(dd*100,4),
                exits={r:sum(t["reason"]==r for t in ts) for r in ("stop","target","timeout")})
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--days",type=int,default=90)
    p.add_argument("--cost-bps-per-side",type=float,default=10)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    if not 20<=a.days<=180 or a.cost_bps_per_side<0:p.error("invalid days/cost")
    a.out.mkdir(parents=True,exist_ok=True)
    res=dict(captured_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
             study="compression-range breakout",days=a.days,
             cost_bps_per_side=a.cost_bps_per_side,
             assumptions="GET-only OHLC; 70/30 chronological; next-open; adverse-first; 2-sided taker+slippage proxy",
             results=[])
    for sym in ("BTCUSDT","ETHUSDT","SOLUSDT"):
      for interval in (5,15):
        rec=dict(symbol=sym,interval=interval)
        try:
            b,g=load(sym,interval,a.days)
            rec.update(bars=len(b),gaps=g)
            if len(b)<1000 or g>max(2,int(len(b)*.001)):
                raise ValueError("insufficient bars/excess gaps")
            cut=int(.7*len(b));train=b[:cut];valid=b[cut:]
            rec.update(train_from=train[0][0],train_to=train[-1][0],
                       test_from=valid[0][0],test_to=valid[-1][0])
            choices=[]
            for n in (12,24,48):
             for squeeze in (.6,.8,1.):
              for hold in (6,12,24):
               for stop,reward in ((.006,1.5),(.01,2.0)):
                tr=test(train,n,squeeze,hold,stop,reward,a.cost_bps_per_side)
                m=stats(tr)
                if m["n"]>=20:
                    choices.append((m["avg_net_pct"],m["n"],n,squeeze,hold,stop,reward))
            rec["eligible_train_candidates"]=len(choices)
            if choices:
                choices.sort(reverse=True)
                _,_,n,squeeze,hold,stop,reward=choices[0]
                tv=test(train,n,squeeze,hold,stop,reward,a.cost_bps_per_side)
                vv=test(valid,n,squeeze,hold,stop,reward,a.cost_bps_per_side)
                rec.update(params=dict(lookback=n,squeeze=squeeze,hold=hold,stop=stop,
                                       reward=reward),train=stats(tv),oos=stats(vv))
                path=a.out/(sym+"_"+str(interval)+"m_oos_trades.json")
                path.write_text(json.dumps(vv,indent=2),encoding="utf-8")
                rec["oos_file"]=path.name
            else:rec["verdict"]="INSUFFICIENT_TRAIN_SIGNALS"
        except Exception as e:rec["error"]=str(e)
        res["results"].append(rec)
    (a.out/"report.json").write_text(json.dumps(res,indent=2),encoding="utf-8")
    print(json.dumps(res,indent=2))
if __name__=="__main__":main()
