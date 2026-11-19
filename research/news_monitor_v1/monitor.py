#!/usr/bin/env python3
"""Local, manual-refresh, read-only NEWS Intelligence monitor. Python stdlib only."""
import argparse
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

TIER2 = Path.home() / ".local/share/news-phase4-v25/tier2.sqlite"
NEWS = Path.home() / ".local/share/news-phase4-v24/v24.sqlite"
HTML = """<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NEWS Intelligence Monitor</title>
<style>
body{font:15px system-ui,sans-serif;background:#f6f7f9;color:#20242a;max-width:1050px;margin:30px auto;padding:0 16px}
h1{font-size:24px}button{background:#234a7b;color:white;border:0;border-radius:6px;padding:10px 18px;cursor:pointer}
header,.summary,.line{display:flex;gap:16px;flex-wrap:wrap;align-items:center}
header{justify-content:space-between}.card{background:white;border:1px solid #dbe0e6;border-radius:10px;padding:18px;margin:14px 0}
small,.muted{color:#626d79}table{width:100%;border-collapse:collapse}td,th{text-align:left;border-bottom:1px solid #e6e9ed;padding:8px}
strong{overflow-wrap:anywhere}.error{color:#b52626}.ok{color:#1b793a}.warn{color:#936300}
</style>
<header><h1>NEWS Intelligence — Monitor</h1><button id="reload">Обновить</button></header>
<div class="muted">Только локальный просмотр. Автообновления нет.</div>
<div id="status" class="card">Нажмите «Обновить»</div><div id="events"></div>
<script>
const el=id=>document.getElementById(id);
const node=(tag,text,cls)=>{const n=document.createElement(tag);n.textContent=String(text??"—");if(cls)n.className=cls;return n};
const date=ms=>ms?new Date(ms).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}):"—";
async function update(){
 el('reload').disabled=true;
 try{
  const r=await fetch('/api/snapshot',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);
  const d=await r.json();const s=el('status');s.replaceChildren();
  s.append(node('div','Проверено: '+new Date(d.checked_at).toLocaleString('ru-RU')+' · Время событий: МСК'));
  const c=d.counts||{};s.append(node('p','Тройки: '+(c.triples??0)+' · Метки: '+(c.labelled??0)+' · Вызовы: '+(c.model_calls??0)));
  for(const [key,v] of Object.entries(d.components||{}))s.append(node('div',key+': '+(v.ActiveState??v.state??'неизвестно')+' / '+(v.UnitFileState??'—')));
  for(const err of d.errors||[])s.append(node('p',err,'error'));
  const box=el('events');box.replaceChildren();
  if(!d.events.length)box.append(node('div','Принятых событий пока нет.','card'));
  for(const e of d.events){
   const card=node('article','','card');
   card.append(node('small',date(e.published_ms)+' · '+(e.source??e.category)));
   card.append(node('h3',e.title??('Событие '+e.id)));
   const t=document.createElement('table'),h=document.createElement('tr');
   for(const txt of ['Вариант','Решение','Рост','Прогноз, bps'])h.append(node('th',txt));t.append(h);
   for(const a of e.arms||[]){
    const tr=document.createElement('tr');
    for(const txt of [a.arm,a.action,a.p_up==null?'—':(a.p_up*100).toFixed(0)+'%',a.expected_return_bps])tr.append(node('td',txt));
    t.append(tr);
   }
   card.append(t);
   card.append(node('p','Метка: '+e.label_state+' · BTC: '+(e.market_bps??'ожидание')+' bps'));
   box.append(card);
  }
 }catch(e){el('status').replaceChildren(node('div','Ошибка монитора: '+e.message,'error'))}
 finally{el('reload').disabled=false}
}
el('reload').addEventListener('click',update);
</script></html>"""

def db(path):
    if not path.is_file():
        return None
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn

def has_table(c, table):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())

def state(unit):
    try:
        p = subprocess.run(["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "UnitFileState", "--no-pager"], capture_output=True, text=True, timeout=2)
        return dict(x.split("=",1) for x in p.stdout.splitlines() if "=" in x) if p.returncode == 0 else {"state":"unknown"}
    except (OSError,subprocess.TimeoutExpired):
        return {"state":"unavailable"}

def snapshot(tier2, news, series=None):
    out = {"checked_at": datetime.now(timezone.utc).isoformat(), "events": [],
           "counts": {"triples":0,"labelled":0,"model_calls":0}, "errors": [],
           "components": {k:state(u) for k,u in [
               ("collector","news-phase4-collector.timer"),
               ("trial","news-phase4-tier2-trial.timer"),
               ("label","news-phase4-tier2-label.timer")]}}
    titles = {}
    try:
        c = db(news)
        if c is None:
            raise FileNotFoundError(str(news))
        try:
            if has_table(c, "v24_events"):
                for r in c.execute("SELECT published_ms,category,title,source_id FROM v24_events ORDER BY published_ms DESC LIMIT 5000"):
                    titles.setdefault((r["published_ms"],r["category"]),[]).append(dict(r))
        finally:
            c.close()
    except (OSError,sqlite3.Error) as e:
        out["errors"].append("News DB: " + str(e))
    sources = [("first",tier2)]
    if series is not None:
        sources.append(("series100",series))
    for tag,path in sources:
        try:
            c=db(path)
            if c is None:
                if tag == "series100":
                    continue
                raise FileNotFoundError(str(path))
            try:
                if not has_table(c,"tier2_triples"):
                    continue
                triples=c.execute(
                    "SELECT event_id,cohort,published_ms,created_ms,entry_price FROM tier2_triples ORDER BY created_ms DESC LIMIT 300"
                ).fetchall()
                arms={}
                if has_table(c,"tier2_arms"):
                    for row in c.execute(
                        "SELECT event_id,arm,action,p_up,expected_return_bps FROM tier2_arms"
                    ):
                        arms.setdefault(row["event_id"],[]).append(dict(row))
                outcomes={}
                if has_table(c,"tier2_outcomes"):
                    outcomes={row["event_id"]:dict(row) for row in c.execute(
                        "SELECT event_id,price,label_state FROM tier2_outcomes"
                    )}
                out["counts"]["triples"] += c.execute("SELECT COUNT(*) FROM tier2_triples").fetchone()[0]
                out["counts"]["labelled"] += sum(
                    1 for row in outcomes.values() if row["label_state"]=="labelled")
                if has_table(c,"tier2_calls"):
                    out["counts"]["model_calls"] += c.execute("SELECT COUNT(*) FROM tier2_calls").fetchone()[0]
                for t in triples:
                    category=t["cohort"].split("|",1)[0]
                    found=titles.get((t["published_ms"],category),[])
                    src=found[0] if len(found)==1 else {}
                    outcome=outcomes.get(t["event_id"],{})
                    bps=round(10000*(outcome["price"]/t["entry_price"]-1),2) if outcome.get("label_state")=="labelled" and t["entry_price"] else None
                    out["events"].append({
                        "id":t["event_id"],"trial":tag,"published_ms":t["published_ms"],
                        "created_ms":t["created_ms"],"category":category,
                        "title":src.get("title"),"source":src.get("source_id"),
                        "arms":sorted(arms.get(t["event_id"],[]),
                          key=lambda x:{"news":0,"news_market":1,"market":2}.get(x["arm"],9)),
                        "label_state":outcome.get("label_state","pending"),"market_bps":bps})
            finally:
                c.close()
        except (OSError,sqlite3.Error) as e:
            out["errors"].append(tag+" DB: "+str(e))
    out["events"].sort(key=lambda x:x.get("created_ms") or 0,reverse=True)
    return out

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        target=urlsplit(self.path).path
        if target=="/": payload=HTML.encode();mime="text/html; charset=utf-8"
        elif target=="/health": payload=b"ok\n";mime="text/plain; charset=utf-8"
        elif target=="/api/snapshot":
            payload=json.dumps(snapshot(self.server.tier2_db,self.server.news_db,self.server.series_db),ensure_ascii=False).encode()
            mime="application/json; charset=utf-8"
        else: self.send_error(404);return
        self.send_response(200)
        for k,v in {"Content-Type":mime,"Content-Length":str(len(payload)),"Cache-Control":"no-store",
            "X-Content-Type-Options":"nosniff","Content-Security-Policy":"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'"}.items():self.send_header(k,v)
        self.end_headers();self.wfile.write(payload)
    def log_message(self,*args):pass

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--port",type=int,default=8788)
    p.add_argument("--tier2-db",type=Path,default=TIER2)
    p.add_argument("--news-db",type=Path,default=NEWS)
    p.add_argument("--series-db",type=Path,
                   default=Path.home()/".local/share/news-phase4-v25-series100/tier2_series.sqlite")
    a=p.parse_args()
    server=ThreadingHTTPServer(("127.0.0.1",a.port),Handler)
    server.tier2_db=a.tier2_db;server.news_db=a.news_db;server.series_db=a.series_db
    print("NEWS Monitor on http://127.0.0.1:"+str(a.port),flush=True)
    server.serve_forever()
if __name__=="__main__":main()
