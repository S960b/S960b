import sqlite3
import unittest
from p4_collector_schema import SCHEMA_SQL, SCHEMA_VERSION

H='a'*64

class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:'); self.db.executescript(SCHEMA_SQL)
        self.db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',('e',100000,100001,H,'v1','v1'))
        self.db.execute("INSERT INTO features VALUES ('e','news',99990,99991,?)",(H,))
    def tearDown(self): self.db.close()

    def test_version(self): self.assertEqual(SCHEMA_VERSION,4)
    def test_no_update(self):
        with self.assertRaises(sqlite3.IntegrityError): self.db.execute("UPDATE events SET feature_version='x'")
    def test_no_delete(self):
        with self.assertRaises(sqlite3.IntegrityError): self.db.execute("DELETE FROM events")
    def test_future_feature(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO features VALUES ('e','market',100002,100003,?)",(H,))
    def test_missing_social(self):
        self.db.execute("INSERT INTO source_missing VALUES('e','social',99999,'unavailable')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO features VALUES('e','social',99990,99991,?)",(H,))
    def test_ledger_unique_attempt_per_arm(self):
        self.db.execute("INSERT INTO attempt_reservations VALUES('a','e','news',100001,?)",(H,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO attempt_reservations VALUES('b','e','news',100002,?)",(H,))
    def test_quote_requires_both_arms(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO entry_quotes VALUES('e',100005,100006,100,'binance_public')")
    def test_outcomes_after_due(self):
        self.db.execute("INSERT INTO features VALUES ('e','market',99990,99991,?)",(H,))
        for attempt, arm in (('a','news'),('b','news_market')):
            self.db.execute('INSERT INTO attempt_reservations VALUES (?,?,?,?,?)',(attempt,'e',arm,100001,H))
        for arm in ('news','news_market'):
            self.db.execute('INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)',('e',arm,'UP',.6,'provider','model',100002,H))
        self.db.execute("INSERT INTO entry_quotes VALUES('e',100003,100004,100,'binance_public')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO outcomes VALUES('e',1,100003,100003,101)")
        self.db.execute("INSERT INTO outcomes VALUES('e',1,3700003,3820003,101)")
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM outcomes').fetchone()[0],1)
    def test_observation_after_cutoff_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO features VALUES ('e','market',99980,100001,?)",(H,))

    def test_missing_check_after_cutoff_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO source_missing VALUES('e','social',100001,'unavailable')")

    def test_reservation_requires_market_before_model(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO attempt_reservations VALUES('b','e','news_market',100001,?)",(H,))
        self.db.execute("INSERT INTO features VALUES ('e','market',99990,99991,?)",(H,))
        self.db.execute("INSERT INTO attempt_reservations VALUES('b','e','news_market',100001,?)",(H,))

    def test_reservation_not_before_cutoff(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO attempt_reservations VALUES('a','e','news',99999,?)",(H,))

    def test_decision_not_before_reservation(self):
        self.db.execute("INSERT INTO attempt_reservations VALUES('a','e','news',100020,?)",(H,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)",
                            ('e','news','UP',0.5,'provider','model',100010,H))

    def test_session_immutable(self):
        self.db.execute("INSERT INTO attempt_reservations VALUES('x','e','news',100001,?)",(H,))
        self.db.execute("INSERT INTO attempt_sessions VALUES('x','session123',100002)")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE attempt_sessions SET session_id='changed' WHERE attempt_id='x'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO attempt_sessions VALUES('x','session124',100003)")

    def test_session_not_before_reservation(self):
        self.db.execute("INSERT INTO attempt_reservations VALUES('x','e','news',100010,?)",(H,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO attempt_sessions VALUES('x','session123',100005)")

    def test_seen_is_immutable(self):
        self.db.execute("INSERT INTO news_observations VALUES(?,?,?,?)",(H,99900,'{}',H))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE news_observations SET first_seen_ms=99999")
    def test_claim_is_unique(self):
        self.db.execute("INSERT INTO news_observations VALUES(?,?,?,?)",(H,99900,'{}',H))
        self.db.execute("INSERT INTO claimed_events VALUES(?,?,?)",(H,'e',100002))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO claimed_events VALUES(?,?,?)",(H,'e',100003))
    def test_claim_not_before_first_seen(self):
        self.db.execute("INSERT INTO news_observations VALUES(?,?,?,?)",(H,99900,'{}',H))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO claimed_events VALUES(?,?,?)",(H,'e',99899))
    def test_no_feature_if_missing(self):
        self.db.execute("INSERT INTO features VALUES ('e','market',99990,99991,?)",(H,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO source_missing VALUES('e','market',99999,'unavailable')")

if __name__ == '__main__': unittest.main()

