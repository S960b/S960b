"""Safe, tiny output mappers for Secure Tunnel PostgreSQL read-only probe."""
from __future__ import annotations

CANARY_ID = 'task_f2c64cfdf7894537b35a19a6def977c6'
CANARY_ACK = 'PGV2_CANARY_ACK_20261122'


def summarize_health(value: dict) -> dict:
    """No DB metadata, passwords, or original response fields leave this tool."""
    return {'pg_connected': bool(isinstance(value, dict) and value.get('ok') is True),
            'backend': 'postgresql', 'read_only': True}


def summarize_canary(value: dict) -> dict:
    """Fixed existing canary, no bodies, no arbitrary task selector."""
    history = value.get('history', []) if isinstance(value, dict) else []
    if not isinstance(history, list):
        history = []
    match = any(isinstance(m, dict) and m.get('from') == 'mt' and
                m.get('kind') == 'report' and
                m.get('body') == CANARY_ACK for m in history)
    count = value.get('total_messages') if isinstance(value, dict) else None
    if type(count) is not int or count < 0:
        count = None
    return {'task_id': CANARY_ID, 'mt_ack_exact': match,
            'total_messages': count, 'read_only': True}
