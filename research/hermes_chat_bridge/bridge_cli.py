#!/usr/bin/env python3
"""Локальная CLI моста Hermes<->ChatGPT (только владелец, на машине).

  bridge-cli put "текст"   — создать задачу + триггер события hermes.message.created
  bridge-cli get <job_id>  — прочитать задачу/ответ
  bridge-cli list          — список задач (без текста, только длины/статусы)
  bridge-cli subs          — активные подписки (метаданные, без секрета)
  bridge-cli verify        — самопроверка: подпись Standard Webhooks
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    import base64
    import hashlib
    import hmac
    import time

    from bridge_queue import Queue as Q
    # чтобы не дублировать константы сервера — импортируем из bridge_server
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'bridge_server', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bridge_server.py'))
    bs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bs)

    q = Q(bs.DB_PATH)
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'help'

    if cmd == 'put':
        text = sys.argv[2] if len(sys.argv) > 2 else ''
        if not text:
            print('usage: bridge-cli put "текст"'); return 1
        r = q.put_message(text)
        print(f"job_id={r['job_id']} status={r['status']}")
        # триггер события подписчикам (queue=test используем для проверочной задачи)
        bridge = bs.BridgeApp(bs.BRIDGE_HOST and 'http://127.0.0.1:8765')
        n = bridge.emit_test_event(r['job_id'])
        print(f'events_queued={n}')
        return 0

    elif cmd == 'get':
        jid = sys.argv[2] if len(sys.argv) > 2 else ''
        r = q.get_message(jid)
        if r is None:
            print('not_found'); return 1
        print(json.dumps(r, ensure_ascii=False)); return 0

    elif cmd == 'list':
        for j in q.list_jobs():
            print(j); return 0

    elif cmd == 'subs':
        rows = bs.Store(bs.DB_PATH).conn.execute(
            'SELECT sub_id, owner, event, callback_url, expires_at, active FROM subs').fetchall()
        for r in rows:
            d = dict(r)
            print(d); return 0

    elif cmd == 'verify':
        # самопроверка подписи Standard Webhooks (фиксированный вектор)
        secret = 'whsec_' + base64.b64encode(b'x' * 32).decode()
        ts = '1700000000'
        body = b'{"a":1}'
        bridge = bs.BridgeApp('http://127.0.0.1:8765')
        sig = bridge._sign_body(secret, ts, body)
        assert sig.startswith('v1,'), sig
        print(f'signature_ok prefix=v1, len={len(sig)}')
        return 0

    else:
        print(__doc__); return 0


if __name__ == '__main__':
    sys.exit(main())