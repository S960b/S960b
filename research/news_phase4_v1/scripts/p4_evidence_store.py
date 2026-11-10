"""Isolated Phase 4 evidence store. No network, SDK calls, or Phase 3 access.

Storage is opt-in via opening this object; imports do not create any files.
All events / first-seen rows are append-only and serialized under a local flock.
Compressed source evidence is content-addressed, mode 0600, never pruned.
The whole Phase 4 root is guarded against a 100 MiB physical allocation budget.
"""
from contextlib import contextmanager
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat

from p4_collector_schema import SCHEMA_SQL, SCHEMA_VERSION
from p4_forward_evidence import seal_evidence

MAX_STORE_BYTES=100*1024*1024
SAFETY_MARGIN=8*1024*1024
MAX_RAW_BYTES=750_000
MAX_NEW_PER_TICK=2
FRESH_NEWS_MS=2*3_600_000


class StoreError(ValueError):
    pass


def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(',',':'),
                      ensure_ascii=True,allow_nan=False).encode('utf8')


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _secure_dir(path):
    path=Path(path)
    if path.is_symlink():
        raise StoreError('unsafe_directory_symlink')
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    st=path.lstat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.geteuid() or
            stat.S_IMODE(st.st_mode)&0o077):
        raise StoreError('unsafe_directory_permissions')
    return path


def _secure_file(path,flags):
    fd=os.open(path,flags|os.O_NOFOLLOW,0o600)
    st=os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or stat.S_IMODE(st.st_mode)&0o077:
        os.close(fd)
        raise StoreError('unsafe_file_permissions')
    return fd


class EvidenceStore:
    def __init__(self,root):
        self.root=Path(root)
        self.db=None
        self.lock_fd=None
        self.evidence=None

    def __enter__(self):
        try:
            self.root=_secure_dir(self.root)
            self.evidence=_secure_dir(self.root/'evidence')
            self.lock_fd=_secure_file(self.root/'.lock',os.O_CREAT|os.O_RDWR)
            try:
                fcntl.flock(self.lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                raise StoreError('collector_already_running') from None
            path=self.root/'news.sqlite'
            if path.is_symlink():
                raise StoreError('unsafe_db_symlink')
            new=not path.exists()
            if new:
                fd=_secure_file(path,os.O_CREAT|os.O_EXCL|os.O_RDWR)
                os.close(fd)
            else:
                fd=_secure_file(path,os.O_RDWR)
                os.close(fd)
            self.db=sqlite3.connect(str(path),timeout=5)
            self.db.execute('PRAGMA foreign_keys=ON')
            self.db.execute('PRAGMA journal_mode=DELETE')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('PRAGMA busy_timeout=5000')
            self.db.execute('PRAGMA max_page_count=4096')
            if new:
                self.db.executescript(SCHEMA_SQL)
                self.db.execute('INSERT INTO meta(key,value) VALUES(?,?)',
                                ('schema_version',str(SCHEMA_VERSION)))
                self.db.commit()
            row=self.db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row!=(str(SCHEMA_VERSION),):
                raise StoreError('unsupported_schema_version_no_auto_migration')
            self._capacity_check()
            return self
        except BaseException:
            self.__exit__(None,None,None)
            raise

    def __exit__(self,*exc):
        if self.db is not None:
            self.db.close()
            self.db=None
        if self.lock_fd is not None:
            fcntl.flock(self.lock_fd,fcntl.LOCK_UN)
            os.close(self.lock_fd)
            self.lock_fd=None

    def _disk_bytes(self):
        total=0
        for path in self.root.rglob('*'):
            st=path.lstat()
            if stat.S_ISLNK(st.st_mode):
                raise StoreError('unsafe_store_symlink')
            if stat.S_ISREG(st.st_mode):
                total+=st.st_size
        return total

    def _capacity_check(self,extra=0):
        if (type(extra) is not int or extra<0 or
                self._disk_bytes()+extra+SAFETY_MARGIN > MAX_STORE_BYTES):
            raise StoreError('phase4_100mib_cap_reached')

    def write_blob(self,raw):
        """Create immutable gz evidence, then fsync; can leave safe orphan on DB error."""
        if not isinstance(raw,bytes) or not 0<len(raw)<=MAX_RAW_BYTES:
            raise StoreError('invalid_evidence_size')
        digest=sha256(raw)
        path=self.evidence/(digest+'.json.gz')
        packed=gzip.compress(raw,compresslevel=9,mtime=0)
        if path.exists():
            if path.is_symlink():
                raise StoreError('unsafe_evidence_symlink')
            with path.open('rb') as f:
                old=f.read(MAX_RAW_BYTES*2+100)
            try:
                if gzip.decompress(old)!=raw:
                    raise StoreError('evidence_hash_collision')
            except (OSError,EOFError):
                raise StoreError('damaged_evidence_blob') from None
            return digest
        self._capacity_check(len(packed))
        temp=self.evidence/('.pending-'+digest)
        try:
            fd=_secure_file(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL)
            with os.fdopen(fd,'wb') as f:
                f.write(packed)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp,path)
            dir_fd=os.open(self.evidence,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try: os.fsync(dir_fd)
            finally: os.close(dir_fd)
        finally:
            if temp.exists():
                temp.unlink()  # only uncommitted temporary file owned by this call
        return digest

    def observe(self,items):
        """Record all newly seen RSS candidates, even if not chosen for an event."""
        if not isinstance(items,list) or len(items)>240:
            raise StoreError('too_many_rss_candidates')
        self._capacity_check()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            inserted=0
            for fingerprint,news in items:
                payload=canonical(news)
                observed=news['first_http_seen_ms']
                if (type(fingerprint) is not str or len(fingerprint)!=64 or
                        any(c not in '0123456789abcdef' for c in fingerprint) or
                        type(observed) is not int or observed<=0 or len(payload)>4000):
                    raise StoreError('invalid_story_observation')
                cur=self.db.execute('INSERT OR IGNORE INTO news_observations VALUES(?,?,?,?)',
                                    (fingerprint,observed,payload.decode('utf8'),sha256(payload)))
                inserted+=cur.rowcount
            self.db.commit()
            self._capacity_check()
            return inserted
        except BaseException:
            self.db.rollback()
            raise

    def pending(self,now_ms,limit=MAX_NEW_PER_TICK):
        if (type(now_ms) is not int or now_ms<=0 or
                type(limit) is not int or not 0<limit<=MAX_NEW_PER_TICK):
            raise StoreError('invalid_pending_query')
        rows=self.db.execute(
            'SELECT n.fingerprint,n.first_seen_ms,n.news_json,n.news_digest '
            'FROM news_observations n LEFT JOIN claimed_events c USING(fingerprint) '
            'WHERE c.fingerprint IS NULL AND n.first_seen_ms BETWEEN ? AND ? '
            'ORDER BY n.first_seen_ms ASC,n.fingerprint ASC LIMIT ?',
            (now_ms-FRESH_NEWS_MS,now_ms,limit)).fetchall()
        return [(fp,first,json.loads(content),digest) for fp,first,content,digest in rows]

    def seal(self,story,market_payload,market_result,cutoff_ms,created_ms):
        """Store genuine pre-cutoff news and market evidence before atomic row inserts.

        Caller verifies clock skew and supplies 26 fully closed candles.
        Untrusted model prompts are NOT constructed here.
        """
        fp,first,news,stored_digest=story
        news_raw=canonical(news)
        if sha256(news_raw)!=stored_digest:
            raise StoreError('news_first_seen_payload_changed')
        if sha256(market_payload)!=market_result['digest']:
            raise StoreError('market_payload_feature_digest_mismatch')
        news_observed=news['first_http_seen_ms']
        news_rec={'source_id':'rss-allowlist-v1','available_ms':first,
                  'observed_ms':news_observed,'digest':stored_digest,
                  'size_bytes':len(news_raw)}
        market_rec={'source_id':'binance-spot-btcusdt-1h-v1',
                    'available_ms':market_result['available_ms'],
                    'observed_ms':market_result['observed_ms'],
                    'digest':market_result['digest'],
                    'size_bytes':len(market_payload)}
        envelope=seal_evidence(
            news=news_rec,market=market_rec,news_first_seen_ms=first,
            cutoff_ms=cutoff_ms,created_ms=created_ms,
            social_missing_reason='unavailable',social_checked_ms=cutoff_ms)
        self._capacity_check(len(news_raw)+len(market_payload)+8192)
        self.write_blob(news_raw)
        self.write_blob(market_payload)
        self.write_blob(canonical(envelope['frozen']))
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT first_seen_ms,news_digest FROM news_observations '
                                'WHERE fingerprint=?',(fp,)).fetchone()
            if row!=(first,stored_digest):
                raise StoreError('news_observation_conflict')
            self.db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',envelope['event'])
            self.db.executemany('INSERT INTO features VALUES (?,?,?,?,?)',envelope['features'])
            self.db.executemany('INSERT INTO source_missing VALUES (?,?,?,?)',envelope['source_missing'])
            self.db.execute('INSERT INTO claimed_events VALUES (?,?,?)',
                            (fp,envelope['event'][0],created_ms))
            self.db.commit()
            self._capacity_check()
            return envelope['event'][0]
        except BaseException:
            self.db.rollback()
            raise

    def counts(self):
        return dict(zip(('observed','events','evidence_bytes'),
               (self.db.execute('SELECT COUNT(*) FROM news_observations').fetchone()[0],
                self.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],
                self._disk_bytes())))
