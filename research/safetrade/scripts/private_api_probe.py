#!/usr/bin/env python3
"""Приватный read-only клиент SafeTrade (Peatio/OpenWare signed API) — укреплён.

Авторизация (OpenWare SDK 2.6):
  X-Auth-Apikey:    публичный ключ (16 hex)
  X-Auth-Nonce:     миллисекундный timestamp UTC, УНИКАЛЕН для каждого запроса
                   (монотонный счётчик добавляется при совпадении мс)
  X-Auth-Signature: HMAC-SHA256(secret, nonce + apikey) в hex

Защита (ревью 2e96fc9):
- WHITELIST: только подтверждённые операций ЧТЕНИЯ; любой другой путь
  (включая /orders/123/cancel, который в этом стеке может быть GET) отклоняется
  ДО транспорта (ValueError);
- РЕДИРЕКТЫ ЗАПРЕЩЕНЫ: HTTPRedirectHandler переопределён — 3xx = ошибка,
  заголовок X-Auth-Apikey не пересылается на другой домен;
- nonce уникален на запрос: миллисекунды + монотонный счётчик;
- таймаут 20с, до 3 попыток, 429 -> backoff 2/4с; сеть отделена от API-ошибок;
- показ обезличен: статусы/коды/схема полей/агрегаты, НЕ содержимое ордеров,
  НЕ даты/цены/количества сделок.

Секреты читаются из ~/Documents/safetrade/apikey (2 строки: apikey, secret),
в вывод не попадают.
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

# Только чтение. Каталог для будущего ордерного этапа (create/cancel) НЕ включён.
READ_ONLY_WHITELIST = {
    '/trade/public/markets',
    '/trade/public/depth',
    '/trade/public/trades',
    '/trade/public/tickers',
    '/trade/ping',
    '/trade/balances',           # 401 invalid_permission: метод есть, прав нет
    '/trade/market/orders',      # свои ордера (история + открытые)
    '/trade/market/trades',      # свои исполнения
}
_last_nonce_ms = [0]


def _unique_nonce():
    """миллисекундный ts, строго монотонный: два запроса в одну мс получают
    разные nonce (last_ms+1), формат остаётся миллисекундным (как ждёт сервер)."""
    ms = int(time.time() * 1000)
    if ms <= _last_nonce_ms[0]:
        ms = _last_nonce_ms[0] + 1
    _last_nonce_ms[0] = ms
    return str(ms)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, 'redirect forbidden',
                                     headers, None)


def load_key():
    with open(KEY_FILE) as fh:
        lines = [l.strip() for l in fh if l.strip()]
    if len(lines) < 2:
        raise RuntimeError(f'apikey: ожидалось 2 строки (apikey, secret), получено {len(lines)}')
    return lines[0], lines[1]


def sign_headers(path, apikey, secret):
    nonce = _unique_nonce()
    sig = hmac.new(secret.encode(), (nonce + apikey).encode(), hashlib.sha256).hexdigest()
    return {
        'User-Agent': UA,
        'Accept': 'application/json',
        'X-Auth-Apikey': apikey,
        'X-Auth-Nonce': nonce,
        'X-Auth-Signature': sig,
    }


def api_get(path, apikey, secret, params=None, attempts=MAX_ATTEMPTS):
    """GET только по whitelist-пути; редиректы запрещены; повтор на 429/сеть."""
    base_path = path.split('?')[0]
    if base_path not in READ_ONLY_WHITELIST:
        raise ValueError(f'path вне whitelist чтения: {path}')
    if params:
        from urllib.parse import urlencode
        path = path + '?' + urlencode(params)
    url = BASE + path
    last = None
    opener = urllib.request.build_opener(NoRedirect)
    for i in range(attempts):
        req = urllib.request.Request(url, headers=sign_headers(base_path, apikey, secret))
        try:
            with opener.open(req, timeout=TIMEOUT) as r:
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


def show_anonymized(label, code, body, max_show=240):
    """Обезличенный показ: статус, тип ответа, схема полей, агрегаты.
    Содержимое ордеров/сделок (даты, цены, количества) НЕ печатается."""
    print(f'{label}: HTTP {code}')
    if body is None:
        print('  (нет тела)')
        return
    try:
        obj = json.loads(body)
    except ValueError:
        print(f'  сырое: {body[:max_show]}')
        return
    if isinstance(obj, list):
        print(f'  список из {len(obj)} элементов; поля первой записи: '
              f'{sorted(obj[0].keys()) if obj and isinstance(obj[0], dict) else "—"}')
        if obj and isinstance(obj[0], dict) and 'state' in obj[0]:
            from collections import Counter
            states = Counter(o.get('state') for o in obj if isinstance(o, dict))
            print(f'  state: {dict(states)}')
        if obj and isinstance(obj[0], dict) and 'type' in obj[0]:
            from collections import Counter
            types = Counter(o.get('type') for o in obj if isinstance(o, dict))
            print(f'  type: {dict(types)}')
        return
    if isinstance(obj, dict) and obj.get('errors'):
        print(f'  errors: {obj["errors"]}')
        return
    print('  ' + json.dumps(obj, ensure_ascii=False)[:max_show])


def run_probe():
    apikey, secret = load_key()
    print(f'ключ: {KEY_FILE} (читается, не выводится); whitelist: только чтение')
    print('---')
    print('WHITELIST (разрешены только эти GET-пути):')
    for p in sorted(READ_ONLY_WHITELIST):
        print(f'  {p}')
    print('---')
    # 1. публичный контроль
    code, body = api_get('/trade/public/markets', apikey, secret,
                         params={'market': 'prlusdt'})
    show_anonymized('PUBLIC markets (контроль)', code, body)
    # 2. балансы: точный адрес по документации стека
    code, body = api_get('/trade/balances', apikey, secret)
    show_anonymized('PRIVATE /trade/balances (метод есть; прав ключа нет?)',
                    code, body)
    # 3. свои ордера: статус/тип/схема — без содержимого
    code, body = api_get('/trade/market/orders', apikey, secret,
                         params={'market': 'prlusdt', 'limit': 5})
    show_anonymized('PRIVATE market/orders (свои, prlusdt)', code, body)
    # 4. свои исполнения: схема + агрегат комиссии (без дат/цен/сделок)
    code, body = api_get('/trade/market/trades', apikey, secret,
                         params={'market': 'prlusdt', 'limit': 50})
    show_anonymized('PRIVATE market/trades (свои, prlusdt)', code, body)
    if code == 200:
        try:
            obj = json.loads(body)
            if isinstance(obj, list) and obj:
                fees = [float(t['fee']) / (float(t['amount']) * float(t['price']))
                        for t in obj if float(t.get('amount') or 0) > 0]
                from statistics import median
                print(f'  агрегат: n={len(obj)} fee_rate bps '
                      f'min={min(fees)*1e4:.2f} med={median(fees)*1e4:.2f} '
                      f'max={max(fees)*1e4:.2f}')
        except Exception as e:
            print(f'  (агрегат недоступен: {type(e).__name__})')


if __name__ == '__main__':
    run_probe()