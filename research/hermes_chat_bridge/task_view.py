"""Read-only conversation projection. Transport job IDs never become board rows."""
import time
from datetime import datetime


def project_task(rows, title, now=None, include_history=True):
    now = time.time() if now is None else now
    latest = rows[-1]
    target = 'ChatGPT' if latest['direction'] == 'to_chatgpt' else 'MT'
    sender = 'MT' if target == 'ChatGPT' else 'ChatGPT'
    people = {sender: {'color': 'green', 'text': 'передал данные'},
              target: {'color': 'grey', 'text': 'нет активного поручения'}}
    stamp = latest['updated_at_utc'] or latest['created_at_utc']
    age = max(0, now - datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp())
    if latest['status'] == 'pending':
        live = latest['claimed_by'] and (latest['lease_until_epoch'] or 0) > now
        people[target] = {'color': 'red' if age >= 600 else 'yellow',
                          'text': ('нет активности 10 минут' if age >= 600 else
                                   ('в работе' if live else 'ожидает получения'))}
    elif latest['status'] == 'failed':
        people[target] = {'color': 'red', 'text': 'ошибка'}
    elif latest['reply'] is not None:
        sender, target = target, sender
        people[sender] = {'color': 'green', 'text': 'ответил'}
        people[target] = {'color': 'grey', 'text': 'ответ получен'}
    history = []
    linked_parents = {row['parent_job_id'] for row in rows}
    for row in rows if include_history else []:
        author = 'MT' if row['direction'] == 'to_chatgpt' else 'ChatGPT'
        history.append({'from': author, 'text': row['text'], 'at': row['created_at_utc'],
                        'turn_id': row['job_id']})
        if row['reply'] is not None:
            # complete_to_hermes stores the report both in reply and a linked row.
            linked = row['job_id'] in linked_parents
            if not linked:
                history.append({'from': 'ChatGPT' if author == 'MT' else 'MT',
                                'text': row['reply'], 'at': row['updated_at_utc'],
                                'turn_id': row['job_id']})
    return {'task_id': latest['task_id'], 'title': title,
            'arrow': 'ChatGPT → MT' if sender == 'ChatGPT' else 'ChatGPT ← MT',
            'people': people, 'updated_at_utc': stamp, 'status': latest['status'],
            'current_turn_id': latest['job_id'], 'history': history}


def fetch_tasks(conn, limit=80, include_history=True):
    tasks = conn.execute('SELECT t.* FROM bridge_tasks t JOIN jobs j ON j.task_id=t.task_id '
                         'GROUP BY t.task_id ORDER BY max(j.updated_at_utc) DESC LIMIT ?',
                         (max(1, min(limit, 300)),)).fetchall()
    result = []
    for task in tasks:
        if not include_history:
            # Do not load message bodies into either Python or the browser on every poll.
            row = dict(conn.execute(
                "SELECT job_id, task_id, direction, status, created_at_utc, updated_at_utc, "
                "claimed_by, lease_until_epoch, parent_job_id, '' AS text, "
                "CASE WHEN reply IS NULL THEN NULL ELSE '' END AS reply "
                "FROM jobs WHERE task_id=? ORDER BY created_at_utc DESC, rowid DESC LIMIT 1",
                (task['task_id'],)).fetchone())
            summary = project_task([row], task['title'], include_history=False)
            summary.pop('history')
            summary['message_count'] = conn.execute(
                'SELECT count(*) + coalesce(sum(reply IS NOT NULL AND NOT EXISTS '
                '(SELECT 1 FROM jobs child WHERE child.parent_job_id=j.job_id)),0) '
                'FROM jobs j WHERE task_id=?', (task['task_id'],)).fetchone()[0]
            result.append(summary)
            continue
        rows = [dict(r) for r in conn.execute('SELECT * FROM jobs WHERE task_id=? '
                                             'ORDER BY created_at_utc, rowid', (task['task_id'],))]
        result.append(project_task(rows, task['title']))
    return result


def fetch_task_history(conn, task_id):
    task = conn.execute('SELECT title FROM bridge_tasks WHERE task_id=?', (task_id,)).fetchone()
    if task is None:
        return None
    rows = [dict(r) for r in conn.execute('SELECT * FROM jobs WHERE task_id=? '
                                         'ORDER BY created_at_utc, rowid', (task_id,))]
    return project_task(rows, task['title']) if rows else None


PAGE = r'''<!doctype html>
<html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Bridge · беседы</title>
<style>
:root{color-scheme:dark}body{margin:0;padding:24px;background:#0f1115;color:#d7dae0;font:14px system-ui}
h1{font-size:22px;margin-bottom:8px}.muted{color:#929aaa}#err{color:#ff9c9c}
table{width:100%;border-collapse:collapse;margin-top:24px}td,th{text-align:left;padding:14px;border-bottom:1px solid #303643;vertical-align:top}
th{color:#929aaa}code{word-break:break-all}.badge{display:inline-block;padding:6px 10px;border-radius:8px;white-space:nowrap}
.green{background:#15452b;color:#8de9aa}.yellow{background:#594500;color:#ffe08a}
.red{background:#5a2025;color:#ffb0b5}.grey{background:#242a35;color:#adb4c0}
button{cursor:pointer;background:#242a35;color:#d7dae0;border:1px solid #465167;border-radius:6px;padding:5px 8px}
.task-actions{display:flex;flex-direction:column;align-items:flex-start;gap:12px}
.danger{border-color:#9b444d;color:#ffb0b5}.danger:hover{background:#5a2025}
pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:380px;overflow:auto;font:13px ui-monospace,monospace}
dialog{background:#151a23;color:#d7dae0;border:1px solid #465167;border-radius:12px;width:min(800px,85vw)}
article{border-bottom:1px solid #303643;padding:12px 0}#tunnel{color:#8de9aa}
@media(max-width:750px){body{padding:10px}table{display:block;overflow:auto}td,th{padding:8px}}
</style>
<h1>Bridge · один чат — одна задача</h1>
<div class="muted">ChatGPT ↔ MT · <span id="updated">подключение…</span> · обновление 5с</div>
<p>Адрес моста: <a id="tunnel" rel="noopener" target="_blank"></a></p>
<div class="muted">Стрелка — последняя передача. Жёлтый — ожидает / работает; зелёный — ответил;
красный — ошибка или нет активности 10 минут. Серый — активного поручения нет.</div>
<p id="err" role="alert"></p>
<table><thead><tr><th>Задача / постоянный ID</th><th>Обмен</th><th>ChatGPT</th><th>MT</th><th>История</th></tr></thead><tbody id="rows"></tbody></table>
<dialog id="history"><button id="close">Закрыть</button><h2 id="heading"></h2><div id="messages"></div></dialog>
<script>
const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n};
const fmt=iso=>new Date(iso).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'});
let historyRequest=null, deletePending=false, loadSequence=0;
async function show(t){
 if(historyRequest)historyRequest.abort();const controller=new AbortController();historyRequest=controller;
 document.getElementById('heading').textContent=t.title;const out=document.getElementById('messages');out.replaceChildren(node('p','Загрузка…'));
 document.getElementById('history').showModal();
 try{const r=await fetch('/api/tasks/history?task_id='+encodeURIComponent(t.task_id),{cache:'no-store',signal:controller.signal});
  if(!r.ok)throw Error('HTTP '+r.status);const d=await r.json();if(d.error)throw Error(d.error);
  if(controller.signal.aborted)return;out.replaceChildren();
  for(const m of d.task.history){const a=node('article');a.append(node('b',m.from+' · '+fmt(m.at)),node('pre',m.text));out.append(a)}
 }catch(e){if(e.name!=='AbortError')out.replaceChildren(node('p','Не удалось загрузить историю: '+e))}
}
document.getElementById('close').onclick=()=>document.getElementById('history').close();
document.getElementById('history').addEventListener('close',()=>{if(historyRequest)historyRequest.abort();historyRequest=null;document.getElementById('messages').replaceChildren();document.getElementById('heading').textContent=''});
async function deleteTask(t,button){
 if(deletePending)return;
 if(!confirm('Удалить задачу «'+t.title+'»?\nID: '+t.task_id+'\n\nВся переписка и ответы будут удалены без возможности восстановления через табло. Если задача в работе, её результат может не сохраниться.'))return;
 deletePending=true;button.disabled=true;++loadSequence;
 try{const r=await fetch('/api/tasks/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task_id:t.task_id,confirmed:true})});
  const d=await r.json();if(!r.ok||!d.ok)throw Error(d.error||'HTTP '+r.status);
  if(document.getElementById('history').open)document.getElementById('history').close();
 }catch(e){document.getElementById('err').textContent='Удаление не выполнено: '+e;return}
 finally{deletePending=false;button.disabled=false}
 await load();
}
async function load(){if(deletePending)return;const sequence=++loadSequence;try{const r=await fetch('/api/tasks',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const d=await r.json();if(d.error)throw Error(d.error);
 if(sequence!==loadSequence||deletePending)return;
 const rows=document.getElementById('rows');rows.replaceChildren();
 for(const t of d.tasks){const tr=node('tr'),id=node('td'),copy=node('button','Копировать ID');copy.onclick=()=>navigator.clipboard.writeText(t.task_id).catch(e=>document.getElementById('err').textContent=e);
 id.append(node('b',t.title),node('p',t.task_id),copy);tr.append(id,node('td',t.arrow));
 for(const p of ['ChatGPT','MT']){const td=node('td');td.append(node('span',t.people[p].text,'badge '+t.people[p].color));tr.append(td)}
 const h=node('td'),actions=node('div',undefined,'task-actions'),b=node('button','История ('+t.message_count+')'),del=node('button','Удалить задачу','danger');
 b.onclick=()=>show(t);del.onclick=()=>deleteTask(t,del);actions.append(b,del);h.append(actions,node('p',fmt(t.updated_at_utc),'muted'));tr.append(h);rows.append(tr)}
 const link=document.getElementById('tunnel');link.textContent=d.tunnel_url||'не указан';link.removeAttribute('href');
 if(/^https?:\/\//.test(d.tunnel_url||''))link.href=d.tunnel_url;
 document.getElementById('updated').textContent='обновлено '+new Date().toLocaleTimeString('ru-RU',{timeZone:'Europe/Moscow'});
 document.getElementById('err').textContent='';
 }catch(e){document.getElementById('err').textContent='Связь с табло потеряна: '+e+' (данные могли устареть)'}}
load();setInterval(load,5000);
</script></html>'''
