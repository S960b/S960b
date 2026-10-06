"""Модель очереди по ревизии: direction, idempotency, lease, миграция.

Покрывает требования ревизии 22254b3:
- разделение направлений to_chatgpt / to_hermes;
- отсутствие webhook-цикла (bridge_put_message не порождает событие);
- dedup после имитации потерянного ответа (idempotency_key);
- конкурентный захват двумя соединениями — ровно один победитель;
- возврат задачи после истечения lease (повторный захват);
- невозможность claim задачи to_chatgpt;
- обратносовместимая миграция старой схемы БД.
"""
import json
import os
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge_queue as bq


def fresh_queue(tmp_path, name='bridge.db'):
    q = bq.Queue(str(tmp_path / name))
    return q


def test_direction_separation(tmp_path):
    q = fresh_queue(tmp_path)
    a = q.put_message('to gpt', direction='to_chatgpt')
    b = q.put_message('to hermes', direction='to_hermes', idempotency_key='k1')
    assert a['direction'] == 'to_chatgpt'
    assert b['direction'] == 'to_hermes'
    assert q.get_message(a['job_id'])['direction'] == 'to_chatgpt'
    assert q.get_message(b['job_id'])['direction'] == 'to_hermes'
    # to_chatgpt не требует ключа; to_hermes требует
    try:
        q.put_message('no key', direction='to_hermes')
        assert False, 'должен быть ValueError'
    except ValueError:
        pass
    # bad direction
    try:
        q.put_message('x', direction='sideways')
        assert False
    except ValueError:
        pass
    q._conn.close()


def test_no_webhook_loop_for_to_hermes(tmp_path):
    """bridge_put_message (to_hermes) НЕ порождает событие hermes.message.created.

    В ревизии событие порождает только emit_test_event (CLI put -> to_chatgpt).
    to_hermes создаётся без emit; проверим на уровне очереди, что после
    put_message(to_hermes) в outbox НЕТ новых доставок, а после CLI-пути есть.
    """
    import bridge_server as bs
    bs.DB_PATH = str(tmp_path / 'bridge.db')
    bs.OWNER_USER = 'owner'
    bs.OWNER_PASS = 'synthetic-password'
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    app = bs.build_app(f'http://127.0.0.1:{port}')
    bridge = app.state.bridge
    try:
        q = bridge.queue
        store = bridge.store
        import time as _t
        store.save_sub({
            'id': 'sub_loopcheck', 'owner': 'owner',
            'event': 'hermes.message.created',
            'arguments': {'queue': 'test'},
            'callback_url': 'https://connectors.api.openai.com/webhook/mcp-events/loopcheck',
            'secret': 'test-secret-loop', 'expires_at': _t.time() + 3600,
            'active': True, 'generation': 1,
        })
        # CLI-путь (to_chatgpt): put_message + emit -> событие есть
        j_gpt = q.put_message('cli-like', direction='to_chatgpt')
        n = bridge.emit_test_event(j_gpt['job_id'])
        assert n >= 1
        before = len(store.pending_deliveries())
        # to_hermes создаётся БЕЗ emit (как bridge_put_message)
        j_her = q.put_message('tool-like', direction='to_hermes', idempotency_key='k-loop-1')
        after = len(store.pending_deliveries())
        assert after == before, 'to_hermes НЕ должен добавлять события в outbox'
        assert j_her['direction'] == 'to_hermes'
    finally:
        bridge.queue._conn.close()
        bridge.store.conn.close()


def test_idempotency_dedup_after_lost_response(tmp_path):
    """Повтор с тем же ключом и текстом -> исходный job_id (после 'потерянного
    ответа' consumer повторяет запрос и получает ту же задачу)."""
    q = fresh_queue(tmp_path)
    r1 = q.put_message('same payload', direction='to_hermes', idempotency_key='dedup-1')
    r2 = q.put_message('same payload', direction='to_hermes', idempotency_key='dedup-1')
    assert r1['job_id'] == r2['job_id']
    assert r2.get('idempotent') is True
    # другой текст с тем же ключом -> conflict
    r3 = q.put_message('OTHER payload', direction='to_hermes', idempotency_key='dedup-1')
    assert r3['status'] == 'conflict'
    assert r3['job_id'] == r1['job_id']
    # свежий ключ -> новый job
    r4 = q.put_message('same payload', direction='to_hermes', idempotency_key='dedup-2')
    assert r4['job_id'] != r1['job_id']
    assert r4['status'] == 'pending'
    q._conn.close()


def test_concurrent_claim_exactly_one_winner(tmp_path):
    """Два соединения одновременно claim'ят одну задачу — ровно один победитель."""
    db = str(tmp_path / 'bridge.db')
    q0 = bq.Queue(db)
    job = q0.put_message('race', direction='to_hermes', idempotency_key='race-1')
    q0._conn.close()

    results = []

    def worker(name):
        q = bq.Queue(db)
        try:
            got = q.claim_next_to_hermes(worker=name, lease_s=60)
            results.append((name, got['job_id'] if got else None))
        finally:
            q._conn.close()

    t1 = threading.Thread(target=worker, args=('w1',))
    t2 = threading.Thread(target=worker, args=('w2',))
    t1.start(); t2.start(); t1.join(); t2.join()

    winners = [r for r in results if r[1] is not None]
    assert len(winners) == 1, f'ожидался ровно 1 победитель, got {results}'
    qc = bq.Queue(db)
    row = qc.get_message(job['job_id'])
    assert row['claimed_by'] == winners[0][0]
    assert row['lease_until_epoch'] is not None
    qc._conn.close()


def test_lease_return_and_reclaim(tmp_path):
    """Истёкший lease -> повторный захват другим воркером; активный lease не отбирается."""
    q = fresh_queue(tmp_path)
    job = q.put_message('lease', direction='to_hermes', idempotency_key='lease-1')
    w1 = q.claim_next_to_hermes(worker='w1', lease_s=0.1)  # короткий lease
    assert w1 is not None and w1['job_id'] == job['job_id']
    # активный lease: второй воркер НЕ получает задачу
    w2 = q.claim_next_to_hermes(worker='w2', lease_s=60)
    assert w2 is None
    # ждём истечения и проверяем повторный захват тем же/другим воркером
    import time
    time.sleep(0.3)
    w3 = q.claim_next_to_hermes(worker='w3', lease_s=60)
    assert w3 is not None and w3['job_id'] == job['job_id']
    assert q.get_message(job['job_id'])['claimed_by'] == 'w3'
    q._conn.close()


def test_cannot_claim_to_chatgpt(tmp_path):
    q = fresh_queue(tmp_path)
    q.put_message('to gpt', direction='to_chatgpt')
    q.put_message('to hermes', direction='to_hermes', idempotency_key='c-1')
    got = q.claim_next_to_hermes(worker='w', lease_s=60)
    assert got is not None and got['direction'] == 'to_hermes'
    # второй claim -> None (to_chatgpt недоступен claim)
    assert q.claim_next_to_hermes(worker='w2', lease_s=60) is None
    q._conn.close()


def test_migration_old_schema(tmp_path):
    """Старая БД (без новых колонок) открывается с миграцией; данные целы."""
    db = str(tmp_path / 'bridge.db')
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE jobs (job_id TEXT PRIMARY KEY, text TEXT NOT NULL,'
                 ' created_at_utc TEXT NOT NULL, status TEXT NOT NULL,'
                 ' reply TEXT, updated_at_utc TEXT)')
    conn.execute("INSERT INTO jobs VALUES ('job_legacy','legacy text',"
                 "'2026-10-01T00:00:00Z','pending',NULL,'2026-10-01T00:00:00Z')")
    conn.commit(); conn.close()

    q = bq.Queue(db)
    row = q.get_message('job_legacy')
    assert row['text'] == 'legacy text'
    assert row['direction'] == 'to_chatgpt', 'старые задачи по умолчанию to_chatgpt'
    assert row['idempotency_key'] is None
    assert row['claimed_by'] is None
    # новые операции работают на мигрированной БД
    j2 = q.put_message('new', direction='to_hermes', idempotency_key='mig-1')
    assert j2['status'] == 'pending'
    assert q.claim_next_to_hermes(worker='w', lease_s=60)['job_id'] == j2['job_id']
    q._conn.close()


def test_rate_limit_per_owner(tmp_path):
    """Простой per-owner rate limit в памяти (без внешней инфраструктуры)."""
    q = fresh_queue(tmp_path)
    under = bq.RATE_LIMIT_PER_MIN
    ok = 0
    limited = None
    for i in range(under + 5):
        r = q.put_message(f'flood {i}', direction='to_hermes',
                          idempotency_key=f'flood-{i}')
        if r.get('status') == 'rate_limited':
            limited = r
            break
        ok += 1
    assert limited is not None, 'лимит должен сработать'
    assert ok <= under
    q._conn.close()


# --- жизненный цикл to_hermes: complete/fail (ревизия) ---

def _claim_one(q, worker='w'):
    got = q.claim_next_to_hermes(worker=worker, lease_s=300)
    assert got is not None
    return got


def test_complete_success_creates_report_once(tmp_path):
    q = fresh_queue(tmp_path)
    job = q.put_message('do it', direction='to_hermes', idempotency_key='lc-1')
    got = _claim_one(q, 'w1')
    r = q.complete_to_hermes(got['job_id'], 'w1', report_text='RESULT ok')
    assert r['status'] == 'completed' and r['created'] is True
    assert r['report_job_id'] and r['report_job_id'] != got['job_id']
    # задача в терминальном статусе, lease снят
    row = q.get_message(got['job_id'])
    assert row['status'] == 'completed'
    assert row['claimed_by'] is None and row['lease_until_epoch'] is None
    # отчёт to_chatgpt с parent_job_id
    rep = q.get_message(r['report_job_id'])
    assert rep['direction'] == 'to_chatgpt'
    assert rep['parent_job_id'] == got['job_id']
    assert rep['text'] == 'RESULT ok'
    # повтор complete (потеря ответа) -> тот же report_job_id, created=False
    r2 = q.complete_to_hermes(got['job_id'], 'w1', report_text='RESULT ok')
    assert r2['status'] == 'completed' and r2['created'] is False
    assert r2['report_job_id'] == r['report_job_id']
    # ровно один отчёт
    rows = [x for x in q.list_jobs() if x['job_id'] == r['report_job_id']]
    assert len(rows) == 1
    q._conn.close()


def test_fail_terminal_and_never_reclaimed(tmp_path):
    q = fresh_queue(tmp_path)
    job = q.put_message('do it', direction='to_hermes', idempotency_key='lc-f1')
    got = _claim_one(q, 'w1')
    r = q.fail_to_hermes(got['job_id'], 'w1', reason='temp error')
    assert r['status'] == 'failed'
    row = q.get_message(got['job_id'])
    assert row['status'] == 'failed' and row['claimed_by'] is None
    # повторный fail идемпотентен
    r2 = q.fail_to_hermes(got['job_id'], 'w1')
    assert r2['status'] == 'failed' and r2.get('idempotent') is True
    # failed никогда не выбирается claim
    assert q.claim_next_to_hermes(worker='w2', lease_s=60) is None
    q._conn.close()


def test_complete_denied_for_to_chatgpt_and_wrong_owner(tmp_path):
    q = fresh_queue(tmp_path)
    jg = q.put_message('to gpt', direction='to_chatgpt')
    r = q.complete_to_hermes(jg['job_id'], 'w1')
    assert r['status'] == 'conflict'
    # to_hermes: чужой воркер не может завершить
    jh = q.put_message('to hermes', direction='to_hermes', idempotency_key='lc-o1')
    _claim_one(q, 'w1')
    r2 = q.complete_to_hermes(jh['job_id'], 'intruder')
    assert r2['status'] == 'not_owner'
    assert q.get_message(jh['job_id'])['status'] == 'pending'
    q._conn.close()


def test_complete_after_lease_expiry_denied(tmp_path):
    """Истёкший lease: старый воркер завершить не может."""
    import time
    q = fresh_queue(tmp_path)
    job = q.put_message('do it', direction='to_hermes', idempotency_key='lc-e1')
    got = q.claim_next_to_hermes(worker='w1', lease_s=0.1)
    assert got is not None
    time.sleep(0.3)
    r = q.complete_to_hermes(got['job_id'], 'w1', report_text='late')
    assert r['status'] == 'lease_expired'
    assert q.get_message(got['job_id'])['status'] == 'pending'
    # другой воркер может перезахватить и завершить
    got2 = q.claim_next_to_hermes(worker='w2', lease_s=60)
    assert got2 is not None and got2['job_id'] == got['job_id']
    r2 = q.complete_to_hermes(got2['job_id'], 'w2', report_text='done by w2')
    assert r2['status'] == 'completed'
    q._conn.close()


def test_concurrent_complete_one_winner(tmp_path):
    """Два воркера конкурируют за complete одной задачи — ровно один отчёт."""
    db = str(tmp_path / 'bridge.db')
    q0 = bq.Queue(db)
    job = q0.put_message('race', direction='to_hermes', idempotency_key='lc-race')
    q0.claim_next_to_hermes(worker='w_holder', lease_s=300)
    q0._conn.close()
    # теперь один воркер — holder, второй — посторонний
    import threading
    results = []

    def worker(name, holder):
        q = bq.Queue(db)
        try:
            if holder:
                r = q.complete_to_hermes(job['job_id'], 'w_holder',
                                         report_text='race done')
            else:
                r = q.complete_to_hermes(job['job_id'], 'w_intruder')
            results.append((name, r['status']))
        finally:
            q._conn.close()

    t1 = threading.Thread(target=worker, args=('h', True))
    t2 = threading.Thread(target=worker, args=('i', False))
    t1.start(); t2.start(); t1.join(); t2.join()
    statuses = sorted(r[1] for r in results)
    assert statuses == ['completed', 'not_owner'], results
    qc = bq.Queue(db)
    reps = [x for x in qc.list_jobs(100) if x['job_id'] != job['job_id']
            and qc.get_message(x['job_id'])['parent_job_id'] == job['job_id']]
    assert len(reps) == 1, 'ровно один отчёт'
    qc._conn.close()


def test_attempts_limit_after_release_retry(tmp_path):
    """release/retry: число попыток ограничено MAX_ATTEMPTS."""
    q = fresh_queue(tmp_path)
    job = q.put_message('retry me', direction='to_hermes', idempotency_key='lc-r1')
    for i in range(bq.MAX_ATTEMPTS):
        got = q.claim_next_to_hermes(worker=f'w{i}', lease_s=60)
        assert got is not None and got['job_id'] == job['job_id']
        q.release_claim(got['job_id'], f'w{i}')
    # попытки исчерпаны: задача больше не выбирается
    assert q.claim_next_to_hermes(worker='last', lease_s=60) is None
    row = q.get_message(job['job_id'])
    assert row['attempts'] >= bq.MAX_ATTEMPTS
    q._conn.close()