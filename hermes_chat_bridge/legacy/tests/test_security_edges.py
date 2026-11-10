import asyncio
import base64
import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
import pytest
import bridge_server as bs
from bridge_queue import Queue
from test_review_3b082fe import bridge, api, rpc, sub_params, stored_sub, BASE


def test_login_atomic_consume_across_connections(bridge):
    bridge.store.save_state('s', {'client_state':'c'})
    other = bs.Store(bs.DB_PATH)
    barrier = Barrier(2)
    def consume(store):
        barrier.wait()
        return store.attempt_state('s', True)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(consume, [bridge.store, other]))
        assert sum(r is not None for r in results) == 1
    finally: other.conn.close()


def test_tls_connect_uses_literal_address_and_original_sni(monkeypatch):
    calls = []
    class Sock:
        def settimeout(self, t): calls.append(('timeout',t))
        def connect(self, a): calls.append(('connect',a))
        def close(self): calls.append(('close',))
    class TLS:
        check_hostname = True
        verify_mode = 2
        def wrap_socket(self, raw, server_hostname):
            calls.append(('sni',server_hostname)); return raw
    monkeypatch.setattr(bs.ssl, 'create_default_context', lambda: TLS())
    monkeypatch.setattr(bs.socket, 'socket', lambda *a: Sock())
    monkeypatch.setattr(bs.socket, 'getaddrinfo', lambda *a, **k: pytest.fail('unexpected DNS during connect'))
    conn = bs.PinnedHTTPSConnection('callback.example', '93.184.216.34', 443, 1)
    conn.connect()
    assert ('connect', ('93.184.216.34',443)) in calls
    assert ('sni','callback.example') in calls
    assert conn._context.check_hostname
    assert conn._context.verify_mode == bs.ssl.CERT_REQUIRED


def test_callback_workers_bounded_even_when_cancelled(bridge, monkeypatch):
    release = Event(); started = Event()
    count = 0
    def slow(*a):
        nonlocal count
        count += 1
        if count == 4: started.set()
        release.wait(2)
        return 200,b'{}'
    monkeypatch.setattr(bridge, '_post_pinned', slow)
    async def check():
        tasks = [asyncio.create_task(bridge._http_post('https://callback.example/',b'{}',{})) for _ in range(4)]
        try:
            while not started.is_set(): await asyncio.sleep(.001)
            with pytest.raises(RuntimeError, match='busy'):
                await bridge._http_post('https://callback.example/',b'{}',{})
            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError): await tasks[0]
            with pytest.raises(RuntimeError, match='busy'):
                await bridge._http_post('https://callback.example/',b'{}',{})
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(check())


def test_challenge_new_id_each_attempt(bridge, monkeypatch):
    calls=[]
    async def receiver(url,body,headers,timeout=10):
        calls.append(headers['webhook-id'])
        return 200, json.dumps({'challenge':json.loads(body)['challenge']}).encode()
    monkeypatch.setattr(bridge,'_http_post',receiver)
    s=stored_sub(bridge)
    assert asyncio.run(bridge.verify_callback(s))
    assert asyncio.run(bridge.verify_callback(s))
    assert calls[0] != calls[1]


def test_terminal_age_limit(bridge, monkeypatch):
    s=stored_sub(bridge)
    bridge.store.enqueue_delivery(s['id'],'evt_old',{})
    bridge.store.conn.execute('UPDATE deliveries SET created_at=?',(time.time()-86401,)); bridge.store.conn.commit()
    async def receiver(*a,**k): pytest.fail('must not deliver')
    monkeypatch.setattr(bridge,'_http_post',receiver)
    asyncio.run(bridge.deliver_pending())
    row=bridge.store.conn.execute('SELECT * FROM deliveries').fetchone()
    assert row['done'] == 1 and row['terminal_reason']=='retry_budget_exhausted'


def test_cli_list_and_subs_all_rows(bridge, monkeypatch, capsys):
    import bridge_cli
    bridge.queue.put_message('one');bridge.queue.put_message('two')
    s=stored_sub(bridge);s['id']='sub_two';bridge.store.save_sub(s)
    monkeypatch.setattr('sys.argv',['bridge-cli','list'])
    assert bridge_cli.main()==0
    assert len(capsys.readouterr().out.splitlines())==2
    monkeypatch.setattr('sys.argv',['bridge-cli','subs'])
    assert bridge_cli.main()==0
    assert len(capsys.readouterr().out.splitlines())==2
    bridge.store.conn.execute('DELETE FROM subs');bridge.store.conn.commit()
    assert bridge_cli.main()==0
    assert capsys.readouterr().out == ''


def test_subscription_dns_does_not_block(bridge, monkeypatch):
    def slow_validate(url): time.sleep(.1)
    monkeypatch.setattr(bridge,'_validate_callback_url',slow_validate)
    async def yes(sub): return True
    monkeypatch.setattr(bridge,'verify_callback',yes)
    async def check():
        start=time.monotonic()
        task=asyncio.create_task(bridge.events_subscribe(None,sub_params()))
        await asyncio.sleep(.01)
        assert time.monotonic()-start < .08
        await task
    asyncio.run(check())
