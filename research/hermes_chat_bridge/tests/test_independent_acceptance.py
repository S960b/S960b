"""Independent post-fix checks; no network or production DB access."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bridge_server as bs
import socket
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

def test_pinned_socket_preserves_tls_hostname(monkeypatch):
    calls=[]
    class Raw:
        def settimeout(self, value): calls.append(('timeout',value))
        def connect(self, address): calls.append(('connect',address))
        def close(self): calls.append(('close',))
    class TLS:
        def wrap_socket(self, sock, server_hostname):
            calls.append(('sni',server_hostname)); return sock
    monkeypatch.setattr(bs.ssl,'create_default_context',lambda: TLS())
    monkeypatch.setattr(bs.socket,'socket',lambda *a: Raw())
    monkeypatch.setattr(bs.socket,'getaddrinfo',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('second DNS lookup')))
    connection=bs.PinnedHTTPSConnection('callback.example','93.184.216.34',443,3)
    connection.connect()
    assert ('connect',('93.184.216.34',443)) in calls
    assert ('sni','callback.example') in calls

def test_internal_state_consumed_once_across_connections(tmp_path):
    path=str(tmp_path/'state.db')
    a,b=bs.Store(path),bs.Store(path)
    try:
        a.save_state('s',{'marker':'synthetic'})
        barrier=Barrier(2,timeout=3)
        def consume(store):
            barrier.wait(); return store.attempt_state('s',True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            f1=pool.submit(consume,a); f2=pool.submit(consume,b)
            results=[f1.result(timeout=5),f2.result(timeout=5)]
        assert sum(r is not None for r in results)==1
        assert {'marker':'synthetic'} in results
        assert a.load_state('s') is None
    finally:
        a.conn.close();b.conn.close()

def test_pinned_socket_closes_on_tls_failure(monkeypatch):
    calls=[]
    class Raw:
        def settimeout(self, value): pass
        def connect(self, address): pass
        def close(self): calls.append('closed')
    class TLS:
        def wrap_socket(self,*a,**kw): raise bs.ssl.SSLCertVerificationError('synthetic invalid cert')
    monkeypatch.setattr(bs.ssl,'create_default_context',lambda:TLS())
    monkeypatch.setattr(bs.socket,'socket',lambda *a:Raw())
    connection=bs.PinnedHTTPSConnection('callback.example','93.184.216.34',443,3)
    import pytest
    with pytest.raises(bs.ssl.SSLCertVerificationError): connection.connect()
    assert calls==['closed']
