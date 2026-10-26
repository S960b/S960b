"""Stable task identity, single active turn and truthful board states."""
import sqlite3
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge_queue import Queue
from task_view import fetch_tasks, project_task, PAGE


def test_roundtrip_one_row(tmp_path):
    q = Queue(str(tmp_path / 'db'))
    a = q.put_message('First', title='Мост')
    task = a['task_id']
    assert q.claim_for_chat(a['job_id'], 'chatgpt_worker')['task_id'] == task
    q.put_claimed_reply(a['job_id'], 'chatgpt_worker', 'Next')
    b = q.put_message('Run', task_id=task, direction='to_hermes', idempotency_key='k')
    assert b['task_id'] == task and b['job_id'] != a['job_id']
    busy = q.put_message('parallel', task_id=task)
    assert busy['status'] == 'task_busy'
    retry = q.put_message('Run', task_id=task, direction='to_hermes', idempotency_key='k')
    assert retry['idempotent'] and retry['job_id'] == b['job_id']
    q.claim_next_to_hermes('mt')
    done = q.complete_to_hermes(b['job_id'], 'mt', 'Result')
    assert q.get_message(done['report_job_id'])['task_id'] == task
    tasks = fetch_tasks(q._conn)
    assert len(tasks) == 1 and tasks[0]['title'] == 'Мост'
    assert tasks[0]['arrow'] == 'ChatGPT ← MT'
    assert sum(m['text'] == 'Result' for m in tasks[0]['history']) == 1
    assert tasks[0]['people']['ChatGPT']['color'] == 'yellow'
    assert tasks[0]['people']['MT']['color'] == 'green'
    q._conn.close()


def test_migration_preserves_unrelated_and_groups_parent(tmp_path):
    path = str(tmp_path / 'db')
    q = Queue(path)
    a = q.put_message('one')
    b = q.put_message('two')
    q._conn.execute('UPDATE jobs SET parent_job_id=? WHERE job_id=?', (a['job_id'], b['job_id']))
    q._conn.commit(); q._conn.close()
    q = Queue(path)
    assert q.get_message(a['job_id'])['task_id'] == q.get_message(b['job_id'])['task_id']
    c = q.put_message('three')
    assert c['task_id'] != a['task_id']
    assert len(fetch_tasks(q._conn)) == 2
    assert q._conn.execute('SELECT count(*) FROM jobs').fetchone()[0] == 3
    q._conn.close()


def test_no_unknown_or_foreign_task(tmp_path):
    q = Queue(str(tmp_path / 'db'))
    a = q.put_message('x', owner='alice')
    assert q.put_message('x', task_id='missing')['status'] == 'not_found'
    assert q.put_message('x', task_id=a['task_id'], owner='bob')['status'] == 'not_found'
    q._conn.close()


def test_concurrent_followups_single_winner(tmp_path):
    path = str(tmp_path / 'db')
    q = Queue(path)
    a = q.put_message('x')
    q.put_reply(a['job_id'], 'ok'); q._conn.close()
    def send(n):
        conn = Queue(path)
        try:
            return conn.put_message(str(n), task_id=a['task_id'])['status']
        finally:
            conn._conn.close()
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(send, [1, 2]))
    assert sorted(results) == ['pending', 'task_busy']


def test_timeout_and_heartbeat(tmp_path):
    q = Queue(str(tmp_path / 'db'))
    a = q.put_message('x')
    row = q.get_message(a['job_id'])
    from datetime import datetime
    start = datetime.fromisoformat(row['updated_at_utc'].replace('Z', '+00:00')).timestamp()
    assert project_task([row], 'x', now=start+599)['people']['ChatGPT']['color'] == 'yellow'
    assert project_task([row], 'x', now=start+600)['people']['ChatGPT']['color'] == 'red'
    q.claim_for_chat(a['job_id'], 'chatgpt_worker')
    task = fetch_tasks(q._conn)[0]
    assert task['people']['ChatGPT']['text'] == 'в работе'
    q.put_claimed_reply(a['job_id'], 'chatgpt_worker', '<script>evil</script>')
    task = fetch_tasks(q._conn)[0]
    assert task['people']['ChatGPT']['color'] == 'green'
    assert task['people']['MT']['color'] == 'grey'
    assert task['arrow'] == 'ChatGPT → MT'
    assert 'innerHTML' not in PAGE and 'textContent' in PAGE
    q._conn.close()


@pytest.mark.parametrize('status', ['failed', 'completed'])
def test_closed_job_not_claimed(tmp_path, status):
    q = Queue(str(tmp_path / 'db'))
    a = q.put_message('x')
    q._conn.execute('UPDATE jobs SET status=? WHERE job_id=?', (status, a['job_id']))
    q._conn.commit()
    result = q.claim_for_chat(a['job_id'], 'chatgpt_worker')
    assert result['status'] == status and 'text' not in result
    q._conn.close()
