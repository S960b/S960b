"""SQLite-очередь моста Hermes<->ChatGPT (план hermes_chat_bridge_task.md).

Данные:
- jobs: job_id (уникальный), text (<=8KiB), created_at_utc (ISO Z),
  status (pending/replied/conflict), reply (None или текст), updated_at_utc,
  direction ('to_chatgpt'|'to_hermes'), idempotency_key (None|str),
  owner (владелец, для rate limit и уникальности idempotency),
  claimed_by/claimed_at_utc/lease_until_epoch (атомарный lease для to_hermes).
Контракты (план §«Минимальные возможности» + ревизия 22254b3):
- уникальный job_id; UTC-время; повтор ТОГО ЖЕ ответа идемпотентен;
- ДРУГОЙ ответ на уже отвеченную задачу — явный конфликт;
- направление обязательно; bridge_cli put = to_chatgpt (+emit события),
  bridge_put_message = to_hermes (без emit);
- idempotency_key обязателен для to_hermes: повтор ключа+текста -> исходный
  job_id, ключ+другой текст -> conflict;
- claim to_hermes — атомарный lease (один победитель), повторный захват
  после истечения lease; to_chatgpt не claim'ится.
"""
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone

MAX_TEXT_BYTES = 8192
LEASE_DEFAULT_S = 300.0
RATE_LIMIT_PER_MIN = 20  # простой per-owner лимит (в памяти, без внешней инфраструктуры)
MAX_ATTEMPTS = 5  # максимальное число захватов/попыток дочерней задачи


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


class Queue:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            'CREATE TABLE IF NOT EXISTS jobs ('
            ' job_id TEXT PRIMARY KEY, text TEXT NOT NULL,'
            ' created_at_utc TEXT NOT NULL, status TEXT NOT NULL,'
            ' reply TEXT, updated_at_utc TEXT)')
        self._migrate()
        self._rate = {}  # owner -> [(ts, ...)]  (per-process, pilot-приемлемо)

    # --- миграция: обратносовместимое добавление колонок/индексов ---
    def _migrate(self):
        cols = [r[1] for r in self._conn.execute('PRAGMA table_info(jobs)').fetchall()]
        adds = {
            'direction': "TEXT NOT NULL DEFAULT 'to_chatgpt'",
            'idempotency_key': 'TEXT',
            'owner': "TEXT NOT NULL DEFAULT 'owner'",
            'claimed_by': 'TEXT',
            'claimed_at_utc': 'TEXT',
            'lease_until_epoch': 'REAL',
            'parent_job_id': 'TEXT',          # для отчёта to_chatgpt от to_hermes
            'completed_at_utc': 'TEXT',        # терминальные статусы
            'completed_by': 'TEXT',
            'attempts': 'INTEGER NOT NULL DEFAULT 0',  # попытки дочерней задачи
        }
        for name, ddl in adds.items():
            if name not in cols:
                self._conn.execute(f'ALTER TABLE jobs ADD COLUMN {name} {ddl}')
        # уникальность idempotency в пределах owner+direction (частичный индекс)
        self._conn.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS jobs_idem_uniq'
            ' ON jobs(owner, direction, idempotency_key)'
            ' WHERE idempotency_key IS NOT NULL')
        self._conn.commit()

    # --- локальная CLI (только владелец) ---
    def put_message(self, text: str, max_bytes: int = MAX_TEXT_BYTES,
                    direction: str = 'to_chatgpt', idempotency_key: str | None = None,
                    owner: str = 'owner') -> dict:
        """Создать задачу с направлением; вернуть job_id/text/created/status.

        to_hermes требует idempotency_key: повтор ключа+текста возвращает
        исходный job_id (status=pending, idempotent=True); ключ+другой текст
        -> conflict. to_chatgpt (CLI) сохраняет прежнее поведение без ключа.
        """
        if direction not in ('to_chatgpt', 'to_hermes'):
            raise ValueError(f'bad direction: {direction}')
        if len(text.encode('utf-8')) > max_bytes:
            raise ValueError(f'text > {max_bytes} bytes')
        if direction == 'to_hermes' and not idempotency_key:
            raise ValueError('idempotency_key required for to_hermes')
        if not self._rate_ok(owner):
            return {'job_id': None, 'status': 'rate_limited',
                    'reason': f'> {RATE_LIMIT_PER_MIN}/min per owner'}
        now = utc_now_iso()

        if idempotency_key:
            row = self._conn.execute(
                'SELECT * FROM jobs WHERE owner=? AND direction=? AND idempotency_key=?',
                (owner, direction, idempotency_key)).fetchone()
            if row is not None:
                if row['text'] == text:
                    return {'job_id': row['job_id'], 'status': row['status'],
                            'idempotent': True, 'text': text,
                            'created_at_utc': row['created_at_utc']}
                return {'job_id': row['job_id'], 'status': 'conflict',
                        'reason': 'idempotency_key reused with different text'}

        job_id = 'job_' + secrets.token_hex(12)
        self._conn.execute(
            'INSERT INTO jobs (job_id, text, created_at_utc, status, updated_at_utc,'
            ' direction, idempotency_key, owner) VALUES (?,?,?,?,?,?,?,?)',
            (job_id, text, now, 'pending', now, direction, idempotency_key, owner))
        self._conn.commit()
        return self.get_message(job_id)

    def _rate_ok(self, owner: str) -> bool:
        now = time.time()
        bucket = [t for t in self._rate.get(owner, []) if now - t < 60.0]
        if len(bucket) >= RATE_LIMIT_PER_MIN:
            self._rate[owner] = bucket
            return False
        bucket.append(now)
        self._rate[owner] = bucket
        return True

    def get_message(self, job_id: str, owner_ok: bool = True) -> dict | None:
        row = self._conn.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone()
        return dict(row) if row else None

    def list_jobs(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            'SELECT job_id, status, direction, created_at_utc, updated_at_utc,'
            ' length(text) AS text_len,'
            ' CASE WHEN reply IS NOT NULL THEN length(reply) ELSE NULL END AS reply_len,'
            ' claimed_by, lease_until_epoch'
            ' FROM jobs ORDER BY created_at_utc DESC LIMIT ?', (limit,)).fetchall()
        return [dict(r) for r in rows]

    # --- инструменты (после авторизации) ---
    def get_for_chat(self, job_id: str) -> dict | None:
        """Только существующая задача; текст в ограничении 8KiB."""
        row = self._conn.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone()
        if row is None:
            return None
        return {'job_id': row['job_id'], 'text': row['text'],
                'created_at_utc': row['created_at_utc'], 'status': row['status'],
                'reply': row['reply'], 'direction': row['direction']}

    def put_reply(self, job_id: str, text: str, max_bytes: int = MAX_TEXT_BYTES) -> dict:
        """Записать ответ. Идемпотентен для одинакового; другой — conflict."""
        if len(text.encode('utf-8')) > max_bytes:
            raise ValueError(f'text > {max_bytes} bytes')
        row = self._conn.execute(
            'UPDATE jobs SET reply=?, status=?, updated_at_utc=?'
            ' WHERE job_id=? AND reply IS NULL',
            (text, 'replied', utc_now_iso(), job_id)).rowcount
        self._conn.commit()
        if row:
            return {'job_id': job_id, 'status': 'replied', 'reply': text}
        cur = self._conn.execute('SELECT reply FROM jobs WHERE job_id=?', (job_id,)).fetchone()
        if cur is None:
            return {'job_id': job_id, 'status': 'not_found', 'reason': 'unknown job'}
        if cur['reply'] == text:
            return {'job_id': job_id, 'status': 'replied', 'reply': text, 'idempotent': True}
        return {'job_id': job_id, 'status': 'conflict',
                'reason': 'different reply to answered job'}

    # --- атомарный lease для to_hermes (consumer/воркер) ---
    def claim_next_to_hermes(self, worker: str = 'worker',
                             lease_s: float = LEASE_DEFAULT_S) -> dict | None:
        """Атомарно захватить старейшую to_hermes pending-задачу.

        Один победитель (BEGIN IMMEDIATE + UPDATE ... WHERE ... IS NULL/
        lease истёк). Истёкший lease -> повторный захват другим воркером.
        to_chatgpt не claim'ится никогда; completed/failed — не выбираются;
        attempts >= MAX_ATTEMPTS — не выбираются (лимит retry).
        """
        now_epoch = time.time()
        lease_until = now_epoch + lease_s
        conn = self._conn
        with conn:  # транзакция (BEGIN IMMEDIATE под капотом при UPDATE)
            conn.execute('BEGIN IMMEDIATE')
            try:
                row = conn.execute(
                    'SELECT job_id FROM jobs'
                    ' WHERE direction=? AND status=?'
                    ' AND (attempts IS NULL OR attempts < ?)'
                    ' AND (claimed_by IS NULL OR lease_until_epoch IS NULL'
                    '      OR lease_until_epoch < ?)'
                    ' ORDER BY created_at_utc ASC LIMIT 1',
                    ('to_hermes', 'pending', MAX_ATTEMPTS, now_epoch)).fetchone()
                if row is None:
                    conn.execute('COMMIT')
                    return None
                conn.execute(
                    'UPDATE jobs SET claimed_by=?, claimed_at_utc=?,'
                    ' lease_until_epoch=?, attempts=COALESCE(attempts,0)+1,'
                    ' updated_at_utc=? WHERE job_id=?',
                    (worker, utc_now_iso(), lease_until, utc_now_iso(), row['job_id']))
                conn.execute('COMMIT')
            except Exception:
                conn.execute('ROLLBACK')
                raise
        return self.get_message(row['job_id'])

    def _lease_holder(self, job_id: str) -> dict | None:
        return self._conn.execute(
            'SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone()

    def _check_lease(self, row, worker: str) -> dict | None:
        """Валидация lease перед complete/fail. Возвращает None или ошибку."""
        if row is None:
            return {'job_id': None, 'status': 'not_found'}
        if row['direction'] != 'to_hermes':
            return {'job_id': row['job_id'], 'status': 'conflict',
                    'reason': 'complete/fail allowed only for to_hermes'}
        if row['status'] not in ('pending',):
            return {'job_id': row['job_id'], 'status': row['status'],
                    'reason': 'terminal status; no lease active'}
        if row['claimed_by'] != worker:
            return {'job_id': row['job_id'], 'status': 'not_owner',
                    'reason': f'claimed by {row["claimed_by"]}'}
        if row['lease_until_epoch'] is None or row['lease_until_epoch'] < time.time():
            return {'job_id': row['job_id'], 'status': 'lease_expired',
                    'reason': 'lease expired; reclaim first'}
        return None

    def complete_to_hermes(self, job_id: str, worker: str,
                           report_text: str | None = None) -> dict:
        """Атомарно завершить to_hermes текущим владельцем lease.

        В одной транзакции: перевод в 'completed' + снятие lease +
        создание (идемпотентно) связанного отчёта to_chatgpt с
        parent_job_id. Повторный complete возвращает тот же report_job_id
        и created=False (сервер НЕ должен emit'ить второе событие).
        Истёкший lease -> lease_expired (старый воркер завершить не может).
        to_chatgpt -> conflict.
        """
        conn = self._conn
        now = utc_now_iso()
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                row = self._lease_holder(job_id)
                err = self._check_lease(row, worker)
                if err is not None:
                    conn.execute('COMMIT')
                    # повтор complete ТЕМ ЖЕ воркером после потери ответа:
                    # задача уже completed -> тот же report_job_id, created=False;
                    # чужой воркер не получает idempotent-успех
                    if (row is not None and row['status'] == 'completed'
                            and row['completed_by'] == worker):
                        report = conn.execute(
                            'SELECT job_id FROM jobs WHERE parent_job_id=?'
                            ' AND direction=? LIMIT 1',
                            (job_id, 'to_chatgpt')).fetchone()
                        return {'job_id': job_id, 'status': 'completed',
                                'report_job_id': report['job_id'] if report else None,
                                'created': False}
                    return err
                conn.execute(
                    'UPDATE jobs SET status=?, reply=?, claimed_by=NULL,'
                    ' claimed_at_utc=NULL, lease_until_epoch=NULL,'
                    ' completed_at_utc=?, completed_by=?, updated_at_utc=?'
                    ' WHERE job_id=?',
                    ('completed', report_text or row['text'], now, worker, now, job_id))
                # связанный отчёт to_chatgpt (parent_job_id, идемпотентно)
                report_key = f'report:{job_id}'
                from sqlite3 import IntegrityError
                report_job = 'job_' + secrets.token_hex(12)
                try:
                    conn.execute(
                        'INSERT INTO jobs (job_id, text, created_at_utc, status,'
                        ' updated_at_utc, direction, idempotency_key, owner,'
                        ' parent_job_id) VALUES (?,?,?,?,?,?,?,?,?)',
                        (report_job, report_text or row['text'], now, 'pending', now,
                         'to_chatgpt', report_key, row['owner'] or 'owner', job_id))
                    created = True
                except IntegrityError:
                    existing = conn.execute(
                        'SELECT job_id FROM jobs WHERE idempotency_key=?'
                        ' AND direction=?', (report_key, 'to_chatgpt')).fetchone()
                    report_job = existing['job_id']
                    created = False
                conn.execute('COMMIT')
            except Exception:
                conn.execute('ROLLBACK')
                raise
        return {'job_id': job_id, 'status': 'completed',
                'report_job_id': report_job, 'created': created}

    def fail_to_hermes(self, job_id: str, worker: str, reason: str | None = None) -> dict:
        """Атомарно перевести to_hermes в 'failed' текущим владельцем lease.

        Снимает lease. Повторный fail_idempotent; истёкший lease ->
        lease_expired; to_chatgpt -> conflict. Отчёт не создаётся.
        """
        conn = self._conn
        now = utc_now_iso()
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                row = self._lease_holder(job_id)
                err = self._check_lease(row, worker)
                if err is not None:
                    conn.execute('COMMIT')
                    if row is not None and row['status'] == 'failed':
                        return {'job_id': job_id, 'status': 'failed',
                                'idempotent': True}
                    return err
                conn.execute(
                    'UPDATE jobs SET status=?, reply=?, claimed_by=NULL,'
                    ' claimed_at_utc=NULL, lease_until_epoch=NULL,'
                    ' completed_at_utc=?, completed_by=?, updated_at_utc=?'
                    ' WHERE job_id=?',
                    ('failed', reason or 'failed', now, worker, now, job_id))
                conn.execute('COMMIT')
            except Exception:
                conn.execute('ROLLBACK')
                raise
        return {'job_id': job_id, 'status': 'failed', 'idempotent': False}

    def renew_lease(self, job_id: str, worker: str,
                    lease_s: float = LEASE_DEFAULT_S) -> bool:
        row = self._conn.execute(
            'UPDATE jobs SET lease_until_epoch=?, updated_at_utc=?'
            ' WHERE job_id=? AND claimed_by=?',
            (time.time() + lease_s, utc_now_iso(), job_id, worker)).rowcount
        self._conn.commit()
        return bool(row)

    def release_claim(self, job_id: str, worker: str) -> bool:
        row = self._conn.execute(
            'UPDATE jobs SET claimed_by=NULL, claimed_at_utc=NULL,'
            ' lease_until_epoch=NULL, updated_at_utc=?'
            ' WHERE job_id=? AND claimed_by=?',
            (utc_now_iso(), job_id, worker)).rowcount
        self._conn.commit()
        return bool(row)


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