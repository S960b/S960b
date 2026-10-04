#!/usr/bin/env python3
"""Полная классификация приватных read-only методов SafeTrade (без секретов в выводе).
Схема подписи: OpenWare SDK 2.6 — HMAC-SHA256(secret, nonce_ms + apikey).
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.private_api_probe import api_get, load_key

ENDPOINTS = [
    ('/trade/public/markets', {'market': 'prlusdt'}, 'PUBLIC markets'),
    ('/trade/account/balances', {}, 'balances (docs: /account/balances)'),
    ('/trade/balances', {}, 'balances alt'),
    ('/trade/account/orders', {}, 'orders account'),
    ('/trade/orders', {}, 'orders (все)'),
    ('/trade/market/orders', {}, 'orders market (история/открытые)'),
    ('/trade/market/orders', {'market': 'prlusdt'}, 'orders market prlusdt'),
    ('/trade/history/orders', {}, 'history orders'),
    ('/trade/history/orders', {'market': 'prlusdt'}, 'history orders prlusdt'),
    ('/trade/history/trades', {}, 'history trades'),
    ('/trade/history/trades', {'market': 'prlusdt'}, 'history trades prlusdt'),
    ('/trade/market/trades', {}, 'market trades (мои)'),
    ('/trade/trades', {}, 'trades'),
    ('/trade/account/trades', {}, 'account trades'),
    ('/trade/deposits', {}, 'deposits'),
    ('/trade/withdraws', {}, 'withdraws'),
]
k, s = load_key()
print(f'key_file={os.path.expanduser("~/Documents/safetrade/apikey")} (не выводится)')
for path, params, label in ENDPOINTS:
    code, body = api_get(path, k, s, params=params)
    err = ''
    try:
        obj = json.loads(body)
        if isinstance(obj, dict) and obj.get('errors'):
            err = obj['errors'][0]
        elif isinstance(obj, list):
            err = f'list[{len(obj)}]'
    except Exception:
        err = body[:40]
    print(f'{code:>4} {path:<28} {label:<28} {err}')