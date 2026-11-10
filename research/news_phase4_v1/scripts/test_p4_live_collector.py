"""Entire collector runs against fake HTTP captures and a private tempfile only."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from p4_evidence_store import EvidenceStore, StoreError
from p4_live_collector import (capture_public, collect_once, probe,
                               _market_evidence, main, CollectorError)
from p4_public_sources import (Captured, FEEDS, BINANCE_TIME, BINANCE_KLINES,
                               check_binance_time, parse_feed, CaptureError)
from p4_market_features import HOUR_MS

T=(1_800_000_000_000//HOUR_MS)*HOUR_MS+1_200_000
H='a'*64


def rss(title='Bitcoin ETF market sees a major change'):
    return (f'<rss><channel><item><title>{title}</title>'
            f'<description>Bitcoin news today</description>'
            f'<link>https://news.example/story</link>'
            f'<pubDate>Sat, 15 Jan 2027 00:00:00 GMT</pubDate>'
            f'</item></channel></rss>').encode()


def feed_time():
    from datetime import datetime,timezone
    from email.utils import format_datetime
    return format_datetime(datetime.fromtimestamp((T-3_600_000)/1000,
                         tz=timezone.utc)).encode()


def fixture_rss(title='Bitcoin ETF market sees a major change'):
    return (b'<rss><channel><item><title>'+title.encode()+
            b'</title><description>Bitcoin market and ETF policy</description>'
            b'<link>https://news.example/story</link><pubDate>'+
            feed_time()+b'</pubDate></item></channel></rss>')


def fixture_candles():
    head=(T//HOUR_MS)*HOUR_MS
    return [[head-(47-i)*HOUR_MS,'100','101','99',str(30000+i),
             str(100+i),head-(46-i)*HOUR_MS-1,'0','0','0','0','0']
            for i in range(48)]


def fake_fetch(url,*,max_bytes):
    if url in FEEDS.values():
        body=fixture_rss()
        observed=T-20_000
    elif url==BINANCE_TIME:
        body=json.dumps({'serverTime':T-5_000}).encode()
        observed=T-5_000
    elif url==BINANCE_KLINES:
        body=json.dumps(fixture_candles()).encode()
        observed=T-1_000
    else:
        raise AssertionError('unapproved_network')
    if len(body)>max_bytes:
        raise CaptureError('bound_exceeded')
    return Captured(url,body,observed-500,observed,200)


class CollectorTests(unittest.TestCase):
    def test_market_digest_reproducible(self):
        f,p=_market_evidence(fixture_candles(),T)
        from hashlib import sha256
        self.assertEqual(sha256(p).hexdigest(),f['digest'])
        self.assertEqual(f['candle_count'],26)
    def test_full_probe_pure(self):
        p=probe(fake_fetch,lambda:T)
        self.assertEqual(p['feeds_ok'],4)
        self.assertEqual(p['market_closed_candles'],26)
        self.assertEqual(p['news_candidates'],1)
        self.assertEqual(p['exchange_skew_ms'],250)
    def test_feed_unsafe_xml(self):
        c=Captured(FEEDS['decrypt'],b'<!DOCTYPE rss><rss/>',T-1,T,200)
        with self.assertRaises(CaptureError): parse_feed(c,'decrypt')
    def test_feed_rss_timestamp(self):
        c=fake_fetch(FEEDS['decrypt'],max_bytes=750_000)
        out=parse_feed(c,'decrypt')
        self.assertEqual(len(out),1)
        self.assertEqual(out[0][1]['first_http_seen_ms'],T-20_000)
    def test_binance_clock_skew(self):
        c=fake_fetch(BINANCE_TIME,max_bytes=2048)
        self.assertEqual(check_binance_time(c),250)
    def test_binance_clock_mismatch(self):
        c=Captured(BINANCE_TIME,json.dumps({'serverTime':T+10_000}).encode(),
                   T-5_500,T-5_000,200)
        with self.assertRaises(CaptureError): check_binance_time(c)
    def test_fail_closed_when_all_feeds_down(self):
        def bad(url,**kwargs):
            if url in FEEDS.values(): raise CaptureError('http_not_ok')
            return fake_fetch(url,**kwargs)
        with self.assertRaises(CollectorError):
            capture_public(bad,lambda:T)
    def test_partial_feed_outage(self):
        def flaky(url,**kwargs):
            if url==FEEDS['coindesk']:raise CaptureError('http_not_ok')
            return fake_fetch(url,**kwargs)
        r=capture_public(flaky,lambda:T)
        self.assertEqual(r['feed_ok'],3)
        self.assertEqual(r['feed_failed'],1)
    def test_clock_not_before_http_end(self):
        with self.assertRaises(CollectorError):
            capture_public(fake_fetch,lambda:T-2_000)
    def test_entry_collect_and_dedupe(self):
        with tempfile.TemporaryDirectory() as temp:
            with EvidenceStore(Path(temp)/'p4') as s:
                first=collect_once(s,fake_fetch,lambda:T)
                self.assertEqual(first['events_created'],1)
                self.assertEqual(first['newly_observed'],1)
                self.assertEqual(s.counts()['events'],1)
                second=collect_once(s,fake_fetch,lambda:T)
                self.assertEqual(second['events_created'],0)
                self.assertEqual(second['newly_observed'],0)
                self.assertEqual(s.db.execute('SELECT COUNT(*) FROM features').fetchone()[0],2)
                self.assertEqual(s.db.execute('SELECT COUNT(*) FROM source_missing').fetchone()[0],1)
    def test_raw_evidence_private_immutable(self):
        with tempfile.TemporaryDirectory() as temp:
            with EvidenceStore(Path(temp)/'p4') as s:
                s.write_blob(b'{"news":"ok"}')
                blobs=list((s.root/'evidence').glob('*.gz'))
                self.assertEqual(len(blobs),1)
                self.assertEqual(blobs[0].stat().st_mode&0o777,0o600)
                self.assertEqual(s.write_blob(b'{"news":"ok"}'),blobs[0].name[:64])
    def test_symlink_root_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'alias').symlink_to(root,target_is_directory=True)
            with self.assertRaises(StoreError):
                with EvidenceStore(root/'alias'): pass
    def test_store_evidence_cap_enforced(self):
        import p4_evidence_store as mod
        with tempfile.TemporaryDirectory() as temp:
            with EvidenceStore(Path(temp)/'p4') as s:
                with patch.object(mod,'MAX_STORE_BYTES',s._disk_bytes()+mod.SAFETY_MARGIN+3):
                    with self.assertRaises(StoreError): s.write_blob(b'aaa'*200)
    def test_readonly_status_does_not_create(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'nonexistent'
            from p4_live_collector import read_status
            self.assertEqual(read_status(root)['status'],'not_initialized')
            self.assertFalse(root.exists())
    def test_collect_requires_opt_in(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'production'
            with contextlib.redirect_stderr(io.StringIO()):
                rc=main(['--mode','collect','--root',str(root)])
            self.assertEqual(rc,2)
            self.assertFalse(root.exists())
    def test_coindesk_canonical_rss_endpoint_allowed(self):
        from p4_public_sources import _safe_url, ALLOWED_STATIC_URLS
        canonical='https://www.coindesk.com/arc/outboundfeeds/rss'
        redirected_from=canonical+'/'
        self.assertEqual(FEEDS['coindesk'],canonical)
        self.assertIn(canonical,ALLOWED_STATIC_URLS)
        _safe_url(canonical)
        # Redirect handling must remain exact-string allowlisted, not
        # broaden to any URL on this publisher host.
        with self.assertRaises(CaptureError):
            _safe_url(redirected_from)
        with self.assertRaises(CaptureError):
            _safe_url(canonical+'?tracking=1')
        with self.assertRaises(CaptureError):
            _safe_url('https://www.coindesk.com/arc/outboundfeeds/private')

    def test_private_binance_endpoint_never_allowed(self):
        from p4_public_sources import _safe_url
        with self.assertRaises(CaptureError):
            _safe_url('https://data-api.binance.vision/api/v3/order')

    def test_only_exact_one_minute_maturity_query_allowed(self):
        from p4_public_sources import _safe_url
        url='https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1m&startTime=1794140400000&limit=1'
        _safe_url(url)
        with self.assertRaises(CaptureError):
            _safe_url(url.replace('BTCUSDT','ETHUSDT'))

    def test_no_model_calls_possible_here(self):
        import inspect,p4_live_collector
        source=inspect.getsource(p4_live_collector)
        self.assertNotIn('session.prompt',source)
        self.assertNotIn('session.create',source)
        self.assertNotIn('api/v3/order',source)
        self.assertNotIn('news-phase3',source)


if __name__=='__main__': unittest.main()
