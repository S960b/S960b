"""Регрессии maker-фиксов (ревью 738de3a, P0.1-P0.6). Offline, без сети."""
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maker.feasibility import (_fetch_trades, _vwap_for_budget, run_screen, screen_pair,
                               SafeTradeRateLimiter, safe_http)
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
        raise RuntimeError('synthetic HTTP 429')


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


async def main():
    out = {'reviewed_commit': 'fixed', 'network_calls': 0}

    # P0.4: partial depth НЕ выдаётся за полное покрытие бюджета
    v = _vwap_for_budget([['99', '.01']], [['100', '.01']], 10)
    out['budget10_with_only1usdt_ask'] = v
    assert v['ask_cov'] == 'partial', v

    # P0.5: повтор страницы не даёт false full; перекрытие страниц обрабатывается
    for name, adapter in [('repeated_page', RepeatedPages()),
                          ('overlapping_full_page', OverlappingPages()),
                          ('failed_request', FailedRequest())]:
        trades, cov, failed = await _fetch_trades(adapter, 'p', ImmediateLimiter(), pages=3)
        out[name] = {'n_records': len(trades), 'coverage': cov, 'request_failed': failed}
    assert out['repeated_page']['coverage'] in ('full', 'full_at_page')
    assert out['repeated_page']['n_records'] == 100, out['repeated_page']
    assert out['overlapping_full_page']['n_records'] == 199, out['overlapping_full_page']
    assert out['failed_request']['request_failed'] is True
    assert out['failed_request']['coverage'] == 'request_failed'

    # P0.6: missing book -> НЕ pass
    with patch('maker.feasibility.SafeTradeAdapter', MissingBook):
        res = await run_screen(depth_snaps=1, trade_pages=1, rpm=1e9)
        p = res.pairs[0]
        out['missing_book'] = {'candidate_status': p.candidate_status,
                               'n_depth': p.n_depth, 'n_depth_valid': p.n_depth_valid,
                               'min_notional_est': p.min_notional_est, 'reason': p.reason}
    assert p.candidate_status == 'insufficient_evidence', p

    # P0.3: TZ-независимость
    old_tz = os.environ.get('TZ')
    try:
        # сделки mock в 10:00Z и 09:59Z; now = 10:05:00Z -> возраст 5 и 6 мин
        now_ts = datetime.fromisoformat('2026-10-03T10:05:00+00:00').timestamp()
        out['trade_age_same_utc_input'] = {}
        for zone in ('UTC', 'Europe/Moscow'):
            os.environ['TZ'] = zone
            time.tzset()
            with patch('maker.feasibility.time.time', return_value=now_ts):
                p = await screen_pair(TimestampTrades(), 'p', 'PUSDT', 'P',
                    {'status': 'enabled', 'min_qty': '1'}, ImmediateLimiter(),
                    depth_snaps=0, trade_pages=1)
            out['trade_age_same_utc_input'][zone] = p.last_trade_age_min
        assert abs(out['trade_age_same_utc_input']['UTC'] -
                   out['trade_age_same_utc_input']['Europe/Moscow']) < 0.01
        assert abs(out['trade_age_same_utc_input']['UTC'] - 5.0) < 0.5, out
    finally:
        if old_tz is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = old_tz
        time.tzset()

    # parse_iso_utc: aware + дробные секунды
    assert parse_iso_utc('2026-10-03T10:00:00Z') == parse_iso_utc('2026-10-03T13:00:00+03:00')
    assert abs(parse_iso_utc('2026-10-03T10:00:00.5Z') - (parse_iso_utc('2026-10-03T10:00:00Z') + 0.5)) < 1e-6

    # valid_book: пустой/crossed
    assert valid_book([], []) == (False, None, None)
    ok, bb, ba = valid_book([['102', '1']], [['100', '1']])
    assert not ok, 'crossed должен быть invalid'

    # P0.2: analyzer unique vs records vs in_window
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        rid = 'mk_review_fixture'
        depth = [
            {'pair': 'PUSDT', 't': 1_000_000_000, 'ok': True,
             'bids': [['99', '.01']], 'asks': [['100', '.01']]},
            {'pair': 'PUSDT', 't': 61_000_000_000, 'ok': True, 'bids': [], 'asks': []},
            {'pair': 'PUSDT', 't': 121_000_000_000, 'ok': True,
             'bids': [['102', '1']], 'asks': [['100', '1']]},  # crossed
        ]
        trade = {'id': 1, 'created_at': '2026-10-02T00:00:00Z',
                 'price': '100', 'amount': '.01', 'total': '1'}
        polls = [{'pair': 'PUSDT', 't': t, 'ok': True, 'trades': [trade]} for t in range(3)]
        for suffix, rows in [('depth', depth), ('trades', polls)]:
            with open(Path(td) / f'{rid}_{suffix}.jsonl', 'w') as f:
                for row in rows:
                    f.write(json.dumps(row) + '\n')
        arep = analyze_run(td, rid)
        p = arep['pairs'][0]
        out['analyzer'] = {'n_valid_depth': p.get('n_valid_depth'),
                           'n_unique_trades': p.get('n_unique_trades'),
                           'n_duplicates': p.get('n_duplicates'),
                           'spread': p.get('spread_p50_bps')}
        assert p['n_valid_depth'] == 1, p
        assert p['n_unique_trades'] == 1, p
        assert p['n_duplicates'] == 2, p

    print(json.dumps(out, ensure_ascii=False, indent=2))
    print('ALL MAKER REVIEW REGRESSIONS PASSED')


if __name__ == '__main__':
    asyncio.run(main())