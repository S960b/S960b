#!/usr/bin/env python3
"""Бинарный поиск своих trade_id в публичном потоке (ревью 6d01aae, этап side).

Цель: найти публичные записи с id, равными id моих исполнений (все от
market-ордеров => агрессор = сторона моего ордера). Если public.side ==
мой order_side — публичное side кодирует сторону АГРЕССОРА. Если наоборот —
сторону лимитного (пассивного) ордера.

Без создания новых сделок: ищем в истории. Обрезанный вывод: id/ts/side.
"""
import sys, json, time
sys.path.insert(0, '/home/kali/safetrade-research')
import urllib.request

UA = 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'
BASE = 'https://safetrade.com/api/v2/trade/public/markets/prlusdt/trades'


def fetch(page, limit=100):
    url = f'{BASE}?limit={limit}&page={page}'
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def page_time(page):
    """created_at последней (самой старой) записи страницы."""
    try:
        d = fetch(page)
        if not d:
            return None
        return d[-1]['created_at'], d[0]['created_at'], d[0]['id'], d[-1]['id']
    except Exception as e:
        return None


def main():
    target_id = 23218311          # моя PRL-сделка sell, 2026-09-23T10:13:54Z
    target_ts = '2026-09-23T10:13:54Z'
    print(f'ищу id={target_id} ({target_ts}) в публичном потоке PRL')
    # разведка: сколько страниц (~100 записей) до 23.09
    lo, hi = 1, 1
    ts_hi = None
    # грубая разведка: page=1 (свежие) и page=2000/4000 (глубже)
    for p in (1, 500, 1000, 2000, 4000):
        info = page_time(p)
        print(f'  page={p}: new={info[0] if info else None} old={info[1] if info else None} '
              f'id0={info[2] if info else None} idN={info[3] if info else None}')
        if info and info[1] and info[1] <= target_ts:
            hi = p
            break
    print(f'начальный диапазон: lo={lo} hi={hi}')
    # бинарный поиск страницы, где target_ts в [old, new]
    for _ in range(14):
        if hi <= lo + 1:
            break
        mid = (lo + hi) // 2
        info = page_time(mid)
        if info is None:
            time.sleep(1)
            continue
        old, new = info[1], info[0]
        if target_ts < old:
            lo = mid          # target глубже (старее) — правая половина
        elif target_ts > new:
            hi = mid          # target свежее — левая половина
        else:
            lo, hi = mid, mid
        print(f'  mid={mid}: new={new} old={old} -> lo={lo} hi={hi}')
    # сканируем найденную страницу и соседние
    found = []
    for p in range(max(1, lo - 2), hi + 3):
        d = fetch(p)
        for t in d:
            if t['id'] == target_id:
                found.append((p, t))
                print(f'  НАЙДЕН на page={p}: {json.dumps(t, ensure_ascii=False)}')
        if found:
            break
        time.sleep(0.3)
    if not found:
        print('  не найден на страницах вокруг lo/hi; пробую окрестность шире')
        for p in range(max(1, lo - 10), hi + 11):
            d = fetch(p)
            for t in d:
                if t['id'] == target_id:
                    found.append((p, t))
                    print(f'  НАЙДЕН на page={p}: {json.dumps(t, ensure_ascii=False)}')
            if found:
                break
            time.sleep(0.3)
    print('итог:', 'найден' if found else 'не найден (контракт unknown)')


if __name__ == '__main__':
    main()