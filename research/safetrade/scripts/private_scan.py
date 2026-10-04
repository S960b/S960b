#!/usr/bin/env python3
"""Классификация приватных read-only методов SafeTrade (без секретов в выводе).

Схема подписи: OpenWare SDK 2.6 — HMAC-SHA256(secret, nonce_ms+apikey).
Клиент защищён (ревью 2e96fc9): whitelist GET, редиректы запрещены, уникальный
nonce. Показываются ТОЛЬКО коды/ошибки/схемы — содержимое ордеров и сделок
не печатается.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.private_api_probe import api_get, load_key, READ_ONLY_WHITELIST

# сценарий 1: whitelist-пути — фактические коды (что доступно read-only ключу)
ENDPOINTS = [
    ('/trade/public/markets', {'market': 'prlusdt'}, 'PUBLIC markets'),
    ('/trade/balances', {}, 'balances (метод существует?)'),
    ('/trade/market/orders', {}, 'orders market (свои, все)'),
    ('/trade/market/orders', {'market': 'prlusdt'}, 'orders market prlusdt'),
    ('/trade/market/trades', {}, 'market trades (свои)'),
]
# сценарий 2: пути, ранее возвращавшие 401/404 — НЕ в whitelist, но их статус
# важен для отчёта; проверяем их через исходный (незащищённый) клиент, чтобы
# не блокировать диагностику фактом отсутствия в whitelist.
PROBE_ONLY = [
    ('/trade/history/orders', {}, 'history orders'),
    ('/trade/history/trades', {}, 'history trades'),
    ('/trade/deposits', {}, 'deposits'),
    ('/trade/withdraws', {}, 'withdraws'),
    ('/orders/123/cancel', {}, 'cancel-путь (НЕ должен попасть в whitelist)'),
]


def show(code, body):
    err = ''
    try:
        obj = json.loads(body)
        if isinstance(obj, dict) and obj.get('errors'):
            err = obj['errors'][0]
        elif isinstance(obj, list):
            err = f'list[{len(obj)}]'
    except Exception:
        err = body[:40]
    print(f'  {code}: {err}', flush=True)


def main():
    k, s = load_key()
    print(f'key_file=~/Documents/safetrade/apikey (не выводится) | '
          f'whitelist={len(READ_ONLY_WHITELIST)} путей чтения')
    print('--- whitelist-проверка (фактические коды read-only ключа):')
    for path, params, label in ENDPOINTS:
        code, body = api_get(path, k, s, params=params)
        print(f'{path:<32} {label:<24}', end='')
        show(code, body)
    print('--- диагностика неза­крытых путей (без whitelist, для отчёта):')
    # прямой GET-пробник: только чтение, ничего не пишет
    import urllib.request, urllib.error, time, hashlib, hmac
    for path, params, label in PROBE_ONLY:
        url = 'https://safetrade.com/api/v2' + path
        nonce = str(int(time.time() * 1000))
        sig = hmac.new(s.encode(), (nonce + k).encode(), hashlib.sha256).hexdigest()
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0',
            'Accept': 'application/json',
            'X-Auth-Apikey': k, 'X-Auth-Nonce': nonce, 'X-Auth-Signature': sig})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                print(f'{path:<32} {label:<24}  {r.status}: (ok)')
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors='replace')
            print(f'{path:<32} {label:<24}', end='', flush=True)
            show(e.code, body)
        except Exception as e:
            print(f'{path:<32} {label:<24}  net: {type(e).__name__}')
    print('--- whitelist-отказ (путь вне списка отклоняется ДО транспорта):')
    try:
        api_get('/trade/orders', k, s)
        print('  /trade/orders НЕ ОТКЛОНЁН (баг!)')
    except ValueError as e:
        print(f'  /trade/orders -> ValueError: {e}')
    try:
        api_get('/orders/123/cancel', k, s)
        print('  /orders/123/cancel НЕ ОТКЛОНЁН (баг!)')
    except ValueError as e:
        print(f'  /orders/123/cancel -> ValueError: {e}')


if __name__ == '__main__':
    main()