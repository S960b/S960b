#!/usr/bin/env python3
"""NEWS Phase 4 isolated public-data capture CLI.

Status and probe are read-only; collect explicitly enables local Phase4 writes.
No private Binance API, model calls, social collection, trading or Phase3 access.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

from p4_evidence_store import EvidenceStore, StoreError, canonical, sha256
from p4_market_features import (closed_precutoff_features, MarketFeatureError,
                                MARKET_FEATURE_VERSION, HOUR_MS, _read)
from p4_public_sources import (FEEDS, BINANCE_TIME, BINANCE_KLINES,
                               MAX_API_BYTES, Captured, CaptureError,
                               fetch_public, parse_feed, decode_json, check_binance_time,
                               now_ms)

DEFAULT_ROOT = Path.home()/'.local/share/news-phase4'
MAX_EVENTS_PER_CYCLE = 2


class CollectorError(ValueError):
    pass


def _market_evidence(rows,cutoff_ms):
    """Canonical 26-bar bytes MUST match the pure builder's exact hash."""
    result=closed_precutoff_features(rows,cutoff_ms,skew_ms=3000)
    closed=sorted((_read(row) for row in rows if
                   type(row) in (list,tuple) and len(row)>=7),
                  key=lambda r:r[0])
    recent=[r for r in closed if r[1]+3000<=cutoff_ms][-26:]
    evidence=canonical({'version':MARKET_FEATURE_VERSION,'symbol':'BTCUSDT',
                        'interval':'1h','closed_candles':recent})
    if len(recent)!=26 or sha256(evidence)!=result['digest']:
        raise CollectorError('market_digest_mismatch')
    return result,evidence


def capture_public(fetcher=fetch_public, clock=now_ms):
    """Fetch 4 allowlisted RSS and two public Binance endpoints.

    Does not make filesystem writes. Caller decides whether to persist.
    """
    found,ok,failed=[],0,0
    for source,url in FEEDS.items():
        try:
            capture=fetcher(url,max_bytes=750_000)
            if not isinstance(capture,Captured) or capture.url!=url:
                raise CollectorError('untrusted_feed_capture')
            found.extend(parse_feed(capture,source))
            ok+=1
        except (CaptureError,CollectorError,ValueError):
            failed+=1
    if ok==0:
        raise CollectorError('all_news_sources_unavailable')
    # First observation of each normalized headline wins, independent of RSS order.
    unique={}
    for fp,item in found:
        if fp not in unique or item['first_http_seen_ms']<unique[fp]['first_http_seen_ms']:
            unique[fp]=item
    if not unique:
        return {'news':[],'feed_ok':ok,'feed_failed':failed,
                'market':None,'cutoff_ms':None,'exchange_skew_ms':None}
    clock_sample=fetcher(BINANCE_TIME,max_bytes=2048)
    if not isinstance(clock_sample,Captured):
        raise CollectorError('invalid_clock_capture')
    skew=check_binance_time(clock_sample)
    candles_sample=fetcher(BINANCE_KLINES,max_bytes=MAX_API_BYTES)
    if not isinstance(candles_sample,Captured) or candles_sample.url!=BINANCE_KLINES:
        raise CollectorError('invalid_kline_capture')
    cutoff=clock()
    if (type(cutoff) is not int or
            cutoff<max(clock_sample.observed_ms,candles_sample.observed_ms) or
            cutoff-clock_sample.observed_ms>60_000):
        raise CollectorError('clock_sample_stale_or_reversed')
    data=decode_json(candles_sample)
    if not isinstance(data,list) or not 26<=len(data)<=48:
        raise CollectorError('invalid_kline_count')
    result,payload=_market_evidence(data,cutoff)
    result={**result,'observed_ms':candles_sample.observed_ms,
            'started_ms':candles_sample.started_ms,
            'response_digest':hashlib.sha256(candles_sample.body).hexdigest()}
    if result['available_ms']>result['observed_ms']:
        raise CollectorError('market_was_not_observed_when_available')
    return {'news':list(unique.items()),'feed_ok':ok,'feed_failed':failed,
            'market':{'features':result,'payload':payload},
            'cutoff_ms':cutoff,'exchange_skew_ms':skew}


def collect_once(store,fetcher=fetch_public,clock=now_ms):
    """One bounded synchronous collection cycle; NO model, quote or social calls."""
    if not isinstance(store,EvidenceStore) or store.db is None:
        raise CollectorError('store_not_open')
    batch=capture_public(fetcher,clock)
    inserted=store.observe(batch['news'])
    pending=store.pending(clock(),limit=MAX_EVENTS_PER_CYCLE)
    created=[]
    if pending and batch['market'] is not None:
        result=batch['market']['features']
        payload=batch['market']['payload']
        for story in pending:
            stamp=clock()
            eid=store.seal(story,payload,result,batch['cutoff_ms'],stamp)
            created.append(eid[:16])
    return {'status':'ok','mode':'capture_only','models':'off',
            'feeds_ok':batch['feed_ok'],'feeds_failed':batch['feed_failed'],
            'newly_observed':inserted,'events_created':len(created),
            'event_id_prefixes':created,'skew_ms':batch['exchange_skew_ms'],
            'stored':store.counts()}


def probe(fetcher=fetch_public,clock=now_ms):
    batch=capture_public(fetcher,clock)
    market=batch['market']
    return {'status':'ok','mode':'read_only_probe','news_candidates':len(batch['news']),
            'feeds_ok':batch['feed_ok'],'feeds_failed':batch['feed_failed'],
            'exchange_skew_ms':batch['exchange_skew_ms'],
            'market_closed_candles':market['features']['candle_count'] if market else 0,
            'market_digest_prefix':market['features']['digest'][:16] if market else None}


def read_status(root):
    path=Path(root)/'news.sqlite'
    if not path.is_file() or path.is_symlink():
        return {'status':'not_initialized','mode':'read_only_status'}
    db=sqlite3.connect(f'file:{path.as_posix()}?mode=ro',uri=True)
    try:
        return {'status':'ok','mode':'read_only_status',
                'news_observed':db.execute('SELECT COUNT(*) FROM news_observations').fetchone()[0],
                'events':db.execute('SELECT COUNT(*) FROM events').fetchone()[0],
                'attempts':db.execute('SELECT COUNT(*) FROM attempt_reservations').fetchone()[0],
                'decisions':db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0]}
    finally:
        db.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=('status','probe','collect'),default='status')
    parser.add_argument('--root',type=Path,default=DEFAULT_ROOT)
    parser.add_argument('--enable-collector',action='store_true',default=False)
    args=parser.parse_args(argv)
    try:
        if args.mode=='status':
            output=read_status(args.root)
        elif args.mode=='probe':
            output=probe()
        elif args.enable_collector:
            with EvidenceStore(args.root) as store:
                output=collect_once(store)
        else:
            raise CollectorError('collector_requires_explicit_opt_in')
    except (CollectorError,CaptureError,StoreError,MarketFeatureError,
            OSError,sqlite3.Error,ValueError):
        output={'status':'error','code':'collector_fail_closed'}
        print(json.dumps(output),file=sys.stderr)
        return 2
    print(json.dumps(output,sort_keys=True,separators=(',',':')))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
