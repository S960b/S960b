#!/usr/bin/env python3
"""Приватный read-only клиент SafeTrade (Peatio/OpenWare signed API).

Авторизация (OpenWare SDK 2.6, https://www.openware.com/sdk/2.6/docs/peatio/api/trading-api):
  X-Auth-Apikey:   публичный ключ (16 hex символов)
  X-Auth-Nonce:    миллисекундный timestamp UTC (дноразовое число)
  X-Auth-Signature: HMAC-SHA256(secret, nonce + apikey) в hex

ТОЛЬКО чтение: account/balances, market/orders, history/orders, history/trades,
market/trades (свои). Никаких create/cancel. Секреты читаются из файла
~/Documents/safetrade/apikey (2 строки: apikey, secret), в вывод не попадают.
"""
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = 'https://safetrade.com/api/v2'
KEY_FILE = os.path.expanduser('~/Documents/safetrade/apikey')
UA = 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'
MAX_ATTEMPTS = 3
TIMEOUT = 20


def load_key():
    with open(KEY_FILE) as fh:
        lines = [l.strip() for l in fh if l.strip()]
    if len(lines) < 2:
        raise RuntimeError(f'apikey: ожидалось 2 строки (apikey, secret), получено {len(lines)}')
    return lines[0], lines[1]


def sign_headers(path, apikey, secret):
    nonce = str(int(time.time() * 1000))          # миллисекундный UTC ts
    sig = hmac.new(secret.encode(), (nonce + apikey).encode(), hashlib.sha256).hexdigest()
    return {
        'User-Agent': UA,
        'Accept': 'application/json',
        'X-Auth-Apikey': apikey,
        'X-Auth-Nonce': nonce,
        'X-Auth-Signature': sig,
    }


def api_get(path, apikey, secret, params=None, attempts=MAX_ATTEMPTS):
    if params:
        from urllib.parse import urlencode
        path = path + '?' + urlencode(params)
    url = BASE + path
    last = None
    for i in range(attempts):
        req = urllib.request.Request(url, headers=sign_headers(path.split('?')[0], apikey, secret))
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                body = r.read().decode()
                return r.status, body
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors='replace')
            last = (e.code, body)
            if e.code == 429:
                time.sleep(2 * (i + 1))
                continue
            return e.code, body
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = ('net', f'{type(e).__name__}: {e}')
            time.sleep(1.5 * (i + 1))
    return last or ('err', 'no response')


def show(label, code, body, redact=True):
    print(f'{label}: HTTP {code}')
    if body is None:
        print('  (нет тела)')
        return
    try:
        obj = json.loads(body)
    except ValueError:
        print(f'  сырое: {body[:200]}')
        return
    txt = json.dumps(obj, ensure_ascii=False)[:100000]
    if redact:
        # маскируем только подозрительно-чувствительные поля (адреса/ключи/секреты)
        import re
        txt = re.sub(r'(0x[a-fA-F0-9]{20,})', '<REDACTED_ADDR>', txt)
    print('  ' + txt[:800])


def check(method_path, label, params=None):
    apikey, secret = load_key()
    code, body = api_get(method_path, apikey, secret, params=params)
    show(label, code, body)
    return code, body


if __name__ == '__main__':
    print(f'ключ: {KEY_FILE} (читается, не выводится)')
    check('/trade/public/markets', 'PUBLIC markets (контроль)', params={'market': 'prlusdt'})
    check('/trade/ping', 'PUBLIC ping')
    # приватные — ТОЛЬКО чтение
    code, body = check('/trade/account/balances', 'PRIVATE account/balances')
    code, body = check('/trade/market/orders', 'PRIVATE market/orders (открытые ордера)',
                       params={'market': 'prlusdt', 'limit': 5})
    code, body = check('/trade/market/orders', 'PRIVATE market/orders без market (все)',
                       params={'limit': 5})
    code, body = check('/trade/history/orders', 'PRIVATE history/orders (история)',
                       params={'market': 'prlusdt', 'limit': 5})
    code, body = check('/trade/history/trades', 'PRIVATE history/trades (исполнения)',
                       params={'market': 'prlusdt', 'limit': 5})