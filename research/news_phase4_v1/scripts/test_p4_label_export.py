import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from urllib.parse import parse_qs,urlsplit

from p4_collector_schema import SCHEMA_SQL
from p4_labeler import (due_outcomes,outcome_url,verify_label,
                        label_once,LabelError,HOUR_MS,MINUTE_MS)
from p4_export import coverage,sealed_events,open_read_only,ExportError
from p4_public_sources import Captured,BINANCE_TIME
from p4_evidence_store import EvidenceStore

T=(1_800_000_000_000//60_000)*60_000
E='a'*64
H='b'*64
NOW=T+HOUR_MS+240_000
ENTRY=T+1000


def seed(db):
    db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',(E,T-10000,T-9000,H,'v1','v1'))
    db.execute('INSERT INTO features VALUES (?,?,?,?,?)',(E,'news',T-13000,T-12000,H))
    db.execute('INSERT INTO features VALUES (?,?,?,?,?)',(E,'market',T-13000,T-12000,H))
    for arm,aid in (('news','c'*64),('news_market','d'*64)):
        db.execute('INSERT INTO attempt_reservations VALUES (?,?,?,?,?)',(aid,E,arm,T-9000,H))
        db.execute('INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)',
                   (E,arm,'UP',0.5,'gateway','deepseek',T-1000,H))
    db.execute('INSERT INTO entry_quotes VALUES(?,?,?,?,?)',(E,ENTRY,ENTRY,35000.0,'binance_public'))
    db.commit()


def fake_fetch(url,*,max_bytes):
    if url==BINANCE_TIME:
        body=json.dumps({'serverTime':NOW-500}).encode()
        return Captured(url,body,NOW-1000,NOW,200)
    target=int(parse_qs(urlsplit(url).query)['startTime'][0])
    rows=[[target,'35000','35200','34800','35100','123',target+MINUTE_MS-1]]
    return Captured(url,json.dumps(rows).encode(),NOW+1,NOW+2,200)


class LabelAndExportTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.executescript(SCHEMA_SQL)
        seed(self.db)
    def tearDown(self): self.db.close()
    def test_due_horizons_only(self):
        self.assertEqual([(e,h) for e,_,h in due_outcomes(self.db,NOW)],[(E,1)])
    def test_not_due_before_guard(self):
        self.assertEqual(due_outcomes(self.db,ENTRY+HOUR_MS+119999),[])
    def test_exact_1m_target(self):
        url,target,due=outcome_url(ENTRY,1)
        self.assertEqual(target,((due+MINUTE_MS-1)//MINUTE_MS)*MINUTE_MS)
        self.assertIn('symbol=BTCUSDT',url)
        self.assertIn('interval=1m',url)
        self.assertIn('limit=1',url)
    def test_closed_label(self):
        url,target,due=outcome_url(ENTRY,1)
        sample=fake_fetch(url,max_bytes=4096)
        self.assertEqual(verify_label(sample,url,target,due)[1],35100.0)
    def test_wrong_clock_rejected(self):
        url,target,due=outcome_url(ENTRY,1)
        c=Captured(url,json.dumps([[target,1,1,1,'35000',1,target+MINUTE_MS-1]]).encode(),
                   due+1,due+5,200)
        with self.assertRaises(LabelError):
            verify_label(c,url,target,due)
    def test_future_candle_rejected(self):
        url,target,due=outcome_url(ENTRY,1)
        c=Captured(url,json.dumps([[target,1,1,1,'35000',1,target+MINUTE_MS-1]]).encode(),
                   due+120000,due+120001,200)
        c=Captured(url,json.dumps([[target+MINUTE_MS,1,1,1,'35000',1,target+2*MINUTE_MS-1]]).encode(),
                   c.started_ms,c.observed_ms,200)
        with self.assertRaises(LabelError):
            verify_label(c,url,target,due)
    def test_invalid_label_price(self):
        url,target,due=outcome_url(ENTRY,1)
        c=Captured(url,json.dumps([[target,1,1,1,'nan',1,target+MINUTE_MS-1]]).encode(),
                   NOW,NOW+1,200)
        with self.assertRaises(LabelError):
            verify_label(c,url,target,due)
    def test_label_no_pending_no_fetch(self):
        def no_http(*args,**kwargs): raise AssertionError('unexpected HTTP')
        self.assertEqual(label_once(type('S',(),{'db':self.db})(),no_http,
                                    lambda:ENTRY+HOUR_MS)['labels_created'],0)
    def test_sealed_export_is_strict(self):
        obj=sealed_events(self.db,ENTRY+1)
        self.assertEqual(len(obj),1)
        self.assertEqual(set(obj[0]['decisions']),{'news','news_market'})
        self.assertEqual(len(obj[0]['outcomes']),0)
    def test_coverage_counts(self):
        c=coverage(self.db)
        self.assertEqual(c['events'],1)
        self.assertEqual(c['paired_decisions'],1)
        self.assertEqual(c['entry_quotes'],1)
    def test_one_label_enforced_immutable(self):
        target=((ENTRY+HOUR_MS+MINUTE_MS-1)//MINUTE_MS)*MINUTE_MS
        self.db.execute('INSERT INTO outcomes VALUES (?,?,?,?,?)',
                        (E,1,target+MINUTE_MS-1,NOW,35100))
        self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute('UPDATE outcomes SET exit_price=35000')
        self.assertEqual(len(sealed_events(self.db,NOW)[0]['outcomes']),1)
    def test_export_as_of_filters_later_settlement(self):
        target=((ENTRY+HOUR_MS+MINUTE_MS-1)//MINUTE_MS)*MINUTE_MS
        self.db.execute('INSERT INTO outcomes VALUES (?,?,?,?,?)',
                        (E,1,target+MINUTE_MS-1,NOW+120000,35100))
        self.db.commit()
        self.assertEqual(len(sealed_events(self.db,NOW)[0]['outcomes']),0)
    def test_missing_store_readonly_does_not_create(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'no-store'
            with self.assertRaises(ExportError):open_read_only(path)
            self.assertFalse(path.exists())
    def test_store_ro_schema_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'p4'
            with EvidenceStore(root):pass
            db=open_read_only(root)
            db.close()
    def test_reject_invalid_asof(self):
        with self.assertRaises(ExportError): sealed_events(self.db,True)
    def test_real_label_tick_writes_only_due(self):
        with tempfile.TemporaryDirectory() as temp:
            with EvidenceStore(Path(temp)/'p4') as s:
                seed(s.db)
                out=label_once(s,fake_fetch,lambda:NOW)
                self.assertEqual(out['labels_created'],1)
                self.assertEqual(s.db.execute('SELECT COUNT(*) FROM outcomes').fetchone()[0],1)
                self.assertEqual(label_once(s,fake_fetch,lambda:NOW)['labels_created'],0)


if __name__=='__main__':unittest.main()
