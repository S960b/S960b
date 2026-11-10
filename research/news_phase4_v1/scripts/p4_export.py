#!/usr/bin/env python3
"""READ-ONLY Phase4 paired paper-study exporter: no raw RSS, credentials or writes.

Uses the existing strict offline p4_compare evaluator. SQL may contain other
partial/unmatched events; these are reported as coverage, never silently
fabricated into scored rows. No retrospective model inference.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
from time import time_ns

from p4_compare import evaluate, validate_event, InvalidStudy

MAX_EXPORT_EVENTS=5000


class ExportError(ValueError):
    pass


def open_read_only(root):
    path=Path(root)/'news.sqlite'
    if not path.is_file() or path.is_symlink():
        raise ExportError('phase4_db_absent')
    uri=path.absolute().as_uri()+'?mode=ro'
    db=sqlite3.connect(uri,uri=True)
    db.execute('PRAGMA query_only=ON')
    db.execute('PRAGMA foreign_keys=ON')
    version=db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if version!=('4',):
        db.close()
        raise ExportError('unsupported_schema')
    return db


def coverage(db):
    def count(query):
        return db.execute(query).fetchone()[0]
    return {'events':count('SELECT COUNT(*) FROM events'),
            'news_first_seen':count('SELECT COUNT(*) FROM news_observations'),
            'model_attempts':count('SELECT COUNT(*) FROM attempt_reservations'),
            'decisions':count('SELECT COUNT(*) FROM decisions'),
            'paired_decisions':count("""SELECT COUNT(*) FROM events e WHERE
                (SELECT COUNT(*) FROM decisions d WHERE d.event_id=e.event_id
                   AND d.arm IN ('news','news_market'))=2"""),
            'entry_quotes':count('SELECT COUNT(*) FROM entry_quotes'),
            'outcomes':count('SELECT COUNT(*) FROM outcomes'),
            'unknown_attempts':count("""SELECT COUNT(*) FROM attempt_reservations a
                WHERE (SELECT status FROM attempt_status_events s
                    WHERE s.attempt_id=a.attempt_id
                    ORDER BY ordinal DESC LIMIT 1)='unknown'""")}


def sealed_events(db,as_of_ms):
    if type(as_of_ms) is not int or as_of_ms<=0:
        raise ExportError('invalid_as_of')
    quotes=db.execute('''SELECT q.event_id,e.cutoff_ms,q.entry_ms,q.entry_price
        FROM entry_quotes q JOIN events e USING(event_id)
        WHERE q.entry_ms<=?
        ORDER BY q.entry_ms ASC LIMIT ?''',(as_of_ms,MAX_EXPORT_EVENTS+1)).fetchall()
    if len(quotes)>MAX_EXPORT_EVENTS:
        raise ExportError('too_many_sealed_events')
    events=[]
    for event_id,cutoff,entry,price in quotes:
        features={}
        for src,available,digest in db.execute(
            'SELECT source,available_ms,digest FROM features WHERE event_id=?',
            (event_id,)):
            features[src]={'available_ms':available,'digest':digest}
        decisions={}
        for arm,direction,confidence,provider,model,completed,hash_input in db.execute('''
            SELECT d.arm,d.direction,d.confidence,d.provider_id,d.model_id,
                   d.completed_ms,a.input_hash
            FROM decisions d JOIN attempt_reservations a
             ON a.event_id=d.event_id AND a.arm=d.arm
            WHERE d.event_id=?''',(event_id,)):
            decisions[arm]={'direction':direction,'confidence':confidence,
                            'model':provider+'/'+model,'completed_ms':completed,
                            'input_hash':hash_input}
        outcomes=[{'horizon_h':h,'exit_ms':exit_ms,'exit_price':exit_price,
                   'settled_ms':settled_ms}
                  for h,exit_ms,settled_ms,exit_price in db.execute('''
                   SELECT horizon_h,exit_ms,settled_ms,exit_price
                   FROM outcomes WHERE event_id=? AND settled_ms<=?
                   ORDER BY horizon_h''',(event_id,as_of_ms))]
        event={'event_id':event_id,'cutoff_ms':cutoff,'entry_ms':entry,
               'entry_price':price,'features':features,
               'decisions':decisions,'outcomes':outcomes}
        validate_event(event,as_of_ms)
        events.append(event)
    return events


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path.home()/'.local/share/news-phase4')
    p.add_argument('--mode',choices=('coverage','evaluate','jsonl'),default='coverage')
    p.add_argument('--as-of-ms',type=int)
    a=p.parse_args(argv)
    now=a.as_of_ms if a.as_of_ms is not None else time_ns()//1_000_000
    try:
        db=open_read_only(a.root)
        try:
            if a.mode=='coverage':
                result={'status':'ok','mode':'read_only','coverage':coverage(db)}
                print(json.dumps(result,sort_keys=True,separators=(',',':')))
            else:
                events=sealed_events(db,now)
                if a.mode=='evaluate':
                    result=evaluate(events,now)
                    result['coverage']=coverage(db)
                    print(json.dumps(result,sort_keys=True,separators=(',',':')))
                else:
                    for event in events:
                        print(json.dumps(event,sort_keys=True,separators=(',',':')))
        finally:
            db.close()
    except (ExportError,InvalidStudy,sqlite3.Error,OSError,ValueError):
        print(json.dumps({'status':'error','code':'phase4_export_fail_closed'}),file=sys.stderr)
        return 2
    return 0


if __name__=='__main__':
    raise SystemExit(main())
