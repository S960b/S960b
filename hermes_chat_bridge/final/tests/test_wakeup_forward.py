import json
import threading
import pytest
from git_dialog import DialogError
from git_dialog import wakeup_forward as forward
from git_dialog.wakeup import WakeupGate
from git_dialog.wakeup_smoke import fixture_event

KEY = b'x' * 32

@pytest.mark.parametrize('port', [0, 80, 8787, 8789, 65536])
def test_fixed_ports(port):
    with pytest.raises(DialogError): forward.WakeupForwarder(KEY, port=port)

def test_default_no_thread():
    sender = forward.WakeupForwarder(KEY)
    assert sender._thread is None and not sender.offer(fixture_event()['data'])
    sender.stop()
    with pytest.raises(DialogError): sender.start()

def test_metadata_signed_and_normalized(monkeypatch):
    done = threading.Event(); received = []
    def post(port, raw, signature):
        received.append(json.loads(raw))
        assert port == 8790 and WakeupGate(KEY).accept(raw, signature)[0] == 202
        done.set()
    monkeypatch.setattr(forward, '_post', post)
    sender = forward.WakeupForwarder(KEY)
    data = fixture_event()['data']; data['created_at_utc'] = '2020-01-01T00:00:00Z'
    sender.start()
    try:
        assert sender.offer(data) and done.wait(1)
        assert set(received[0]) == {'name', 'eventId', 'timestamp', 'data'}
        assert received[0]['data'] == data and not sender.offer(data)
    finally: sender.stop()
    assert not sender._thread.is_alive()

@pytest.mark.parametrize('change', ['task_id', 'direction', 'queue', 'status', 'extra'])
def test_out_of_scope(monkeypatch, change):
    monkeypatch.setattr(forward, '_post', lambda *a: pytest.fail('network'))
    sender = forward.WakeupForwarder(KEY); sender.start()
    try:
        data = fixture_event()['data']; data[change] = 'wrong'
        assert not sender.offer(data)
    finally: sender.stop()

def test_error_no_retry(monkeypatch):
    done = threading.Event(); calls = []
    def post(*a):
        calls.append(1); done.set(); raise RuntimeError('not logged')
    monkeypatch.setattr(forward, '_post', post)
    sender = forward.WakeupForwarder(KEY); sender.start()
    try: assert sender.offer(fixture_event()['data']) and done.wait(1)
    finally: sender.stop()
    assert calls == [1]

def test_http_fixed_route_and_host(monkeypatch):
    sent, addresses = [], []
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def settimeout(self, value): assert 0 < value <= 2
        def connect(self, addr): addresses.append(addr)
        def sendall(self, raw): sent.append(raw)
        def recv(self, limit): return b'HTTP/1.1 202 Accepted\r\nContent-Length: 0\r\n\r\n'
    monkeypatch.setattr(forward.socket, 'socket', lambda *a: Socket())
    assert forward._post(8790, b'{}', 'sha256=' + 'a' * 64)
    assert addresses == [('127.0.0.1', 8790)]
    assert sent[0].startswith(b'POST /bridge/wakeup HTTP/1.1\r\nHost: 127.0.0.1:8790')
    assert b'Authorization:' not in sent[0]

def test_http_trickle_has_total_deadline(monkeypatch):
    clock = [0.0]
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def settimeout(self, value): pass
        def connect(self, addr): pass
        def sendall(self, raw): pass
        def recv(self, limit): clock[0] += 1; return b'x'
    monkeypatch.setattr(forward.socket, 'socket', lambda *a: Socket())
    monkeypatch.setattr(forward.time, 'monotonic', lambda: clock[0])
    assert not forward._post(8790, b'{}', 'sha256=' + 'a' * 64)
    assert clock[0] == 2

def test_created_after_event_rejected():
    import hashlib
    import hmac
    obj = fixture_event(); obj['data']['created_at_utc'] = '2099-01-01T00:00:00Z'
    raw = json.dumps(obj).encode()
    sig = 'sha256=' + hmac.new(KEY, raw, hashlib.sha256).hexdigest()
    assert WakeupGate(KEY).accept(raw, sig)[0] == 400

def test_forward_smoke_cli_no_parameters():
    from git_dialog.forward_smoke import main
    with pytest.raises(SystemExit): main(['--self-test', '--port', '8790'])


@pytest.mark.parametrize('accepted', [False, True])
def test_only_fixed_nonsecret_deployment_notices(monkeypatch, capsys, accepted):
    done = threading.Event()
    def post(*args): done.set(); return accepted
    monkeypatch.setattr(forward, '_post', post)
    sender = forward.WakeupForwarder(KEY); sender.start()
    try: assert sender.offer(fixture_event()['data']) and done.wait(1)
    finally: sender.stop()
    captured = capsys.readouterr()
    assert captured.out == ''
    expected = '{"beta_wakeup":"forwarder_started"}\n'
    if accepted: expected += '{"beta_wakeup":"receiver_accepted"}\n'
    assert captured.err == expected


def test_notice_failure_is_isolated(monkeypatch):
    def fail(*args, **kw): raise OSError('must not propagate')
    monkeypatch.setattr('builtins.print', fail)
    forward._notice()
    forward._notice(accepted=True)
