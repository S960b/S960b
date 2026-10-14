#!/usr/bin/env python3
"""365d SOLUSDT 1h frozen-rule walk-forward: baseline vs trend+ATR filter.
Public Bybit GET only. No orders. All comparisons on identical calendar folds.
"""
import argparse,datetime as dt,hashlib,json,statistics,time,urllib.parse,urllib.request
from pathlib import Path
HOUR=3600000; URL="https://api.bybit.com/v5/market/kline"
def fetch(days):
    end=int(time.time()*1000)//HOUR*HOUR;start=end-days*24*HOUR;cur=end;out={}
    while cur>start:
        q=urllib.parse.urlencode({"category":"linear","symbol":"SOLUSDT","interval":"60","end":cur,"limit":1000})
        for j in range(6):
            try:
                req=urllib.request.Request(URL+"?"+q,headers={"User-Agent":"readonly-walkforward/1.0"})
                with urllib.request.urlopen(req,timeout=30) as f: d=json.load(f)
                if d.get("retCode")==0: break
                if d.get("retCode")!=10006:raise RuntimeError(str(d)[:300])
            except OSError:
                if j==5:raise
            time.sleep(min(30,2**j))
        else:raise RuntimeError("rate limit")
        xs=d["result"].get("list",[])
        if not xs:break
        old=min(int(x[0]) for x in xs)
        for x in xs:
            t=int(x[0])
            if start<=t and t+HOUR<=end:
                out[t]=dict(t=t,o=float(x[1]),h=float(x[2]),l=float(x[3]),c=float(x[4]),v=float(x[5]))
        if old<=start or len(xs)<1000:break
        if old>=cur:raise RuntimeError("pagination not advancing")
        cur=old-1;time.sleep(.2)
    rows=[out[k] for k in sorted(out)]
    gaps=sum(rows[i]["t"]-rows[i-1]["t"]!=HOUR for i in range(1,len(rows)))
    return rows,gaps
def mean(v):return sum(v)/len(v)
def signal(rows,i,filtered):
    if i<120:return 0,None
    b=rows[i];p=rows[i-20:i];hi=max(x["h"] for x in p);lo=min(x["l"] for x in p)
    side=1 if b["c"]>hi else -1 if b["c"]<lo else 0
    if side==0:return 0,None
    tr=[max(rows[j]["h"]-rows[j]["l"],abs(rows[j]["h"]-rows[j-1]["c"]),abs(rows[j]["l"]-rows[j-1]["c"])) for j in range(i-13,i+1)]
    atr=mean(tr)
    if filtered:
        trend=mean([x["c"] for x in rows[i-48:i]])-mean([x["c"] for x in rows[i-120:i]])
        if side*trend<=0 or atr/max(b["c"],1e-10)<.0075:return 0,None
    return side,atr
def sim(rows,begin,end,filtered,fee,slip):
    trades=[];i=max(begin,120)
    while i+1<end:
        side,atr=signal(rows,i,filtered)
        if not side:i+=1;continue
        e=i+1;ep=rows[e]["o"]*(1+side*slip/10000)
        if ep<=0 or atr<=0:i+=1;continue
        stop=ep-side*1.5*atr;exit_idx=None;price=None;reason=None
        last=min(e+48-1,end-1)
        for j in range(e,last+1):
            bar=rows[j]
            if (bar["l"]<=stop if side>0 else bar["h"]>=stop):
                exit_idx=j;price=min(bar["o"],stop) if side>0 else max(bar["o"],stop);reason="stop";break
            if j+1<end:
                history=rows[j-20:j]
                opposite=(bar["c"]<min(x["l"] for x in history)) if side>0 else (bar["c"]>max(x["h"] for x in history))
                if opposite:
                    exit_idx=j+1;price=rows[j+1]["o"];reason="opposite";break
        if exit_idx is None:
            if last+1>=end:break
            exit_idx=last+1;price=rows[exit_idx]["o"];reason="timeout"
        price*=1-side*slip/10000
        ret=side*(price/ep-1)-2*fee/10000
        trades.append({"entry_ts":rows[e]["t"],"exit_ts":rows[exit_idx]["t"],"side":side,
                       "entry":ep,"exit":price,"net":ret,"reason":reason})
        i=exit_idx+1
    return trades
def metrics(tr):
    r=[x["net"] for x in tr]
    if not r:return {"n":0}
    gain=sum(max(0,x) for x in r);loss=-sum(min(0,x) for x in r)
    eq=peak=1.;dd=0
    for x in r:
        eq*=max(0,1+x);peak=max(peak,eq);dd=max(dd,1-eq/peak)
    ordered=sorted(r,reverse=True)
    stripped=r.copy()
    for v in ordered[:min(3,len(r))]:stripped.remove(v)
    return {"n":len(r),"ev_pct":round(100*mean(r),5),"pf":round(gain/loss,4) if loss else None,
            "winrate":round(sum(x>0 for x in r)/len(r),4),
            "compounded_pct":round(100*(eq-1),4),"maxdd_pct":round(100*dd,4),
            "best3_net_pct":round(100*sum(ordered[:3]),4),
            "ev_ex_best3_pct":round(100*mean(stripped),5) if stripped else None,
            "exits":{k:sum(t["reason"]==k for t in tr) for k in ("stop","timeout","opposite")}}
def main():
    a=argparse.ArgumentParser();a.add_argument("--days",type=int,default=365)
    a.add_argument("--out",required=True,type=Path);p=a.parse_args()
    if p.days!=365:a.error("frozen protocol requires exactly 365 days")
    p.out.mkdir(parents=True,exist_ok=True)
    rows,gaps=fetch(p.days)
    if gaps or len(rows)<365*24-4:
        raise RuntimeError(f"DATA_GAP bars={len(rows)} gaps={gaps}")
    # Initial 120d observation window. Five consecutive ~49d OOS calendar folds.
    boundaries=[120*24+i*49*24 for i in range(6)]
    if boundaries[-1]>len(rows):raise RuntimeError("insufficient bars for 5 folds")
    report={"captured_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
            "asset":"SOLUSDT","bars":len(rows),"gaps":gaps,
            "protocol":"Frozen trend_and_atr from prior 180d study; NO fitting or optimization on any fold. Five sequential disjoint OOS windows following 120d warmup; trading only inside each window. OHLC not true execution.",
            "folds":[],"aggregate":{}}
    for fold in range(5):
        start,end=boundaries[fold],boundaries[fold+1]
        obj={"fold":fold+1,"start_utc":dt.datetime.fromtimestamp(rows[start]["t"]/1000,dt.timezone.utc).isoformat(),
             "end_utc":dt.datetime.fromtimestamp(rows[end-1]["t"]/1000,dt.timezone.utc).isoformat(),"cost_scenarios":{}}
        for name,fee,slip in (("base",6.5,3.),("stress",10.,8.),("severe",15.,12.)):
            values={}
            for label,filter_on in (("baseline",False),("trend_and_atr",True)):
                tr=sim(rows,start,end,filter_on,fee,slip)
                values[label]=metrics(tr)
                path=p.out/f"fold{fold+1}_{name}_{label}.json"
                path.write_text(json.dumps(tr,indent=2),encoding="utf8")
            obj["cost_scenarios"][name]=values
        report["folds"].append(obj)
    for label,filtered in (("baseline",False),("trend_and_atr",True)):
        # Aggregate fold-respecting trades (do not allow positions to cross folds).
        for name,fee,slip in (("base",6.5,3.),("stress",10.,8.),("severe",15.,12.)):
            alltr=[]
            for f in range(5):
                alltr+=sim(rows,boundaries[f],boundaries[f+1],filtered,fee,slip)
            report["aggregate"][f"{label}_{name}"]=metrics(alltr)
    (p.out/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf8")
    manifest={x.name:hashlib.sha256(x.read_bytes()).hexdigest() for x in p.out.iterdir() if x.is_file()}
    (p.out/"manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf8")
    print(json.dumps({"status":"ok","report":str(p.out/"report.json"),
                      "aggregate":report["aggregate"],"folds":report["folds"]},ensure_ascii=False))
if __name__=="__main__":main()
