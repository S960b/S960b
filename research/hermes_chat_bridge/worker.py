#!/usr/bin/env python3
"""Локальный worker протокола hermes-control.v1 (один процесс, одна пилотная БД).

Цикл: worker создаёт JSON-сообщение (protocol, goal, completed_step, report)
через существующую очередь моста и событие test; ждёт reply именно своего
job_id; ответ ChatGPT (protocol, job_id, action, instruction, reason)
исполняется ТОЛЬКО при action=execute и совпадении job_id. wait/done/need_user
и обычный текст модель не запускают. Один worker, последовательно, flock.

Состояния: idle -> report_pending -> waiting_reply -> running -> report_pending;
терминальные done / need_user; при сбое running после рестарта -> interrupted
-> report_pending. Состояние — отдельный JSON (0600, атомарная запись).
"""
import argparse
import fcntl
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROTOCOL = 'hermes-control.v1'
ALLOWED_ACTIONS = frozenset({'execute', 'wait', 'done', 'need_user'})
STATE_SCHEMA = 1
POLL_S = float(os.environ.get('WORKER_POLL_S', '2.0'))
MAX_ITERATIONS = int(os.environ.get('WORKER_MAX_ITERATIONS', '10000'))
HERMES_TIMEOUT_S = float(os.environ.get('WORKER_HERMES_TIMEOUT_S', '600'))
HERMES_MAX_TURNS = int(os.environ.get('WORKER_HERMES_MAX_TURNS', '8'))
OUTPUT_TAIL_CHARS = 2000


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def instruction_hash(instruction: str) -> str:
    return hashlib.sha256(instruction.encode('utf-8')).hexdigest()


def build_report_message(goal: str, completed_step: str, report: dict) -> dict:
    return {'protocol': PROTOCOL, 'goal': goal,
            'completed_step': completed_step, 'report': report}


def parse_control_reply(text: str, current_job_id: str) -> tuple[dict | None, str]:
    """Валиден только JSON-ответ: protocol=hermes-control.v1, job_id совпадает,
    action из allowlist; для execute обязателен непустой instruction."""
    if not isinstance(text, str) or not text.strip():
        return None, 'empty reply'
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None, 'not json'
    if not isinstance(data, dict):
        return None, 'not object'
    if data.get('protocol') != PROTOCOL:
        return None, 'wrong protocol'
    if data.get('job_id') != current_job_id:
        return None, 'wrong job_id'
    action = data.get('action')
    if action not in ALLOWED_ACTIONS:
        return None, 'unknown action'
    if action == 'execute' and (not isinstance(data.get('instruction'), str)
                                or not data['instruction'].strip()):
        return None, 'missing instruction'
    return data, ''


def next_state(state: str, action: str | None = None) -> str:
    """Таблица переходов: возвращает новое состояние или поднимает ValueError."""
    transitions = {
        'idle': {'start': 'report_pending'},
        'report_pending': {'sent': 'waiting_reply'},
        'waiting_reply': {'execute': 'running', 'wait': 'waiting_external',
                          'done': 'done', 'need_user': 'need_user'},
        'waiting_external': {'resume': 'waiting_reply'},
        'running': {'finished': 'report_pending', 'interrupted': 'report_pending'},
        'interrupted': {'reported': 'report_pending'},
        'done': {}, 'need_user': {},
    }
    if state not in transitions:
        raise ValueError(f'unknown state {state!r}')
    if action is None:
        return state
    if action not in transitions[state]:
        raise ValueError(f'transition {state!r} + {action!r} not allowed')
    return transitions[state][action]


def atomic_save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def load_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    with open(path, 'r', encoding='utf-8') as fh:
        state = json.load(fh)
    if state.get('schema') != STATE_SCHEMA:
        raise ValueError('unsupported worker state schema')
    return state


def new_state(goal: str, worker_id: str | None = None) -> dict:
    return {'schema': STATE_SCHEMA,
            'worker_id': worker_id or ('wrk_' + secrets.token_hex(8)),
            'goal': goal, 'state': 'idle', 'current_job_id': None,
            'instruction_hash': None, 'started_at': utc_now_iso(),
            'finished_at': None, 'exit_code': None,
            'report_job_id': None, 'attempt': 1,
            'completed_step': 'worker_started', 'report': {}}


def run_hermes(instruction: str, *, cwd: str, timeout_s: float,
               max_turns: int, hermes_bin: str, log_dir: Path) -> dict:
    """Один Hermes turn: shell=False, без PTY, stdin закрыт, текущее окружение.
    Успех только exit_code=0 и завершившийся процесс; timeout -> interrupted."""
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = str(int(time.time()))
    out_path = log_dir / f'run_{stamp}.out.log'
    err_path = log_dir / f'run_{stamp}.err.log'
    argv = [hermes_bin, 'chat', '-Q', '--max-turns', str(max_turns),
            '-q', instruction]
    started = time.time()
    timed_out = False
    try:
        with open(out_path, 'wb') as fo, open(err_path, 'wb') as fe:
            proc = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                                  stdout=fo, stderr=fe, timeout=timeout_s,
                                  env=os.environ.copy())
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = None
        proc = exc.process if hasattr(exc, 'process') else None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except (subprocess.TimeoutExpired, AttributeError):
                try:
                    proc.kill()
                    proc.wait(timeout=3)
                except (subprocess.TimeoutExpired, AttributeError):
                    pass
    duration_s = round(time.time() - started, 2)

    def tail(path: Path) -> str:
        data = path.read_bytes()[-OUTPUT_TAIL_CHARS:]
        return data.decode('utf-8', errors='replace')

    result = {'exit_code': exit_code, 'duration_s': duration_s,
              'stdout_tail': tail(out_path), 'stderr_tail': tail(err_path),
              'stdout_log': str(out_path), 'stderr_log': str(err_path),
              'status': ('interrupted' if timed_out
                         else ('ok' if exit_code == 0 else 'failed')),
              'reason': ('timeout' if timed_out else None)}
    return result


def _emit_event(job_id: str, db_path: str) -> int:
    """Тот же механизм, что bridge-cli put: BridgeApp + emit_test_event.
    Живой сервер моста сам доставит webhook из deliveries."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import bridge_server as bs
    bridge = bs.BridgeApp(bs.BRIDGE_HOST and 'http://127.0.0.1:8765')
    return bridge.emit_test_event(job_id)


class Worker:
    def __init__(self, db_path: str, state_path: Path, project_cwd: str,
                 goal: str, hermes_bin: str, log_dir: Path):
        self.db_path = db_path
        self.state_path = state_path
        self.project_cwd = project_cwd
        self.goal = goal
        self.hermes_bin = hermes_bin
        self.log_dir = Path(log_dir)
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from bridge_queue import Queue
        self.queue = Queue(db_path)

    # --- помощники ---
    def _save(self, state: dict) -> None:
        atomic_save_state(self.state_path, state)

    def _create_report(self, state: dict, completed_step: str, report: dict) -> dict:
        message = build_report_message(state['goal'], completed_step, report)
        job = self.queue.put_message(json.dumps(message, ensure_ascii=False))
        state['report_job_id'] = job['job_id']
        state['current_job_id'] = job['job_id']
        state['completed_step'] = completed_step
        state['report'] = report
        _emit_event(job['job_id'], self.db_path)
        return state

    def _ensure_emitted(self, state: dict) -> dict:
        _emit_event(state['current_job_id'], self.db_path)
        return state

    # --- один шаг цикла ---
    def step(self, state: dict) -> dict:
        s = state['state']
        if s in ('done', 'need_user'):
            return state
        if s == 'idle':
            state = self._create_report(state, 'worker_started',
                                        {'text': 'started'})
            state['state'] = next_state('idle', 'start')
            return self.step(state)
        if s == 'report_pending':
            # Идемпотентно: тот же job_id, повторное событие не дублирует job.
            self._ensure_emitted(state)
            state['state'] = next_state('report_pending', 'sent')
            return self.step(state)
        if s == 'waiting_reply':
            job = self.queue.get_message(state['current_job_id'])
            if job is None or job['status'] != 'replied' or not job['reply']:
                return state  # продолжаем ждать (poll снаружи)
            parsed, reason = parse_control_reply(job['reply'], state['current_job_id'])
            if parsed is None:
                # Невалидный/чужой ответ: безопасная остановка, модель не трогаем.
                state['state'] = 'need_user'
                state['report'] = {'error': f'invalid_control_reply: {reason}'}
                return state
            action = parsed['action']
            if action == 'execute':
                state['instruction_hash'] = instruction_hash(parsed['instruction'])
                state['state'] = next_state('waiting_reply', 'execute')
                self._save(state)  # running зафиксировано ДО Popen
                result = run_hermes(parsed['instruction'], cwd=self.project_cwd,
                                    timeout_s=HERMES_TIMEOUT_S,
                                    max_turns=HERMES_MAX_TURNS,
                                    hermes_bin=self.hermes_bin,
                                    log_dir=self.log_dir)
                state['exit_code'] = result['exit_code']
                state['finished_at'] = utc_now_iso()
                summary = {k: result[k] for k in
                           ('exit_code', 'status', 'reason', 'duration_s',
                            'stdout_tail', 'stderr_tail', 'stdout_log',
                            'stderr_log')}
                state = self._create_report(state, 'hermes_turn_completed',
                                            summary)
                state['state'] = next_state('running', 'finished')
                return self.step(state)
            if action == 'wait':
                state['state'] = next_state('waiting_reply', 'wait')
                return state
            if action == 'done':
                state['state'] = next_state('waiting_reply', 'done')
                state['finished_at'] = utc_now_iso()
                return state
            if action == 'need_user':
                state['state'] = next_state('waiting_reply', 'need_user')
                return state
        if s == 'waiting_external':
            state['state'] = next_state('waiting_external', 'resume')
            return state
        if s == 'running':
            # Рестарт во время исполнения: interrupted, отчёт, без повтора.
            state['state'] = next_state('running', 'interrupted')
            state['exit_code'] = None
            state['finished_at'] = utc_now_iso()
            state = self._create_report(state, 'worker_interrupted',
                                        {'error': 'interrupted_before_finish'})
            state['state'] = next_state('interrupted', 'reported')
            return self.step(state)
        raise ValueError(f'unhandled state {s!r}')

    def run(self) -> int:
        state = load_state(self.state_path)
        if state is None:
            state = new_state(self.goal)
            self._save(state)
        for _ in range(MAX_ITERATIONS):
            state = self.step(state)
            self._save(state)
            if state['state'] in ('done', 'need_user'):
                break
            time.sleep(POLL_S)
        if state['state'] == 'done':
            return 0
        if state['state'] == 'need_user':
            reason = state.get('report', {}).get('error', 'owner action required')
            print(f'need_user: {reason}', file=sys.stderr)
            return 3
        print('worker: iteration budget exhausted, last state '
              f'{state["state"]}', file=sys.stderr)
        return 4


def main() -> int:
    parser = argparse.ArgumentParser(description='hermes-control.v1 worker')
    parser.add_argument('--db', default=os.environ.get('BRIDGE_DB_PATH'))
    parser.add_argument('--state', default=os.environ.get('WORKER_STATE_PATH'))
    parser.add_argument('--cwd', default=os.environ.get('PROJECT_CWD'))
    parser.add_argument('--goal', default=os.environ.get('WORKER_GOAL'))
    parser.add_argument('--hermes', default=os.environ.get('HERMES_BIN') or shutil.which('hermes'))
    parser.add_argument('--log-dir', default=os.environ.get('WORKER_LOG_DIR'))
    parser.add_argument('--lock', default=os.environ.get('WORKER_LOCK_PATH'))
    args = parser.parse_args()
    if not (args.db and args.state and args.cwd and args.goal and args.hermes):
        print('usage: worker --db DB --state STATE --cwd DIR --goal GOAL --hermes BIN '
              '[--log-dir DIR] [--lock PATH]', file=sys.stderr)
        return 2
    lock_path = Path(args.lock or (args.state + '.lock'))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        print('worker: another worker holds the lock', file=sys.stderr)
        return 5
    try:
        worker = Worker(db_path=args.db, state_path=Path(args.state),
                        project_cwd=args.cwd, goal=args.goal,
                        hermes_bin=args.hermes,
                        log_dir=Path(args.log_dir or (args.state + '.logs')))
        return worker.run()
    finally:
        os.close(fd)


if __name__ == '__main__':
    sys.exit(main())