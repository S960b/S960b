"""Stage 33F-B. One fixed, harmless SDK canary. Caller has NO parameters.

No legacy bridge_app, no SQL with client-supplied sender. Never use as a
production general-purpose, unauthenticated MCP server.
"""
from __future__ import annotations
import os
import stat
from pathlib import Path

ARM_FILE = Path('/home/kali/hermes-pg-stage33fb-mcp-ready/armed-stage33fc')
ARM_CONTENT = b'ALLOW_FIXED_SEQ5_AFTER_EXCLUSIVE_SDK_SWITCH\n'


def is_armed():
    """True only after future explicit Stage 33F-C cutover creates private gate."""
    try:
        st = ARM_FILE.lstat()
        return (stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid()
                and stat.S_IMODE(st.st_mode) == 0o600 and st.st_nlink == 1
                and not ARM_FILE.is_symlink() and ARM_FILE.read_bytes() == ARM_CONTENT)
    except OSError:
        return False

CANARY = 'task_f2c64cfdf7894537b35a19a6def977c6'
TASK_MID = 'pgv2-stage33f-sdk-task-v1'
REPORT_MID = 'pgv2-stage33f-sdk-report-v1'
ACK = 'PGV2_ROLE_BOUND_MT_SDK_ACK_20261122'
PREV_ACK = 'PGV2_TUNNEL_WRITE_ACK_20261122'
BODY = (
    'Безопасная проверка доставки через роль PostgreSQL bridge_agent и MultiTool SDK. '
    'Это не просьба выполнять команды ОС, обращаться в сеть или изменять файлы, '
    'кроме временного UTF-8 файла с ответом. Используй только указанный в MT Inbox '
    'локальный CLI Stage 33F для публикации отчёта в текущем PostgreSQL-диалоге. '
    'Тело отчёта строго одна строка: PGV2_ROLE_BOUND_MT_SDK_ACK_20261122. '
    'Никакие другие задания не выполняй.'
)


def _need(condition: bool, error: str) -> None:
    if not condition:
        raise ValueError(error)


def _view(db):
    result = db.view_dialog(CANARY, 0, 8)
    _need(isinstance(result, dict), 'view_unavailable')
    entries = result.get('messages')
    _need(isinstance(entries, list), 'invalid_view')
    return result, entries


def _receipt(db, message_id):
    r = db.send_status(CANARY, message_id)
    _need(isinstance(r, dict), 'send_status_unavailable')
    return r


def _exact(entries, seq, mid, sender, kind, body):
    return any(isinstance(x, dict) and x.get('seq') == seq and
               x.get('message_id') == mid and x.get('sender') == sender and
               x.get('kind') == kind and x.get('body') == body and
               x.get('returned') is False for x in entries)


def _prior_ok(meta, entries):
    return (meta.get('state') == 'active' and meta.get('last_seq') in (4, 5, 6) and
            _exact(entries, 4, 'pgv2-tunnel-write-ack-20261122-v1',
                   'agent', 'report', PREV_ACK))


def status(db):
    """Never reveal message bodies. This is a read-only operation."""
    meta = db.dialog_metadata(CANARY)
    _need(isinstance(meta, dict), 'metadata_unavailable')
    view, entries = _view(db)
    _need(view.get('last_seq') == meta.get('last_seq'), 'view_metadata_mismatch')
    _need(_prior_ok(meta, entries), 'prior_canary_state_invalid')
    receipt = _receipt(db, TASK_MID)
    report = _receipt(db, REPORT_MID)
    task_found = receipt.get('found') is True
    report_found = report.get('found') is True
    task_exact = (task_found and receipt.get('seq') == 5 and
                  receipt.get('sender') == 'chatgpt' and
                  _exact(entries, 5, TASK_MID, 'chatgpt', 'task', BODY))
    report_exact = (report_found and report.get('seq') == 6 and
                    report.get('sender') == 'agent' and
                    _exact(entries, 6, REPORT_MID, 'agent', 'report', ACK))
    _need(not task_found or task_exact, 'task_idempotency_collision')
    _need(not report_found or report_exact, 'report_idempotency_collision')
    expected_seq = 6 if report_found else 5 if task_found else 4
    expected_actor = 'chatgpt' if expected_seq in (4, 6) else 'agent'
    _need(meta.get('last_seq') == expected_seq and
          meta.get('next_actor') == expected_actor,
          'unexpected_dialog_turn')
    delivery = db.delivery_status(CANARY, 5) if task_found else None
    delivery_status = delivery.get('status') if isinstance(delivery, dict) else None
    return {'task_id': CANARY, 'actor_role': 'bridge_chatgpt',
            'seq': expected_seq, 'test_task_committed': bool(task_exact),
            'test_report_exact': bool(report_exact),
            'task_delivery_status': delivery_status,
            'sdk_roundtrip_complete': bool(task_exact and report_exact and
                                           delivery_status == 'delivered'),
            'bounded_canary_only': True}


def send_once(db):
    """Fixed task and idempotency key, only eligible from exactly seq=4."""
    state = status(db)
    if state['test_task_committed']:
        return {'ok': True, 'task_id': CANARY, 'send_seq': 5,
                'already_committed': True, 'bounded_canary_only': True}
    _need(state['seq'] == 4, 'turn_not_ready')
    meta = db.dialog_metadata(CANARY)
    _need(meta.get('state') == 'active' and meta.get('next_actor') == 'chatgpt',
          'not_chatgpt_turn')
    # Postgres enforces expected_version and unique idempotency message_id.
    result = db.send_message(CANARY, TASK_MID, 'task', BODY,
                             expected_version=meta['version'])
    _need(isinstance(result, dict) and result.get('seq') == 5,
          'commit_outcome_uncertain')
    return {'ok': True, 'task_id': CANARY, 'send_seq': 5,
            'already_committed': not bool(result.get('created')),
            'bounded_canary_only': True}
