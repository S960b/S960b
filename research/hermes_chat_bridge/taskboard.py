#!/usr/bin/env python3
"""Hermes<->ChatGPT bridge taskboard - minimal visual task list.

Serves:
  /             HTML page (live-updating table, filters, tunnel URL)
  /api/jobs     JSON list of jobs (query: ?status=&direction=&queue=&limit=)
  /api/stats    JSON counts by direction/status/queue

Reads the pilot.db directly (read-only connection). Port 8787.
Job ids look like  test_3acba1b6.job  (queue_8hex.job) - human readable.
"""
import json
import sqlite3
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DB = Path('/home/kali/.local/state/hermes-bridge/pilot-20261004T140717Z/pilot.db')
TUNNEL_URL_FILE = Path('/home/kali/.local/state/hermes-bridge/current_tunnel_url.txt')
PORT = 8787

PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bridge taskboard</title>
<style>
:root { color-scheme: dark; }
body { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
       background:#0f1115; color:#d7dae0; margin:0; padding:24px; }
h1 { font-size:20px; margin:0 0 4px; }
.sub { color:#8b93a3; font-size:13px; margin-bottom:18px; }
.tunnel { color:#7ee787; font-size:13px; margin-bottom:14px; }
.filters { margin-bottom:14px; font-size:13px; }
.filters button { background:#1c212b; color:#d7dae0; border:1px solid #2d3543;
  border-radius:6px; padding:4px 10px; margin-right:6px; cursor:pointer; }
.filters button.on { background:#3b82f6; border-color:#3b82f6; color:#fff; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th,td { text-align:left; padding:6px 10px; border-bottom:1px solid #232936;
  vertical-align:top; white-space:nowrap; }
th { color:#8b93a3; font-weight:600; font-size:12px; position:sticky; top:0;
  background:#0f1115; }
td.txt { white-space:normal; max-width:420px; color:#aab2c0; }
td.jid { color:#79c0ff; }
.badge { display:inline-block; padding:1px 8px; border-radius:10px; font-size:11px; }
.st-pending { background:#6b4f00; color:#ffd66b; }
.st-replied { background:#0e4f2b; color:#7ee787; }
.st-completed { background:#134e8a; color:#8fd3ff; }
.st-failed { background:#5a1d1d; color:#ff9c9c; }
.st-claimed { background:#3b1d5e; color:#d0a8ff; }
.dir-to_chatgpt { color:#79c0ff; } .dir-to_hermes { color:#ffa657; }
.q { color:#8b93a3; font-size:11px; border:1px solid #2d3543; border-radius:8px;
  padding:1px 6px; }
.meta { color:#5c6470; font-size:11px; }
.mono { font-size:12px; }
#err { color:#ff9c9c; font-size:13px; }
</style></head><body>
<h1>Bridge taskboard</h1>
<div class="sub">Hermes &harr; ChatGPT &middot; <span id="updated">-</span> &middot; автообновление 5с</div>
<div class="tunnel" id="tunnel">-</div>
<div class="filters" id="filters"></div>
<div id="err"></div>
<table><thead><tr>
<th>job_id</th><th>напр.</th><th>queue</th><th>статус</th><th>создан (МСК)</th>
<th>кому/кем</th><th>текст</th><th>ответ</th></tr></thead>
<tbody id="rows"></tbody></table>
<script>
const FMT = new Intl.DateTimeFormat('ru-RU',{day:'2-digit',month:'2-digit',
  hour:'2-digit',minute:'2-digit'});
let f = { status:'', direction:'', queue:'' };
function msk(iso){ if(!iso) return '-'; return FMT.format(new Date(iso)); }
function el(t,c){ return '<span class="badge '+c+'">'+t+'</span>'; }
async function load(){
  const q = new URLSearchParams();
  if(f.status) q.set('status',f.status);
  if(f.direction) q.set('direction',f.direction);
  if(f.queue) q.set('queue',f.queue);
  q.set('limit','80');
  try{
    const r = await fetch('/api/jobs?'+q);
    const data = await r.json();
    if(data.error){ document.getElementById('err').textContent = data.error; return; }
    document.getElementById('err').textContent='';
    const tb = document.getElementById('rows');
    tb.innerHTML = data.jobs.map(j=>{
      const st = j.claimed_by ? 'claimed' : j.status;
      const stl = j.claimed_by ? 'st-claimed' : 'st-'+j.status;
      const who = j.claimed_by ? j.claimed_by : (j.completed_by ? j.completed_by : '-');
      const rep = (j.reply_len!=null)? j.reply_len+'б' : (j.reply ? 'да' : '-');
      const txt = (j.text||'').slice(0,160);
      return '<tr><td class="jid mono">'+j.job_id+'</td>'+
        '<td class="dir-'+j.direction+'">'+j.direction.replace('to_','')+'</td>'+
        '<td><span class="q">'+j.queue+'</span></td>'+
        '<td>'+el(st,stl)+'</td>'+
        '<td class="meta">'+msk(j.created_at_utc)+'</td>'+
        '<td class="meta">'+who+'</td>'+
        '<td class="txt">'+txt.replace(/</g,'&lt;')+'</td>'+
        '<td class="meta">'+rep+'</td></tr>';
    }).join('') || '<tr><td colspan="8" class="meta">пусто</td></tr>';
    document.getElementById('updated').textContent = 'обновлено '+new Date().toLocaleTimeString('ru-RU');
    document.getElementById('tunnel').textContent = 'Туннель: ' + data.tunnel_url;
  }catch(e){ document.getElementById('err').textContent = 'fetch error: '+e; }
}
function buildFilters(){
  const d = document.getElementById('filters');
  const defs = [
    ['status','',['pending','replied','completed','failed']],
    ['direction','',['to_hermes','to_chatgpt']],
    ['queue','',['test','ctf_A','ctf_B']],
  ];
  d.innerHTML = defs.map(([k,_,vals])=> vals.map(v=>{
    const on = f[k]===v?' on':'';
    return '<button class="'+k+'-'+v+on+'" data-k="'+k+'" data-v="'+v+'">'+v+'</button>';
  }).join('')).join(' &nbsp;|&nbsp; ') +
    ' <button data-k="clear">сброс</button>';
  d.querySelectorAll('button').forEach(b=>b.onclick=()=>{
    if(b.dataset.k==='clear'){ f={status:'',direction:'',queue:''}; }
    else { f[b.dataset.k] = (f[b.dataset.k]===b.dataset.v)? '' : b.dataset.v; }
    buildFilters(); load();
  });
}
buildFilters(); load(); setInterval(load,5000);
</script></body></html>"""


def q(url: str):
    p = urllib.parse.urlparse(url)
    return {k: v[0] for k, v in urllib.parse.parse_qs(p.query).items()}


def fetch_jobs(params):
    conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    where, args = [], []
    if params.get('status'):
        where.append('status=?'); args.append(params['status'])
    if params.get('direction'):
        where.append('direction=?'); args.append(params['direction'])
    if params.get('queue'):
        where.append('queue=?'); args.append(params['queue'])
    w = ('WHERE ' + ' AND '.join(where)) if where else ''
    limit = min(int(params.get('limit', 80)), 300)
    rows = conn.execute(
        'SELECT job_id, direction, queue, status, created_at_utc,'
        ' substr(text,1,200) AS text,'
        ' CASE WHEN reply IS NOT NULL THEN length(reply) ELSE NULL END AS reply_len,'
        ' claimed_by, completed_by, completed_at_utc'
        f' FROM jobs {w} ORDER BY created_at_utc DESC LIMIT ?', (*args, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def fetch_stats():
    conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=10)
    rows = conn.execute(
        'SELECT direction, status, count(*) c FROM jobs'
        ' GROUP BY direction, status ORDER BY direction, status').fetchall()
    conn.close()
    return [{'direction': r[0], 'status': r[1], 'count': r[2]} for r in rows]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self._send(200, PAGE.encode(), 'text/html; charset=utf-8')
        elif self.path.startswith('/api/jobs'):
            try:
                self._send(200, json.dumps({
                    'jobs': fetch_jobs(q(self.path)),
                    'tunnel_url': TUNNEL_URL_FILE.read_text().strip()
                    if TUNNEL_URL_FILE.exists() else '(нет)',
                }).encode())
            except Exception as e:
                self._send(500, json.dumps({'error': repr(e)}).encode())
        elif self.path.startswith('/api/stats'):
            try:
                self._send(200, json.dumps({'stats': fetch_stats()}).encode())
            except Exception as e:
                self._send(500, json.dumps({'error': repr(e)}).encode())
        else:
            self._send(404, b'{"error":"not found"}')


if __name__ == '__main__':
    print(f'taskboard on http://127.0.0.1:{PORT}')
    ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()