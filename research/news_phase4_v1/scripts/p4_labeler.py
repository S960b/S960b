#!/usr/bin/env python3
"""Phase 4 delayed outcome labels: PUBLIC Binance 1m closed candles only.

No SDK/model calls, trading, private API or Phase3 writes.
Labels may run only after entry quote + horizon +120s safety guard.
Actual request/response time is preserved as settled_ms, not retroactively
backdated to the candle's exchange timestamp.
"""
import argparse
import json
import math
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlencode

from p4_evidence_store import EvidenceStore, StoreError
from p4_public_sources import (fetch_public,Captured,CaptureError,BINANCE_TIME,
                                check_binance_time,now_ms)

ROOT=Path.home()/'.local/share/news-phase4'
BASE='https://data-api.binance.vision/api/v3/klines'
MINUTE_MS=60_000
HOUR_MS=3_600_000
MAX_LABELS_PER_TICK=3


class LabelError(ValueError):
    pass


def require(value,code):
    if not value: raise LabelError(code)


def due_outcomes(db,now_ms_value,limit=MAX_LABELS_PER_TICK):
    require(type(now_ms_value) is int and now_ms_value>0,'invalid_label_clock')
    require(type(limit) is int and 1<=limit<=MAX_LABELS_PER_TICK,'invalid_label_limit')
    rows=db.execute('''
        SELECT e.event_id,e.entry_ms,h.h
        FROM entry_quotes e
        CROSS JOIN (SELECT 1 AS h UNION ALL SELECT 4 UNION ALL SELECT 24) h
        LEFT JOIN outcomes o ON o.event_id=e.event_id AND o.horizon_h=h.h
        WHERE o.event_id IS NULL
          AND e.entry_ms+h.h*3600000+120000<=?
        ORDER BY (e.entry_ms+h.h*3600000) ASC,e.event_id ASC
        LIMIT ?
    ''',(now_ms_value,limit)).fetchall()
    return rows


def outcome_url(entry_ms,horizon_h):
    require(type(entry_ms) is int and entry_ms>0 and horizon_h in (1,4,24),
            'invalid_outcome_request')
    due=entry_ms+horizon_h*HOUR_MS
    target=((due+MINUTE_MS-1)//MINUTE_MS)*MINUTE_MS
    query=urlencode({'symbol':'BTCUSDT','interval':'1m',
                     'startTime':target,'limit':1})
    return BASE+'?'+query,target,due


def verify_label(response,url,target,due):
    require(isinstance(response,Captured) and response.url==url and response.status==200,
            'unverified_label_source')
    require(response.started_ms>=due+120_000 and
            response.observed_ms>=response.started_ms and
            response.observed_ms-response.started_ms<=20_000,
            'label_before_maturity')
    try:
        rows=json.loads(response.body)
        require(type(rows) is list and len(rows)==1,'invalid_label_rows')
        row=rows[0]
        require(type(row) is list and len(row)>=7,'invalid_label_candle')
        open_ms,close_ms=row[0],row[6]
        require(type(open_ms) is int and type(close_ms) is int and
                open_ms==target and close_ms==target+MINUTE_MS-1 and
                close_ms<=response.observed_ms,'label_not_closed')
        price=float(row[4])
        require(math.isfinite(price) and 1000<price<10_000_000,'invalid_label_price')
        return close_ms,price,response.observed_ms
    except (ValueError,TypeError,IndexError,KeyError):
        raise LabelError('invalid_public_label') from None


def label_once(store,fetcher=fetch_public,clock=now_ms):
    """A bounded label tick; no inferred or interpolated price."""
    pending=due_outcomes(store.db,clock())
    if not pending:
        return {'status':'ok','labels_created':0,'reason':'none_due'}
    server=fetcher(BINANCE_TIME,max_bytes=2048)
    skew=check_binance_time(server)
    count=0
    for event_id,entry,h in pending:
        url,target,due=outcome_url(entry,h)
        resp=fetcher(url,max_bytes=4096)
        close,price,settled=verify_label(resp,url,target,due)
        store.write_blob(resp.body)
        store.db.execute('BEGIN IMMEDIATE')
        try:
            store.db.execute('INSERT INTO outcomes VALUES(?,?,?,?,?)',
                             (event_id,h,close,settled,price))
            store.db.commit()
        except BaseException:
            store.db.rollback()
            raise
        count+=1
    return {'status':'ok','labels_created':count,'exchange_skew_ms':skew}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('status','labels'),default='status')
    p.add_argument('--root',type=Path,default=ROOT)
    p.add_argument('--enable-labels',action='store_true',default=False)
    a=p.parse_args(argv)
    try:
        if a.mode=='status':
            from p4_live_collector import read_status
            out=read_status(a.root)
        elif not a.enable_labels:
            raise LabelError('labels_disabled')
        elif not (a.root/'news.sqlite').is_file():
            raise LabelError('phase4_store_absent')
        else:
            with EvidenceStore(a.root) as store:
                out=label_once(store)
    except (LabelError,StoreError,CaptureError,sqlite3.Error,
            OSError,ValueError,TypeError):
        print(json.dumps({'status':'error','code':'phase4_labels_fail_closed'}),file=sys.stderr)
        return 2
    print(json.dumps(out,sort_keys=True,separators=(',',':')))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
