#!/usr/bin/env python3
"""Hermes<->ChatGPT bridge taskboard - minimal visual task list.

Serves:
  /                 Conversation board (one row per stable task_id)
  /api/tasks        Task projections with actor states and full history
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
import os
import sqlite3
import urllib.parse
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DB = Path(os.environ.get('BRIDGE_DB_PATH', '/home/kali/.local/state/hermes-bridge/pilot-20261004T140717Z/pilot.db'))
TUNNEL_URL_FILE = Path('/home/kali/.local/state/hermes-bridge/current_tunnel_url.txt')
PORT = 8787

from task_view import PAGE


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
        elif urllib.parse.urlparse(self.path).path == '/api/tasks':
            from task_view import fetch_tasks
            try:
                with closing(_conn_ro()) as conn:
                    conn.row_factory = sqlite3.Row
                    tasks = fetch_tasks(conn)
                self._send(200, json.dumps({'tasks': tasks, 'tunnel_url':
                    TUNNEL_URL_FILE.read_text().strip() if TUNNEL_URL_FILE.exists() else ''}).encode())
            except Exception as e:
                self._send(500, json.dumps({'error': str(e)}).encode())
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
    from bridge_queue import Queue
    # Idempotent additive migration, no job deletion or guessed cross-chat grouping.
    Queue(str(DB))._conn.close()
    print(f'taskboard on http://127.0.0.1:{PORT}')
    ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
