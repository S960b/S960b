#!/usr/bin/env python3
"""Read-only проверка Pionex API ключа (без вывода секретов).

1. GET /api/v1/account/balances — подтвердить аутентификацию и баланс.
2. GET /api/v1/trade/fills (пустой запрос допустим чтением) — если получится,
   посмотреть структуру для поиска fee.
3. Попытка write-запроса POST /api/v1/trade/massOrder с заведомо отключаемыми
   параметрами — ожидаем PERMISSION_DENIED (подтверждает read-only права).
   Ордер реально НЕ создаётся (массовый пустой список).
Значения ключа никогда не печатаются.
"""
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = 'https://api.pionex.com'
UA = 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'


def load_key():
    # Путь к файлу ключа: аргумент --key-file, либо env PIONEX_KEY_FILE,
    # либо локальный путь по умолчанию (НЕ публикуется с секретом).
    import argparse
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('--key-file')
    args, _ = ap.parse_known_args()
    path = args.key_file or Path('/home/kali/Documents/key/apikey')
    lines = [l.strip() for l in path.read_text().splitlines() if l.strip()]
    return lines[0], lines[1]


def signed(method, path, params=None, body=None):
    key, secret = load_key()
    params = dict(params or {})
    ts = int(time.time() * 1000)
    params['timestamp'] = ts
    # ВАЖНО: сервер строит канон из ОТСОРТИРОВАННЫХ алфавитно query-параметров
    qs = urllib.parse.urlencode(sorted(params.items()))
    # Эмпирически подтверждённый канон: METHOD + PATH + '?' + QUERY(sorted)
    canonical = method + path + '?' + qs
    if body:
        canonical += json.dumps(body, separators=(',', ':'))
    sig = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    url = BASE + path + '?' + qs
    headers = {
        'User-Agent': UA, 'Content-Type': 'application/json',
        'PIONEX-KEY': key, 'PIONEX-SIGNATURE': sig,
    }
    data = json.dumps(body, separators=(',', ':')).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {'exception': repr(e)}


def main():
    print('== 1) GET /account/balances')
    code, body = signed('GET', '/api/v1/account/balances')
    print('  http:', code, 'result:', body.get('result'))
    if code == 200 and body.get('result'):
        bals = body.get('data', {}).get('balances', [])
        print('  balances (coin:free):', [(b['coin'], b['free']) for b in bals][:20])
    else:
        print('  body:', json.dumps(body)[:300])

    print('== 2) GET /trade/fills (чтение истории, для fee)')
    code, body = signed('GET', '/api/v1/trade/fills', {'symbol': 'BTC_USDT', 'limit': 5})
    print('  http:', code, 'result:', body.get('result'))
    if code == 200 and body.get('result'):
        fl = body.get('data', {}).get('fills', [])
        print('  fills count:', len(fl))
        for f in fl[:3]:
            if isinstance(f, dict):
                print('   fee sample:', {k: f.get(k) for k in ('symbol', 'side', 'role', 'fee', 'feeCoin', 'price', 'size')})
    else:
        print('  body:', json.dumps(body)[:200])

    print('== 3) write-запрос (пустой massOrder) — ждём AUTH_UNAVAILABLE/PERMISSION_DENIED')
    code, body = signed('POST', '/api/v1/trade/massOrder', body={'orders': []})
    print('  http:', code, 'result:', body.get('result'), 'code:', body.get('code'))
    write_blocked = (
        body.get('result') is False
        and ('AUTH' in str(body.get('code', '')) or 'PERMISSION' in str(body.get('code', ''))
             or 'DENIED' in str(body.get('code', '')))
    )
    print('  WRITE-БЛОКИРОВКА ПОДТВЕРЖДЕНА:', bool(write_blocked), '| raw:', json.dumps(body)[:160])


if __name__ == '__main__':
    main()