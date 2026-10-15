#!/usr/bin/env python3
"""Hermes<->ChatGPT bridge taskboard - minimal visual task list.

Serves:
  /                 HTML page (live-updating table, filters, actions, tunnel URL)
  /api/jobs         JSON list of jobs (query: ?status=&direction=&queue=&limit=)
  /api/stats        JSON counts by direction/status/queue
  POST /api/action  {job_id, action} - admin actions on a job:
                      complete  (pending to_hermes -> completed, completed_by='hermes-admin')
                      fail      (pending -> failed, admin reason)
                      reopen    (failed -> pending, clears completed_by/at)

Reads pilot.db directly (read-only for GET, short write txn for POST actions).
Port 8787, localhost only. Job ids look like  test_3acba1b6.job  (queue_8hex.job).
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
.tunnel { background:#14241a; border:1px solid #2d5c3a; border-radius:10px;
  padding:10px 14px; margin-bottom:16px; font-size:14px; }
.tunnel a { color:#7ee787; text-decoration:none; word-break:break-all; }
.tunnel a:hover { text-decoration:underline; }
.tunnel .hint { color:#6f8f7a; font-size:12px; margin-top:4px; }
.filters { margin-bottom:14px; font-size:13px; }
.filters button { background:#1c212b; color:#d7dae0; border:1px solid #2d3543;
  border-radius:6px; padding:4px 10px; margin-right:6px; cursor:pointer; }
.filters button.on { background:#3b82f6; border-color:#3b82f6; color:#fff; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th,td { text-align:left; padding:6px 9px; border-bottom:1px solid #232936;
  vertical-align:top; }
th { color:#8b93a3; font-weight:600; font-size:12px; position:sticky; top:0;
  background:#0f1115; }
td.txt { white-space:normal; max-width:360px; color:#aab2c0; }
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
.btn { background:#1c212b; color:#d7dae0; border:1px solid #2d3543; border-radius:6px;
  padding:2px 8px; font-size:11px; cursor:pointer; margin:1px; }
.btn:hover { border-color:#3b82f6; color:#fff; }
.btn.go { border-color:#2d5c3a; color:#7ee787; }
.btn.danger { border-color:#5a1d1d; color:#ff9c9c; }
.jcopy { padding:0 5px; margin-left:6px; font-size:12px; line-height:1.4; }
#err { color:#ff9c9c; font-size:13px; }
#toast { position:fixed; bottom:16px; right:16px; background:#1c212b; color:#d7dae0;
  border:1px solid #2d3543; border-radius:8px; padding:8px 14px; font-size:13px;
  opacity:0; transition:opacity .3s; }
</style></head><body>
<h1>Bridge taskboard</h1>
<div class="sub">Hermes &harr; ChatGPT &middot; <span id="updated">-</span> &middot; автообновление 5с</div>
<div class="tunnel">
  <b>Постоянный адрес моста (для ChatGPT):</b><br>
  <a id="tunnelLink" href="#" target="_blank" rel="noopener">-</a>
  <button class="btn" id="copyTunnel" style="margin-left:8px">⧉ копировать</button>
  <div class="hint">Tailscale Funnel. Адрес не меняется.</div>
</div>
<div class="filters" id="filters"></div>
<div id="err"></div>
<table><thead><tr>
<th>job_id</th><th>напр.</th><th>queue</th><th>статус</th><th>создан (МСК)</th>
<th>кому/кем</th><th>текст</th><th>ответ</th><th>действия</th></tr></thead>
<tbody id="rows"></tbody></table>
<div id="toast"></div>
<script>
const FMT = new Intl.DateTimeFormat('ru-RU',{day:'2-digit',month:'2-digit',
  hour:'2-digit',minute:'2-digit'});
let f = { status:'', direction:'', queue:'' };
let TUNNEL = '';
function msk(iso){ if(!iso) return '-'; return FMT.format(new Date(iso)); }
function el(t,c){ return '<span class="badge '+c+'">'+t+'</span>'; }
function esc(s){ return String(s).replace(/</g,'&lt;'); }
function toast(msg){ const t=document.getElementById('toast'); t.textContent=msg;
  t.style.opacity=1; setTimeout(()=>t.style.opacity=0, 2200); }
async function act(jobId, action, label){
  if(!confirm(label+'?\\n'+jobId)) return;
  try{
    const r = await fetch('/api/action', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({job_id: jobId, action: action})});
    const d = await r.json();
    if(d.ok){ toast('OK: '+jobId+' -> '+action+' (rows='+d.rowcount+')'); load(); }
    else { toast('ОШИБКА: '+(d.error||'?')); }
  }catch(e){ toast('fetch error: '+e); }
}
function copyJob(jobId, btn){
  const done = ()=>{ toast('job_id скопирован'); };
  const cb = ()=>{ if(navigator.clipboard && navigator.clipboard.writeText){
      navigator.clipboard.writeText(jobId).then(done).catch(()=>fallbackCopy(jobId));
    } else fallbackCopy(jobId); };
  cb();
}
function actionBtns(j){
  const del = '<button class="btn danger" title="удалить навсегда" onclick="act(\\''+j.job_id+'\\',\\'delete\\',\\'Удалить навсегда?\\')">🗑</button>';
  let b = '';
  if(j.status==='pending' && j.direction==='to_hermes'){
    b += '<button class="btn go" onclick="act(\\''+j.job_id+'\\',\\'complete\\',\\'Завершить (completed, admin)\\\')">✓ завершить</button>';
    b += '<button class="btn danger" onclick="act(\\''+j.job_id+'\\',\\'fail\\',\\'Закрыть (failed)\\\')">✕ закрыть</button>';
    b += del;
  } else if(j.status==='pending' && j.direction==='to_chatgpt'){
    b += '<button class="btn danger" onclick="act(\\''+j.job_id+'\\',\\'fail\\',\\'Закрыть (failed)\\\')">✕ закрыть</button>';
    b += del;
  } else if(j.status==='replied'){
    b += '<button class="btn go" onclick="act(\\''+j.job_id+'\\',\\'complete\\',\\'Завершить (completed, admin)\\\')">✓ завершить</button>';
    b += del;
  } else if(j.status==='completed'){
    b += '<button class="btn go" onclick="act(\\''+j.job_id+'\\',\\'reopen\\',\\'Вернуть в очередь (pending)\\\')">↺ вернуть</button>';
    b += del;
  } else if(j.status==='failed'){
    b += '<button class="btn go" onclick="act(\\''+j.job_id+'\\',\\'reopen\\',\\'Вернуть в очередь (pending)\\\')">↺ вернуть</button>';
    b += del;
  }
  return b || '<span class="meta">-</span>';
}
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
    TUNNEL = data.tunnel_url || '(нет)';
    const tl = document.getElementById('tunnelLink');
    tl.href = (TUNNEL.startsWith('http')) ? TUNNEL : '#';
    tl.textContent = TUNNEL;
    const tb = document.getElementById('rows');
    tb.innerHTML = data.jobs.map(j=>{
      const st = j.claimed_by ? 'claimed' : j.status;
      const stl = j.claimed_by ? 'st-claimed' : 'st-'+j.status;
      const who = j.claimed_by ? j.claimed_by : (j.completed_by ? j.completed_by : '-');
      const rep = (j.reply_len!=null)? j.reply_len+'б' : (j.reply ? 'да' : '-');
      return '<tr><td class="jid mono">'+esc(j.job_id)+
        '<button class="btn jcopy" title="копировать job_id" onclick="copyJob(\\''+j.job_id+'\\')">⧉</button></td>'+
        '<td class="dir-'+j.direction+'">'+j.direction.replace('to_','')+'</td>'+
        '<td><span class="q">'+esc(j.queue)+'</span></td>'+
        '<td>'+el(st,stl)+'</td>'+
        '<td class="meta">'+msk(j.created_at_utc)+'</td>'+
        '<td class="meta">'+esc(who)+'</td>'+
        '<td class="txt">'+esc((j.text||'').slice(0,160))+'</td>'+
        '<td class="meta">'+esc(rep)+'</td>'+
        '<td>'+actionBtns(j)+'</td></tr>';
    }).join('') || '<tr><td colspan="9" class="meta">пусто</td></tr>';
    document.getElementById('updated').textContent = 'обновлено '+new Date().toLocaleTimeString('ru-RU');
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
document.getElementById('copyTunnel').onclick = ()=>{
  const url = TUNNEL;
  const done = ()=>{ toast('URL скопирован'); };
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(url).then(done).catch(()=>fallbackCopy(url));
  } else fallbackCopy(url);
};
function fallbackCopy(text){
  const ta=document.createElement('textarea'); ta.value=text;
  document.body.appendChild(ta); ta.select();
  try{ document.execCommand('copy'); toast('URL скопирован'); }
  catch(e){ toast('копировать вручную: '+text); }
  document.body.removeChild(ta);
}
buildFilters(); load(); setInterval(load,5000);
</script></body></html>"""


def _conn_ro():
    return sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=10)


def q(url: str):
    p = urllib.parse.urlparse(url)
    return {k: v[0] for k, v in urllib.parse.parse_qs(p.query).items()}


def fetch_jobs(params):
    conn = _conn_ro()
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
    conn = _conn_ro()
    rows = conn.execute(
        'SELECT direction, status, count(*) c FROM jobs'
        ' GROUP BY direction, status ORDER BY direction, status').fetchall()
    conn.close()
    return [{'direction': r[0], 'status': r[1], 'count': r[2]} for r in rows]


def apply_action(job_id: str, action: str) -> tuple[bool, int, str]:
    """Admin actions per the bridge skill (completed_by='hermes-admin',
    no report row creation - documented admin path). Returns (ok, rowcount, err)."""
    if action not in ('complete', 'fail', 'reopen', 'delete'):
        return False, 0, f'unknown action: {action}'
    now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc) \
        .isoformat().replace('+00:00', 'Z')
    conn = sqlite3.connect(DB, timeout=15)
    conn.execute('PRAGMA busy_timeout=15000')
    try:
        if action == 'complete':
            # pending to_hermes (в т.ч. claimed) или replied (ChatGPT ответил) -> completed
            cur = conn.execute(
                "UPDATE jobs SET status='completed',"
                " reply=COALESCE(reply,'завершено вручную из taskboard (admin)'),"
                " completed_at_utc=?, completed_by='hermes-admin',"
                " claimed_by=NULL, claimed_at_utc=NULL, lease_until_epoch=NULL,"
                " updated_at_utc=?"
                " WHERE job_id=? AND ((status='pending' AND direction='to_hermes') OR status='replied')",
                (now, now, job_id))
        elif action == 'fail':
            cur = conn.execute(
                "UPDATE jobs SET status='failed',"
                " reply=COALESCE(reply,'закрыто вручную из taskboard (admin)'),"
                " completed_at_utc=?, completed_by='hermes-admin',"
                " claimed_by=NULL, claimed_at_utc=NULL, lease_until_epoch=NULL,"
                " updated_at_utc=?"
                " WHERE job_id=? AND status='pending'",
                (now, now, job_id))
        elif action == 'reopen':
            # failed ИЛИ completed -> pending (откат закрытия)
            cur = conn.execute(
                "UPDATE jobs SET status='pending',"
                " completed_at_utc=NULL, completed_by=NULL,"
                " updated_at_utc=?"
                " WHERE job_id=? AND status IN ('failed','completed')",
                (now, job_id))
        else:  # delete — физическое удаление строки (необратимо)
            cur = conn.execute("DELETE FROM jobs WHERE job_id=?", (job_id,))
        conn.commit()
        rc = cur.rowcount
        conn.close()
        return True, rc, '' if rc else 'no row changed (status constraint?)'
    except Exception as e:
        conn.close()
        return False, 0, repr(e)


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

    def do_POST(self):
        if not self.path.startswith('/api/action'):
            self._send(404, b'{"error":"not found"}')
            return
        try:
            n = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(n) or b'{}')
            job_id = body.get('job_id', '')
            action = body.get('action', '')
            ok, rc, err = apply_action(job_id, action)
            self._send(200, json.dumps({'ok': ok, 'rowcount': rc,
                                        'error': err or None}).encode())
        except Exception as e:
            self._send(200, json.dumps({'ok': False, 'rowcount': 0,
                                        'error': repr(e)}).encode())


if __name__ == '__main__':
    print(f'taskboard on http://127.0.0.1:{PORT}')
    ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()