"""SQLite-очередь моста Hermes<->ChatGPT (план hermes_chat_bridge_task.md).

Данные:
- jobs: job_id (уникальный), text (<=8KiB), created_at_utc (ISO Z),
  status (pending/replied/conflict), reply (None или текст), updated_at_utc.
Контракты (план §«Минимальные возможности»):
- уникальный job_id; UTC-время; повтор ТОГО ЖЕ ответа идемпотентен;
- ДРУГОЙ ответ на уже отвеченную задачу — явный конфликт (не молчаливая
  перезапись); отвечать чужому job_id/неизвестной задаче нельзя.
"""
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


class Queue:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            'CREATE TABLE IF NOT EXISTS jobs ('
            ' job_id TEXT PRIMARY KEY, text TEXT NOT NULL,'
            ' created_at_utc TEXT NOT NULL, status TEXT NOT NULL,'
            ' reply TEXT, updated_at_utc TEXT)')
        self._conn.commit()

    # --- локальная CLI (только владелец) ---
    def put_message(self, text: str, max_bytes: int = 8192) -> dict:
        """Создать задачу; вернуть job_id/text/created/status."""
        if len(text.encode('utf-8')) > max_bytes:
            raise ValueError(f'text > {max_bytes} bytes')
        job_id = 'job_' + secrets.token_hex(12)
        now = utc_now_iso()
        self._conn.execute(
            'INSERT INTO jobs (job_id, text, created_at_utc, status, updated_at_utc)'
            ' VALUES (?,?,?,?,?)', (job_id, text, now, 'pending', now))
        self._conn.commit()
        return self.get_message(job_id)

    def get_message(self, job_id: str, owner_ok: bool = True) -> dict | None:
        row = self._conn.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone()
        return dict(row) if row else None

    def list_jobs(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            'SELECT job_id, status, created_at_utc, updated_at_utc,'
            ' length(text) AS text_len,'
            ' CASE WHEN reply IS NOT NULL THEN length(reply) ELSE NULL END AS reply_len'
            ' FROM jobs ORDER BY created_at_utc DESC LIMIT ?', (limit,)).fetchall()
        return [dict(r) for r in rows]

    # --- инструменты (после авторизации) ---
    def get_for_chat(self, job_id: str) -> dict | None:
        """Только существующая задача; текст в ограничении 8KiB."""
        row = self._conn.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone()
        if row is None:
            return None
        return dict(row)

    def put_reply(self, job_id: str, text: str, max_bytes: int = 8192) -> dict:
        """Записать ответ. Идемпотентен ТОЛЬКО при идентичном ответе;
        другой ответ на отвеченную задачу — конфликт ('conflict')."""
        if len(text.encode('utf-8')) > max_bytes:
            raise ValueError(f'reply > {max_bytes} bytes')
        now = utc_now_iso()
        # Conditional UPDATE is the compare-and-swap. SQLite serializes writers.
        cur = self._conn.execute(
            'UPDATE jobs SET status=?,reply=?,updated_at_utc=? WHERE job_id=? AND reply IS NULL',
            ('replied', text, now, job_id))
        changed = cur.rowcount
        self._conn.commit()
        if changed:
            return {'job_id': job_id, 'status': 'replied', 'reply': text, 'idempotent': False}
        row = self._conn.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone()
        if row is None:
            return {'job_id': job_id, 'status': 'not_found', 'reason': 'unknown job'}
        if row['reply'] == text:
            return {'job_id': job_id, 'status': 'replied', 'reply': text, 'idempotent': True}
        return {'job_id': job_id, 'status': 'conflict', 'reason': 'different reply to answered job'}



if __name__ == '__main__':
    import sys
    db = os.path.expanduser('~/hermes-chat-bridge/data/bridge.db')
    q = Queue(db)
    if len(sys.argv) > 1 and sys.argv[1] == 'list':
        for j in q.list_jobs():
            print(j)
    elif len(sys.argv) > 1 and sys.argv[1] == 'get':
        print(q.get_message(sys.argv[2]))
    elif len(sys.argv) > 2 and sys.argv[1] == 'put':
        r = q.put_message(sys.argv[2])
        print(f"job_id={r['job_id']} status={r['status']}")
        print(json.dumps(r, ensure_ascii=False))
    else:
        print('usage: queue.py list | get <job_id> | put <text>')