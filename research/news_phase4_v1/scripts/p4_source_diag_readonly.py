#!/usr/bin/env python3
"""Read-only Phase4 RSS -> immutable SQLite reconciliation.

Fetches ONLY the already approved public RSS URLs, never creates/updates SQLite,
never requests model inference and never prints headlines, URLs or credentials.
For diagnostic use against the production Phase4 root.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from urllib.parse import quote

from p4_public_sources import FEEDS, fetch_public, parse_feed, now_ms

DEFAULT_ROOT=Path.home()/'.local/share/news-phase4'
TWO_HOURS=2*3_600_000


def reconcile(root,fetcher=fetch_public,clock=now_ms):
    root=Path(root).expanduser().absolute()
    dbpath=root/'news.sqlite'
    if root.is_symlink() or dbpath.is_symlink() or not dbpath.is_file():
        raise ValueError('invalid_existing_readonly_root')
    # file mode=ro AND PRAGMA query_only: no persistent data writes.
    uri='file:'+quote(str(dbpath),safe='/')+'?mode=ro'
    con=sqlite3.connect(uri,uri=True,timeout=5)
    try:
        con.execute('PRAGMA query_only=ON')
        before=con.execute('SELECT COUNT(*),COALESCE(MAX(first_seen_ms),0) FROM news_observations').fetchone()
        existing={fp for (fp,) in con.execute('SELECT fingerprint FROM news_observations')}
        claimed={fp for (fp,) in con.execute('SELECT fingerprint FROM claimed_events')}
        events=con.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        now=clock()
        fresh_pending=con.execute(
            'SELECT COUNT(*) FROM news_observations n '
            'LEFT JOIN claimed_events c ON c.fingerprint=n.fingerprint '
            'WHERE c.fingerprint IS NULL AND n.first_seen_ms BETWEEN ? AND ?',
            (now-TWO_HOURS,now)).fetchone()[0]
    finally:
        con.close()

    unique={}
    per_feed=[]
    for source,url in FEEDS.items():
        try:
            capture=fetcher(url,max_bytes=750_000)
            items=parse_feed(capture,source)
            for fp,news in items:
                unique.setdefault(fp,news)
            per_feed.append({
                'source':source,'ok':True,'rss_candidates':len(items),
                'unseen_fingerprints':sum(fp not in existing for fp,_ in items),
                'unseen_published_last_2h':sum(
                    fp not in existing and now-TWO_HOURS<=n['published_ms']<=now
                    for fp,n in items),
                'newest_publication_age_min':(
                    round((now-max(n['published_ms'] for _,n in items))/60000,1)
                    if items else None),
                'fetch_duration_ms':capture.observed_ms-capture.started_ms
            })
        except Exception as err:
            # Only an error class; do not print network exception details or URLs.
            per_feed.append({'source':source,'ok':False,'error_type':type(err).__name__})

    unseen={fp:news for fp,news in unique.items() if fp not in existing}
    output={
        'status':'ok',
        'mode':'read_only_rss_sqlite_reconciliation',
        'root_db_path_hash_prefix':hashlib.sha256(str(dbpath).encode()).hexdigest()[:12],
        'audit_ms':now,
        'sqlite_observations':before[0],
        'sqlite_newest_first_seen_age_min':(
            round((now-before[1])/60000,1) if before[1] else None),
        'sqlite_events':events,
        'sqlite_unclaimed_first_seen_2h':fresh_pending,
        'feeds':per_feed,
        'unique_rss_candidates':len(unique),
        'rss_unseen_fingerprints':len(unseen),
        'rss_unseen_published_last_2h':sum(
            now-TWO_HOURS<=v['published_ms']<=now for v in unseen.values()),
        'rss_unseen_older_than_2h':sum(v['published_ms']<now-TWO_HOURS for v in unseen.values()),
        'rss_in_db_but_unclaimed':sum(fp in existing and fp not in claimed for fp in unique),
        'source_module_sha256_prefix':hashlib.sha256(
            (Path(__file__).parent/'p4_public_sources.py').read_bytes()).hexdigest()[:16],
    }
    return output


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=DEFAULT_ROOT)
    a=p.parse_args()
    try:
        result=reconcile(a.root)
        print(json.dumps(result,sort_keys=True,separators=(',',':')))
    except (OSError,ValueError,sqlite3.Error) as exc:
        print(json.dumps({'status':'error','reason_type':type(exc).__name__}),file=sys.stderr)
        raise SystemExit(2)
