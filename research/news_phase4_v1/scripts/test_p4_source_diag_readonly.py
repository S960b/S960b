"""Read-only diagnostic tests. All network captures synthetic, DB temp only."""
import tempfile
from pathlib import Path
import unittest

from p4_evidence_store import EvidenceStore
from p4_source_diag_readonly import reconcile
from p4_public_sources import Captured, FEEDS
from test_p4_live_collector import fake_fetch, fixture_rss, T


class SourceDiagnosticTests(unittest.TestCase):
    def test_unseen_candidate_is_visible_without_sqlite_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'p4'
            with EvidenceStore(root) as store:
                old=fake_fetch(FEEDS['coindesk'],max_bytes=750_000)
                store.observe([(fp,news) for fp,news in
                               __import__('p4_public_sources').parse_feed(old,'coindesk')])
                before=store.db.execute(
                    'SELECT COUNT(*) FROM news_observations').fetchone()[0]
            def new_feed(url,*,max_bytes):
                cap=fake_fetch(url,max_bytes=max_bytes)
                if url==FEEDS['decrypt']:
                    return Captured(url,fixture_rss('Bitcoin ETF brand new headline'),
                                    cap.started_ms,cap.observed_ms,200)
                return cap
            result=reconcile(root,new_feed,lambda:T)
            self.assertEqual(result['sqlite_observations'],before)
            self.assertEqual(result['rss_unseen_fingerprints'],1)
            self.assertEqual(result['rss_unseen_published_last_2h'],1)
            with EvidenceStore(root) as store:
                self.assertEqual(store.db.execute(
                    'SELECT COUNT(*) FROM news_observations').fetchone()[0],before)

    def test_missing_root_must_not_create_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'absent'
            with self.assertRaises(ValueError):
                reconcile(root,fake_fetch,lambda:T)
            self.assertFalse(root.exists())


if __name__=='__main__':
    unittest.main()
