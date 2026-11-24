"""Strictly bounded one-shot write proof; never use as production auth."""
from __future__ import annotations

CANARY_ID = 'task_f2c64cfdf7894537b35a19a6def977c6'
CANARY_PREVIOUS_ACK = 'PGV2_CANARY_ACK_20261122'
WRITE_MESSAGE_ID = 'pgv2-secure-tunnel-canary-20261122-v1'
WRITE_ACK = 'PGV2_TUNNEL_WRITE_ACK_20261122'
WRITE_BODY = (
    'Тест канала ChatGPT -> OpenAI Secure MCP Tunnel -> PostgreSQL -> MultiTool. '
    'Никаких команд ОС, файловых изменений, сетевых запросов или иных задач не выполняй. '
    'Ответь только одной строкой PGV2_TUNNEL_WRITE_ACK_20261122 через этот же PostgreSQL-диалог.'
)


def start_state(dialog: dict) -> bool:
    """Only original two-message canary where ChatGPT has the turn."""
    if not isinstance(dialog, dict):
        return False
    history = dialog.get('history')
    return bool(
        dialog.get('task_id') == CANARY_ID
        and dialog.get('total_messages') == 2
        and dialog.get('waiting_for') == 'chatgpt'
        and isinstance(history, list) and len(history) >= 1
        and isinstance(history[0], dict)
        and history[0].get('seq') == 2
        and history[0].get('from') == 'mt'
        and history[0].get('kind') == 'report'
        and history[0].get('body') == CANARY_PREVIOUS_ACK
    )


def message_is_ours(record: dict) -> bool:
    return bool(isinstance(record, dict)
                and record.get('message_id') == WRITE_MESSAGE_ID
                and record.get('from') == 'chatgpt'
                and record.get('kind') == 'task'
                and record.get('body') == WRITE_BODY)


def compact_status(dialog: dict, receipt: dict) -> dict:
    """Never disclose bodies, arbitrary message rows or private database metadata."""
    msgs = dialog.get('history', []) if isinstance(dialog, dict) else []
    if not isinstance(msgs, list):
        msgs = []
    ack = any(isinstance(m, dict)
              and m.get('from') == 'mt'
              and m.get('kind') == 'report'
              and m.get('body') == WRITE_ACK
              and m.get('seq') == 4 for m in msgs)
    found = bool(isinstance(receipt, dict) and receipt.get('found') is True)
    seq = receipt.get('seq') if found else None
    if type(seq) is not int or seq != 3:
        seq = None
    return {'task_id': CANARY_ID, 'send_committed': seq == 3,
            'send_seq': seq, 'agent_ack_exact': ack,
            'roundtrip_complete': bool(seq == 3 and ack),
            'bounded_canary_only': True}
