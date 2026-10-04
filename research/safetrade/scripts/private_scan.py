#!/usr/bin/env python3
"""Классификация приватных read-only методов SafeTrade (без секретов в выводе).

Схема подписи: OpenWare SDK 2.6 — HMAC-SHA256(secret, nonce_ms+apikey).
ВЕСЬ транспорт идёт через защищённый api_get (ревью 1fa1d2b): whitelist GET,
редиректы запрещены, уникальный nonce, retry-policy. Никаких прямых urlopen
и обходных «диагностических» запросов. Показываются ТОЛЬКО коды/ошибки/схемы
полей/агрегаты — содержимое ордеров и сделок не печатается ни в какой форме.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.private_api_probe import api_get, load_key, READ_ONLY_WHITELIST

ENDPOINTS = [
    ('/trade/public/markets', {'market': 'prlusdt'}, 'PUBLIC markets'),
    ('/trade/balances', {}, 'balances (статус)'),
    ('/trade/market/orders', {}, 'orders market (свои, все)'),
    ('/trade/market/orders', {'market': 'prlusdt'}, 'orders market prlusdt'),
    ('/trade/market/trades', {}, 'market trades (свои)'),
]


def show_anonymized(path, label, code, body):
    """Только код + тип/схема ответа; значения не печатаются (ревью 1fa1d2b)."""
    desc = ''
    try:
        obj = json.loads(body)
        if isinstance(obj, dict) and obj.get('errors'):
            errs = obj['errors']
            desc = f'errors[{len(errs)}]: ' + ', '.join(str(e) for e in errs)
        elif isinstance(obj, list):
            desc = f'list[{len(obj)}]'
            if obj and isinstance(obj[0], dict):
                desc += f' fields={sorted(obj[0].keys())}'
        elif isinstance(obj, dict):
            desc = f'dict keys={sorted(obj.keys())} (значения скрыты)'
        else:
            desc = f'{type(obj).__name__} (значения скрыты)'
    except Exception:
        desc = 'non-JSON (значения скрыты)'
    print(f'{path:<32} {label:<24} HTTP {code}: {desc}', flush=True)


def main():
    k, s = load_key()
    print(f'key_file=~/Documents/safetrade/apikey (не выводится) | '
          f'whitelist={len(READ_ONLY_WHITELIST)} путей чтения | '
          'ТОЛЬКО api_get, без обходов')
    print('--- whitelist-проверка (фактические коды read-only ключа):')
    for path, params, label in ENDPOINTS:
        code, body = api_get(path, k, s, params=params)
        show_anonymized(path, label, code, body)
    print('--- вне-whitelist пути НЕ запрашиваются (запрещены ДО транспорта);')
    print('    для будущего ордерного этапа контракт сначала подтверждается,')
    print('    затем путь отдельно добавляется в allowlist.')
    print('--- whitelist-отказ (offline, транспорт не вызывается):')
    for bad in ('/trade/orders', '/orders/123/cancel'):
        try:
            api_get(bad, k, s)
            print(f'  {bad} НЕ ОТКЛОНЁН (баг!)')
        except ValueError as e:
            print(f'  {bad} -> ValueError: {e}')


if __name__ == '__main__':
    main()