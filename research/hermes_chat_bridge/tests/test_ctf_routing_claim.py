"""CTF routing: queue isolation + atomic Work claim."""
import threading
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge_queue import Queue, validate_queue_name, validate_consumer_id


def test_queue_and_consumer_validation():
    assert validate_queue_name('ctf_crypto-01') == 'ctf_crypto-01'
    assert validate_consumer_id('work.crypto:01') == 'work.crypto:01'
    for bad in ('', '../x', 'a b', 'x'*65):
        try:
            validate_queue_name(bad)
            assert False, bad
        except ValueError:
            pass


def test_claim_hides_text_from_loser_and_protects_reply(tmp_path):
    q = Queue(str(tmp_path/'q.db'))
    job = q.put_message('secret task', direction='to_chatgpt', queue='ctf_A')
    a = q.claim_for_chat(job['job_id'], 'work.A', 900)
    assert a['status'] == 'claimed' and a['text'] == 'secret task'
    b = q.claim_for_chat(job['job_id'], 'work.B', 900)
    assert b['status'] == 'already_claimed' and 'text' not in b
    assert q.put_claimed_reply(job['job_id'], 'work.B', 'wrong')['status'] == 'not_owner'
    ok = q.put_claimed_reply(job['job_id'], 'work.A', 'done')
    assert ok['status'] == 'replied'
    again = q.claim_for_chat(job['job_id'], 'work.B', 900)
    assert again['status'] == 'already_replied' and 'text' not in again
    q._conn.close()


def test_same_consumer_renews_and_expired_claim_can_move(tmp_path):
    q = Queue(str(tmp_path/'q.db'))
    job = q.put_message('task', direction='to_chatgpt', queue='ctf_A')
    a1 = q.claim_for_chat(job['job_id'], 'work.A', 60)
    a2 = q.claim_for_chat(job['job_id'], 'work.A', 120)
    assert a2['status'] == 'claimed' and a2['idempotent']
    assert a2['lease_until_epoch'] > a1['lease_until_epoch']
    q._conn.execute('UPDATE jobs SET lease_until_epoch=0 WHERE job_id=?', (job['job_id'],))
    q._conn.commit()
    b = q.claim_for_chat(job['job_id'], 'work.B', 60)
    assert b['status'] == 'claimed' and b['text'] == 'task'
    q._conn.close()


def test_concurrent_claim_exactly_one_initial_winner(tmp_path):
    db = str(tmp_path/'race.db')
    seed = Queue(db)
    job = seed.put_message('race task', direction='to_chatgpt', queue='ctf_A')
    seed._conn.close()
    barrier = threading.Barrier(10)
    results = []
    lock = threading.Lock()

    def run(i):
        q = Queue(db)
        try:
            barrier.wait()
            r = q.claim_for_chat(job['job_id'], f'work.{i}', 900)
            with lock:
                results.append(r)
        finally:
            q._conn.close()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()
    winners = [r for r in results if r['status'] == 'claimed' and 'text' in r]
    losers = [r for r in results if r['status'] == 'already_claimed']
    assert len(winners) == 1
    assert len(losers) == 9
    assert all('text' not in r for r in losers)
