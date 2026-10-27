"""Delete only the confirmed task; lazy history and scoped notification cleanup."""
import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import taskboard
from bridge_queue import Queue
from task_view import fetch_tasks, fetch_task_history


@pytest.fixture
def board(tmp_path, monkeypatch):
    db = tmp_path / 'board.db'
    monkeypatch.setattr(taskboard, 'DB', db)
    q = Queue(str(db))
    yield q
    q._conn.close()


def post(body, origin='http://localhost:8787', content_type='application/json'):
    handler = object.__new__(taskboard.H)
    data = json.dumps(body).encode()
    handler.path = '/api/tasks/delete'
    handler.headers = {'Host': 'localhost:8787', 'Origin': origin,
                       'Content-Type': content_type, 'Content-Length': str(len(data))}
    handler.rfile = io.BytesIO(data)
    response = []
    handler._send = lambda code, data: response.append((code, json.loads(data)))
    handler.do_POST()
    return response[0]


def test_confirmed_delete_removes_full_task_only(board):
    first = board.put_message('first', title='delete me')
    board.put_reply(first['job_id'], 'reply')
    second = board.put_message('followup', task_id=first['task_id'])
    keep = board.put_message('keep')
    code, result = post({'task_id': first['task_id'], 'confirmed': True})
    assert code == 200 and result['deleted_jobs'] == 2
    assert board.get_message(first['job_id']) is None
    assert board.get_message(second['job_id']) is None
    assert board.get_message(keep['job_id'])['text'] == 'keep'
    assert fetch_task_history(board._conn, first['task_id']) is None
    assert [t['task_id'] for t in fetch_tasks(board._conn)] == [keep['task_id']]
    # In-flight workers cannot append into the deleted conversation.
    assert board.put_message('late', task_id=first['task_id'])['status'] == 'not_found'
    assert board.put_reply(second['job_id'], 'late reply')['status'] == 'not_found'


@pytest.mark.parametrize('confirmation', [False, None, 1, 'true'])
def test_no_delete_without_explicit_confirmation(board, confirmation):
    task = board.put_message('stay')
    code, result = post({'task_id': task['task_id'], 'confirmed': confirmation})
    assert code == 400 and not result['ok']
    assert board.get_message(task['job_id']) is not None


def test_delete_unknown_or_sql_payload_does_not_touch_other_tasks(board):
    task = board.put_message('stay')
    for task_id in ['missing', "' OR 1=1 --", '', None]:
        code, result = post({'task_id': task_id, 'confirmed': True})
        assert code == 400 and not result['ok']
    assert board.get_message(task['job_id']) is not None


def test_delete_scopes_delivery_cleanup(board):
    task = board.put_message('remove')
    keep = board.put_message('keep')
    c = board._conn
    c.execute('CREATE TABLE deliveries (payload TEXT)')
    payloads = [{'job_id': task['job_id']}, {'task_id': task['task_id']},
                {'job_id': keep['job_id']}]
    c.executemany('INSERT INTO deliveries VALUES (?)', [(json.dumps(p),) for p in payloads])
    c.execute("INSERT INTO deliveries VALUES ('invalid json')")
    c.commit()
    assert taskboard.delete_task(task['task_id'], True)['ok']
    remaining = [r[0] for r in c.execute('SELECT payload FROM deliveries')]
    assert remaining == [json.dumps(payloads[2]), 'invalid json']


def test_failure_rolls_back_entire_deletion(board):
    task = board.put_message('remove')
    c = board._conn
    c.execute('CREATE TABLE deliveries (payload TEXT)')
    c.execute('INSERT INTO deliveries VALUES (?)', (json.dumps({'job_id': task['job_id']}),))
    c.execute("CREATE TRIGGER stop_delete BEFORE DELETE ON jobs BEGIN SELECT RAISE(ABORT,'test'); END")
    c.commit()
    code, result = post({'task_id': task['task_id'], 'confirmed': True})
    assert code == 500 and not result['ok']
    assert c.execute('SELECT count(*) FROM deliveries').fetchone()[0] == 1
    assert board.get_message(task['job_id']) is not None
    assert fetch_task_history(c, task['task_id']) is not None


@pytest.mark.parametrize('origin,content_type', [('https://evil.example', 'application/json'),
                                                ('http://localhost:8787', 'text/plain')])
def test_cross_origin_and_simple_forms_rejected(board, origin, content_type):
    task = board.put_message('stay')
    code, result = post({'task_id': task['task_id'], 'confirmed': True}, origin, content_type)
    assert code == 403 and not result['ok']
    assert board.get_message(task['job_id']) is not None


def test_lazy_list_excludes_history_bodies(board):
    task = board.put_message('BODY_SECRET_MARKER', title='Title')
    board.put_reply(task['job_id'], 'REPLY_SECRET_MARKER')
    board.put_message('followup', task_id=task['task_id'])
    tasks = fetch_tasks(board._conn, include_history=False)
    assert len(tasks) == 1 and tasks[0]['message_count'] == 3
    assert 'history' not in tasks[0]
    assert 'SECRET_MARKER' not in json.dumps(tasks)
    assert len(fetch_task_history(board._conn, task['task_id'])['history']) == 3
