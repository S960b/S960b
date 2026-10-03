"""Регрессии maker-этапа 1 (ревью 738de3a P0.1-P0.6). Offline, без сети.

Кейсы: partial depth не выдаётся за полное покрытие; повтор/перекрытие страниц;
request_failed отделён от no_trades; missing book => insufficient_evidence;
TZ-независимость возраста; valid_book (пустой/crossed); analyzer dedup+window.
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest

from maker.feasibility import _fetch_trades, _vwap_for_budget, run_screen, screen_pair
from maker.detail_analyze import analyze_run
from maker.util import parse_iso_utc, valid_book


class ImmediateLimiter:
    async def wait(self):
        pass


class RepeatedPages:
    def _http(self, url):
        return [{'id': i} for i in range(100)]


class OverlappingPages:
    def _http(self, url):
        start = 0 if url.endswith('page=1') else 99
        return [{'id': i} for i in range(start, start + 100)]


class FailedRequest:
    def _http(self, url):
        raise RuntimeError('synthetic HTTP failure')


class MissingBook:
    def discover_markets(self):
        return [{'symbol': 'P/USDT', 'native': 'pusdt', 'base': 'P',
                 'quote': 'USDT', 'status': 'enabled', 'min_qty': '1'}]

    async def rest_depth(self, *args, **kwargs):
        raise RuntimeError('synthetic depth unavailable')

    def _http(self, url):
        end = int(time.time())
        return [{'id': i, 'total': '1', 'created_at': time.strftime(
            '%Y-%m-%dT%H:%M:%SZ', time.gmtime(end - i * 30))} for i in range(2)]


class TimestampTrades:
    def _http(self, url):
        return [{'id': 1, 'total': '1', 'created_at': '2026-10-03T10:00:00Z'},
                {'id': 2, 'total': '1', 'created_at': '2026-10-03T09:59:00Z'}]


def test_partial_depth_not_full_coverage():
    v = _vwap_for_budget([['99', '.01']], [['100', '.01']], 10)
    assert v['ask_cov'] == 'partial'
    assert v['bid_cov'] == 'partial'
    assert v['ask_quote'] < 10.0        # потрачен 1 USDT, не 10
    assert v['bid_quote'] < 10.0


def test_repeated_page_not_false_full():
    """Повтор всей страницы НЕ доказывает конец истории (P0.1 0b71d28)."""
    trades, cov, failed = asyncio.run(_fetch_trades(RepeatedPages(), 'p', ImmediateLimiter(), pages=3))
    assert failed is False
    assert len(trades) == 100           # повторы не попали в unique
    assert cov not in {'full', 'full_at_page', 'caught_up'}, cov


def test_overlapping_pages_records_199():
    """Частичное перекрытие страниц: новые записи сохраняются (199), состояние
    пагинации честное (полный повтор всей страницы = не конец истории)."""
    trades, cov, failed = asyncio.run(_fetch_trades(OverlappingPages(), 'p', ImmediateLimiter(), pages=3))
    assert len(trades) == 199
    assert not failed


def test_failed_request_is_request_failed_not_no_trades():
    trades, cov, failed = asyncio.run(_fetch_trades(FailedRequest(), 'p', ImmediateLimiter(), pages=2))
    assert failed is True
    assert cov == 'request_failed'
    assert trades == []


def test_missing_book_not_pass():
    with patch('maker.feasibility.SafeTradeAdapter', MissingBook):
        res = asyncio.run(run_screen(depth_snaps=1, trade_pages=1, rpm=1e9))
        p = res.pairs[0]
    assert p.candidate_status == 'insufficient_evidence'
    assert p.n_depth_valid == 0
    assert p.min_notional_est is None
    assert 'depth_invalid_or_missing' in p.reason


def test_trade_age_tz_independent():
    old_tz = os.environ.get('TZ')
    try:
        now_ts = datetime.fromisoformat('2026-10-03T10:05:00+00:00').timestamp()
        ages = {}
        for zone in ('UTC', 'Europe/Moscow'):
            os.environ['TZ'] = zone
            time.tzset()
            with patch('maker.feasibility.time.time', return_value=now_ts):
                p = asyncio.run(screen_pair(TimestampTrades(), 'p', 'PUSDT', 'P',
                    {'status': 'enabled', 'min_qty': '1'}, ImmediateLimiter(),
                    depth_snaps=0, trade_pages=1))
            ages[zone] = p.last_trade_age_min
        assert abs(ages['UTC'] - ages['Europe/Moscow']) < 0.01
        assert abs(ages['UTC'] - 5.0) < 0.5
    finally:
        if old_tz is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = old_tz
        time.tzset()


def test_parse_iso_utc_aware_and_fractional():
    assert parse_iso_utc('2026-10-03T10:00:00Z') == parse_iso_utc('2026-10-03T13:00:00+03:00')
    base = parse_iso_utc('2026-10-03T10:00:00Z')
    assert abs(parse_iso_utc('2026-10-03T10:00:00.5Z') - (base + 0.5)) < 1e-6
    assert parse_iso_utc(None) is None
    assert parse_iso_utc('garbage') is None


def test_valid_book_empty_and_crossed():
    assert valid_book([], []) == (False, None, None)
    ok, bb, ba = valid_book([['102', '1']], [['100', '1']])
    assert not ok       # crossed bid>ask
    ok2, _, _ = valid_book([['99', '1']], [['100', '1']])
    assert ok2 is True


def test_analyzer_unique_and_window_filter(tmp_path):
    rid = 'mk_review_fixture'
    depth = [
        {'pair': 'PUSDT', 't': 1_000_000_000, 'ok': True,
         'bids': [['99', '.01']], 'asks': [['100', '.01']]},
        {'pair': 'PUSDT', 't': 61_000_000_000, 'ok': True, 'bids': [], 'asks': []},
        {'pair': 'PUSDT', 't': 121_000_000_000, 'ok': True,
         'bids': [['102', '1']], 'asks': [['100', '1']]},   # crossed
    ]
    trade = {'id': 1, 'created_at': '2026-10-02T00:00:00Z',
             'price': '100', 'amount': '.01', 'total': '1'}
    # poll'ы с явной диагностикой (warmup=False, coverage) — иначе легаси
    # без coverage честно даёт coverage_unknown (92d4079)
    polls = [{'pair': 'PUSDT', 't': t, 'ok': True, 'warmup': False,
              'coverage': 'caught_up', 'trades': [trade]} for t in range(3)]
    for suffix, rows in [('depth', depth), ('trades', polls)]:
        with open(tmp_path / f'{rid}_{suffix}.jsonl', 'w') as f:
            for row in rows:
                f.write(json.dumps(row) + '\n')
    with open(tmp_path / f'{rid}_manifest.json', 'w') as f:
        json.dump({'started_utc': '2026-10-02T00:00:00Z'}, f)
    arep = analyze_run(str(tmp_path), rid)
    p = arep['pairs'][0]
    assert p['n_valid_depth'] == 1
    assert p['n_unique_trades'] == 1
    assert p['n_duplicates'] == 2
    assert p['trade_coverage'] != 'coverage_unknown'


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-q']))