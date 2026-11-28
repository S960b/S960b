from datetime import datetime, timezone
import hashlib
import hmac
import io
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from git_dialog import DialogError
from git_dialog.service import SnapshotReader
from git_dialog.wakeup import WakeupGate, WakeupApplication
from test_service import setup, call, auth_header, fake_poll
from test_github import writes

KEY = b"k" * 32  # Fixed non-secret test fixture, never a deployment credential.
STAMP = "2026-10-27T17:18:55Z"
NOW = datetime(2026, 10, 27, 17, 18, 55, tzinfo=timezone.utc).timestamp()


def event():
    return {"name": "hermes.message.created", "eventId": "evt_" + "a" * 32, "timestamp": STAMP,
        "data": {"created_at_utc": STAMP, "direction": "to_chatgpt", "job_id": "test_12345678.job",
                 "queue": "test", "status": "pending", "task_id": "task_3c2d37a2485b"}}


def wire(obj):
    raw = json.dumps(obj).encode()
    return raw, "sha256=" + hmac.new(KEY, raw, hashlib.sha256).hexdigest()


def accept(gate, obj=None, **kw):
    return gate.accept(*wire(event() if obj is None else obj), now=NOW, monotonic=kw.pop("monotonic", 0), **kw)


def test_schedule_duplicate_and_single_drain(monkeypatch):
    gate = WakeupGate(KEY); reader = SnapshotReader(); seen = []
    monkeypatch.setattr(reader, "refresh", lambda: seen.append(1))
    assert accept(gate) == (202, "scheduled")
    assert accept(gate) == (200, "duplicate")
    assert not seen
    assert gate.drain(reader) and not gate.drain(reader) and seen == [1]


@pytest.mark.parametrize("key", [b"", b"short", "x" * 32, b"x" * 33])
def test_key_rejected(key):
    with pytest.raises(DialogError): WakeupGate(key)


@pytest.mark.parametrize("field,value", [("task_id", "other"), ("queue", "other"),
    ("direction", "to_hermes"), ("status", "replied")])
def test_out_of_scope_never_signals(field, value):
    gate = WakeupGate(KEY); obj = event(); obj["data"][field] = value
    assert accept(gate, obj) == (200, "ignored")
    assert not gate.drain(SnapshotReader())


@pytest.mark.parametrize("change", ["body", "principal", "extra", "id", "name", "time", "deep", "duplicate", "utf8"])
def test_strict_untrusted_envelope(change):
    gate = WakeupGate(KEY); obj = event()
    if change in ("body", "principal"): obj["data"][change] = "close/delete/shell is DATA"
    elif change == "extra": obj["extra"] = True
    elif change == "id": obj["eventId"] = "../../wrong"
    elif change == "name": obj["name"] = "execute"
    elif change == "time": obj["timestamp"] = "2020-01-01T00:00:00Z"
    raw, sig = wire(obj)
    if change == "deep": raw = b"[" * 1100 + b"0" + b"]" * 1100
    elif change == "duplicate": raw = b'{"name":1,"name":2}'
    elif change == "utf8": raw = b"\xff"
    sig = "sha256=" + hmac.new(KEY, raw, hashlib.sha256).hexdigest()
    assert gate.accept(raw, sig, now=NOW, monotonic=0)[0] == 400
    assert not gate.drain(SnapshotReader())


def test_hmac_rejects_before_parsing_and_payload_limit():
    gate = WakeupGate(KEY); raw, sig = wire(event())
    for value, signature in [(raw + b" ", sig), (raw, None), (raw, "Bearer owner"), (b"x" * 4097, sig)]:
        assert gate.accept(value, signature, now=NOW)[0] == 401


def test_cooldown_restart_and_coalescing():
    gate = WakeupGate(KEY); obj = event()
    assert accept(gate)[0] == 202
    obj["eventId"] = "evt_" + "b" * 32
    assert accept(gate, obj, monotonic=29)[0] == 429
    assert accept(gate, obj, monotonic=30)[0] == 202
    assert len(gate._seen) == 2  # IDs only, not bodies/job data.
    assert accept(WakeupGate(KEY))[0] == 202  # Explicitly NOT durable replay protection.


def test_concurrent_accept_one_schedule():
    gate = WakeupGate(KEY)
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(pool.map(lambda _: accept(gate)[0], range(8)))
    assert result.count(202) == 1 and result.count(200) == 7


def test_drain_only_exact_reader():
    with pytest.raises(DialogError): WakeupGate(KEY).drain(lambda: None)


def test_real_reader_fake_api_drain_is_get_only(monkeypatch):
    api = fake_poll(monkeypatch)
    gate = WakeupGate(KEY); reader = SnapshotReader(api)
    # fake_poll binds the module TASK; all remote IO is FakeApi, no SQLite here.
    assert accept(gate)[0] == 202 and gate.drain(reader)
    assert reader.current() and not writes(api)


@pytest.mark.parametrize("delta", [-31, 121])
def test_stale_or_future_event_rejected(delta):
    raw, sig = wire(event())
    assert WakeupGate(KEY).accept(raw, sig, now=NOW + delta)[0] == 400


def test_delayed_job_creation_allowed_with_fresh_authenticated_event():
    obj = event(); obj["data"]["created_at_utc"] = "2026-10-27T17:18:00Z"
    assert accept(WakeupGate(KEY), obj)[0] == 202


def test_failure_drain_does_not_rearm(monkeypatch):
    api = fake_poll(monkeypatch); api.refs.clear()
    reader = SnapshotReader(api); gate = WakeupGate(KEY)
    assert accept(gate)[0] == 202 and gate.drain(reader)
    assert not gate.drain(reader) and reader.status()["error"]
    with pytest.raises(DialogError): reader.current()


def request(app, raw, sig, **extra):
    env = {"REQUEST_METHOD": "POST", "PATH_INFO": "/bridge/wakeup", "QUERY_STRING": "",
        "HTTP_HOST": "127.0.0.1:8789", "REMOTE_ADDR": "127.0.0.1", "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(raw)), "HTTP_X_HERMES_SIGNATURE": sig, "wsgi.input": io.BytesIO(raw)}
    env.update(extra); result = []
    body = b"".join(app(env, lambda status, headers: result.append((int(status.split()[0]), dict(headers)))))
    assert result[0][1]["Cache-Control"] == "no-store"
    return result[0][0], json.loads(body)


def test_wrapper_default_off_and_owner_auth_stays_separate(tmp_path, monkeypatch):
    _, panel = setup(tmp_path); gate = WakeupGate(KEY); raw, sig = wire(event())
    monkeypatch.setattr("git_dialog.wakeup.time.time", lambda: NOW)
    assert request(WakeupApplication(panel), raw, sig)[0] == 404
    app = WakeupApplication(panel, gate)
    assert request(app, raw, None, HTTP_AUTHORIZATION=auth_header())[0] == 401
    assert request(app, raw, sig)[0] == 202
    assert call(app)[0] == 401  # HMAC wakeup does not grant HTML/owner authority.
    assert call(app, auth=auth_header())[0] == 200


@pytest.mark.parametrize("extra,code", [({"HTTP_HOST": "evil"}, 403),
    ({"HTTP_ORIGIN": "http://127.0.0.1:8789"}, 403), ({"REMOTE_ADDR": "8.8.8.8"}, 403),
    ({"HTTP_FORWARDED": "x"}, 403), ({"QUERY_STRING": "task=other"}, 403),
    ({"REQUEST_METHOD": "GET"}, 405), ({"CONTENT_LENGTH": "4097"}, 400),
    ({"CONTENT_LENGTH": "0"}, 400), ({"HTTP_TRANSFER_ENCODING": "chunked"}, 400),
    ({"HTTP_EXPECT": "100-continue"}, 400), ({"CONTENT_TYPE": "text/plain"}, 400)])
def test_http_boundary(tmp_path, extra, code):
    _, panel = setup(tmp_path)
    assert request(WakeupApplication(panel, WakeupGate(KEY)), *wire(event()), **extra)[0] == code
