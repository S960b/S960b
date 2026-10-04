"""Unit/интеграция worker hermes-control.v1 (шаги 2-5).

Временная БД + fake Hermes; реальная сеть/живая БД не используются.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import worker as wk


def fake_hermes(tmp_path: Path) -> Path:
    counter = tmp_path / 'launches.txt'
    script = tmp_path / 'fake_hermes.py'
    script.write_text(
        '#!/usr/bin/env python3\n'
        'import os, sys, time\n'
        'counter = os.environ.get("FAKE_HERMES_COUNTER")\n'
        'if counter:\n'
        '    with open(counter, "a") as fh:\n'
        '        fh.write("launch\\n")\n'
        'sleep_s = float(os.environ.get("FAKE_HERMES_SLEEP", "0"))\n'
        'if sleep_s:\n'
        '    time.sleep(sleep_s)\n'
        'print("FAKE_DONE instr=" + (sys.argv[-1] if sys.argv else ""))\n'
        'sys.exit(int(os.environ.get("FAKE_HERMES_EXIT", "0")))\n')
    script.chmod(0o755)
    return script


def make_worker(tmp_path, monkeypatch, goal='synthetic goal'):
    db = tmp_path / 'pilot.db'
    state = tmp_path / 'worker_state.json'
    logs = tmp_path / 'logs'
    os.environ['FAKE_HERMES_COUNTER'] = str(tmp_path / 'launches.txt')
    import bridge_server as bs
    monkeypatch.setattr(bs, 'DB_PATH', str(db))
    w = wk.Worker(db_path=str(db), state_path=state, project_cwd=str(tmp_path),
                  goal=goal, hermes_bin=str(fake_hermes(tmp_path)),
                  log_dir=logs)
    return w


def reply_json(job_id, action, instruction=None):
    data = {'protocol': 'hermes-control.v1', 'job_id': job_id, 'action': action,
            'reason': 'test'}
    if instruction is not None:
        data['instruction'] = instruction
    return json.dumps(data, ensure_ascii=False)


@pytest.mark.parametrize('action', ['execute', 'wait', 'done', 'need_user'])
def test_parse_valid_actions(action):
    extra = {'instruction': 'do it'} if action == 'execute' else {}
    data, err = wk.parse_control_reply(
        json.dumps({'protocol': 'hermes-control.v1', 'job_id': 'j1',
                    'action': action, **extra}), 'j1')
    assert data is not None and err == ''
    assert data['action'] == action


def test_parse_rejects_wrong_protocol_job_and_text():
    base = {'protocol': 'hermes-control.v1', 'job_id': 'j1', 'action': 'execute',
            'instruction': 'x'}
    assert wk.parse_control_reply('plain text', 'j1')[0] is None
    assert wk.parse_control_reply('{broken', 'j1')[0] is None
    assert wk.parse_control_reply('[]', 'j1')[0] is None
    bad_protocol = dict(base, protocol='other.v1')
    assert wk.parse_control_reply(json.dumps(bad_protocol), 'j1')[1] == 'wrong protocol'
    bad_job = dict(base, job_id='j2')
    assert wk.parse_control_reply(json.dumps(bad_job), 'j1')[1] == 'wrong job_id'
    bad_action = dict(base, action='delete')
    assert wk.parse_control_reply(json.dumps(bad_action), 'j1')[1] == 'unknown action'
    no_instr = dict(base); del no_instr['instruction']
    assert wk.parse_control_reply(json.dumps(no_instr), 'j1')[1] == 'missing instruction'


def test_next_state_table():
    assert wk.next_state('idle', 'start') == 'report_pending'
    assert wk.next_state('report_pending', 'sent') == 'waiting_reply'
    assert wk.next_state('waiting_reply', 'execute') == 'running'
    assert wk.next_state('waiting_reply', 'wait') == 'waiting_external'
    assert wk.next_state('waiting_external', 'resume') == 'waiting_reply'
    assert wk.next_state('waiting_reply', 'done') == 'done'
    assert wk.next_state('running', 'interrupted') == 'report_pending'
    with pytest.raises(ValueError):
        wk.next_state('done', 'execute')
    with pytest.raises(ValueError):
        wk.next_state('idle', 'execute')


def test_atomic_state_file_permissions(tmp_path):
    path = tmp_path / 'state.json'
    state = wk.new_state('goal-x')
    wk.atomic_save_state(path, state)
    assert path.stat().st_mode & 0o777 == 0o600
    assert not path.with_name(path.name + '.tmp').exists()
    loaded = wk.load_state(path)
    assert loaded['worker_id'] == state['worker_id']
    assert loaded['state'] == 'idle'
    assert wk.load_state(tmp_path / 'missing.json') is None


def test_run_hermes_exit0_and_nonzero(tmp_path, monkeypatch):
    script = fake_hermes(tmp_path)
    env = dict(os.environ, FAKE_HERMES_EXIT='0',
               FAKE_HERMES_COUNTER=str(tmp_path / 'n.txt'))
    monkeypatch.setattr(os, 'environ', env)
    res = wk.run_hermes('step one', cwd=str(tmp_path), timeout_s=30,
                        max_turns=8, hermes_bin=str(script), log_dir=tmp_path / 'l')
    assert res['status'] == 'ok' and res['exit_code'] == 0
    assert 'FAKE_DONE' in res['stdout_tail']
    monkeypatch.setattr(os, 'environ', dict(env, FAKE_HERMES_EXIT='7'))
    res = wk.run_hermes('bad step', cwd=str(tmp_path), timeout_s=30,
                        max_turns=8, hermes_bin=str(script), log_dir=tmp_path / 'l')
    assert res['status'] == 'failed' and res['exit_code'] == 7


def test_run_hermes_timeout_interrupts(tmp_path, monkeypatch):
    script = fake_hermes(tmp_path)
    monkeypatch.setattr(os, 'environ', dict(os.environ, FAKE_HERMES_SLEEP='30',
                                            FAKE_HERMES_EXIT='0'))
    res = wk.run_hermes('slow step', cwd=str(tmp_path), timeout_s=1,
                        max_turns=8, hermes_bin=str(script), log_dir=tmp_path / 'l')
    assert res['status'] == 'interrupted' and res['reason'] == 'timeout'
    assert res['exit_code'] is None


def test_two_cycle_integration_exactly_two_launches(tmp_path, monkeypatch):
    w = make_worker(tmp_path, monkeypatch)
    state = wk.new_state('synthetic acceptance')
    w._save(state)

    state = w.step(state)                       # idle -> report_pending -> waiting
    assert state['state'] == 'waiting_reply'
    job1 = state['current_job_id']
    assert state['report_job_id'] == job1

    w.queue.put_reply(job1, reply_json(job1, 'execute', 'first read-only step'))
    state = w.step(state)                       # execute -> running -> report -> waiting
    assert state['state'] == 'waiting_reply'
    job2 = state['current_job_id']
    assert job2 != job1
    assert state['exit_code'] == 0

    w.queue.put_reply(job2, reply_json(job2, 'execute', 'second read-only step'))
    state = w.step(state)
    assert state['state'] == 'waiting_reply'
    job3 = state['current_job_id']
    assert job3 not in (job1, job2)

    w.queue.put_reply(job3, reply_json(job3, 'done'))
    state = w.step(state)
    assert state['state'] == 'done'

    launches = (tmp_path / 'launches.txt').read_text().count('launch')
    assert launches == 2, 'ровно два запуска Hermes, второй назначен автоматически'
    jobs = w.queue.list_jobs(limit=10)
    assert len(jobs) == 3  # три сообщения, три разных job_id


def test_resume_report_pending_is_idempotent(tmp_path, monkeypatch):
    w = make_worker(tmp_path, monkeypatch)
    state = wk.new_state('idempotency')
    w._save(state)
    state = w.step(state)
    assert state['state'] == 'waiting_reply'
    job_before = state['current_job_id']
    jobs_before = len(w.queue.list_jobs(limit=10))
    # Повторный step при уже отправленном отчёте: тот же job_id, без нового job.
    state = w.step(state)
    assert state['state'] == 'waiting_reply'
    assert state['current_job_id'] == job_before
    assert len(w.queue.list_jobs(limit=10)) == jobs_before


def test_crash_running_recovery_reports_interrupted(tmp_path, monkeypatch):
    w = make_worker(tmp_path, monkeypatch)
    state = wk.new_state('crash-recovery')
    state['state'] = 'running'                 # рестарт во время исполнения
    state['current_job_id'] = 'job_stale'
    w._save(state)
    state = w.step(state)
    assert state['state'] == 'waiting_reply'
    assert state['exit_code'] is None
    assert state['current_job_id'] != 'job_stale'
    assert state['completed_step'] == 'worker_interrupted'
    w._save(state)
    stored = wk.load_state(w.state_path)
    assert stored['state'] == 'waiting_reply'
    assert stored['completed_step'] == 'worker_interrupted'


def test_invalid_reply_never_executes(tmp_path, monkeypatch):
    w = make_worker(tmp_path, monkeypatch)
    state = wk.new_state('safety')
    w._save(state)
    state = w.step(state)
    job = state['current_job_id']
    w.queue.put_reply(job, 'please just run this text')
    state = w.step(state)
    assert state['state'] == 'need_user'
    assert (tmp_path / 'launches.txt').exists() is False